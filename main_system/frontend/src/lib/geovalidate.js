/* Is this slick somewhere a slick can be?
 *
 * The map draws whatever geometry a run wrote. That is the right default --
 * the workspace never moves or invents a position -- but it means a run whose
 * georeferencing is wrong, or whose mask bled onto the coast, draws oil on a
 * city with nothing on screen saying so. This module is the check, and it
 * only ever answers; it never corrects:
 *
 *   validateSlick   the geometry is finite, in range, and inside (or near)
 *                   the footprint of the scene it was segmented from. A slick
 *                   that fails is NOT drawn and the workspace says
 *                   LOCATION DATA UNAVAILABLE with the reason.
 *   landShare       how much of the outline lies on land, from the product's
 *                   own coastline (public/geo/land.json, the globe's dataset).
 *                   A slick on land is still drawn where the run put it --
 *                   hiding it would hide the problem -- but it is flagged.
 *
 * The coastline is ~1:110m, so a vertex within a few kilometres of the shore
 * can read as land. The wording that uses this says "intersects the
 * coastline dataset", never "is on land".
 */

const finite = (v) => typeof v === "number" && Number.isFinite(v);

function rings(geom) {
  if (!geom) return [];
  if (geom.type === "Polygon") return [geom.coordinates?.[0] || []];
  if (geom.type === "MultiPolygon") return (geom.coordinates || []).map((p) => p?.[0] || []);
  return [];
}

/** @returns {{ok: boolean, reason: string|null}} */
export function validateSlick(slick, sceneBbox, marginDeg = 0.25) {
  const feats = slick?.features || [];
  if (!feats.length) return { ok: true, reason: null };          // nothing segmented is not an error
  const pts = feats.flatMap((f) => rings(f.geometry)).flat();
  if (!pts.length) return { ok: false, reason: "the slick has no polygon geometry" };
  for (const c of pts) {
    if (!Array.isArray(c) || !finite(c[0]) || !finite(c[1])) return { ok: false, reason: "the slick geometry contains a non-numeric coordinate" };
    if (c[0] < -180 || c[0] > 180 || c[1] < -90 || c[1] > 90) return { ok: false, reason: "the slick geometry is outside WGS84 range (is it in pixel or projected units?)" };
  }
  if (Array.isArray(sceneBbox) && sceneBbox.length === 4 && sceneBbox.every(finite)) {
    const [w, s, e, n] = sceneBbox;
    const inside = pts.some((c) => c[0] >= w - marginDeg && c[0] <= e + marginDeg && c[1] >= s - marginDeg && c[1] <= n + marginDeg);
    if (!inside) return { ok: false, reason: "the slick geometry lies outside the footprint of the scene it was segmented from" };
  }
  return { ok: true, reason: null };
}

function inRing(ring, x, y) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** True when (lon, lat) falls inside a land polygon (holes respected). */
export function pointOnLand(land, lon, lat) {
  for (const f of land?.features || []) {
    const g = f.geometry;
    const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
    for (const poly of polys) {
      if (!poly?.[0]?.length || !inRing(poly[0], lon, lat)) continue;
      if (!poly.slice(1).some((hole) => inRing(hole, lon, lat))) return true;
    }
  }
  return false;
}

/** Share (0..1) of the slick's outline vertices that fall on land, and
 *  whether its centroid does. null when there is no coastline to test with. */
export function landShare(land, slick) {
  if (!land?.features?.length) return null;
  const feats = slick?.features || [];
  const pts = feats.flatMap((f) => rings(f.geometry)).flat();
  if (!pts.length) return null;
  const stride = Math.max(1, Math.floor(pts.length / 240));
  let on = 0, n = 0;
  for (let i = 0; i < pts.length; i += stride) { n += 1; if (pointOnLand(land, pts[i][0], pts[i][1])) on += 1; }
  const c = feats[0]?.properties?.centroid;
  return { share: n ? on / n : 0, centroidOnLand: Array.isArray(c) ? pointOnLand(land, c[0], c[1]) : null };
}
