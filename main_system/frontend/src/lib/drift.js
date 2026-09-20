/* The one place the UI turns an origin cloud into "the probable origin".
 *
 * The map ring, the drift panel and the case brief all used to be free to
 * pick their own point. The map picked the ellipse halfway through the whole
 * backtrack (T-12 h of a 24 h run) and drew a fixed 2.2 km ring -- neither of
 * which is what the hindcast published. The run states an origin WINDOW and
 * an origin UNCERTAINTY; this reads both and nothing else.
 *
 * Returned position: the centre of the hindcast ellipse whose timestamp is
 * nearest the middle of the published origin window. Radius: the published
 * `origin_uncertainty_km`, falling back to that ellipse's semi-major axis only
 * when the run did not publish one (and saying so via `radiusBasis`).
 */

const isEllipse = (f) =>
  (f?.properties?.feature_type || f?.properties?.kind) === "ellipse";

export function originEstimate(origin) {
  const md = origin?.metadata;
  const ells = (origin?.features ?? []).filter(isEllipse);
  if (!md || !ells.length) return null;

  const start = Date.parse(md.origin_window_start_utc ?? "");
  const end = Date.parse(md.origin_window_end_utc ?? "");
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
  const mid = (start + end) / 2;

  let best = null;
  let bestDt = Infinity;
  for (const f of ells) {
    const t = Date.parse(f.properties.t_utc ?? "");
    if (!Number.isFinite(t) || !Array.isArray(f.properties.center)) continue;
    const dt = Math.abs(t - mid);
    // Ties (several confidence levels at one step) keep the widest level.
    if (dt < bestDt || (dt === bestDt
        && (f.properties.confidence_level ?? 0) > (best.properties.confidence_level ?? 0))) {
      best = f; bestDt = dt;
    }
  }
  if (!best) return null;

  const published = md.origin_uncertainty_km;
  const radiusKm = published != null
    ? Number(published)
    : (best.properties.semi_major_m ?? 0) / 1000;
  return {
    center: best.properties.center,               // [lon, lat]
    tUtc: best.properties.t_utc,
    stepIndex: best.properties.step_index ?? null,
    radiusKm,
    radiusBasis: published != null ? "origin_uncertainty_km" : "ellipse semi-major axis",
    windowStartUtc: md.origin_window_start_utc,
    windowEndUtc: md.origin_window_end_utc,
    windowHours: (end - start) / 3.6e6,
    method: md.origin_window_method ?? null,
    // The run could not place a release earlier than the image: say so
    // wherever the origin is shown rather than only in the drift panel.
    weak: Boolean(md.origin_peak_utc && md.origin_peak_utc === md.origin_window_end_utc),
  };
}

/** What the screening model said about the region a slick was measured on.
 *
 *  "oil" / "lookalike" when the slick's centroid falls inside a detection
 *  candidate's box (oil wins a tie -- the screen confirmed something there);
 *  null when no candidate box contains it or the run has no candidates.
 *
 *  Why this exists: runs before the look-alike screening fix characterised
 *  the segmenter's FULL mask, so the largest slick -- the one drift and
 *  attribution trace -- could be a region the screen had rejected. The
 *  frozen flagship is such a run. Boxes are an approximation of regions,
 *  so the UI words this as "inside the box of", never as a certainty. */
export function screenVerdict(slickProps, detect) {
  const c = slickProps?.centroid;
  const cands = detect?.candidates;
  if (!Array.isArray(c) || !cands?.length) return null;
  const hits = cands.filter((x) => Array.isArray(x.bbox)
    && c[0] >= x.bbox[0] && c[0] <= x.bbox[2] && c[1] >= x.bbox[1] && c[1] <= x.bbox[3]);
  if (hits.some((x) => x.class === "oil")) return "oil";
  if (hits.some((x) => x.class === "lookalike")) return "lookalike";
  return null;
}

/** Arrow segments for one forcing grid (a /forcing_field section) at the
 *  timestep nearest `timeMs`.
 *
 *  Lengths are RELATIVE: the longest arrow in the grid spans 0.45 of a grid
 *  cell, so wind (m/s of air) and currents (tenths of m/s of water) are both
 *  legible. The absolute speed travels on every segment for the hover, and
 *  the legend says the scale is relative -- an arrow's length is not a
 *  distance travelled. Direction is where the flow is going TO (u east,
 *  v north), the oceanographic convention, which is also how it pushes oil. */
export function vectorArrows(field, timeMs) {
  const none = { segments: [], timeUtc: null };
  if (!field?.u?.length || !field.lats?.length || !field.lons?.length) return none;
  const times = (field.times_utc ?? []).map((t) => Date.parse(t));
  let ti = 0;
  if (times.length && Number.isFinite(timeMs)) {
    let best = Infinity;
    times.forEach((t, i) => {
      const d = Math.abs(t - timeMs);
      if (d < best) { best = d; ti = i; }
    });
  }
  const U = field.u[Math.min(ti, field.u.length - 1)];
  const V = field.v[Math.min(ti, field.v.length - 1)];
  const cell = Math.min(
    field.lats.length > 1 ? Math.abs(field.lats[1] - field.lats[0]) : 0.25,
    field.lons.length > 1 ? Math.abs(field.lons[1] - field.lons[0]) : 0.25);
  let max = 0;
  U.forEach((row, i) => row.forEach((u, j) => { max = Math.max(max, Math.hypot(u, V[i][j])); }));
  if (!max) return none;

  const out = [];
  field.lats.forEach((lat, i) => field.lons.forEach((lon, j) => {
    const u = U[i]?.[j]; const v = V[i]?.[j];
    const speed = Math.hypot(u, v);
    if (!Number.isFinite(speed) || speed === 0) return;
    const len = 0.45 * cell * (speed / max);
    const kx = 1 / Math.max(Math.cos((lat * Math.PI) / 180), 0.2);
    const tip = [lon + (len * u / speed) * kx, lat + len * v / speed];
    // Arrowhead: two barbs at +/-150 degrees from the shaft.
    const head = 0.3 * len;
    const ang = Math.atan2(v, u);
    const barb = (a) => [tip[0] + head * Math.cos(ang + a) * kx, tip[1] + head * Math.sin(ang + a)];
    const toDeg = ((90 - (ang * 180) / Math.PI) + 360) % 360;
    out.push({ path: [[lon, lat], tip], speed, toDeg });
    out.push({ path: [barb(2.618), tip, barb(-2.618)], speed, toDeg });
  }));
  return { segments: out, timeUtc: field.times_utc?.[ti] ?? null };
}
