/* The investigation state machine.
 *
 * The workspace is ONE screen whose panels and overlays change as an
 * investigation moves through its stages. This module is the single
 * definition of those stages, of how each one is judged done / running /
 * pending from the run's OWN status rows and artefacts, and of what the map
 * should show for it. Nothing here executes anything: it reads.
 *
 * Two kinds of stage exist and the distinction is kept honest everywhere:
 *
 *   EXECUTED  a pipeline stage the server reports (`detect`, `characterise`,
 *             `drift_hindcast`, `drift_forecast`, `attribution`);
 *   DERIVED   a step an analyst thinks in (scene loaded, pre-processing,
 *             tiling, validation, evidence, report) whose state is READ from
 *             what the executed stages produced. A derived stage never claims
 *             a status the artefacts do not support.
 */

const DONE = new Set(["ok", "fallback", "mock"]);

/* Stage order. `chip` groups stages under the bottom bar's five headings;
 * `title` is the map's headline; `basemap` the default map surface. */
export const STAGES = [
  { id: "acquisition", chip: "SCENE", label: "Scene acquisition",
    title: "Maritime Oil Spill Analysis",
    sub: "Load and analyse Sentinel-1 SAR imagery to detect oil spills and investigate potential source vessels",
    basemap: "satellite", panel: "scene", derived: true },
  { id: "scene", chip: "SCENE", label: "Scene loaded",
    title: "Maritime Oil Spill Analysis", basemap: "sar", panel: "scene", derived: true },
  { id: "preprocess", chip: "DETECTION", label: "SAR pre-processing",
    title: "Radiometric Calibration", basemap: "sar", panel: "processing", derived: true },
  { id: "tiling", chip: "DETECTION", label: "Georeferencing & image tiling",
    title: "Georeferencing & Image Tiling", basemap: "geographic", panel: "tiling", derived: true },
  { id: "detection", chip: "DETECTION", label: "Oil-slick segmentation",
    title: "Oil-Slick Segmentation", basemap: "sar", panel: "detection", executed: "detect" },
  { id: "validation", chip: "DETECTION", label: "Look-alike validation",
    title: "Oil-Slick Segmentation", basemap: "geographic", panel: "validation", derived: true },
  { id: "geometry", chip: "GEOMETRY", label: "Slick characterisation",
    title: "Validated Slick", basemap: "satellite", panel: "geometry", executed: "characterise" },
  { id: "drift", chip: "DRIFT", label: "Drift hindcast & origin",
    title: "Drift Hindcast", basemap: "satellite", panel: "drift", executed: "drift_hindcast" },
  { id: "ais", chip: "VESSELS", label: "AIS trajectory correlation",
    title: "Validated Slick", basemap: "satellite", panel: "ais", executed: "attribution" },
  { id: "attribution", chip: "ATTRIBUTION", label: "Vessel attribution",
    title: "Vessel Attribution", basemap: "satellite", panel: "attribution", executed: "attribution" },
  { id: "evidence", chip: "ATTRIBUTION", label: "Evidence & investigation record",
    title: "Investigation Record", basemap: "satellite", panel: "evidence", derived: true },
  { id: "report", chip: "ATTRIBUTION", label: "Incident report",
    title: "Incident Report", basemap: "satellite", panel: "report", derived: true },
];

export const STAGE_INDEX = Object.fromEntries(STAGES.map((s, i) => [s.id, i]));
export const CHIPS = ["SCENE", "DETECTION", "GEOMETRY", "DRIFT", "VESSELS", "ATTRIBUTION"];

export function stageById(id) {
  return STAGES[STAGE_INDEX[id]] || STAGES[0];
}

/** Executed-stage row lookup from the server's status list. */
function rowOf(stages, name) {
  return (stages || []).find((s) => s.stage === name) || null;
}

function stateOfRow(row) {
  if (!row) return "pending";
  if (DONE.has(row.status)) return "done";
  if (row.status === "running") return "running";
  if (row.status === "failed") return "failed";
  if (row.status === "cancelled") return "cancelled";
  return "pending";
}

/**
 * Judge every stage from what the run actually reported.
 *
 * @param {object} ctx
 * @param {Array}  ctx.stages    rows from /investigations/{id}/status (or the manifest)
 * @param {object} ctx.layers    contract payloads that have landed, by layer name
 * @param {object} ctx.runRow    /runs/{id} registry row (manifest, incident_id)
 * @param {boolean} ctx.hasScene an investigation/scene is selected at all
 * @param {string}  ctx.runState the run's overall state ("running", "complete", ...)
 * @returns {Object<string, {state:string, row:object|null, note:string|null}>}
 */
export function judgeStages({ stages, layers = {}, runRow, hasScene, runState }) {
  const detect = rowOf(stages, "detect");
  const charac = rowOf(stages, "characterise");
  const hind = rowOf(stages, "drift_hindcast");
  const fore = rowOf(stages, "drift_forecast");
  const attr = rowOf(stages, "attribution");
  const detectState = stateOfRow(detect);
  const running = runState === "running";

  const out = {};
  out.acquisition = { state: "done", row: null, note: null };
  out.scene = {
    state: layers.scene_meta || hasScene ? "done" : "pending", row: null,
    note: layers.scene_meta ? null : hasScene ? "scene selected; metadata arrives with the first run" : "no scene selected",
  };
  /* Pre-processing and tiling are properties of the detection stage: the
   * calibrated raster is the input and the tiles are cut inside `detect`.
   * They read as running while detect runs and done when it lands. */
  const pre = detectState === "done" ? "done"
    : detectState === "running" || (running && detectState === "pending") ? "running"
      : detectState;
  out.preprocess = { state: pre, row: detect, note: pre === "pending" ? "runs inside the detection stage" : null };
  out.tiling = { state: pre, row: detect, note: pre === "pending" ? "runs inside the detection stage" : null };
  out.detection = { state: detectState, row: detect, note: detect?.detail || null };
  /* Validation is the screening model's verdict, written into the detection
   * response. It is "done" the moment detect lands, never earlier. */
  out.validation = {
    state: detectState === "done" ? (layers.detect ? "done" : "loading") : detectState,
    row: detect, note: detectState === "done" && !layers.detect ? "loading the detection response" : null,
  };
  out.geometry = { state: stateOfRow(charac), row: charac, note: charac?.detail || null };
  /* Drift is two executed stages; the derived stage is done only when both
   * are, and failed if either failed. */
  const hs = stateOfRow(hind), fs = stateOfRow(fore);
  out.drift = {
    state: hs === "failed" || fs === "failed" ? "failed"
      : hs === "done" && fs === "done" ? "done"
        : hs === "running" || fs === "running" ? "running"
          : hs === "done" && fs === "pending" ? "running" : hs,
    row: hind, forecastRow: fore, note: hind?.detail || null,
  };
  const at = stateOfRow(attr);
  out.ais = { state: at, row: attr, note: runRow?.manifest?.ais?.detail || attr?.detail || null };
  out.attribution = { state: at, row: attr, note: attr?.detail || null };
  const sealed = Boolean(runRow?.manifest?.artefact_digest);
  const terminal = runState && runState !== "running" && runState !== "pending";
  out.evidence = {
    state: sealed ? "done" : terminal ? (runState === "complete" ? "loading" : "pending") : running ? "pending" : "pending",
    row: null, note: sealed ? null : runState === "cancelled" ? "a cancelled run is not sealed" : "artefacts are sealed when the run completes",
  };
  out.report = { state: sealed ? "done" : "pending", row: null,
    note: sealed ? null : "reports compose from sealed artefacts only" };
  return out;
}

/** The stage the workspace should land on when a run is opened: the one that
 *  is running, else the furthest ANALYTICAL stage done (attribution at most:
 *  the record and report stages are reached by the analyst, not landed on). */
export function landingStage(judged) {
  const runningIdx = STAGES.findIndex((s) => judged[s.id]?.state === "running");
  if (runningIdx >= 0) return STAGES[runningIdx].id;
  let last = 0;
  const cap = STAGE_INDEX.attribution;
  STAGES.forEach((s, i) => { if (i <= cap && judged[s.id]?.state === "done") last = i; });
  return STAGES[last].id;
}

/** True when a stage may be visited: its predecessor produced something, or
 *  it is one of the always-open stages. */
export function stageReachable(id, judged) {
  const i = STAGE_INDEX[id];
  if (i <= 1) return true;
  const prev = STAGES[i - 1];
  const st = judged[prev.id]?.state;
  return st === "done" || st === "running" || st === "failed" || judged[id]?.state === "done"
    || judged[id]?.state === "running";
}

/** Wall-clock completion of each executed stage, from the run's start and
 *  the cumulative stage seconds. Used for the timeline's stage markers; a
 *  stage without a recorded duration has no timestamp and says so. */
export function stageClock(runRow, stages) {
  const start = Date.parse(runRow?.started_utc || "");
  if (!Number.isFinite(start)) return {};
  let t = start;
  const out = {};
  for (const s of stages || []) {
    if (s.seconds == null) { out[s.stage] = null; continue; }
    t += Number(s.seconds) * 1000;
    out[s.stage] = t;
  }
  return out;
}

/** Which layers a stage wants visible by default. The user's own toggles
 *  override these once touched. */
export function stageLayers(id) {
  const on = (...k) => Object.fromEntries(k.map((x) => [x, true]));
  switch (id) {
    case "acquisition": return { sar: false, slick: false, geometry: false, forecast: false, hindcast: false, origin: false, vessels: false, lookalikes: false, wind: false, currents: false, footprints: true, aoi: true };
    case "scene": return { ...on("sar", "aoi", "footprints"), slick: false, geometry: false, forecast: false, hindcast: false, origin: false, vessels: false, lookalikes: false };
    case "preprocess": return { ...on("sar", "aoi"), slick: false, geometry: false, forecast: false, hindcast: false, origin: false, vessels: false, lookalikes: false };
    case "tiling": return { ...on("sar", "tiles"), slick: false, geometry: false, forecast: false, hindcast: false, origin: false, vessels: false, lookalikes: false };
    case "detection": return { ...on("sar", "tiles", "slick", "lookalikes"), geometry: false, forecast: false, hindcast: false, origin: false, vessels: false };
    case "validation": return { ...on("slick", "lookalikes", "wind", "tiles"), sar: false, geometry: false, forecast: false, hindcast: false, origin: false, vessels: false };
    case "geometry": return { ...on("slick", "geometry"), sar: false, forecast: false, hindcast: false, origin: false, vessels: false, lookalikes: false, tiles: false };
    case "drift": return { ...on("slick", "hindcast", "origin", "forecast"), sar: false, geometry: false, vessels: false, lookalikes: false, tiles: false };
    case "ais": return { ...on("slick", "origin", "vessels"), sar: false, geometry: false, forecast: false, hindcast: false, lookalikes: false, tiles: false };
    case "attribution": return { ...on("slick", "origin", "vessels"), sar: false, geometry: false, forecast: false, hindcast: false, lookalikes: false, tiles: false };
    case "evidence": return { ...on("slick", "origin", "vessels", "forecast"), sar: false, geometry: false, hindcast: false, lookalikes: false, tiles: false };
    case "report": return { ...on("slick", "origin", "vessels"), sar: false, geometry: false, forecast: false, hindcast: false, lookalikes: false, tiles: false };
    default: return {};
  }
}

/* --------------------------------------------------------------- tiles --- */

/** The tile grid the detector cut, from the raster shape and the model's tile
 *  size. Rows/cols are the whole numbers the segmenter walked; `n` is the count
 *  actually stated by the detect warnings when available. */
export function tileGrid(tilesInfo, tileSizePx = 256) {
  const shape = tilesInfo?.shape;
  const bounds = tilesInfo?.bounds_wgs84;
  if (!shape || !bounds) return null;
  const [h, w] = shape;
  const cols = Math.ceil(w / tileSizePx), rows = Math.ceil(h / tileSizePx);
  return { cols, rows, n: cols * rows, bounds, tileSizePx, shape };
}

/** Which tile a lon/lat falls in (1-based index, row-major from the north-west
 *  corner, which is how the tiles are written), or null outside the scene. */
export function tileAt(grid, lon, lat) {
  if (!grid || lon == null || lat == null) return null;
  const [w, s, e, n] = grid.bounds;
  if (lon < w || lon > e || lat < s || lat > n) return null;
  const col = Math.min(grid.cols - 1, Math.floor(((lon - w) / (e - w)) * grid.cols));
  const row = Math.min(grid.rows - 1, Math.floor(((n - lat) / (n - s)) * grid.rows));
  return { row, col, index: row * grid.cols + col + 1,
    bbox: [w + (col / grid.cols) * (e - w), n - ((row + 1) / grid.rows) * (n - s),
           w + ((col + 1) / grid.cols) * (e - w), n - (row / grid.rows) * (n - s)] };
}

export const fmtTile = (index) => (index == null ? "—" : `TILE ${String(index).padStart(2, "0")}`);
