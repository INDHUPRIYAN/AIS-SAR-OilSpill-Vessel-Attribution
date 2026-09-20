// @ts-check
/* The one map palette.
 *
 * Every map surface used to carry its own colour table and they disagreed --
 * "hindcast" was magenta on one screen and sky blue on another. A colour on a
 * map is a claim about what a thing is, so there is one table:
 *
 *   amber          oil on the water (the detected slick)
 *   orange -> red  backward drift, converging on the origin
 *   blue           the ocean's future: forward drift / forecast
 *   white          the vessel being looked at; candidates by rank
 *   grey           AIS traffic that was filtered out
 *   dashed red     AIS gaps (a transponder that went quiet)
 *   cyan           the interface's own accent: selection, zones, footprints
 *
 * RGB triples for deck.gl; `css()` for legends and MapLibre paint. */

/** @typedef {[number, number, number]} RGB */

/** @type {Record<string, RGB>} */
export const MAP_COLORS = {
  slick: [245, 158, 11],
  slickEdge: [253, 186, 60],
  lookalike: [148, 163, 184],
  hindcastOld: [249, 115, 22],   // far back in time
  hindcastNew: [239, 68, 68],    // converged, near the origin
  origin: [239, 68, 68],
  originRing: [254, 202, 202],
  forecast: [56, 139, 253],
  forecastEdge: [125, 179, 255],
  vessel: [226, 232, 240],
  candidate: [255, 255, 255],
  filtered: [100, 116, 139],
  aisGap: [239, 68, 68],
  wind: [186, 230, 253],
  current: [45, 212, 191],
  zone: [34, 211, 238],
  zoneMine: [45, 212, 191],
  footprint: [34, 211, 238],
  incident: [244, 63, 94],
  detection: [245, 158, 11],
  investigation: [167, 139, 250],
  selection: [255, 255, 255],
};

/** @param {RGB} rgb @param {number} [alpha] 0..1 */
export function css(rgb, alpha = 1) {
  return alpha >= 1 ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}

/** @param {RGB} rgb @param {number} alpha 0..255 @returns {[number, number, number, number]} */
export function rgba(rgb, alpha) {
  return [rgb[0], rgb[1], rgb[2], alpha];
}

/** Backward-drift ramp: `t` = 0 at the oldest step, 1 at the origin end.
 *  @param {number} t @returns {RGB} */
export function hindcastColor(t) {
  const k = Math.max(0, Math.min(1, t));
  const a = MAP_COLORS.hindcastOld;
  const b = MAP_COLORS.hindcastNew;
  return [Math.round(a[0] + (b[0] - a[0]) * k), Math.round(a[1] + (b[1] - a[1]) * k), Math.round(a[2] + (b[2] - a[2]) * k)];
}
