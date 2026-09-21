/* What a Sentinel-1 product name says about the scene.
 *
 * `scene_meta.json` records the acquisition time, footprint and polarisation,
 * but not the mission, beam mode or product type -- and several screens filled
 * that silence with constants: "Sentinel-1", "IW", "GRD", and "VV" whenever no
 * polarisation was recorded. On a scene that was not an IW GRD product those
 * were simply false, and one of them was printed in the incident report.
 *
 * The ESA product name is the authority for those facts, and it is already in
 * every run: S1A_IW_GRDH_1SDV_20230108T001008_… reads mission S1A, beam mode
 * IW, product GRD at high resolution, dual polarisation VV+VH. So they are read
 * from the name, and a scene whose name does not follow that convention gets
 * "not recorded" rather than a guess.
 *
 * https://sentinel.esa.int/web/sentinel/user-guides/sentinel-1-sar/naming-conventions
 */

// Mission, beam mode and product type lead every Sentinel-1 name; the
// polarisation block after them is absent from the short names the reference
// corpus uses (S1A_IW_GRDH_MALACCA), so it is optional here.
const S1 = /^(S1[ABCD])_(S[1-6]|IW|EW|WV)_(RAW|SLC|GRD|OCN)([FHM_])(?:_([0-2])([SA])(SH|SV|DH|DV|HH|HV|VV|VH)_)?/;

const POL = {
  SH: "HH (single)", SV: "VV (single)", DH: "HH + HV (dual)", DV: "VV + VH (dual)",
  HH: "HH (partial)", HV: "HV (partial)", VV: "VV (partial)", VH: "VH (partial)",
};
const RES = { F: "full", H: "high", M: "medium", _: null };

/** @returns {null | {mission, mode, product, resolution, polarisation, source}} */
export function parseS1Name(sceneId) {
  const m = S1.exec(String(sceneId || ""));
  if (!m) return null;
  return {
    mission: `Sentinel-${m[1].slice(1, 2)}${m[1].slice(2)}`,
    mode: m[2],
    product: m[3],
    resolution: RES[m[4]],
    polarisation: (m[7] && POL[m[7]]) || null,
    source: "ESA product name",
  };
}

/** A scene fact, from the record if it has one, else from the product name,
 *  else said to be missing. Never a constant.
 *  @param {object} sceneMeta @param {"mission"|"mode"|"product"|"polarisation"} key */
export function sceneFact(sceneMeta, key) {
  const recorded = key === "polarisation" ? sceneMeta?.polarisation
    : key === "product" ? sceneMeta?.product_type
      : key === "mode" ? sceneMeta?.mode
        : sceneMeta?.mission || sceneMeta?.platform;
  if (recorded) return recorded;
  const named = parseS1Name(sceneMeta?.scene_id);
  return named?.[key] || "not recorded";
}
