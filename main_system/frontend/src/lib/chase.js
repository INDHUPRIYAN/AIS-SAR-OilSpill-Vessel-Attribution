/* The drift chase camera: where to look while the hindcast runs backwards and
 * the forecast runs forwards.
 *
 *   clock before the acquisition   follow the hindcast head, close in
 *   hindcast has reached the window  ease out a little: origin + slick in frame
 *   clock after the acquisition    follow the forecast centre, close in
 *   forecast has reached its end   ease out a little: slick + every footprint
 *
 * Every point followed is the run's own: the hourly ellipse centres of
 * origin_cloud and the centroid of the widest forecast envelope per horizon.
 * Between two of them the camera is interpolated in time, nothing else.
 */

const isEllipse = (f) => (f?.properties?.feature_type || f?.properties?.kind) === "ellipse";

function ringCentroid(f) {
  const g = f?.geometry;
  const ring = g?.type === "Polygon" ? g.coordinates?.[0] : g?.type === "MultiPolygon" ? g.coordinates?.[0]?.[0] : null;
  if (!ring?.length) return null;
  /* a GeoJSON ring repeats its first vertex to close: count it once */
  const last = ring[ring.length - 1], first = ring[0];
  const pts = ring.length > 1 && last[0] === first[0] && last[1] === first[1] ? ring.slice(0, -1) : ring;
  let x = 0, y = 0;
  for (const q of pts) { x += q[0]; y += q[1]; }
  return [x / pts.length, y / pts.length];
}

function bboxOf(points, margin) {
  const pts = points.filter((q) => Array.isArray(q) && Number.isFinite(q[0]) && Number.isFinite(q[1]));
  if (!pts.length) return null;
  const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  return [Math.min(...xs) - margin, Math.min(...ys) - margin, Math.max(...xs) + margin, Math.max(...ys) + margin];
}

/** @returns {null | { hind: {t:number,c:number[]}[], fore: {t:number,c:number[]}[], winStart:number|null, t0:number, zoom:number, originBox:number[]|null, forecastBox:number[]|null }} */
export function buildChase(originCloud, forecast, slick, sceneT0, est) {
  const slickC = slick?.features?.[0]?.properties?.centroid;
  if (!sceneT0 || !slickC) return null;
  const hind = (originCloud?.features || []).filter(isEllipse)
    .map((f) => ({ t: Date.parse(f.properties.t_utc ?? ""), c: f.properties.center }))
    .filter((d) => Number.isFinite(d.t) && Array.isArray(d.c))
    .sort((a, b) => a.t - b.t);
  const byH = new Map();
  for (const f of forecast?.features || []) {
    const h = f.properties?.horizon_h;
    if (h == null) continue;
    const cur = byH.get(h);
    if (!cur || (f.properties.confidence_level ?? 0) > (cur.properties.confidence_level ?? 0)) byH.set(h, f);
  }
  const fore = [{ t: sceneT0, c: slickC }, ...[...byH.entries()].sort((a, b) => a[0] - b[0])
    .map(([h, f]) => ({ t: Date.parse(f.properties.valid_utc ?? "") || sceneT0 + h * 3.6e6, c: ringCentroid(f) }))
    .filter((d) => d.c)];
  const winStart = Date.parse(originCloud?.metadata?.origin_window_start_utc ?? "");

  /* How close: the view is about six slick-lengths across, so the slick and
   * the cloud around it are large on screen. Clamped so a tiny slick does not
   * put the camera inside a pixel and a huge one does not leave the region. */
  const ring = slick?.features?.[0]?.geometry;
  const pts = ring?.type === "Polygon" ? ring.coordinates[0] : ring?.type === "MultiPolygon" ? ring.coordinates[0][0] : [];
  const sb = bboxOf(pts, 0);
  const span = sb ? Math.max(sb[2] - sb[0], sb[3] - sb[1], 0.004) : 0.02;
  const zoom = Math.max(9.5, Math.min(13.2, Math.log2(82 / span)));

  const inWindow = hind.filter((d) => !Number.isFinite(winStart) || d.t >= winStart).map((d) => d.c);
  return {
    hind, fore, t0: sceneT0, zoom,
    winStart: Number.isFinite(winStart) ? winStart : (hind[0]?.t ?? null),
    originBox: bboxOf([...pts, est?.center, ...inWindow], 0.012),   // the whole slick outline, not only its centroid
    forecastBox: bboxOf([slickC, ...fore.map((d) => d.c)], 0.02),
  };
}

function interp(list, t) {
  if (!list.length) return null;
  if (t <= list[0].t) return list[0].c;
  if (t >= list[list.length - 1].t) return list[list.length - 1].c;
  let i = 1;
  while (i < list.length && list[i].t < t) i++;
  const a = list[i - 1], b = list[i];
  const f = b.t > a.t ? (t - a.t) / (b.t - a.t) : 1;
  return [a.c[0] + (b.c[0] - a.c[0]) * f, a.c[1] + (b.c[1] - a.c[1]) * f];
}

/** What the camera should do at clock time `t`.
 *  @returns {null | { kind: "follow", center: number[], zoom: number } | { kind: "frame", key: string, bbox: number[] }} */
export function chaseAt(chase, t) {
  if (!chase || t == null || !Number.isFinite(t)) return null;
  const HOLD = 20 * 60 * 1000;                       // within 20 min of the acquisition nothing is moving yet
  if (Math.abs(t - chase.t0) < HOLD) return null;
  if (t < chase.t0) {
    if (!chase.hind.length) return null;
    if (chase.winStart != null && t <= chase.winStart && chase.originBox) return { kind: "frame", key: "origin", bbox: chase.originBox };
    /* the hindcast list ends at the acquisition with the slick itself */
    const c = interp([...chase.hind, { t: chase.t0, c: chase.fore[0].c }], t);
    return c ? { kind: "follow", center: c, zoom: chase.zoom } : null;
  }
  if (chase.fore.length < 2) return null;
  const last = chase.fore[chase.fore.length - 1];
  if (t >= last.t && chase.forecastBox) return { kind: "frame", key: "forecast", bbox: chase.forecastBox };
  const c = interp(chase.fore, t);
  return c ? { kind: "follow", center: c, zoom: chase.zoom } : null;
}
