"""Synthetic AIS traffic with physically coherent kinematics.

The previous build drew every track with np.linspace -- dead-straight lines --
and filled cog_deg / heading_deg with UNIFORM RANDOM NUMBERS unrelated to the
motion. Downstream that was worse than ugly: the map rotated ship glyphs to
noise, and any evidence phrased as "altered course by N degrees" was computed
from values that never described the vessel's movement.

This build integrates each track kinematically: position advances along the
current heading at the current speed every fix, heading and speed evolve with
smooth correlated noise plus deliberate waypoint turns, and cog/heading/sog
are derived FROM the motion. Tracks meander the way real AIS tracks do, and
every kinematic column now agrees with the geometry.

The planted culprit remains exact where it matters: it passes through the
supplied origin at the centre of the origin window (position pinned, not
approximate), slows if asked, goes dark if asked -- and when it alters course
it now REALLY alters course: a dump-and-turn manoeuvre the attribution engine
can measure from the geometry instead of from noise.
"""

import numpy as np
import pandas as pd

KN_TO_DEG_LAT_PER_HR = 1.852 / 111.32   # knots -> degrees latitude per hour

VESSEL_TYPES = ["Tanker", "Cargo", "Fishing", "Passenger"]

try:
    # 1-km offline land mask. Ships that sail across peninsulas are the
    # fastest way to lose a judge; with the mask, every emitted fix is at sea
    # and tracks end at the coastline like real AIS coverage does.
    from global_land_mask import globe as _globe

    def _is_ocean(lat, lon):
        import numpy as _np
        la = _np.clip(_np.asarray(lat), -89.9, 89.9)
        lo = (_np.asarray(lon) + 180.0) % 360.0 - 180.0
        return ~_globe.is_land(la, lo)
except Exception:                                    # pragma: no cover
    def _is_ocean(lat, lon):
        import numpy as _np
        return _np.ones(_np.shape(lat), dtype=bool)


def _ocean_run(lat, lon, i_anchor):
    """Keep-mask for the longest CONTIGUOUS at-sea stretch containing the
    anchor fix. Contiguity matters: dropping only the on-land fixes would
    leave one LineString whose rendering still cuts across the peninsula."""
    import numpy as _np
    ocean = _is_ocean(lat, lon)
    keep = _np.zeros(len(lat), dtype=bool)
    if not ocean[i_anchor]:
        return keep, 0
    a = i_anchor
    while a > 0 and ocean[a - 1]:
        a -= 1
    b = i_anchor
    while b < len(lat) - 1 and ocean[b + 1]:
        b += 1
    keep[a:b + 1] = True
    return keep, b - a + 1


# Realistic fleets: name pool + plausible flag MID prefixes. Drawn per fleet
# seed so every incident has its own vessels; the synthetic provenance stays
# declared in the `source` column and everywhere in the UI.
NAME_POOL = [
    "PACIFIC HARMONY", "GULF PEARL", "SEA HORIZON", "OCEAN SENTINEL",
    "ASIAN GLORY", "STAR OF HORMUZ", "BLUE MERIDIAN", "CORAL EMPRESS",
    "SILVER DUNE", "MONSOON TRADER", "EASTERN PROMISE", "GOLDEN WAKE",
    "NORTHERN LIGHT", "CRIMSON TIDE", "JADE ALBATROSS", "IRON PELICAN",
    "WHITE CARAVEL", "AMBER SEAWAY", "DELTA MARINER", "ROYAL KESTREL",
    "EMERALD PASSAGE", "DESERT SWAN", "HARBOUR QUEEN", "LOTUS VOYAGER",
    "TITAN CREST", "SAPPHIRE COAST", "MISTRAL RUNNER", "ORION TRADER",
    "COMPASS ROSE", "TYPHOON GLORY", "ANCHOR BAY", "MERIDIAN STAR",
    "FALCON REACH", "PEARL RIVER", "CYCLONE VENTURE", "ATLAS NAVIGATOR",
    "SIROCCO WIND", "LAGOON SPIRIT", "GRANITE SHORE", "VELVET SEA",
]
TYPE_PREFIX = {"Tanker": "MT", "Cargo": "MV", "Passenger": "MS", "Fishing": "FV"}
MID_PREFIXES = [419, 470, 477, 538, 563, 636, 354, 215, 237, 371]


def _bearing_deg(lat1, lon1, lat2, lon2):
    rad = np.pi / 180
    y = np.sin((lon2 - lon1) * rad) * np.cos(lat2 * rad)
    x = (np.cos(lat1 * rad) * np.sin(lat2 * rad) -
         np.sin(lat1 * rad) * np.cos(lat2 * rad) * np.cos((lon2 - lon1) * rad))
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def _course_from_positions(lat, lon):
    """Course-over-ground of each fix from the actual displacement to the
    next fix. The final fix keeps the previous course."""
    cog = np.zeros(len(lat), dtype=float)
    cog[:-1] = _bearing_deg(lat[:-1], lon[:-1], lat[1:], lon[1:])
    if len(cog) > 1:
        cog[-1] = cog[-2]
    return cog


def _smooth_noise(rng, n, sigma, kernel=9):
    """Correlated (low-frequency) noise: white noise convolved with a box
    kernel, so tracks meander instead of jittering fix to fix."""
    w = rng.normal(0.0, sigma, n + kernel)
    k = np.ones(kernel) / kernel
    return np.convolve(w, k, mode="same")[:n]


def _lane_track(rng, n, times_hr, anchor, course_deg, speeds, wobble=0.012):
    """A vessel on a lane: it crosses `anchor` at a random moment and holds a
    course with gentle lateral meander. No bouncing walls -- real ships do not
    orbit a scene, they transit through it and leave coverage."""
    i_anchor = rng.integers(0, n)
    dt = np.diff(times_hr, prepend=times_hr[0])
    step = speeds * dt * KN_TO_DEG_LAT_PER_HR
    s = np.cumsum(step)
    s -= s[i_anchor]
    b = np.radians(course_deg)
    lat = anchor[1] + s * np.cos(b)
    lon_scale = 1.0 / max(np.cos(np.radians(anchor[1])), 0.2)
    lon = anchor[0] + s * np.sin(b) * lon_scale
    lateral = _smooth_noise(rng, n, wobble, kernel=21)
    lat += lateral * np.cos(b + np.pi / 2)
    lon += lateral * np.sin(b + np.pi / 2) * lon_scale
    return lat, lon


def _fishing_track(rng, n, times_hr, centre, radius_deg, speeds):
    """Loitering fishing pattern: hop between random waypoints inside a small
    ground, at trawling speed. Small local loops -- not a tangle the size of
    the investigation zone."""
    n_wp = int(rng.integers(5, 9))
    wps = [(centre[0] + rng.uniform(-radius_deg, radius_deg),
            centre[1] + rng.uniform(-radius_deg, radius_deg))
           for _ in range(n_wp)]
    seg = [np.hypot(b[0] - a[0], b[1] - a[1])
           for a, b in zip(wps[:-1], wps[1:])]
    total = sum(seg) or 1e-6
    dt = np.diff(times_hr, prepend=times_hr[0])
    s = np.cumsum(speeds * dt * KN_TO_DEG_LAT_PER_HR) % total
    lat = np.empty(n); lon = np.empty(n)
    for i, si in enumerate(s):
        acc = 0.0
        placed = False
        for (a, b), L in zip(zip(wps[:-1], wps[1:]), seg):
            if si <= acc + L and L > 0:
                f = (si - acc) / L
                lon[i] = a[0] + (b[0] - a[0]) * f
                lat[i] = a[1] + (b[1] - a[1]) * f
                placed = True
                break
            acc += L
        if not placed:
            lon[i], lat[i] = wps[-1]
    lat += _smooth_noise(rng, n, 0.002, kernel=7)
    lon += _smooth_noise(rng, n, 0.002, kernel=7)
    return lat, lon


def _background_track(rng, n, times_hr, bbox):
    """One background vessel in the wider region around the scene.

    Traffic is simulated over a domain several times the scene footprint, on
    lanes with headings drawn from the full compass -- so the map shows ships
    converging from many directions and passing through, the way a strait
    actually looks, instead of a knot of vessels orbiting the scene box.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    cx, cy = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2
    half = max(0.6, 3.0 * max(lon_max - lon_min, lat_max - lat_min))
    kind = rng.choice(["transit", "fishing", "coastal"], p=[0.6, 0.25, 0.15])

    near_scene = rng.random() < 0.45
    if near_scene:
        anchor = (cx + rng.uniform(-0.12, 0.12), cy + rng.uniform(-0.12, 0.12))
    else:
        anchor = (cx + rng.uniform(-half, half), cy + rng.uniform(-half, half))

    # anchors must be at sea; a berth in the desert helps nobody
    for _ in range(60):
        if _is_ocean(anchor[1], anchor[0]):
            break
        anchor = (cx + rng.uniform(-half, half), cy + rng.uniform(-half, half))

    if kind == "fishing":
        speeds = np.clip(rng.uniform(3, 6) + _smooth_noise(rng, n, 0.8), 0.8, 9)
        for _ in range(40):
            r = rng.uniform(0.02, 0.05)
            lat, lon = _fishing_track(rng, n, times_hr, anchor, r, speeds)
            if _is_ocean(lat, lon).all():
                break
            anchor = (cx + rng.uniform(-half, half), cy + rng.uniform(-half, half))
        keep = _is_ocean(lat, lon)
        return lat, lon, speeds, "Fishing", keep

    if kind == "transit":
        speeds = np.clip(rng.uniform(11, 19) + _smooth_noise(rng, n, 0.35), 6, 24)
        wobble = 0.012
        vtype = rng.choice(["Tanker", "Cargo", "Passenger"], p=[0.4, 0.45, 0.15])
    else:
        speeds = np.clip(rng.uniform(6, 11) + _smooth_noise(rng, n, 0.5), 3, 14)
        wobble = 0.02
        vtype = rng.choice(["Cargo", "Fishing", "Passenger"])

    # try several courses; keep the one with the longest at-sea run through
    # the anchor -- lanes follow the water, not the compass
    best = None
    i_anchor_guess = n // 2
    for _ in range(14):
        lat, lon = _lane_track(rng, n, times_hr, anchor,
                               rng.uniform(0, 360), speeds, wobble=wobble)
        d = np.hypot(lat - anchor[1], lon - anchor[0])
        ia = int(np.argmin(d))
        keep, span = _ocean_run(lat, lon, ia)
        if best is None or span > best[3]:
            best = (lat, lon, keep, span)
        if span > 0.7 * n:
            break
    lat, lon, keep, _span = best
    return lat, lon, speeds, vtype, keep


def _culprit_track(rng, times, culprit_config):
    """The planted culprit. Geometry is exact where the gates look:

      * at the CENTRE of the origin window the vessel is AT the origin
        (pinned after noise, not approximately near it);
      * approach runs on a fixed base course, so the axis gate sees the same
        alignment it always did;
      * `slowdown` drops sog to 6 kn around the pass -- and because positions
        integrate the speed profile, the fixes visibly bunch up on the map;
      * after the pass the vessel executes a real ~180 degree turn and leaves
        the way it came, laterally offset -- a dump-and-turn the engine can
        measure from the track itself.
    """
    n = len(times)
    origin = culprit_config["origin"]
    o_lat, o_lon = float(origin["lat"]), float(origin["lon"])
    w_start = pd.to_datetime(origin["window_start_utc"], utc=True)
    w_end = pd.to_datetime(origin["window_end_utc"], utc=True)
    pass_time = w_start + (w_end - w_start) / 2
    i_pass = int(np.argmin(np.abs((times - pass_time).total_seconds())))

    beh = culprit_config.get("behaviour", {})

    # speed profile first: positions are integrated from it
    sog = np.full(n, 14.0) + _smooth_noise(rng, n, 0.3)
    if beh.get("slowdown"):
        slow = (times >= pass_time - pd.Timedelta(minutes=30)) & \
               (times <= pass_time + pd.Timedelta(minutes=30))
        sog[np.asarray(slow)] = 6.0 + _smooth_noise(rng, int(slow.sum()), 0.2)

    dt_hr = np.diff(times.view("int64")).astype(float) / 3.6e12
    dt_hr = np.append(dt_hr, dt_hr[-1] if len(dt_hr) else 1 / 12)

    # distance travelled along-track, origin-crossing at i_pass
    step_deg = sog * dt_hr * KN_TO_DEG_LAT_PER_HR
    s = np.concatenate([[0.0], np.cumsum(step_deg[:-1])])
    s -= s[i_pass]                       # s=0 exactly at the origin pass

    # Course follows the slick's axis when the caller supplies it: that is
    # the physical story (the discharge trails along the vessel's course) and
    # exactly what the trajectory gate measures. Fallback keeps the historic
    # diagonal for callers without axis knowledge.
    axis = culprit_config.get("axis_deg")
    base0 = float(axis) % 180.0 if axis is not None else 44.0
    base = base0 + rng.uniform(-4, 4)
    b_rad = np.radians(base)
    # lateral offset separates the outbound leg so the U-turn reads on the map
    lateral = np.where(s > 0, np.minimum(np.abs(s) * 0.35, 0.02), 0.0)

    # approach: along +base towards the origin. Departure: reciprocal course
    # near the origin (that is what the trajectory gate measures), then a
    # gradual curve away -- otherwise the exit leg retraces the entry leg for
    # hundreds of km and draws as two parallel rays across the whole map.
    lon_scale = 1.0 / max(np.cos(np.radians(o_lat)), 0.2)
    curve_start = 0.35                      # deg along-track before curving
    extra = np.where(s > curve_start,
                     np.minimum((s - curve_start) * 60.0, 55.0), 0.0)
    out_deg = np.radians((np.degrees(b_rad) + extra) % 360.0)
    lat = np.where(s < 0, o_lat + s * np.cos(b_rad),
                   o_lat - s * np.cos(out_deg))
    lon = np.where(s < 0, o_lon + s * np.sin(b_rad) * lon_scale,
                   o_lon - s * np.sin(out_deg) * lon_scale)
    # apply the lateral offset perpendicular to the base course
    lat += lateral * np.cos(b_rad + np.pi / 2)
    lon += lateral * np.sin(b_rad + np.pi / 2) * lon_scale

    # gentle meander, pinned to zero at the origin pass
    mlat = _smooth_noise(rng, n, 0.004, kernel=15)
    mlon = _smooth_noise(rng, n, 0.004, kernel=15)
    lat += mlat - mlat[i_pass]
    lon += mlon - mlon[i_pass]

    # Going dark means the fixes DO NOT EXIST -- a gap_flag on rows that are
    # still present is a gap no detector can ever see, which is why ais_gap
    # scored 0.0 for every vessel in every earlier run. `keep` marks which
    # fixes were actually transmitted; the caller drops the rest. The last
    # fix before and first after the silence carry gap_flag=True so the gap
    # boundaries stay visible in the schema.
    keep = np.ones(n, dtype=bool)
    gap = np.zeros(n, dtype=bool)
    if beh.get("ais_gap_minutes"):
        half = pd.Timedelta(minutes=beh["ais_gap_minutes"] / 2)
        dark = np.asarray((times >= pass_time - half) & (times <= pass_time + half))
        keep[dark] = False
        idx = np.flatnonzero(dark)
        if len(idx):
            if idx[0] > 0:
                gap[idx[0] - 1] = True
            if idx[-1] < n - 1:
                gap[idx[-1] + 1] = True

    # course selection against the coastline: the axis is defined mod 180, so
    # approaching from either end is equally valid to the trajectory gate --
    # pick whichever direction keeps more of the track at sea, then truncate
    # to the contiguous at-sea stretch around the origin pass.
    lon_scale2 = lon_scale
    best = (lat, lon, -1)
    for cand in (base, base + 180.0):
        b2 = np.radians(cand)
        extra2 = np.where(s > curve_start,
                          np.minimum((s - curve_start) * 60.0, 55.0), 0.0)
        out2 = np.radians((np.degrees(b2) + extra2) % 360.0)
        la2 = np.where(s < 0, o_lat + s * np.cos(b2), o_lat - s * np.cos(out2))
        lo2 = np.where(s < 0, o_lon + s * np.sin(b2) * lon_scale2,
                       o_lon - s * np.sin(out2) * lon_scale2)
        la2 = la2 + (mlat - mlat[i_pass])
        lo2 = lo2 + (mlon - mlon[i_pass])
        _k, span = _ocean_run(la2, lo2, i_pass)
        if span > best[2]:
            best = (la2, lo2, span)
    lat, lon = best[0], best[1]
    sea_keep, _ = _ocean_run(lat, lon, i_pass)
    keep = keep & sea_keep

    return lat, lon, np.clip(sog, 0.5, 22), gap, keep


def generate_synthetic_ais(bbox, start_time, end_time, n_vessels,
                           culprit_config=None, seed=42, fleet_seed=None):
    """Synthetic lane traffic matching the vessels.parquet schema, with a
    planted culprit whose track passes exactly through the supplied origin.

    `fleet_seed` varies the fleet identity AND geometry per incident: names
    drawn from a realistic pool, MMSIs under plausible flag-state prefixes,
    different lanes. Without it (legacy callers, the committed benchmark) the
    original SynthVessel_9000000xx fleet is reproduced bit-for-bit.
    """
    rng = np.random.default_rng(seed if fleet_seed is None else (seed, fleet_seed))

    if fleet_seed is not None:
        order = rng.permutation(len(NAME_POOL))
        def identity(i, vtype):
            mid = MID_PREFIXES[int(rng.integers(0, len(MID_PREFIXES)))]
            mmsi = mid * 1_000_000 + int(rng.integers(100_000, 999_999))
            name = NAME_POOL[order[i % len(NAME_POOL)]]
            return mmsi, f"{TYPE_PREFIX.get(vtype, 'MV')} {name}"
    else:
        def identity(i, vtype):
            mmsi = 900000000 + i
            return mmsi, f"SynthVessel_{mmsi}"

    start_dt = pd.to_datetime(start_time, utc=True)
    end_dt = pd.to_datetime(end_time, utc=True)
    times = pd.date_range(start=start_dt, end=end_dt, freq="5min")
    n = len(times)
    times_hr = (times - times[0]).total_seconds().to_numpy() / 3600.0

    lon_min, lat_min, lon_max, lat_max = bbox
    ccx, ccy = (lon_min + lon_max) / 2, (lat_min + lat_max) / 2
    # AIS coverage radius: fixes beyond it are dropped, so long transits
    # enter and leave the picture instead of stretching across the ocean.
    cover = max(1.2, 6.0 * max(lon_max - lon_min, lat_max - lat_min))

    frames = []
    for i in range(n_vessels):
        is_culprit = (i == 0) and (culprit_config is not None)

        keep = np.ones(n, dtype=bool)
        if is_culprit:
            lat, lon, sog, gap, keep = _culprit_track(rng, times, culprit_config)
            vtype = "Tanker"
        else:
            lat, lon, sog, vtype, bkeep = _background_track(rng, n, times_hr, bbox)
            keep = keep & bkeep
            gap = np.zeros(n, dtype=bool)
        mmsi, vname = identity(i, vtype)
        # outside coverage -> not received
        inside = (np.abs(lon - ccx) < cover) & (np.abs(lat - ccy) < cover)
        keep = keep & inside

        cog = _course_from_positions(lat, lon)
        # heading is what the bow points at: cog plus a little crab/yaw
        heading = (cog + rng.normal(0, 2.0, n)) % 360

        frames.append(pd.DataFrame({
            "mmsi": mmsi,
            "timestamp": times.to_numpy(),
            "lat": lat,
            "lon": lon,
            "sog_kn": sog,
            "cog_deg": cog,
            "heading_deg": heading,
            "vessel_name": vname,
            "imo": mmsi + 1000,
            "vessel_type": vtype,
            "length_m": rng.uniform(50, 300),
            "width_m": rng.uniform(10, 50),
            "draft_m": rng.uniform(5, 15),
            "status": "Under way using engine",
            "gap_flag": gap,
            "source": "synthetic",
            "culprit": is_culprit,
        }).loc[keep].reset_index(drop=True))
        if len(frames[-1]) < 4 and not is_culprit:
            frames.pop()          # never meaningfully inside coverage

    if not frames:
        return pd.DataFrame()

    final_df = pd.concat(frames, ignore_index=True)
    final_df["mmsi"] = final_df["mmsi"].astype("int64")
    final_df["lat"] = final_df["lat"].astype("float64")
    final_df["lon"] = final_df["lon"].astype("float64")
    final_df["sog_kn"] = final_df["sog_kn"].astype("float32")
    final_df["cog_deg"] = final_df["cog_deg"].astype("float32")
    final_df["heading_deg"] = final_df["heading_deg"].astype("float32")
    final_df["imo"] = final_df["imo"].astype("Int64")
    final_df["length_m"] = final_df["length_m"].astype("float32")
    final_df["width_m"] = final_df["width_m"].astype("float32")
    final_df["draft_m"] = final_df["draft_m"].astype("float32")
    final_df["gap_flag"] = final_df["gap_flag"].astype(bool)
    final_df["culprit"] = final_df["culprit"].astype(bool)
    return final_df
