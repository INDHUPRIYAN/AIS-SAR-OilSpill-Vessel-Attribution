/* Geodesic distance and bearing for the MAP-mode measure tool.
 *
 * Haversine on a spherical earth, not planar distance on the projected map.
 * The difference matters here specifically: web-mercator stretches east–west
 * by 1/cos(latitude), so a ruler that measured screen pixels would report a
 * 100 km line in the Gulf of Mexico (28°N) as about 113 km, and one in the
 * Baltic (57°N) as about 184 km. An analyst measuring how far a vessel was
 * from an origin cannot be handed a number that depends on where on the globe
 * they happen to be looking.
 *
 * Nautical miles are offered alongside kilometres because the domain is
 * maritime and every AIS speed in the system is in knots — reporting a
 * distance in km beside a speed in kn asks the reader to convert.
 */

const EARTH_RADIUS_KM = 6371.0088;   // IUGG mean radius
const KM_PER_NM = 1.852;             // exact, by definition

const toRad = (deg) => (deg * Math.PI) / 180;
const toDeg = (rad) => (rad * 180) / Math.PI;

/** Great-circle distance in kilometres between two [lon, lat] positions. */
export function haversineKm(a, b) {
  const [lon1, lat1] = a;
  const [lon2, lat2] = b;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(s)));
}

/** Initial bearing in degrees (0–360) from a to b. */
export function bearingDeg(a, b) {
  const [lon1, lat1] = a;
  const [lon2, lat2] = b;
  const φ1 = toRad(lat1);
  const φ2 = toRad(lat2);
  const Δλ = toRad(lon2 - lon1);
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

/** Total length of a [lon, lat] path, in kilometres. */
export function pathLengthKm(points) {
  let total = 0;
  for (let i = 0; i < points.length - 1; i += 1) {
    total += haversineKm(points[i], points[i + 1]);
  }
  return total;
}

export const kmToNm = (km) => km / KM_PER_NM;

/** A measurement formatted for display: both units, plus the bearing. */
export function formatMeasurement(points) {
  if (!points || points.length < 2) return null;
  const km = pathLengthKm(points);
  const bearing = bearingDeg(points[0], points[points.length - 1]);
  return {
    km,
    nm: kmToNm(km),
    bearingDeg: bearing,
    // Stated so a screenshot of a measurement carries its own method. A
    // number without its geodesy is not reproducible.
    method: "great-circle (haversine), spherical earth R=6371.0088 km",
    text: `${km.toFixed(2)} km · ${kmToNm(km).toFixed(2)} nm · ${bearing.toFixed(0)}°`,
  };
}
