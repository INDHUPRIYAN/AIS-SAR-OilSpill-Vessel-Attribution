/* Workspace semantic palette (the build spec's exact color assignments).
 *
 * The cinematic Incident Replay keeps its own palette; the workspace uses
 * this one. The two never mix: forecast and hindcast are REQUIRED to differ
 * (amber vs magenta), candidates are orange, the top suspect red.
 */

export const WS = {
  slick: [220, 38, 38],        // red/crimson — detected slick
  geometry: [248, 113, 113],   // red outline — ellipse/centroid/orientation
  forecast: [245, 158, 11],    // amber — opacity by horizon
  hindcast: [192, 38, 211],    // magenta/purple — particle cloud + ellipses
  origin: [251, 191, 36],      // gold — probable origin ring
  vessel: [34, 211, 238],      // cyan — normal AIS track
  candidate: [251, 146, 60],   // orange — scored candidate
  suspect: [239, 68, 68],      // red + highlight — top suspect
  filtered: [120, 133, 151],   // dimmed gray — excluded vessels
};

export const css = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

/** Contract source flag -> header badge text. Never hardcoded in the UI:
 *  the value always comes from the contract file / stage status. */
export function sourceBadge(source) {
  switch ((source || "").toLowerCase()) {
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
