"""Export the sealed flagship run as one small JSON for the project page's
built-in replay (site/demo/flagship.json).

The GitHub Pages site must be able to show the demo case when the live system
(the team's laptop behind a Tailscale Funnel) is switched off. This script
reads the run's own artefacts -- the same files the live system serves -- and
writes a compact, self-contained copy: geometry rounded and simplified for a
browser, AIS traffic thinned to a 30-minute cadence except for the ranked
suspects, plus the coastline so the page needs no tile server.

Nothing is computed here that the pipeline did not compute. Every number the
page shows is copied from suspects.json / status.json / slick.geojson /
origin_cloud.geojson / forecast.geojson / vessels.parquet of the run, and the
manifest digest is carried along so the page can say which sealed run it is.

    .venv/Scripts/python scripts/site/export_demo.py
    .venv/Scripts/python scripts/site/export_demo.py --run inv-... --out site/demo/flagship.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DEFAULT_RUN = "inv-gulf-flagship-20230108-2day"
# The wider Gulf, so the opening beat has land to fly in over.
CONTEXT_BBOX = (-97.0, 23.5, -85.0, 31.5)   # W, S, E, N
OTHER_TRACK_STEP_MIN = 30                    # traffic that was screened out
SUSPECT_TRACK_STEP_MIN = 5                   # the ranked vessels, as recorded


# ----------------------------------------------------------------------------
# geometry helpers (plain Python: no shapely dependency for a site script)
# ----------------------------------------------------------------------------

def rdp(points, tol):
    """Ramer-Douglas-Peucker on [x, y] pairs; keeps end points."""
    if len(points) < 3:
        return points
    ax, ay = points[0]
    bx, by = points[-1]
    dx, dy = bx - ax, by - ay
    norm = math.hypot(dx, dy)
    best, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = (abs(dy * px - dx * py + bx * ay - by * ax) / norm if norm
             else math.hypot(px - ax, py - ay))
        if d > best:
            best, idx = d, i
    if best > tol:
        return rdp(points[:idx + 1], tol)[:-1] + rdp(points[idx:], tol)
    return [points[0], points[-1]]


def clip_ring(ring, bbox):
    """Sutherland-Hodgman clip of a polygon ring to an axis-aligned box."""
    w, s, e, n = bbox
    edges = (
        (lambda p: p[0] >= w, lambda a, b: _isect_x(a, b, w)),
        (lambda p: p[0] <= e, lambda a, b: _isect_x(a, b, e)),
        (lambda p: p[1] >= s, lambda a, b: _isect_y(a, b, s)),
        (lambda p: p[1] <= n, lambda a, b: _isect_y(a, b, n)),
    )
    out = list(ring)
    for inside, isect in edges:
        if not out:
            return []
        inp, out = out, []
        prev = inp[-1]
        for cur in inp:
            if inside(cur):
                if not inside(prev):
                    out.append(isect(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(isect(prev, cur))
            prev = cur
    return out


def _isect_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return [x, a[1] + t * (b[1] - a[1])]


def _isect_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return [a[0] + t * (b[0] - a[0]), y]


def rnd(points, dp):
    return [[round(x, dp), round(y, dp)] for x, y in points]


def in_box(p, bbox):
    w, s, e, n = bbox
    return w <= p[0] <= e and s <= p[1] <= n


# ----------------------------------------------------------------------------
# export
# ----------------------------------------------------------------------------

def load(run_dir: Path, name: str):
    with open(run_dir / name, encoding="utf-8") as fh:
        return json.load(fh)


def export(run_dir: Path, ne_dir: Path) -> dict:
    status = load(run_dir, "status.json")
    manifest = load(run_dir, "manifest.json")
    scene = load(run_dir, "scene_meta.json")
    suspects = load(run_dir, "suspects.json")
    detect = load(run_dir, "detect_response.json")
    ais_ingest = load(run_dir, "ais_ingest.json")
    identities = load(run_dir, "vessel_identities.json")
    slick = load(run_dir, "slick.geojson")
    origin = load(run_dir, "origin_cloud.geojson")
    forecast = load(run_dir, "forecast.geojson")

    # The digest the live system shows in its provenance strip (`⌗ fd42e078`):
    # the manifest's own sealed artefact digest, copied, never recomputed.
    digest = manifest["artefact_digest"]

    # -- slick pieces: every polygon, simplified to ~30 m, 4 dp -------------
    pieces = []
    for f in slick["features"]:
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0]
        ring = rnd(rdp(ring, 0.0003), 4)
        pieces.append({"id": p["slick_id"], "area_km2": p["area_km2"], "ring": ring})
    pieces.sort(key=lambda x: -x["area_km2"])
    biggest = max(slick["features"], key=lambda f: f["properties"]["area_km2"])["properties"]

    # -- hindcast: the 90 % ellipse per hourly step + the final particle cloud
    steps = []
    for f in origin["features"]:
        p = f["properties"]
        if p.get("feature_type") == "ellipse":
            steps.append({"i": p["step_index"], "t": p["t_utc"],
                          "center": [round(c, 5) for c in p["center"]],
                          "semi_major_m": round(p["semi_major_m"]),
                          "semi_minor_m": round(p["semi_minor_m"]),
                          "orientation_deg": round(p["orientation_deg"], 1),
                          "ring": rnd(f["geometry"]["coordinates"][0], 4)})
    steps.sort(key=lambda s: s["i"])
    last = steps[-1]["i"]
    particles = [[round(f["geometry"]["coordinates"][0], 4),
                  round(f["geometry"]["coordinates"][1], 4),
                  round(f["properties"].get("weight", 1.0), 2)]
                 for f in origin["features"]
                 if f["properties"].get("feature_type") == "particle"
                 and f["properties"].get("step_index") == last]

    omd = origin.get("metadata") or {}
    forcing = omd.get("forcing") or {}
    fmd = forecast.get("metadata") or {}

    # -- forecast envelopes -------------------------------------------------
    envelopes = [{"horizon_h": f["properties"]["horizon_h"],
                  "valid_utc": f["properties"]["valid_utc"],
                  "confidence_level": f["properties"]["confidence_level"],
                  "area_km2": f["properties"]["area_km2"],
                  "ring": rnd(rdp(f["geometry"]["coordinates"][0], 0.0003), 4)}
                 for f in forecast["features"]]

    # -- AIS: suspects at full cadence, everything else thinned ------------
    v = pd.read_parquet(run_dir / "vessels.parquet")
    # Positions sit on whole 5-minute marks (00:10, 00:15, ...) while the
    # window opens at 00:10:08, so minutes are counted from the window's
    # minute, not its second, or no row would ever land on the cadence.
    t0 = pd.Timestamp(ais_ingest["window_utc"][0]).floor("min")
    v = v.assign(minute=((v["timestamp_utc"] - t0).dt.total_seconds() / 60).round().astype(int))
    ranked = {s["mmsi"]: s for s in suspects["suspects"]}
    filtered = {f["mmsi"]: f for f in suspects.get("filtered_out", [])}
    tracks = []
    for mmsi, g in v.sort_values("timestamp_utc").groupby("mmsi"):
        role = "suspect" if mmsi in ranked else "filtered" if mmsi in filtered else "other"
        step = SUSPECT_TRACK_STEP_MIN if role == "suspect" else OTHER_TRACK_STEP_MIN
        dp = 4 if role == "suspect" else 3
        g = g[g["minute"] % step == 0]
        if len(g) < 2:
            continue
        pts = [[round(r.lon, dp), round(r.lat, dp), int(r.minute)] for r in g.itertuples()]
        tracks.append({"mmsi": int(mmsi), "role": role,
                       "name": (identities.get(str(mmsi)) or {}).get("vessel_name"),
                       "type": str(g["vessel_type"].iloc[0]),
                       "length_m": None if pd.isna(g["length_m"].iloc[0]) else float(g["length_m"].iloc[0]),
                       "pts": pts})
    tracks.sort(key=lambda t: {"suspect": 0, "filtered": 1, "other": 2}[t["role"]])

    # -- coast and land for the map: Natural Earth, clipped to the Gulf -----
    coast = []
    for f in load(ne_dir, "coast.json")["features"]:
        cs = f["geometry"]["coordinates"]
        if not any(in_box(p, CONTEXT_BBOX) for p in cs):
            continue
        # Split where the line leaves the box so nothing is drawn across it.
        run = []
        for p in cs:
            if in_box(p, CONTEXT_BBOX):
                run.append(p)
            elif run:
                coast.append(rnd(rdp(run, 0.004), 3)); run = []
        if run:
            coast.append(rnd(rdp(run, 0.004), 3))
    land = []
    for f in load(ne_dir, "countries.json")["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            outer = poly[0]
            if not any(in_box(p, CONTEXT_BBOX) for p in outer):
                continue
            clipped = clip_ring(outer, CONTEXT_BBOX)
            if len(clipped) >= 3:
                land.append(rnd(rdp(clipped, 0.004), 3))

    stages = [{"stage": s["stage"], "status": s["status"], "detail": s["detail"],
               "seconds": s["seconds"], "data_source": s.get("data_source"),
               "source": s.get("source"), "warnings": s.get("warnings", [])}
              for s in status["stages"]]
    detect_classes = {}
    for c in detect.get("candidates", []):
        detect_classes[c["class"]] = detect_classes.get(c["class"], 0) + 1

    return {
        "run": {
            "id": status["run_id"], "digest": digest, "state": status["state"],
            "scene_id": scene["scene_id"], "acquired_utc": scene["acquired_utc"],
            "bbox": scene["bbox"], "polarisation": scene["polarisation"],
            "pixel_spacing_m": scene["pixel_spacing_m"],
            "provider_used": scene["provider_used"],
            "generated_utc": manifest["generated_utc"],
            "total_seconds": manifest["total_seconds"],
            "code_git_sha": manifest.get("code_git_sha"),
            "models": [{"kind": m["kind"], "name": m["name"], "sha256": m["sha256"][:12]}
                       for m in manifest.get("models", [])],
        },
        "stages": stages,
        "detect": {"confidence": detect.get("confidence"), "classes": detect_classes,
                   "candidate_boxes": [[round(x, 4) for x in c["bbox"]] + [c["class"]]
                                       for c in detect.get("candidates", [])]},
        "slick": {"count": len(pieces), "area_km2": round(sum(p["area_km2"] for p in pieces), 1),
                  "centroid": biggest["centroid"], "major_axis_m": biggest["major_axis_m"],
                  "minor_axis_m": biggest["minor_axis_m"], "orientation_deg": biggest["orientation_deg"],
                  "age_hours": biggest["age_hours_estimate"], "age_confidence": biggest["age_confidence_label"],
                  "engine": biggest["engine"], "pieces": pieces},
        # The origin block is the cloud's own metadata (the report's source):
        # the release window, the peak, and the calibrated uncertainty radius.
        "hindcast": {"steps": steps, "particles": particles,
                     "window_start_utc": omd.get("origin_window_start_utc"),
                     "window_end_utc": omd.get("origin_window_end_utc"),
                     "peak_utc": omd.get("origin_peak_utc"),
                     "uncertainty_km": omd.get("origin_uncertainty_km"),
                     "uncertainty_method": omd.get("origin_uncertainty_method"),
                     "method": omd.get("origin_window_method"),
                     "backtrack_hours": omd.get("backtrack_hours"),
                     "n_particles": omd.get("n_particles"),
                     "timestep_minutes": omd.get("timestep_minutes"),
                     "engine": forcing.get("engine"),
                     "currents": (forcing.get("currents") or {}).get("provider"),
                     "wind": (forcing.get("wind") or {}).get("provider")},
        "forecast": {"horizons_h": fmd.get("horizons_h"),
                     "weathering": {k: (fmd.get("weathering") or {}).get(k)
                                    for k in ("model", "oil_type_assumed", "confidence", "states")},
                     "envelopes": envelopes},
        "ais": {"rows": ais_ingest["rows"], "unique_mmsi": ais_ingest["unique_mmsi"],
                "measured_rows": ais_ingest["measured_rows"], "interpolated_rows": ais_ingest["interpolated_rows"],
                "provider": ais_ingest["provider"], "window_utc": ais_ingest["window_utc"],
                "wall_seconds": ais_ingest.get("wall_seconds"),
                "other_step_min": OTHER_TRACK_STEP_MIN, "tracks": tracks},
        "attribution": {"weights": suspects["weights"],
                        "considered": suspects.get("total_vessels_considered"),
                        "filtered_out": len(filtered),
                        "ranked": suspects["suspects"],
                        "filtered": [{"mmsi": f["mmsi"], "reason": f["reason"]} for f in filtered.values()]},
        "map": {"context_bbox": list(CONTEXT_BBOX), "coast": coast, "land": land,
                "source": "Natural Earth 10m (public domain), via main_system/frontend/public/geo/ne"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", default=DEFAULT_RUN)
    ap.add_argument("--runs-dir", default=str(REPO / "data" / "runs"))
    ap.add_argument("--ne-dir", default=str(REPO / "main_system" / "frontend" / "public" / "geo" / "ne"))
    ap.add_argument("--out", default=str(REPO / "site" / "demo" / "flagship.json"))
    args = ap.parse_args(argv)

    run_dir = Path(args.runs_dir) / args.run
    if not (run_dir / "manifest.json").exists():
        print(f"no sealed run at {run_dir}", file=sys.stderr)
        return 2
    data = export(run_dir, Path(args.ne_dir))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    out.write_text(text, encoding="utf-8")
    print(f"{out}: {len(text)/1024:.0f} KB · {data['slick']['count']} slick pieces · "
          f"{len(data['hindcast']['steps'])} hindcast steps · {len(data['ais']['tracks'])} tracks · "
          f"{len(data['map']['coast'])} coast lines / {len(data['map']['land'])} land rings · digest {data['run']['digest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
