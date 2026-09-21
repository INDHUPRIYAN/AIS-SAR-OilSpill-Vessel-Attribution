/* The investigation's presentation script.
 *
 * The workspace already knows its stages (lib/stages) and judges each one
 * from the run's own status rows and artefacts. What it lacked was TIME:
 * a stage landed and every overlay for it appeared at once. This module
 * splits the analytical story into BEATS -- scanning, detection, the mask
 * tracing itself, wind, currents, the hindcast stepping backward, the origin
 * converging, the forecast stepping forward, AIS tracks drawn by timestamp,
 * the gates dimming vessels, candidates lit one by one -- and says, for a
 * beat at progress t, which stage the workspace is in, where the clock is,
 * how much of each REAL artefact is revealed, and what the HUD says.
 *
 * Nothing here executes or invents anything. A beat that needs an artefact
 * the run has not produced HOLDS on the run's real status (or, for optional
 * inputs like the forcing grids, is skipped with a note). A stage the run
 * failed stops the presentation on the failure. The animation only ever
 * reveals what the pipeline actually wrote, progressively.
 *
 * Pure functions throughout: the hook in useCinematic.js owns the clock. */

import { clamp01, easeInOut, easeOut, lerp, span } from "./replay";

/** Share of the forecast beat spent travelling; the rest holds on the result. */
export const FORECAST_TRAVEL = 0.68;

/* ---------------------------------------------------------------- beats --- */

/* `dur` is seconds at 1×. `needs` are the inputs a beat reads (see
 * readiness()); `soft` beats skip when an input is missing instead of
 * stopping the presentation. `stage` is the workspace stage the beat lives
 * in -- the panels, headline and default layers follow it; `sub` is the
 * analytical context the right panel shows within that stage. */
export const BEATS = [
  { id: "globe", stage: "acquisition", chip: "SCENE", dur: 2.5, title: "Global maritime view",
    needs: ["scene", "flight"] },
  { id: "footprint", stage: "scene", chip: "SCENE", dur: 4, title: "Scene acquisition",
    needs: ["scene"] },
  { id: "sar", stage: "scene", chip: "SCENE", dur: 3, title: "Scene loaded",
    needs: ["scene"] },
  { id: "preprocess", stage: "preprocess", chip: "DETECTION", dur: 4.5, title: "SAR pre-processing",
    needs: ["detect"] },
  { id: "tiling", stage: "tiling", chip: "DETECTION", dur: 4, title: "Georeferencing & image tiling",
    needs: ["detect"] },
  { id: "scan", sub: "detection", stage: "detection", chip: "DETECTION", dur: 4.5, title: "Oil spill scanning",
    needs: ["detect"] },
  { id: "detect", sub: "detection", stage: "detection", chip: "DETECTION", dur: 2.5, title: "Detection",
    needs: ["detect", "slick"] },
  { id: "segment", sub: "segmentation", stage: "detection", chip: "DETECTION", dur: 4.5, title: "Oil-slick segmentation",
    needs: ["slick"] },
  { id: "validate", sub: "validation", stage: "validation", chip: "VALIDATION", dur: 5, title: "Look-alike validation",
    needs: ["detect"] },
  { id: "wind", sub: "wind", stage: "validation", chip: "VALIDATION", dur: 3.5, title: "Wind analysis",
    needs: ["forcing"], soft: true },
  { id: "currents", sub: "currents", stage: "drift", chip: "DRIFT", dur: 3.5, title: "Ocean current analysis",
    needs: ["forcing"], soft: true },
  { id: "hindcast", sub: "hindcast", stage: "drift", chip: "HINDCAST", dur: 6.5, title: "Hindcast analysis",
    needs: ["hindcast"] },
  { id: "origin", sub: "hindcast", stage: "drift", chip: "HINDCAST", dur: 3, title: "Estimated origin",
    needs: ["hindcast"] },
  /* Same shape as hindcast + origin (6.5 s of travel, then 3 s on the result):
   * the forecast travels for the first FORECAST_TRAVEL of its beat and HOLDS on
   * the last horizon for the rest, so the camera can ease out and the whole
   * forecast is on screen before the vessels take over. At 5 s with the clock
   * arriving on the beat's last frame, the next frame was already AIS. */
  { id: "forecast", sub: "forecast", stage: "drift", chip: "FORECAST", dur: 9.5, title: "Forecast",
    needs: ["forecast"], soft: true },
  { id: "ais", sub: "traffic", stage: "ais", chip: "VESSELS", dur: 6, title: "AIS traffic reconstruction",
    needs: ["vessels"] },
  { id: "filter", sub: "filter", stage: "ais", chip: "VESSELS", dur: 5, title: "AIS filtering",
    needs: ["vessels", "suspects"] },
  { id: "ranking", sub: "ranking", stage: "ais", chip: "VESSELS", dur: 4, title: "Candidate ranking",
    needs: ["suspects"] },
  { id: "attribution", stage: "attribution", chip: "ATTRIBUTION", dur: 3.5, title: "Vessel attribution",
    needs: ["suspects"] },
  { id: "evidence", stage: "evidence", chip: "EVIDENCE", dur: 3, title: "Investigation record",
    needs: [] },
];

export const BEAT_INDEX = Object.fromEntries(BEATS.map((b, i) => [b.id, i]));
export const BEAT_CHIPS = ["SCENE", "DETECTION", "VALIDATION", "DRIFT", "HINDCAST", "FORECAST", "VESSELS", "ATTRIBUTION", "EVIDENCE"];
export const TOTAL_BEAT_SECONDS = BEATS.reduce((a, b) => a + b.dur, 0);

export const beatById = (id) => BEATS[BEAT_INDEX[id]] || null;

/* ------------------------------------------------------------ readiness --- */

/**
 * Whether each input a beat can need is available, from the run's real
 * state. Every answer is one of:
 *   ready    the artefact is on screen
 *   wait     the run is producing it (or loading it) -- hold, honestly
 *   missing  the run will not produce it -- a soft beat skips, a hard one stops
 *   failed   the stage failed -- stop on the failure, with its detail
 *
 * @param {object} c
 * @param {object} c.judged    judgeStages() output
 * @param {object} c.layers    contract payloads by layer name
 * @param {boolean} c.hasScene an investigation/scene exists
 * @param {string}  c.runState "running" | "complete" | ...
 * @param {string}  c.forcingState "idle" | "loading" | "ok" | "error"
 * @param {boolean} c.awaitingStatus a run exists but its status rows have not
 *                                   arrived yet -- pending reads as "wait"
 * @param {boolean} c.flightDone   the globe has landed on the AOI (the globe
 *                                 beat's own camera flight, not a pipeline stage)
 */
export function readiness({ judged, layers = {}, hasScene, runState, forcingState, awaitingStatus = false, flightDone = true }) {
  const running = runState === "running";
  const fromStage = (st, layer, detail) => {
    if (st === "done") return layer ? { s: "ready" } : { s: "wait", why: "loading the artefact" };
    if (st === "running" || st === "loading") return { s: "wait", why: "running" };
    if (st === "failed") return { s: "failed", why: detail || "failed" };
    if (st === "cancelled") return { s: "failed", why: "cancelled" };
    if (running) return { s: "wait", why: "queued" };
    return awaitingStatus ? { s: "wait", why: "loading the run status" } : { s: "missing", why: "not run" };
  };
  const det = judged?.detection || {};
  const drift = judged?.drift || {};
  const attr = judged?.attribution || {};
  const out = {};
  out.scene = hasScene || layers.scene_meta ? { s: "ready" } : { s: "missing", why: "no scene selected" };
  out.flight = flightDone ? { s: "ready" } : { s: "wait", why: "flying to the area of interest" };
  out.detect = fromStage(det.state, layers.detect || layers.slick || det.state === "done", det.note);
  const slickFeats = layers.slick?.features?.length ?? 0;
  out.slick = det.state === "done"
    ? (layers.slick ? (slickFeats ? { s: "ready" } : { s: "missing", why: "no region segmented" }) : { s: "wait", why: "loading slick.geojson" })
    : fromStage(det.state, false, det.note);
  out.forcing = forcingState === "ok" ? { s: "ready" }
    : forcingState === "loading" || forcingState === "idle" ? { s: "wait", why: "loading the forcing grids" }
      : { s: "missing", why: "no forcing grid recorded for this run" };
  const hs = rowState(drift.row), fs = rowState(drift.forecastRow);
  out.hindcast = fromStage(hs === "pending" && drift.state === "running" ? "running" : hs, layers.origin_cloud, drift.row?.detail);
  out.forecast = fromStage(fs === "pending" && drift.state === "running" ? "running" : fs, layers.forecast?.features?.length, drift.forecastRow?.detail);
  out.vessels = fromStage(attr.state, layers.vessels, attr.note);
  out.suspects = fromStage(attr.state, layers.suspects, attr.note);
  return out;
}

function rowState(row) {
  if (!row) return "pending";
  if (["ok", "fallback", "mock"].includes(row.status)) return "done";
  return row.status || "pending";
}

/** The worst answer among a beat's needs (failed > missing > wait > ready). */
export function beatGate(beat, ready) {
  const order = { failed: 3, missing: 2, wait: 1, ready: 0 };
  let worst = { s: "ready" }, need = null;
  for (const n of beat.needs || []) {
    const r = ready?.[n] || { s: "missing", why: "unknown input" };
    if (order[r.s] > order[worst.s]) { worst = r; need = n; }
  }
  return { ...worst, need };
}

/* ------------------------------------------------------------ AIS gates --- */

/** Which of the three attribution gates a backend filter reason belongs to.
 *  Reason phrases come from the attribution engine; grouping is only for the
 *  order in which the presentation dims them -- the FACT that a vessel was
 *  filtered is the backend's, never this module's. Temporal is tested first:
 *  "outside time window" would otherwise match the spatial "outside". */
export function gateOf(reason) {
  const r = String(reason || "").toLowerCase();
  if (!r) return 0;
  if (/time|window|temporal|before|after/.test(r)) return 2;
  if (/origin region|distance|spatial|outside|far/.test(r)) return 1;
  return 3;
}

export const GATE_LABELS = ["Vessels reconstructed", "Spatial gate", "Temporal gate", "Trajectory gate"];

/** The funnel the map animates: counts per gate from the vessel layer's own
 *  `filtered` / `filter_reason` flags, plus the ranked count from suspects. */
export function filterCounts(vessels, suspects) {
  const feats = vessels?.features || [];
  const total = feats.length;
  const removed = [0, 0, 0, 0];
  for (const f of feats) {
    const p = f.properties || {};
    if (p.filtered) removed[gateOf(p.filter_reason)] += 1;
  }
  const remaining = [total];
  for (let g = 1; g <= 3; g++) remaining.push(remaining[g - 1] - removed[g]);
  return { total, removed, remaining, ranked: suspects?.suspects?.length ?? 0 };
}

/* ----------------------------------------------------------------- frame -- */

/**
 * Everything the workspace needs to draw beat `id` at progress `t` (0..1).
 *
 * @param {string} id        beat id
 * @param {number} t         progress
 * @param {object} D         facts of the run: t0 (scene epoch ms), backtrackH,
 *                           forecastMaxH, aisStart, originT, nRanked, counts
 */
export function frameOf(id, t, D = {}) {
  const i = BEAT_INDEX[id] ?? 0;
  const at = (x) => i === BEAT_INDEX[x];
  const after = (x) => i > BEAT_INDEX[x];
  const t0 = D.t0 ?? null;
  const e = easeInOut(clamp01(t));

  /* the clock */
  let timeMs = t0;
  if (at("hindcast") && t0) timeMs = lerp(t0, t0 - (D.backtrackH ?? 24) * 3.6e6, e);
  else if (at("origin") && t0) timeMs = D.originT ?? t0 - (D.backtrackH ?? 24) * 3.6e6;
  else if (at("forecast") && t0) timeMs = lerp(t0, t0 + (D.forecastMaxH ?? 24) * 3.6e6, easeInOut(clamp01(t / FORECAST_TRAVEL)));
  else if (at("ais") && t0) timeMs = lerp(D.aisStart ?? t0 - 24 * 3.6e6, t0, e);

  /* what is revealed of each real artefact */
  const nRanked = D.nRanked ?? 0;
  const reveal = {
    sar: !after("footprint") ? 0 : at("sar") ? e : at("wind") ? 1 - span(t, 0, 0.4) : after("wind") ? 0 : 1,
    detectBox: at("detect") ? span(t, 0, 0.5) : (after("detect") && !after("validate")) ? 1 : null,
    slick: at("segment") ? t : after("segment") ? null : at("detect") ? 0 : null,
    wind: at("wind") ? span(t, 0.15, 0.85) : after("wind") ? 1 : null,
    currents: at("currents") ? span(t, 0.1, 0.85) : after("currents") ? 1 : null,
    origin: at("origin") ? easeOut(clamp01(t)) : after("origin") ? 1 : null,
    tracksUntil: at("ais"),
    /* gates land at 20 / 40 / 60 %: the outcome of the filtering holds for the rest */
    gate: at("filter") ? Math.min(3, Math.floor(t * 5)) : after("filter") ? 3 : null,
    ranked: at("ranking") ? Math.min(nRanked, Math.floor(t * (nRanked + 1))) : after("ranking") ? nRanked : 0,
    forecastUpTo: at("forecast") && t0 ? (timeMs - t0) / 3.6e6 : after("forecast") ? Infinity : null,
    vectorDim: after("currents"),
    dimOthers: at("attribution") || after("attribution"),
  };

  /* layers the beat wants on; the user's toggles do not apply mid-flight */
  const on = (...k) => Object.fromEntries(k.map((x) => [x, true]));
  const OFF = { sar: false, tiles: false, slick: false, lookalikes: false, geometry: false, wind: false, currents: false,
    hindcast: false, origin: false, forecast: false, vessels: false, footprints: false, aoi: false };
  let show;
  switch (id) {
    case "globe": case "footprint": show = { ...OFF, ...on("aoi", "footprints") }; break;
    case "sar": case "preprocess": show = { ...OFF, ...on("sar", "aoi") }; break;
    case "tiling": case "scan": show = { ...OFF, ...on("sar", "tiles") }; break;
    case "detect": show = { ...OFF, ...on("sar", "tiles") }; break;
    case "segment": show = { ...OFF, ...on("sar", "tiles", "slick") }; break;
    case "validate": show = { ...OFF, ...on("sar", "tiles", "slick", "lookalikes") }; break;
    case "wind": show = { ...OFF, ...on("sar", "slick", "wind") }; break;
    case "currents": show = { ...OFF, ...on("slick", "wind", "currents") }; break;
    case "hindcast": show = { ...OFF, ...on("slick", "wind", "currents", "hindcast") }; break;
    case "origin": show = { ...OFF, ...on("slick", "hindcast", "origin") }; break;
    case "forecast": show = { ...OFF, ...on("slick", "origin", "forecast", "hindcast") }; break;
    case "ais": case "filter": case "ranking": show = { ...OFF, ...on("slick", "origin", "vessels") }; break;
    case "attribution": show = { ...OFF, ...on("slick", "origin", "vessels", "forecast") }; break;
    default: show = { ...OFF, ...on("slick", "origin", "vessels", "forecast", "hindcast") };
  }

  return { id, index: i, t, timeMs, reveal, show, hud: hudOf(id, t, D, reveal, timeMs) };
}

/** The final settled state after the presentation: everything the run
 *  produced, visible at once, with the user's toggles free again. */
export const FINAL_SHOW = { slick: true, origin: true, vessels: true, forecast: true, hindcast: true, wind: true, currents: true };

/* ------------------------------------------------------------------- HUD -- */

const rel = (ms, t0) => {
  if (ms == null || t0 == null) return "";
  const h = (ms - t0) / 3.6e6;
  if (Math.abs(h) < 0.05) return "NOW";
  return `T ${h < 0 ? "−" : "+"} ${Math.abs(h).toFixed(Math.abs(h) < 10 ? 1 : 0)} h`;
};

function hudOf(id, t, D, reveal, timeMs) {
  const c = D.counts;
  const n = (v) => (v == null ? "—" : v);
  switch (id) {
    case "globe": return { line: t < 0.5 ? "Flying to the area of interest…" : "Area of interest locked" };
    case "footprint": return { line: t < 0.45 ? "Searching Sentinel-1 scenes…" : t < 0.7 ? `Scene found${D.sceneId ? ` · ${D.sceneId}` : ""}` : "Loading SAR scene…" };
    case "sar": return { line: "Calibrated σ⁰ backscatter over the area of interest" };
    case "preprocess": return { line: ["Radiometric calibration", "Speckle filtering", "Georeferencing", "Image tiling"][Math.min(3, Math.floor(t * 4))] };
    case "tiling": return { line: t < 0.55 ? (D.tileCount ? `Cutting the scene into ${D.tileCount.toLocaleString()} tiles…` : "Cutting the scene into tiles…") : `${D.tileLabel || "Selected tile"} — ready for segmentation` };
    case "scan": return { line: "Searching for anomalous dark formations…", progress: t };
    case "detect": return { line: t < 0.5 ? "Anomalous dark formation isolated" : "Candidate slick" };
    case "segment": return { line: t < 0.35 ? "Candidate pixels" : t < 0.7 ? "Mask expanding…" : "Tracing the slick boundary…" };
    case "validate": return { line: ["Wind conditions — analysing…", "Backscatter — analysing…", "Shape / texture — analysing…"][Math.min(2, Math.floor(t * 3))] };
    case "wind": return { line: t < 0.15 ? "Loading wind field…" : t < 0.85 ? "Wind vectors" : `Wind field active${D.windProvider ? ` · ${D.windProvider}` : ""}` };
    case "currents": return { line: t < 0.1 ? "Loading surface currents…" : t < 0.85 ? "Current vectors" : `Current field active${D.currentProvider ? ` · ${D.currentProvider}` : ""}` };
    case "hindcast": return { line: "Tracing the slick backward…", clock: rel(timeMs, D.t0) };
    case "origin": return { line: "Hindcast converged — origin uncertainty region", clock: rel(timeMs, D.t0) };
    case "forecast": return { line: "Predicting future slick movement…", clock: rel(timeMs, D.t0) };
    case "ais": return { line: t < 0.2 ? "Loading historical AIS…" : `${n(D.vesselCount ?? c?.total)} vessels found`, clock: rel(timeMs, D.t0) };
    case "filter": {
      const g = reveal.gate ?? 0;
      const steps = [`${n(c?.total)} considered`];
      for (let k = 1; k <= g; k++) steps.push(`${GATE_LABELS[k]} → ${n(c?.remaining?.[k])}`);
      if (g >= 3) steps.push(`${n(c?.ranked)} candidates`);
      return { line: steps.join("  ·  ") };
    }
    case "ranking": return { line: reveal.ranked ? `Candidate ${String(reveal.ranked).padStart(2, "0")} of ${n(D.nRanked)}` : "Scoring candidates…" };
    case "attribution": return { line: "Potential source vessel — highest-ranked candidate" };
    case "evidence": return { line: "Sealed artefacts, decisions and the incident record" };
    default: return { line: "" };
  }
}
