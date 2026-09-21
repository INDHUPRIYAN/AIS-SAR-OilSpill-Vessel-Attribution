/* Incident-replay engine: time model, step definitions, geodesy, particles.
 *
 * The replay is an accelerated visualisation of a finished run. Every number
 * shown on screen is read from the run's contract files; this module only
 * decides WHEN each piece appears and how the camera and clock move. It never
 * invents positions -- vessel motion interpolates real AIS fixes, hindcast
 * motion replays the drift engine's own particles, and wind/current particles
 * advect through the grids the drift engine actually used.
 */

/* ------------------------------------------------------------ geodesy ---- */

const EARTH_R_KM = 6371.0088;

export function haversineKm(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const dp = (lat2 - lat1) * rad, dl = (lon2 - lon1) * rad;
  const a = Math.sin(dp / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_R_KM * Math.asin(Math.sqrt(a));
}

export function bearingDeg(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const y = Math.sin((lon2 - lon1) * rad) * Math.cos(lat2 * rad);
  const x = Math.cos(lat1 * rad) * Math.sin(lat2 * rad) -
    Math.sin(lat1 * rad) * Math.cos(lat2 * rad) * Math.cos((lon2 - lon1) * rad);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

/** Destination point from (lon,lat) after moving `km` on bearing `deg`. */
export function destination(lon, lat, km, deg) {
  const rad = Math.PI / 180;
  const d = km / EARTH_R_KM, b = deg * rad, p1 = lat * rad, l1 = lon * rad;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) +
    Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1),
    Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [l2 / rad, p2 / rad];
}

export function circleRing(lon, lat, km, n = 90) {
  const pts = [];
  for (let i = 0; i <= n; i++) pts.push(destination(lon, lat, km, (i * 360) / n));
  return pts;
}

/* --------------------------------------------------------- formatting ---- */

export const fmtLat = (v) =>
  `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "N" : "S"}`;
export const fmtLon = (v) =>
  `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "E" : "W"}`;

export const fmtUtc = (ms) => {
  if (!Number.isFinite(ms)) return "—";
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16) + " UTC";
};

export const fmtDur = (h) => {
  if (h == null) return "—";
  const H = Math.floor(Math.abs(h)), M = Math.round((Math.abs(h) - H) * 60);
  return `${String(H).padStart(2, "0")}h ${String(M).padStart(2, "0")}m`;
};

export const fmtRel = (ms, t0) => {
  const dh = (ms - t0) / 3.6e6;
  if (Math.abs(dh) < 0.05) return "NOW";
  const s = dh > 0 ? "+" : "−";
  return `${s}${Math.abs(dh).toFixed(Math.abs(dh) < 10 ? 1 : 0)}h`;
};

export const clamp01 = (v) => Math.max(0, Math.min(1, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const easeInOut = (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
export const easeOut = (t) => 1 - (1 - t) ** 3;
/** Progress of `t` through [a,b], clamped. */
export const span = (t, a, b) => clamp01((t - a) / (b - a));

/* ------------------------------------------------------------- steps ----- */

/* Eleven investigation steps. `dur` is seconds at 1x. `simSpan(bundle)` gives
 * the [from, to] the global clock sweeps during the step (null = hold NOW). */
export const STEPS = [
  { id: "location", n: 1, title: "INCIDENT LOCATION", dur: 6,
    blurb: (b) => `Sentinel-1 acquisition over ${b.placeName}. Investigation zone locked to the scene footprint.` },
  { id: "radar", n: 2, title: "MARITIME RADAR SCAN", dur: 8,
    blurb: (b) => `Sweeping the investigation zone. ${b.vesselCount} AIS transponders reconstructed inside the scan radius.` },
  { id: "ais", n: 3, title: "AIS VESSEL MOVEMENT", dur: 8,
    blurb: (b) => `Replaying ${b.aisWindowH?.toFixed(0) ?? "—"} h of reconstructed vessel traffic up to acquisition time. Every position is an AIS fix, not an animation.` },
  { id: "detect", n: 4, title: "OIL SPILL DETECTION", dur: 9,
    blurb: (b) => `Two-stage detector over the SAR scene: YOLO screen, then U-Net segmentation. Engine: ${b.detectEngine}.` },
  { id: "env", n: 5, title: "WIND + OCEAN CURRENT", dur: 7,
    blurb: (b) => `Forcing fields the drift model used — wind ${b.windMean != null ? b.windMean.toFixed(1) + " m/s mean" : "unavailable"}, current ${b.currentMean != null ? b.currentMean.toFixed(2) + " m/s mean" : "unavailable"}.` },
  { id: "drift", n: 6, title: "OIL DRIFT RECONSTRUCTION", dur: 7,
    blurb: (b) => `${b.nParticles?.toLocaleString() ?? "—"} simulated oil particles advected by ${b.driftEngine} under the observed forcing.` },
  { id: "hindcast", n: 7, title: "BACKWARD HINDCAST", dur: 9,
    blurb: (b) => `Reconstructing spill origin: running the drift ${b.backtrackH ?? 24} h backwards from the detected slick.` },
  { id: "origin", n: 8, title: "PROBABLE ORIGIN", dur: 6,
    blurb: () => `Origin region identified. Uncertainty is the model's own confidence ellipse — not implied precision.` },
  { id: "filter", n: 9, title: "AIS FILTERING", dur: 8,
    blurb: (b) => `Gating ${b.consideredCount ?? b.vesselCount} vessels against the origin region, time window and slick axis. Eliminated vessels stay visible, dimmed, with their reason.` },
  { id: "attribution", n: 10, title: "VESSEL ATTRIBUTION", dur: 7,
    blurb: (b) => `Scoring ${b.candidateCount} candidates on proximity, timing, trajectory and behaviour. Weighted, explainable — no black box.` },
  { id: "evidence", n: 11, title: "FINAL EVIDENCE", dur: 8,
    blurb: () => `The top-ranked vessel with its complete evidence trail. A rank is an ordering of evidence, not a finding. Every claim traces to a data point.` },
];

export const stepIndexById = Object.fromEntries(STEPS.map((s, i) => [s.id, i]));
export const TOTAL_DUR = STEPS.reduce((a, s) => a + s.dur, 0);

/** Global clock target for a step at progress p (ms epoch). */
export function stepSimTime(stepId, p, T) {
  const e = easeInOut(clamp01(p));
  switch (stepId) {
    case "ais": return lerp(T.aisStart, T.t0, e);
    case "drift": return lerp(T.hindEnd, T.t0, e);       // past -> NOW
    case "hindcast": return lerp(T.t0, T.hindEnd, e);    // NOW -> past
    case "origin": return T.originMid ?? T.hindEnd;
    case "evidence":
    case "attribution": return T.t0;
    default: return T.t0;
  }
}

/* -------------------------------------------------- bundle preparation --- */

/** Digest raw layer payloads into everything the replay needs, once. */
export function prepareBundle({ sceneMeta, slick, origin, forecast, suspects,
                                vessels, forcing, runMeta }) {
  const t0 = Date.parse(sceneMeta?.acquired_utc ?? 0);
  const md = origin?.metadata ?? {};
  const backtrackH = md.backtrack_hours ?? 24;
  const originStart = Date.parse(md.origin_window_start_utc ?? 0) || t0 - backtrackH * 3.6e6;
  const originEnd = Date.parse(md.origin_window_end_utc ?? 0) || t0;

  /* Hindcast trajectories. The engine writes each timestep as an independent
   * particle batch with globally-unique ids (step*N + j), so the j-th particle
   * of every step is the same physical particle: pair them by their index
   * WITHIN the step, not by the raw id. step_index 0 is at acquisition;
   * higher steps go further into the past. */
  const byStep = new Map();
  const ellipses = [];
  let maxStep = 0;
  for (const f of origin?.features ?? []) {
    const p = f.properties || {};
    if ((p.feature_type || p.kind) === "ellipse") { ellipses.push(f); continue; }
    if (p.particle_id === undefined) continue;
    const s = p.step_index ?? 0;
    maxStep = Math.max(maxStep, s);
    if (!byStep.has(s)) byStep.set(s, []);
    byStep.get(s).push({ id: p.particle_id, pos: f.geometry.coordinates,
                         w: p.weight ?? 0.5 });
  }
  for (const arr of byStep.values()) arr.sort((a, b) => a.id - b.id);
  const nPer = byStep.get(0)?.length ?? 0;
  const trajectories = [];
  for (let j = 0; j < nPer; j++) {
    const path = [], steps = [];
    for (let s = 0; s <= maxStep; s++) {
      const pt = byStep.get(s)?.[j];
      if (pt) { path.push(pt.pos); steps.push(s); }
    }
    if (path.length >= 2) {
      trajectories.push({ path, steps, weight: byStep.get(0)?.[j]?.w ?? 0.5 });
    }
  }
  ellipses.sort((a, b) => (a.properties.step_index ?? 0) - (b.properties.step_index ?? 0));

  /* Vessels with epoch times for exact interpolation. */
  const tracks = (vessels?.features ?? []).map((f) => {
    const p = f.properties;
    return {
      mmsi: p.mmsi, name: p.vessel_name, type: p.vessel_type,
      rank: p.rank ?? null, score: p.total_score ?? null,
      filtered: Boolean(p.filtered), filterReason: p.filter_reason || null,
      source: p.source,
      path: f.geometry.coordinates,
      times: (p.times_epoch ?? []).map((s) => (s == null ? null : s * 1000)),
      sog: p.sog_kn ?? [], headings: p.headings_deg ?? [],
      distanceKm: p.distance_km, durationH: p.duration_h,
      avgKn: p.avg_speed_kn, maxKn: p.max_speed_kn,
    };
  });
  const tTimes = tracks.flatMap((t) => t.times).filter(Boolean);
  const aisStart = tTimes.length ? Math.min(...tTimes) : t0 - 7 * 3.6e6;
  const aisEnd = tTimes.length ? Math.max(...tTimes) : t0;

  const sl = slick?.features?.[0];

  /* The probable origin is the cloud at the middle of the engine's ORIGIN
   * WINDOW -- not the -24h extreme of the backtrack. Uncertainty is measured
   * from the ellipse's actual ring geometry (its semi_major_m property is
   * written as 0 by the current engine build, so the ring is the truth). */
  const stepMs = (md.timestep_minutes ?? 60) * 60e3;
  const originMidMs = (originStart + originEnd) / 2;
  const originStep = Math.min(maxStep,
    Math.max(0, Math.round((t0 - originMidMs) / stepMs)));
  const originEllipse = ellipses.find(
    (e) => (e.properties.step_index ?? 0) === originStep)
    ?? ellipses[ellipses.length - 1];
  const originCenter = originEllipse?.properties?.center
    ?? sl?.properties?.centroid ?? sceneCenter(sceneMeta);
  const uncertaintyKm = originEllipse ? ringRadiusKm(originEllipse) : null;

  /* Filtering stages, from the run's real reasons. Reason phrases come from
   * Engine C; keyword-group them into the three gates for the animation. */
  const reasons = {};
  for (const t of tracks) if (t.filtered && t.filterReason) {
    reasons[t.filterReason] = (reasons[t.filterReason] || 0) + 1;
  }
  const gate = (kws) => Object.keys(reasons).filter(
    (r) => kws.some((k) => r.toLowerCase().includes(k)));
  const spatialReasons = gate(["origin region", "distance", "outside", "far"]);
  const temporalReasons = gate(["time", "window", "before", "after"]);
  const usedR = new Set([...spatialReasons, ...temporalReasons]);
  const trajReasons = Object.keys(reasons).filter((r) => !usedR.has(r));
  const stageCount = (rs) => rs.reduce((a, r) => a + reasons[r], 0);
  const total = tracks.length;
  const filterStages = [
    { label: "Vessels reconstructed", removed: 0, reasons: [] },
    { label: "Spatial gate", removed: stageCount(spatialReasons), reasons: spatialReasons },
    { label: "Temporal gate", removed: stageCount(temporalReasons), reasons: temporalReasons },
    { label: "Trajectory gate", removed: stageCount(trajReasons), reasons: trajReasons },
    { label: "Attribution candidates", removed: 0, reasons: [] },
  ];
  let left = total;
  for (const s of filterStages) { left -= s.removed; s.remaining = left; }

  const suspectsList = suspects?.suspects ?? [];
  const b = {
    runMeta, sceneMeta, slick, forecast, suspects,
    t0, aisStart, aisEnd,
    hindEnd: t0 - backtrackH * 3.6e6,
    originStart, originEnd,
    originMid: (originStart + originEnd) / 2,
    backtrackH, maxStep,
    domain: [t0 - backtrackH * 3.6e6, t0 + maxForecastH(forecast) * 3.6e6],
    trajectories, ellipses, originEllipse, originStep, originCenter,
    tracks, filterStages,
    suspectsList,
    top: suspectsList[0] ?? null,
    candidateCount: suspectsList.length,
    // Every AIS track in the run's vessel layer -- the traffic reconstructed
    // around the scene. NOT the number attribution considered; that is
    // consideredCount, from suspects.json.
    vesselCount: tracks.length,
    consideredCount: suspects?.total_vessels_considered ?? null,
    originUncertaintyKm: md.origin_uncertainty_km ?? null,
    aisWindowH: (aisEnd - aisStart) / 3.6e6,
    nParticles: md.n_particles,
    driftEngine: md.forcing?.engine ?? "drift engine",
    detectEngine: runMeta?.detect_engine ?? "ml",
    windMean: forcing?.wind?.mean_speed ?? null,
    currentMean: forcing?.currents?.mean_speed ?? null,
    wind: forcing?.wind ?? null,
    currents: forcing?.currents ?? null,
    placeName: guessPlace(sceneCenter(sceneMeta)),
    sceneCenter: sceneCenter(sceneMeta),
    uncertaintyKm,
    slickProps: sl?.properties ?? {},
  };
  return b;
}

/** Semi-major radius of an ellipse feature, measured from its ring. */
function ringRadiusKm(f) {
  const ring = f.geometry?.coordinates?.[0];
  if (!ring?.length) return null;
  const [cx, cy] = f.properties.center ?? ring[0];
  let best = 0;
  for (const [x, y] of ring) best = Math.max(best, haversineKm(cy, cx, y, x));
  return Math.max(best, 0.3);
}

function sceneCenter(m) {
  const bb = m?.bbox;
  return bb?.length === 4 ? [(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2] : [0, 0];
}

function maxForecastH(fc) {
  const hs = (fc?.features ?? []).map((f) => f.properties?.horizon_h ?? 0);
  return hs.length ? Math.max(...hs) : 24;
}

/* Coarse place naming so the header reads like an incident, not coordinates.
 * Purely presentational; the coordinates remain the record. */
/* Ordered most-specific first: later entries are broader basins. Purely
 * presentational; the coordinates remain the record. */
const REGIONS = [
  [76, 84, 8, 16, "the Bay of Bengal, off Chennai"],
  [31.5, 33.5, 30.5, 32.5, "the Nile Delta, off Port Said"],
  [30, 37, 33, 37, "the Eastern Mediterranean"],
  [36, 41, 12, 28, "the Red Sea"],
  [53.5, 56.5, 24.5, 26.5, "the Persian Gulf, off Dubai"],
  [47, 57, 23, 31, "the Persian Gulf"],
  [-92, -86, 27, 31, "the Gulf of Mexico, Mississippi Delta"],
  [103.8, 105.5, 2.8, 4.5, "the South China Sea, off Johor"],
  [102, 106.5, 0.5, 4.5, "the Malacca Strait, Singapore approaches"],
  [97, 100, 3, 6, "the northern Malacca Strait, off Sumatra"],
  [104.5, 106.5, -2, 0.5, "the Bangka Strait, off Sumatra"],
  [106, 108.5, -7, -5, "the Java Sea, off Jakarta"],
  [113, 116, -10, -8, "the Bali Sea"],
  [114, 118, -6, -2, "the Makassar Strait, off Kalimantan"],
  [19, 23, 58, 61, "the Baltic Sea, Archipelago Sea"],
  [-127, -123, 43, 48, "the US Pacific Northwest, off Oregon"],
  [-121, -118, 33, 35, "the Santa Barbara Channel, California"],
  [8, 10.5, 42.5, 44.5, "the Ligurian Sea"],
  [66, 76, 15, 25, "the Arabian Sea"],
];

/** A readable name for a position, and whether it is one.
 *
 *  The names come from a short list of hand-drawn boxes in this file, not
 *  from any gazetteer the API serves, so a name here is a convenience label
 *  for a region and must read as one: "near the Bay of Bengal, off Chennai".
 *  Outside every box the coordinates are the only honest answer. */
export function guessPlace([lon, lat]) {
  for (const [w, e, s, n, name] of REGIONS) {
    if (lon > w && lon < e && lat > s && lat < n) return `near ${name}`;
  }
  return `${fmtLat(lat)}, ${fmtLon(lon)}`;
}

/* ------------------------------------------------- vessel interpolation -- */

/** Exact position/heading/speed of one track at epoch `ms` (clamped). */
export function trackStateAt(track, ms) {
  const { path, times, headings, sog } = track;
  if (!path?.length) return null;
  if (!times?.length || times.length !== path.length) {
    return { pos: path[path.length - 1], heading: 0, sog: null };
  }
  let i = times.findIndex((t) => t != null && t >= ms);
  if (i === -1) i = times.length - 1;
  if (i === 0) i = 1;
  const t1 = times[i - 1], t2 = times[i];
  const f = t2 > t1 ? clamp01((ms - t1) / (t2 - t1)) : 1;
  const [x1, y1] = path[i - 1], [x2, y2] = path[i];
  const pos = [lerp(x1, x2, f), lerp(y1, y2, f)];
  const heading = headings?.[i - 1] ?? bearingDeg(y1, x1, y2, x2);
  return { pos, heading, sog: sog?.[i - 1] ?? null, segIndex: i };
}

/** Portion of a path visible up to epoch `ms` (for growing trails). */
export function trackPathUntil(track, ms) {
  const { path, times } = track;
  if (!times?.length || times.length !== path.length) return path;
  const out = [];
  for (let i = 0; i < path.length; i++) {
    if (times[i] == null || times[i] <= ms) out.push(path[i]);
    else {
      const st = trackStateAt(track, ms);
      if (st) out.push(st.pos);
      break;
    }
  }
  return out.length >= 2 ? out : null;
}

/* ------------------------------------------------------ particle field --- */

/** Bilinear sample of a forcing grid at (lon, lat, epoch ms) -> [u, v] m/s. */
export function sampleField(field, lon, lat, ms) {
  if (!field) return [0, 0];
  const { lats, lons, u, v, times_utc } = field;
  let ti = 0;
  if (times_utc?.length > 1 && Number.isFinite(ms)) {
    const ts = times_utc.map((s) => Date.parse(s));
    ti = ts.findIndex((t) => t >= ms);
    if (ti === -1) ti = ts.length - 1;
    if (ti > 0 && ms - ts[ti - 1] < ts[ti] - ms) ti -= 1;
  }
  const gx = gridFrac(lons, lon), gy = gridFrac(lats, lat);
  const U = u[Math.min(ti, u.length - 1)], V = v[Math.min(ti, v.length - 1)];
  return [bilinear(U, gx, gy), bilinear(V, gx, gy)];
}

function gridFrac(axis, val) {
  if (axis.length < 2) return { i: 0, f: 0 };
  let i = 0;
  while (i < axis.length - 2 && axis[i + 1] < val) i++;
  const f = clamp01((val - axis[i]) / (axis[i + 1] - axis[i] || 1));
  return { i, f };
}

function bilinear(grid, gx, gy) {
  const g = (y, x) => {
    const row = grid[Math.min(y, grid.length - 1)];
    return row[Math.min(x, grid[0].length - 1)];
  };
  const a = lerp(g(gy.i, gx.i), g(gy.i, gx.i + 1), gx.f);
  const b = lerp(g(gy.i + 1, gx.i), g(gy.i + 1, gx.i + 1), gx.f);
  return lerp(a, b, gy.f);
}

/** A pool of flow particles advected through a forcing field. Positions are
 *  mutated in place each frame; deck re-reads them via updateTriggers. */
export function makeParticles(bbox, n, seed = 7) {
  let s = seed;
  const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const [w, so, e, no] = bbox;
  const parts = [];
  for (let i = 0; i < n; i++) {
    parts.push({
      lon: w + rnd() * (e - w), lat: so + rnd() * (no - so),
      age: rnd() * 100, life: 60 + rnd() * 90,
      trail: [],
    });
  }
  parts.bbox = bbox;
  return parts;
}

/** Advance particles one frame. `speedScale` converts m/s to visual pace. */
export function advectParticles(parts, field, ms, dtSec, speedScale = 60) {
  const [w, so, e, no] = parts.bbox;
  for (const p of parts) {
    const [u, v] = sampleField(field, p.lon, p.lat, ms);
    const mPerDegLat = 111320;
    const mPerDegLon = 111320 * Math.cos((p.lat * Math.PI) / 180);
    p.trail.unshift([p.lon, p.lat]);
    if (p.trail.length > 5) p.trail.pop();
    p.lon += (u * dtSec * speedScale) / mPerDegLon;
    p.lat += (v * dtSec * speedScale) / mPerDegLat;
    p.speed = Math.hypot(u, v);
    p.age += 1;
    if (p.age > p.life || p.lon < w || p.lon > e || p.lat < so || p.lat > no) {
      p.lon = w + Math.random() * (e - w);
      p.lat = so + Math.random() * (no - so);
      p.age = 0;
      p.trail.length = 0;
    }
  }
  return parts;
}

/* ------------------------------------------------------------ map modes -- */

export const MAP_MODES = [
  { id: "standard", label: "Standard" },
  { id: "dark", label: "Dark Maritime" },
  { id: "satellite", label: "Satellite" },
  { id: "sar", label: "SAR / Radar" },
  { id: "current", label: "Ocean Current" },
  { id: "wind", label: "Wind Flow" },
  { id: "traffic", label: "Vessel Traffic" },
  { id: "oil", label: "Oil Spill Analysis" },
  { id: "hindcastM", label: "Hindcast" },
  { id: "forecastM", label: "Forecast" },
  { id: "hybrid", label: "Hybrid Intelligence" },
];

/* What each mode forces on. `null` leaves the user's own toggles alone. */
export const MODE_PRESETS = {
  standard: null, dark: null, satellite: null, sar: { sar: true },
  current: { currents: true, wind: false, slick: true },
  wind: { wind: true, currents: false, slick: true },
  traffic: { vessels: true, slick: true, wind: false, currents: false },
  oil: { slick: true, sar: true, vessels: false, wind: false, currents: false },
  hindcastM: { hindcast: true, forecast: false, slick: true, origin: true },
  forecastM: { forecast: true, hindcast: false, slick: true, origin: false },
  hybrid: { sar: true, slick: true, wind: true, currents: true, vessels: true,
            hindcast: true, forecast: true, origin: true },
};

export const MODE_BASEMAP = {
  standard: "voyager", dark: "darkmatter", satellite: "esri", sar: "none",
  current: "darkmatter", wind: "darkmatter", traffic: "darkmatter",
  oil: "darkmatter", hindcastM: "darkmatter", forecastM: "darkmatter",
  hybrid: "esri",
};

/** Default: a silence longer than this is worth drawing. Class-A AIS reports
 *  every few minutes under way, so half an hour without a fix is a gap and not
 *  a reporting interval. */
export const AIS_GAP_MIN = 30;

/** Stretches of a track where the transponder went quiet.
 *
 *  Derived from the fixes the run actually recorded -- the interval between
 *  consecutive reports -- not from anything inferred. `suspects.json` carries
 *  one `ais_gap_minutes` TOTAL per candidate, which tells an analyst that a
 *  vessel went dark but not where, so the map could never show it. Each gap is
 *  the straight segment between the last fix before the silence and the first
 *  after it, with how long the silence lasted.
 *
 *  @param {{path: number[][], times: (number|null)[]}} track  times in epoch ms
 *  @param {number} [minMinutes]
 */
export function aisGaps(track, minMinutes = AIS_GAP_MIN) {
  const path = track?.path || [];
  const times = track?.times || [];
  const out = [];
  let last = -1;
  for (let i = 0; i < path.length; i += 1) {
    if (times[i] == null || !Number.isFinite(times[i])) continue;
    if (last >= 0) {
      const minutes = (times[i] - times[last]) / 60000;
      if (minutes >= minMinutes) {
        out.push({ from: path[last], to: path[i], minutes, startMs: times[last], endMs: times[i] });
      }
    }
    last = i;
  }
  return out;
}
