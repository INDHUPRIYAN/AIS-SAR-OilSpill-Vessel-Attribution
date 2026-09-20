/* Workspace semantic palette (the build spec's exact color assignments).
 *
 * The cinematic Incident Replay keeps its own palette; the workspace uses
 * this one. The two never mix: forecast and hindcast are REQUIRED to differ
 * (amber vs magenta), candidates are orange, the top suspect red.
 */

export const WS = {
  slick: [249, 115, 22],       // orange — detected slick mask (frames 10-15)
  slickEdge: [255, 170, 80],   // bright orange outline of the mask
  geometry: [251, 191, 36],    // amber outline — ellipse/centroid/orientation
  forecast: [245, 158, 11],    // amber — opacity by horizon
  hindcast: [192, 38, 211],    // magenta/purple — particle cloud + ellipses
  origin: [226, 232, 240],     // white dashed — origin uncertainty ring (frames 12-15)
  vessel: [200, 214, 232],     // pale — background AIS track (frame 12: white/grey)
  candidate: [255, 255, 255],  // white, brighter — scored candidate
  suspect: [34, 211, 238],     // cyan, bold — the highest-ranked candidate (frame 14)
  selected: [34, 211, 238],    // cyan — the analyst's selection
  filtered: [120, 133, 151],   // dimmed gray — excluded vessels
  lookalike: [45, 212, 191],   // teal, hatched — look-alike: reported, NOT oil
  wind: [125, 211, 252],       // sky — 10 m wind vectors (frame 12 uses cyan barbs)
  current: [45, 212, 191],     // teal — surface current vectors (context)
  aoi: [34, 211, 238],         // cyan — AOI / selected footprint rectangle
  tile: [34, 211, 238],        // cyan — tile grid + selected tile
};

export const css = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

/** Contract source flag -> header badge text. Never hardcoded in the UI:
 *  the value always comes from the contract file / stage status. */
export function sourceBadge(source) {
  switch ((source || "").toLowerCase()) {
    // data_source (where the bytes came from) -- rendered in preference
    case "sensor": return { label: "SENSOR", tone: "ok" };
    // execution provenance (which code path ran) -- legacy `source`
    case "real": return { label: "REAL", tone: "ok" };
    case "cached": return { label: "CACHED", tone: "warn" };
    case "synthetic":
    case "mock": return { label: "SYNTHETIC", tone: "mock" };
    case "fallback": return { label: "FALLBACK", tone: "warn" };
    default: return { label: "—", tone: "neutral" };
  }
}

export const STAGE_LABELS = [
  ["detect", "Detecting"],
  ["characterise", "Characterising"],
  ["drift_hindcast", "Hindcasting"],
  ["drift_forecast", "Forecasting"],
  ["attribution_ais", "Reconstructing AIS"],   // virtual: AIS substep of attribution
  ["attribution", "Attributing"],
];

/** Fallback badge text per stage, when engine_used=fallback. */
export const FALLBACK_LABELS = {
  detect: "threshold fallback",
  characterise: "stand-in geometry",
  drift_hindcast: "Euler drift fallback",
  drift_forecast: "Euler drift fallback",
  attribution: "synthetic AIS",
};
