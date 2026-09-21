/* The Investigation workspace — one persistent screen for the whole
 * investigation, from scene acquisition to the incident report.
 *
 * Layout: left control panel · centre map (or globe) · right intelligence
 * panel · bottom stage timeline. Only the panels' contents and the map's
 * overlays change as the investigation moves through its stages; the map
 * itself never remounts between stages.
 *
 * Runs the whole pipeline live (stages land one by one and the workspace
 * follows them) or replays a finished run from disk. The page never goes
 * blank because one stage failed: whatever landed, renders. Opening a
 * record (`?run=` / `?inv=`) restores the furthest stage its artefacts
 * support, so an old investigation reopens as the same workspace.
 *
 * Starting or replaying an analysis plays it as ONE continuous
 * presentation over the map (lib/cinematic): the globe flies to the AOI,
 * the SAR scene fades in, a sweep scans it, the mask traces itself, wind and
 * current fields fill in, the hindcast steps backward to the origin, the
 * forecast steps forward, AIS tracks draw by timestamp, the gates dim
 * vessels, candidates light up in rank order. Every beat waits on the run's
 * real stage status and reveals only the artefacts the run wrote; the
 * analyst can stop, skip or scrub it at any point and the workspace is
 * exactly the same instrument underneath.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useWorkspaceParams } from "../lib/urls";
import { AnimatePresence, motion } from "framer-motion";
import MapHud from "../components/workspace/MapHud";
import { FlyToInterpolator } from "@deck.gl/core";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Info, Loader2 } from "lucide-react";

import WorkspaceMap from "../components/workspace/WorkspaceMap";
import MapChrome from "../components/workspace/MapChrome";
import { AcquisitionPanel, AnalysisPanel } from "../components/workspace/ControlPanel";
import RightPanel from "../components/workspace/RightPanel";
import StageTimeline from "../components/workspace/StageTimeline";
import LayerPanel from "../components/workspace/LayerPanel";
import MeasureTool from "../components/workspace/MeasureTool";
import { parseCoordinate } from "../components/Globe";
import { useLandGeometry } from "../components/globe/GlobeScene";
import { landShare, validateSlick } from "../lib/geovalidate";
import { sourceBadge } from "../components/workspace/palette";
import { api, useApi, fmt } from "../lib/api";
import { useRunEvents } from "../lib/useRunEvents";
import { isDemoRun } from "../lib/demo";
import { TimeProvider, useTime } from "../components/maps/TimeContext";
import { hasRole, useSession } from "../lib/session";
import { useRegisterCommands, useRunInContext } from "../lib/shell";
import { fmtUtc } from "../lib/replay";
import { originEstimate } from "../lib/drift";
import {
  STAGES, STAGE_INDEX, judgeStages, landingStage, stageById, stageClock, stageLayers,
  tileAt, tileGrid, fmtTile,
} from "../lib/stages";
import { BEAT_INDEX, FINAL_SHOW, filterCounts, frameOf, readiness as beatReadiness } from "../lib/cinematic";
import { useCinematic } from "../lib/useCinematic";
import { aisBadgeFor } from "../lib/useGlobeData";
import { sceneFact } from "../lib/sceneName";

const LAYER_STAGE = {
  scene_meta: "detect", detect: "detect", slick: "characterise",
  origin_cloud: "drift_hindcast", forecast: "drift_forecast",
  suspects: "attribution", vessels: "attribution",
};

const isoDay = (d) => new Date(d).toISOString().slice(0, 10);

/** Strip the map's own report marker, so a derived view reads as a command. */
const stripEcho = ({ __echo, fitBbox, fitPad, ...v }) => v;
const FLY = { transitionDuration: 900, transitionInterpolator: new FlyToInterpolator() };

/** Fit a bbox in the current viewport size, with an eased flight. */
const CUT = { transitionDuration: 0, transitionInterpolator: undefined };

function fitView(viewIn, _size, bbox, pad = 70, motion = FLY) {
  // A camera the map reported is tagged; a camera built here is a command.
  // The command is "frame this box": the map fits it with its real size and
  // projection (WorkspaceMap -> MaritimeGlobe.fitBounds). Working the zoom out
  // here needed the viewport size, which is not known until the map has
  // loaded -- so the first flight of every page load kept the default zoom.
  const { __echo, fitBbox: _old, fitPad: _p, ...view } = viewIn;
  if (!bbox) return view;
  return { ...view, fitBbox: bbox, fitPad: pad, pitch: 0, bearing: 0, ...motion };
}

function bboxOfTracks(vessels) {
  let w = 180, s = 90, e = -180, n = -90, any = false;
  for (const f of vessels?.features || []) {
    for (const [x, y] of f.geometry?.coordinates || []) { any = true; w = Math.min(w, x); s = Math.min(s, y); e = Math.max(e, x); n = Math.max(n, y); }
  }
  return any ? [w, s, e, n] : null;
}

function polygonBbox(geom) {
  const ring = geom?.type === "Polygon" ? geom.coordinates?.[0]
    : geom?.type === "MultiPolygon" ? geom.coordinates?.[0]?.[0]
      : geom?.type === "Feature" ? polygonRing(geom.geometry) : geom?.type === "FeatureCollection" ? polygonRing(geom.features?.[0]?.geometry) : null;
  if (!ring?.length) return null;
  const xs = ring.map((c) => c[0]), ys = ring.map((c) => c[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
function polygonRing(g) {
  return g?.type === "Polygon" ? g.coordinates?.[0] : g?.type === "MultiPolygon" ? g.coordinates?.[0]?.[0] : null;
}
const bboxPolygon = (b) => ({ type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] });

/** Union of bboxes and points, padded by `m` degrees; null when nothing given. */
function unionBbox(items, m = 0.08) {
  let w = 180, s = 90, e = -180, n = -90, any = false;
  for (const it of items) {
    if (!it) continue;
    if (it.length === 2) { any = true; w = Math.min(w, it[0]); s = Math.min(s, it[1]); e = Math.max(e, it[0]); n = Math.max(n, it[1]); }
    else if (it.length === 4) { any = true; w = Math.min(w, it[0]); s = Math.min(s, it[1]); e = Math.max(e, it[2]); n = Math.max(n, it[3]); }
  }
  return any ? [w - m, s - m, e + m, n + m] : null;
}

const featureBbox = (f) => polygonBbox(f?.geometry);

/** Everything the drift stage draws, as one box: the slick, the hindcast's
 *  ellipse centres hour by hour, the origin and the forecast footprints. */
/* The HUD owns the map's upper right and the legend its lower left, so the
 * subject is framed in what is left: more padding on the right than the left. */
const HUD_PAD = (base) => ({ top: base, bottom: base, left: Math.max(60, base - 20), right: base + 250 });

function driftBbox(c, est, origin, forecast) {
  /* Frame what the run actually claims: the slick, the origin, the hindcast
   * INSIDE the published origin window, and the forecast. The backtrack past
   * the window is drawn (faded) but does not get to dominate the view. */
  const winStart = Date.parse(origin?.metadata?.origin_window_start_utc ?? "");
  const centres = (origin?.features || [])
    .filter((f) => (f.properties?.feature_type || f.properties?.kind) === "ellipse" && Array.isArray(f.properties.center))
    .filter((f) => { const t = Date.parse(f.properties.t_utc ?? ""); return !Number.isFinite(winStart) || !Number.isFinite(t) || t >= winStart; })
    .map((f) => f.properties.center);
  return unionBbox([c, est?.center, ...centres, ...(forecast?.features || []).map(featureBbox)], 0.015);
}

/** The ranked candidates where they matter: each one's AIS fixes within
 *  reach of the estimated origin, plus the origin itself. */
/** Bounding box of every slick polygon in the run's own slick layer. */
function slickBbox(slick) {
  const pts = [];
  for (const f of slick?.features || []) {
    const g = f.geometry;
    const polys = g?.type === "Polygon" ? [g.coordinates] : g?.type === "MultiPolygon" ? g.coordinates : [];
    for (const poly of polys) for (const q of poly[0] || []) pts.push(q);
  }
  return pts.length ? unionBbox(pts, 0.002) : null;
}

function candidateBbox(vessels, suspects, est) {
  if (!est?.center || !suspects?.suspects?.length) return null;
  /* The top three only, out to ~18 km: wide enough to watch them sail through
   * the origin now that they tow short trails, not so wide that a dozen
   * candidates' whole tracks set the scale. */
  const ranked = new Set(suspects.suspects.slice(0, 3).map((s) => s.mmsi));
  const [ox, oy] = est.center;
  const reach = Math.max(0.16, (est.radiusKm || 0) / 111 * 6);      // degrees: ~18 km, or 6x the uncertainty
  const pts = [];
  for (const f of vessels?.features || []) {
    if (!ranked.has(f.properties?.mmsi)) continue;
    for (const q of f.geometry?.coordinates || []) if (Math.abs(q[0] - ox) < reach && Math.abs(q[1] - oy) < reach) pts.push(q);
  }
  return unionBbox([est.center, ...pts], 0.02);
}

export default function Investigation() {
  /* One clock for the whole workspace. The stage rail renders it, the map
   * layers read it, and any panel that needs the time on screen subscribes
   * with `useTime()` instead of being handed a prop down four levels. */
  return (
    <TimeProvider>
      <InvestigationWorkspace />
    </TimeProvider>
  );
}

function InvestigationWorkspace() {
  const [params, setParams] = useWorkspaceParams();
  const { user } = useSession();
  const canRun = hasRole(user, "investigator", "analyst");
  const canPublish = hasRole(user, "reviewer", "investigator");

  /* ------------------------------------------------------ investigation -- */
  const { data: invs, reload: reloadInvs } = useApi(() => api.listInvestigations(), []);
  const wantNew = params.get("new") === "1";
  const invId = wantNew ? null : (params.get("inv") || (params.get("run") ? null : invs?.[0]?.id) || null);
  const { data: inv } = useApi(
    () => (invId ? api.getInvestigation(invId) : Promise.resolve(null)), [invId]);

  const [running, setRunning] = useState(false);
  const [job, setJob] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [replayRunId, setReplayRunId] = useState(null);
  const [replayMode, setReplayMode] = useState(true);
  const [status, setStatus] = useState(null);
  const [layers, setLayers] = useState({});
  const [layerErr, setLayerErr] = useState({});
  const [toasts, setToasts] = useState([]);
  const [selectedMmsi, setSelectedMmsi] = useState(null);
  const [hover, setHover] = useState(null);
  const time = useTime();
  const timeMs = time.t;
  const setTimeMs = time.setT;
  const playing = time.playing;
  const setPlaying = useCallback((on) => (on ? time.play() : time.pause()), [time]);
  const speed = time.speed;
  const setSpeed = time.setSpeed;
  const [showOverride, setShowOverride] = useState({});
  const [forcing, setForcing] = useState(null);
  const [view, setView] = useState({ longitude: 80.32, latitude: 13.05, zoom: 5.5, pitch: 0, bearing: 0 });
  const [viewport, setViewport] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [measuring, setMeasuring] = useState(false);
  const [measurePoints, setMeasurePoints] = useState([]);
  const [busy, setBusy] = useState(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [layersOpen, setLayersOpen] = useState(false);
  /* A new investigation starts from the real world: the globe, not a tile. */
  const [surface, setSurface] = useState(params.get("new") === "1" ? "globe" : "map");
  const [basemapOverride, setBasemapOverride] = useState(null);
  const [forcingState, setForcingState] = useState("idle");
  const [flightDone, setFlightDone] = useState(true);
  /* Once the 3D surface has been shown it stays mounted -- hidden, idle, not
   * polling -- so the next flight from orbit starts without the second WebGL
   * context compiling its shaders again on the click. It is never mounted
   * ahead of use: a second context is not free on every workspace visit. */
  /* The two workspace sidebars collapse independently of each other and of
   * the app's navigation rail; the map takes the freed width. Remembered per
   * browser, because it is a preference about the desk, not about a run. */
  const stored = (k) => { try { return localStorage.getItem(k) === "1"; } catch { return false; } };
  const [leftOff, setLeftOff] = useState(() => stored("ot.ws.leftOff"));
  const [rightOff, setRightOff] = useState(() => stored("ot.ws.rightOff"));
  useEffect(() => { try { localStorage.setItem("ot.ws.leftOff", leftOff ? "1" : "0"); localStorage.setItem("ot.ws.rightOff", rightOff ? "1" : "0"); } catch { /* private mode */ } }, [leftOff, rightOff]);
  /* The analytical context shown on the right within each stage. */
  const [subs, setSubs] = useState({});
  const [incidentError, setIncidentError] = useState(null);
  const [reportError, setReportError] = useState(null);

  /* ------------------------------------------------------ acquisition --- */
  const [q, setQ] = useState("");
  const [acq, setAcq] = useState({ mission: "S1", product: "GRD", polarisation: "", orbit: "",
    start: isoDay(Date.now() - 30 * 86400e3), end: isoDay(Date.now()) });
  const [spatial, setSpatial] = useState({ mode: "draw", bbox: null, zoneId: "", aoiId: "" });
  const [drawPoints, setDrawPoints] = useState([]);
  const [drawCursor, setDrawCursor] = useState(null);
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [selectedScene, setSelectedScene] = useState(null);

  /* Precedence: a run the page itself produced (replay / just started) wins,
   * then the deep-linked run, then the investigation's latest. */
  const runId = replayRunId || job?.run_id || params.get("run")
    || status?.run_id || inv?.latest_run_id || null;
  const loadedFor = useRef(null);
  const fetched = useRef(new Set());
  /* One request per layer per run: the run-change path and the stage-landed
   * path both ask for the same files, and the second ask is pure waste. */
  const inflight = useRef(new Set());

  const toast = useCallback((text, tone = "danger") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 8000);
  }, []);

  /* ------------------------------------------------ incremental loading -- */
  const fetchLayer = useCallback(async (name, rid) => {
    const target = rid || runId;
    const key = `${target}:${name}`;
    if (inflight.current.has(key)) return;
    inflight.current.add(key);
    try {
      const data = name === "vessels"
        ? await api.vesselsGeojson(target)
        : target
          ? await api.layer(target, name, { lite: name === "origin_cloud" })
          : await api.invLayer(invId, name);
      setLayers((l) => ({ ...l, [name]: data }));
      setLayerErr((e) => ({ ...e, [name]: null }));
    } catch (e) {
      setLayerErr((er) => ({ ...er, [name]: e }));
      if (e.status === 422) toast(`Malformed contract — ${name}: ${e.message}`);
    } finally {
      inflight.current.delete(key);
    }
  }, [invId, runId, toast]);

  /* Stage transitions over SSE (lib/useRunEvents), with the status poll below
   * as the fallback it was written to replace. At a 2 s poll a 4 s stage looks
   * instantaneous; the stream reports the transition when it happens and says
   * which transport it is using. */
  const runEvents = useRunEvents(running ? runId : null,
    { onEnd: () => setRunning(false) });
  const eventBeat = runEvents.stages.length
    ? `${runEvents.stages.length}:${runEvents.stages[runEvents.stages.length - 1]?.status}` : "";

  /* The pipeline's own progress. `/api/jobs/{id}` counts stages the server has
   * finished; nothing reports progress WITHIN a stage, so nothing claims it. */
  const [progress, setProgress] = useState({ done: 0, total: 5, current: null });
  useEffect(() => {
    if (!running || !runId) return undefined;
    let alive = true;
    const read = () => api.getJob(`job-${runId}`)
      .then((j) => { if (alive && j) setProgress({
        done: j.stages_done ?? 0, total: j.stages_total || 5, current: j.current_stage || null }); })
      .catch(() => {});
    read();
    const id = setInterval(read, 3000);
    return () => { alive = false; clearInterval(id); };
  }, [running, runId, eventBeat]);

  /* A backend that stops answering used to be invisible here: the poll's
   * catch was empty, so the workspace went on showing the last status it had
   * as if it were current. Two misses in a row is not a blip. */
  const [statusMisses, setStatusMisses] = useState(0);
  const statusSeq = useRef(0);
  useEffect(() => {
    if (!invId && !runId) return undefined;
    let alive = true;
    const tick = async () => {
      const mine = ++statusSeq.current;
      try {
        const st = invId ? await api.invStatus(invId, runId) : null;
        if (!alive || mine !== statusSeq.current) return;
        if (st) {
          setStatus(st);
          for (const s of st.stages || []) {
            if ((s.status === "ok" || s.status === "fallback" || s.status === "mock")
                && !fetched.current.has(s.stage)) {
              fetched.current.add(s.stage);
              Object.entries(LAYER_STAGE)
                .filter(([, stage]) => stage === s.stage)
                .forEach(([n]) => fetchLayer(n, st.run_id));
            }
          }
          if (st.state !== "running") setRunning(false);
        }
        setStatusMisses(0);
      } catch {
        // Keep polling: the server may be restarting. Say so after two misses.
        if (alive && mine === statusSeq.current) setStatusMisses((n) => n + 1);
      }
    };
    tick();
    // The stream is the trigger while it is up; the interval is the floor.
    const id = setInterval(tick, running ? (runEvents.transport === "sse" ? 8000 : 2000) : 10000);
    return () => { alive = false; clearInterval(id); };
  }, [invId, running, fetchLayer, runId, eventBeat, runEvents.transport]);

  useEffect(() => {
    if (!runId || loadedFor.current === runId) return;
    loadedFor.current = runId;
    fetched.current = new Set();
    setLayers({}); setLayerErr({}); setSelectedMmsi(null);
    ["scene_meta", "detect", "slick", "forecast", "suspects"].forEach((n) => fetchLayer(n, runId));
    setTimeout(() => { fetchLayer("origin_cloud", runId); fetchLayer("vessels", runId); }, 250);
  }, [runId, fetchLayer]);


  /* -------------------------------------------------- run-scoped facts --- */
  const { data: runRow, reload: reloadRun } = useApi(() => (runId ? api.getRun(runId) : Promise.resolve(null)), [runId]);
  const { data: tilesInfo } = useApi(() => (runId ? api.tilesInfo(runId).catch(() => null) : Promise.resolve(null)), [runId]);
  const { data: funnel } = useApi(() => (runId ? api.runFunnel(runId).catch(() => null) : Promise.resolve(null)), [runId, runRow?.status]);
  const { data: verify } = useApi(() => (runId && runRow?.manifest ? api.verifyRun(runId).catch(() => null) : Promise.resolve(null)), [runId, runRow?.manifest?.artefact_digest]);
  const { data: decisions, reload: reloadDecisions } = useApi(() => (runId ? api.runDecisions(runId).catch(() => []) : Promise.resolve([])), [runId]);
  const { data: autoPreview } = useApi(() => (runId && runRow?.status === "complete" ? api.previewAutoIncident(runId).catch(() => null) : Promise.resolve(null)), [runId, runRow?.status]);
  const { data: incident, reload: reloadIncident } = useApi(() => (runRow?.incident_id ? api.getIncident(runRow.incident_id).catch(() => null) : Promise.resolve(null)), [runRow?.incident_id]);
  const { data: reportsRaw, reload: reloadReports } = useApi(() => (runId ? api.listReports({ run: runId }).catch(() => []) : Promise.resolve([])), [runId]);
  const reports = Array.isArray(reportsRaw) ? reportsRaw : reportsRaw?.items || [];
  const { data: models } = useApi(() => api.models().catch(() => null), []);
  const { data: aisStatus } = useApi(() => api.aisStatus().catch(() => null), [], { interval: 30000 });
  const { data: zonesQ } = useApi(() => api.listZones().catch(() => null), []);
  const { data: aoisQ } = useApi(() => api.listAois().catch(() => []), []);
  const { data: usersQ } = useApi(() => api.listUsers().catch(() => null), []);
  const { data: catalogueQ } = useApi(() => api.localScenes().catch(() => null), []);
  const { data: dossier } = useApi(() => (selectedMmsi ? api.getVessel(selectedMmsi).catch(() => null) : Promise.resolve(null)), [selectedMmsi]);
  const zones = zonesQ?.zones || [];
  const users = usersQ?.users || null;
  const catalogue = catalogueQ?.scenes || [];

  /* ------------------------------------------------------ derived state -- */
  const sceneT0 = useMemo(() => Date.parse(layers.scene_meta?.acquired_utc ?? "") || null, [layers.scene_meta]);
  const domain = useMemo(() => {
    if (!sceneT0) return null;
    const winStart = Date.parse(layers.origin_cloud?.metadata?.origin_window_start_utc ?? "") || sceneT0 - 24 * 3.6e6;
    return [winStart - 6 * 3.6e6, sceneT0 + 24 * 3.6e6];
  }, [sceneT0, layers.origin_cloud]);
  /* The clock's range is the run's own: from six hours before the published
   * origin window to a day past the acquisition, with the acquisition itself
   * marked. Published to the context so every subscriber reads one domain. */
  useEffect(() => {
    if (!domain) return;
    time.setRange(domain, { now: sceneT0, t: timeMs ?? sceneT0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain?.[0], domain?.[1], sceneT0]);

  const maxStep = useMemo(() => {
    let m = 0;
    for (const f of layers.origin_cloud?.features ?? []) m = Math.max(m, f.properties?.step_index ?? 0);
    return m;
  }, [layers.origin_cloud]);

  const stageRows = status?.stages?.length ? status.stages : (runRow?.manifest?.stages || []);
  const runState = status?.state || runRow?.status || null;
  const hasScene = Boolean(inv?.scene_id || selectedScene || layers.scene_meta);
  const judged = useMemo(() => judgeStages({
    stages: stageRows, layers, runRow, hasScene: Boolean(inv?.scene_id || selectedScene), runState,
  }), [stageRows, layers, runRow, inv, selectedScene, runState]);
  const clocks = useMemo(() => stageClock(runRow, runRow?.manifest?.stages || stageRows), [runRow, stageRows]);

  /* --------------------------------------------------- presentation ----- */
  /* What each beat may read right now, from the run's real state. A run
   * whose status has not arrived yet reads as "wait", never as "missing". */
  const ready = useMemo(() => beatReadiness({
    judged, layers, hasScene, runState, forcingState, flightDone,
    awaitingStatus: Boolean(runId) && !stageRows.length && !runRow?.status,
  }), [judged, layers, hasScene, runState, forcingState, flightDone, runId, stageRows.length, runRow?.status]);
  const cine = useCinematic({ readiness: ready });
  const cineRef = useRef(cine);
  cineRef.current = cine;

  /* ------------------------------------------------------------- stage -- */
  const urlStage = params.get("stage");
  const [stageId, setStageId] = useState(urlStage && STAGE_INDEX[urlStage] != null ? urlStage : (wantNew || !params.get("run") && !params.get("inv") ? "acquisition" : "scene"));
  const touched = useRef(Boolean(urlStage));
  const landedFor = useRef(null);
  /* Land on the right stage once the run's artefacts are known; while a run
   * executes, follow the running stage unless the analyst moved away. The
   * presentation, while it plays, is the one deciding the stage. */
  useEffect(() => {
    if (!runId) return;
    const key = `${runId}:${stageRows.map((s) => `${s.stage}=${s.status}`).join(",")}`;
    if (landedFor.current === key) return;
    landedFor.current = key;
    if (cineRef.current.active) return;
    if (touched.current && runState !== "running") return;
    if (!stageRows.length && !layers.scene_meta) return;
    setStageId(landingStage(judged));
  }, [runId, stageRows, judged, runState, layers.scene_meta]);
  useEffect(() => { if (wantNew) { setStageId("acquisition"); touched.current = false; } }, [wantNew]);

  const stage = stageById(stageId);

  /* Forcing grids: fetched once per run, and only when a stage or a toggle
   * asks for them. The endpoint opens the run's NetCDF files, and HDF5 reads
   * are not thread-safe in this backend: concurrent forcing_field calls have
   * taken the server down. Nothing here asks unprompted. */
  const forcingFor = useRef(null);
  const wantForcing = Boolean(showOverride.wind || showOverride.currents
    || stageId === "validation" || stageId === "drift" || cine.active
    || ["wind", "currents", "hindcast"].includes(subs[stageId]));
  const [forcingRetry, setForcingRetry] = useState(0);
  useEffect(() => {
    if (!runId || !wantForcing || forcingFor.current === runId) return undefined;
    /* Never ask while this run's own drift may be reading the same NetCDF
     * files: the server refuses (503) rather than crash in HDF5, and a request
     * that is certain to be refused is not worth sending. */
    if (runState === "running") { setForcingState("loading"); return undefined; }
    forcingFor.current = runId;
    setForcing(null); setForcingState("loading");
    let timer = null;
    api.forcingField(runId).then((d) => { setForcing(d); setForcingState("ok"); })
      .catch((e) => {
        forcingFor.current = null;
        // 503: another run holds the grids. Busy is not missing: try again.
        if (e.status === 503) timer = setTimeout(() => setForcingRetry((n) => n + 1), 5000);
        else setForcingState("error");
      });
    return () => { if (timer) clearTimeout(timer); };
  }, [runId, wantForcing, runState, forcingRetry]);
  useEffect(() => { setForcingState("idle"); }, [runId]);
  /* The stage rides in the URL, so a reload or a shared link restores it. */
  /* A stage the ANALYST chose is a place in history, so Back returns to the
   * previous stage rather than out of the case. A stage the presentation
   * steps through (nineteen beats) replaces, or Back would walk the whole
   * performance in reverse. */
  const gotoStage = useCallback((id, { push = false } = {}) => {
    touched.current = true;
    setStageId(id);
    setParams((prev) => { const n = new URLSearchParams(prev); n.set("stage", id); return n; }, { replace: !push });
  }, [setParams]);
  /* An analyst's own move takes the workspace back from the presentation. */
  const go = useCallback((id) => { cineRef.current.stop(); gotoStage(id, { push: true }); }, [gotoStage]);

  /* Back and Forward change the address; the stage on screen follows it. */
  useEffect(() => {
    if (urlStage && STAGE_INDEX[urlStage] != null && urlStage !== stageId) setStageId(urlStage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlStage]);

  /* The tile the analysed slick sits in: the detector's own grid. */
  const grid = useMemo(() => tileGrid(tilesInfo, (models?.models || []).find((m) => m.kind === "segment")?.metadata?.tile_size ? Number((models.models.find((m) => m.kind === "segment")).metadata.tile_size) : 256), [tilesInfo, models]);
  const slickP = layers.slick?.features?.[0]?.properties;
  const selectedTile = useMemo(() => (grid && slickP?.centroid ? tileAt(grid, slickP.centroid[0], slickP.centroid[1]) : null), [grid, slickP]);

  /* The beat in flight, as a frame: stage, clock, reveal fractions, HUD. */
  const sceneBbox = layers.scene_meta?.bbox || selectedScene?.bbox || inv?.bbox || spatial.bbox || null;
  const est0 = useMemo(() => originEstimate(layers.origin_cloud), [layers.origin_cloud]);
  const aisStart = useMemo(() => {
    let a = null;
    for (const f of layers.vessels?.features || []) for (const s of f.properties?.times_epoch || []) if (s != null) a = a == null ? s : Math.min(a, s);
    return a != null ? a * 1000 : null;
  }, [layers.vessels]);
  /* The funnel the HUD narrates: the backend's own cumulative counts when the
   * run has them, else what the vessel layer's flags add up to. */
  const counts = useMemo(() => (funnel?.indexed != null
    ? { total: funnel.indexed, remaining: [funnel.indexed, funnel.after_spatial, funnel.after_temporal, funnel.after_trajectory], ranked: funnel.candidates }
    : filterCounts(layers.vessels, layers.suspects)), [funnel, layers.vessels, layers.suspects]);
  const cineFrame = cine.active ? frameOf(cine.beat.id, cine.t, {
    t0: sceneT0,
    backtrackH: layers.origin_cloud?.metadata?.backtrack_hours ?? 24,
    forecastMaxH: Math.max(0, ...(layers.forecast?.features || []).map((f) => f.properties?.horizon_h ?? 0)) || 24,
    aisStart, originT: est0?.tUtc ? Date.parse(est0.tUtc) : null,
    nRanked: layers.suspects?.suspects?.length ?? 0, counts, vesselCount: layers.vessels?.features?.length ?? null,
    sceneId: layers.scene_meta?.scene_id || selectedScene?.product_id || selectedScene?.scene_id || null,
    tileCount: grid?.n ?? null, tileLabel: selectedTile ? fmtTile(selectedTile.index) : null,
    windProvider: forcing?.wind?.provider, currentProvider: forcing?.currents?.provider,
  }) : null;

  /* Attribution plays itself: from two hours before the origin window opens
   * to the acquisition, on a loop, so the candidates are seen sailing through
   * the origin rather than parked at one instant. Pausing (or scrubbing, which
   * pauses) hands the clock back; leaving the stage stops it. Not under
   * reduced-motion, and never during the presentation, which has its own clock. */
  const autoPlayed = useRef(null);
  useEffect(() => {
    if (cine.active) return undefined;
    const key = `${stageId}:${runId}`;
    if (stageId !== "attribution" || !layers.vessels || !layers.origin_cloud || !time.range) return undefined;
    if (autoPlayed.current === key) return undefined;
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    const ws = Date.parse(layers.origin_cloud?.metadata?.origin_window_start_utc ?? "");
    if (!Number.isFinite(ws)) return undefined;
    autoPlayed.current = key;
    time.setSpeed(1);
    time.playLoop(ws - 2 * 3.6e6);
    return () => { time.pause(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stageId, runId, Boolean(layers.vessels), Boolean(layers.origin_cloud), Boolean(time.range), cine.active]);
  useEffect(() => { if (stageId !== "attribution") autoPlayed.current = null; }, [stageId]);

  /* The beat decides the stage; the stage decides the panels. */
  const beatId = cine.active ? cine.beat.id : null;
  useEffect(() => {
    if (!beatId) return;
    const s = cine.beat.stage;
    setPlaying(false);
    if (s !== stageId) gotoStage(s);
    if (cine.beat.sub) setSubs((m) => ({ ...m, [s]: cine.beat.sub }));
    if (beatId === "globe" || beatId === "ais") setSelectedMmsi(null);
    if (beatId === "attribution") { const top = layers.suspects?.suspects?.[0]; if (top) setSelectedMmsi(top.mmsi); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [beatId]);
  /* When the presentation ends, settle the workspace on the complete
   * analytical state: everything the run produced, at the scene time, with
   * the analyst's toggles free again. */
  const wasActive = useRef(false);
  useEffect(() => {
    if (cine.active) { wasActive.current = true; return; }
    if (!wasActive.current) return;
    wasActive.current = false;
    touched.current = true;
    if (cine.finished) { setShowOverride(FINAL_SHOW); setSelectedMmsi((m) => m ?? layers.suspects?.suspects?.[0]?.mmsi ?? null); }
    if (sceneT0) setTimeMs(sceneT0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cine.active]);

  const basemap = basemapOverride || (cineFrame ? "satellite" : stage.basemap);
  /* Attribution, evidence and report ask "which vessel": only the ranked
   * candidates are drawn unless the analyst switches the rest on. The AIS
   * stage, whose subject IS the whole traffic picture and its filtering,
   * keeps everything. A switch the analyst has touched always wins. */
  const show = useMemo(() => {
    if (cineFrame) return cineFrame.show;
    const focus = ["attribution", "evidence", "report"].includes(stageId) ? { background: false, excluded: false } : {};
    return { ...stageLayers(stageId), ...focus, ...showOverride };
  }, [stageId, showOverride, cineFrame]);
  const onShow = useCallback((k, v) => setShowOverride((s) => ({ ...s, [k]: v })), []);


  /* Vessel layer filters from the control panel (ranked / background / excluded). */
  const vesselsShown = useMemo(() => {
    if (!layers.vessels) return null;
    const feats = layers.vessels.features.filter((f) => {
      const p = f.properties || {};
      if (p.rank) return show.ranked !== false;
      if (p.filtered) return show.excluded !== false;
      return show.background !== false;
    });
    return { ...layers.vessels, features: feats };
  }, [layers.vessels, show.ranked, show.excluded, show.background]);

  /* Default selection at attribution: the highest-ranked candidate. During
   * the presentation the ranking beat lights candidates itself. */
  useEffect(() => {
    if (cine.active) return;
    if ((stageId === "attribution" || stageId === "ais") && selectedMmsi == null) {
      const top = layers.suspects?.suspects?.[0];
      if (top) setSelectedMmsi(top.mmsi);
    }
  }, [stageId, layers.suspects, selectedMmsi, cine.active]);

  /* ---------------------------------------------------------- camera ----- */
  const size = viewport ? { width: viewport.width, height: viewport.height } : null;
  /* `animate` false is a cut: used when the workspace lands on a stage by
   * itself (opening a record, a run advancing), so a reload does not fly the
   * camera through every stage it passes. The analyst's own moves ease. */
  const flyTo = useCallback((target, animate = true) => {
    const motion = animate ? FLY : CUT;
    setView((v) => {
      if (target === "scene") {
        const b = layers.scene_meta?.bbox || selectedScene?.bbox || inv?.bbox || spatial.bbox;
        return b ? fitView(v, size, b, 70, motion) : v;
      }
      if (Array.isArray(target) && target.length === 4) return fitView(v, size, target, 70, motion);
      if (Array.isArray(target) && target.length === 2) return { ...stripEcho(v), longitude: target[0], latitude: target[1], zoom: Math.max(v.zoom, 9), ...motion };
      if (target?.bbox) return fitView(v, size, target.bbox, target.pad, motion);
      return v;
    });
  }, [layers.scene_meta, selectedScene, inv, spatial.bbox, size]);

  const cameraFor = useRef(null);
  const lastFlight = useRef(0);
  const pendingFlight = useRef(0);
  const flyToRef = useRef(flyTo);
  flyToRef.current = flyTo;
  useEffect(() => {
    const key = `${stageId}:${subs[stageId] || ""}:${runId}:${Boolean(layers.slick)}:${Boolean(layers.vessels)}:${Boolean(layers.scene_meta)}:${Boolean(layers.origin_cloud)}:${Boolean(layers.forecast)}:${selectedTile?.index}`;
    if (cameraFor.current === key) return;
    cameraFor.current = key;
    if (cineRef.current.active) return;   // the presentation flies its own camera
    const bbox = layers.scene_meta?.bbox;
    const est = originEstimate(layers.origin_cloud);
    const anim = touched.current;
    /* One flight at a time. A second target issued while a flight is in the
     * air lost to it: the first transition's interpolated frames kept writing
     * the view state back. The newer target waits for the landing instead. */
    const flyTo = (t) => {
      clearTimeout(pendingFlight.current);
      const wait = anim ? Math.max(0, lastFlight.current + 960 - Date.now()) : 0;
      const go = () => { lastFlight.current = Date.now(); flyToRef.current(t, anim); };
      if (wait) pendingFlight.current = setTimeout(go, wait); else go();
    };
    switch (stageId) {
      case "scene": case "preprocess": case "evidence": case "report":
        if (bbox) flyTo(bbox); break;
      case "tiling":
        if (selectedTile) flyTo({ bbox: selectedTile.bbox, pad: 220 }); else if (bbox) flyTo(bbox); break;
      case "detection":
        if (selectedTile) flyTo({ bbox: selectedTile.bbox, pad: 120 }); else if (slickP?.centroid) flyTo(slickP.centroid); break;
      case "validation":
        if (selectedTile) flyTo({ bbox: selectedTile.bbox, pad: 160 }); else if (slickP?.centroid) flyTo(slickP.centroid); break;
      case "geometry": {
        /* the subject is the slick's shape: fill the view with it */
        const sb = slickBbox(layers.slick);
        if (sb) flyTo({ bbox: sb, pad: HUD_PAD(90) }); else if (selectedTile) flyTo({ bbox: selectedTile.bbox, pad: 160 }); else if (slickP?.centroid) flyTo(slickP.centroid);
        break;
      }
      case "drift": {
        /* frame the whole story: the slick, every hour of the backward trail
         * and every forecast footprint -- not just the two end points */
        const c = slickP?.centroid;
        const b = driftBbox(c, est, layers.origin_cloud, layers.forecast);
        if (b) flyTo({ bbox: b, pad: HUD_PAD(70) }); else if (c) flyTo(c);
        break;
      }
      case "ais": case "attribution": {
        /* ranking and attribution are about the candidates near the origin,
         * not the whole basin of traffic */
        const tight = stageId === "attribution" || subs.ais === "ranking";
        const cb = tight ? candidateBbox(layers.vessels, layers.suspects, est) : null;
        const tb = cb || bboxOfTracks(layers.vessels);
        if (tb) flyTo({ bbox: tb, pad: HUD_PAD(cb ? 80 : 50) }); else if (bbox) flyTo(bbox);
        break;
      }
      default: break;
    }
  }, [stageId, subs, runId, layers.slick, layers.vessels, layers.scene_meta, layers.origin_cloud, layers.forecast, layers.suspects, selectedTile, slickP, flyTo]);

  /* The presentation's camera: one eased flight per beat (two for tiling:
   * the whole grid, then the selected tile). Every target is a real
   * geometry -- scene footprint, tile, slick, origin trail, tracks. */
  const cinePhase = beatId === "tiling" ? (cine.t < 0.55 ? 0 : 1) : 0;
  const cineCamKey = beatId ? `${beatId}:${cinePhase}:${runId}:${Boolean(layers.slick)}:${Boolean(layers.vessels)}:${Boolean(layers.origin_cloud)}:${selectedTile?.index}` : null;
  const cineCamFor = useRef(null);
  useEffect(() => {
    if (!cineCamKey || cineCamFor.current === cineCamKey) return;
    cineCamFor.current = cineCamKey;
    const flyTo = (t) => flyToRef.current(t, true);
    const c = slickP?.centroid;
    const tile = selectedTile;
    switch (beatId) {
      case "globe": break;                                   // the globe flies itself
      case "footprint": case "sar": case "preprocess": case "wind": case "currents":
        if (sceneBbox) flyTo({ bbox: sceneBbox, pad: 70 }); break;
      case "tiling":
        if (cinePhase === 0) { if (sceneBbox) flyTo({ bbox: sceneBbox, pad: 40 }); }
        else if (tile) flyTo({ bbox: tile.bbox, pad: 200 }); else if (c) flyTo(c);
        break;
      case "scan": case "detect": case "segment":
        if (tile) flyTo({ bbox: tile.bbox, pad: 110 }); else if (c) flyTo(c); break;
      case "validate":
        if (tile) flyTo({ bbox: tile.bbox, pad: 150 }); else if (c) flyTo(c); break;
      case "hindcast": case "origin": {
        const b = driftBbox(c, est0, layers.origin_cloud, layers.forecast);
        if (b) flyTo({ bbox: b, pad: 90 }); else if (c) flyTo(c);
        break;
      }
      case "forecast": {
        const b = unionBbox([c, ...(layers.forecast?.features || []).map(featureBbox), featureBbox(layers.slick?.features?.[0])], 0.08);
        if (b) flyTo({ bbox: b, pad: 80 }); else if (c) flyTo(c);
        break;
      }
      case "ranking": case "attribution": case "evidence": {
        const cb = candidateBbox(layers.vessels, layers.suspects, est0) || bboxOfTracks(layers.vessels);
        if (cb) flyTo({ bbox: cb, pad: 120 }); else if (sceneBbox) flyTo({ bbox: sceneBbox, pad: 70 });
        break;
      }
      default: {
        const tb = bboxOfTracks(layers.vessels);
        if (tb) flyTo({ bbox: tb, pad: 70 }); else if (sceneBbox) flyTo({ bbox: sceneBbox, pad: 70 });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cineCamKey]);

  /* The globe beat: out to orbit, then down to the area of interest. One
   * surface, so this is two flights of the same camera rather than a
   * crossfade between two engines -- the Earth never jumps. */
  useEffect(() => {
    if (beatId !== "globe") return undefined;
    if (!sceneBbox) { setSurface("map"); setFlightDone(true); return undefined; }
    const lon = (sceneBbox[0] + sceneBbox[2]) / 2;
    const lat = (sceneBbox[1] + sceneBbox[3]) / 2;
    setSurface("globe");
    setFlightDone(false);
    setView((v) => ({ ...stripEcho(v), longitude: lon, latitude: lat, zoom: 2.4, bearing: 0, pitch: 0,
                      transitionDuration: 900 }));
    const descend = setTimeout(() => {
      setSurface("map");
      setView((v) => ({ ...stripEcho(v), longitude: lon, latitude: lat, zoom: 5.5, transitionDuration: 1700 }));
    }, 1100);
    const landed = setTimeout(() => setFlightDone(true), 2900);
    return () => { clearTimeout(descend); clearTimeout(landed); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [beatId]);

  /* Arriving from the SAR Image Database (FIND VESSELS) or any link that asks
   * for it: play the run that was handed over, once its scene is known. The
   * flag is consumed so a reload does not replay it. */
  const wantPresent = params.get("present") === "1";
  useEffect(() => {
    if (!wantPresent || !runId || !hasScene || cine.active) return;
    setParams((prev) => { const n = new URLSearchParams(prev); n.delete("present"); return n; }, { replace: true });
    if (runState === "running") setRunning(true);
    play("globe");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantPresent, runId, hasScene, runState]);

  /* ------------------------------------------------- shell + palette ---- */
  useRunInContext(runId);
  useRegisterCommands(() => {
    const cmds = [
      { id: "measure", group: "Map", keys: ["M"], scope: "workspace",
        label: measuring ? "Measure tool — turn off" : "Measure tool — turn on", run: () => setMeasuring((v) => !v) },
      { id: "measure-clear", group: "Map", label: "Clear the measurement", run: () => setMeasurePoints([]) },
    ];
    for (const s of STAGES) cmds.push({ id: `stage:${s.id}`, group: "Stage", label: `Go to ${s.label}`, run: () => go(s.id) });
    for (const [key, on] of Object.entries(show)) {
      cmds.push({ id: `layer:${key}`, group: "Layer", label: `${on ? "Hide" : "Show"} ${key}`, run: () => onShow(key, !on) });
    }
    if (sceneT0) cmds.push({ id: "t:acquisition", group: "Time", label: "Jump to scene acquisition time", hint: fmtUtc(sceneT0), run: () => setTimeMs(sceneT0) });
    return cmds;
  }, [measuring, show, sceneT0, go, onShow]);

  /* ------------------------------------------------------------- actions -- */
  /** Play the presentation over the loaded run, from `from` (a beat id). */
  const play = useCallback((from = "globe") => {
    setPlaying(false); setLayersOpen(false); setMeasuring(false);
    cine.start(from);
  }, [cine, setPlaying]);
  /* Two ways to bring a run onto the workspace, and they are different acts:
   *
   *   `present: true`   a NEW investigation starts (Proceed to detection) or
   *                     the pipeline is executed live (Analyse scene): the
   *                     whole analysis plays as one presentation while the
   *                     run lands underneath, holding on its real status.
   *   `present: false`  Replay analysis: the instrument renders the run's
   *                     files at once (the 5 s render budget), and the
   *                     presentation is a click away on the timeline.
   */
  async function run({ present } = {}) {
    if (!invId) return;
    fetched.current = new Set();
    setLayers({}); setLayerErr({}); loadedFor.current = null; touched.current = false;
    try {
      if (replayMode) {
        const r = await api.invReplay(invId);
        setReplayRunId(r.run_id);
        if (present) play("globe");
      } else {
        setReplayRunId(null);
        setRunning(true);
        setBusy("run");
        const started = await api.startRun(invId, { engine: "auto" });
        setJob({ id: started.job_id, run_id: started.run_id });
        if (present !== false) play("globe");
      }
    } catch (e) {
      setRunning(false);
      toast(e.message || "run failed");
    } finally { setBusy(null); }
  }
  /* The job behind the run on screen, whether this tab started it or not. */
  const liveJob = job?.id ? job : (runState === "running" && runId ? { id: `job-${runId}`, run_id: runId } : null);

  async function cancelRun() {
    if (!liveJob?.id) return;
    setCancelling(true);
    try { const r = await api.cancelJob(liveJob.id); toast(r.detail || "cancelling at the next stage boundary", "warn"); }
    catch (e) { toast(e.message || "could not cancel"); setCancelling(false); }
  }
  async function rerunLast() {
    const rid = job?.run_id || status?.run_id;
    if (!rid) return;
    try {
      const started = await api.rerun(rid);
      setJob({ id: started.job_id, run_id: started.run_id }); setRunning(true); touched.current = false;
      toast(`re-running as ${started.run_id}`, "warn");
    } catch (e) { toast(e.message || "could not re-run"); }
  }
  async function loadScene() {
    const s = selectedScene;
    if (!s) return;
    setBusy("load");
    try {
      const body = { name: `${s.label || s.product_id || s.scene_id} · ${fmt.utc(s.acquired_utc).slice(0, 10)}` };
      if (s.kind === "local") body.scene_meta_path = s.scene_meta_path;
      else body.scene_product_id = s.product_id;
      if (spatial.bbox) body.aoi = bboxPolygon(spatial.bbox);
      if (acq.start && acq.end) { body.window_start_utc = new Date(acq.start).toISOString(); body.window_end_utc = new Date(`${acq.end}T23:59:59Z`).toISOString(); }
      const created = await api.createInvestigation(body);
      await reloadInvs();
      setParams({ inv: created.id });
      touched.current = true;
      setStageId("scene");
      toast(`investigation ${created.id} created`, "ok");
    } catch (e) { toast(e.message || "could not create the investigation"); }
    finally { setBusy(null); }
  }
  async function searchScenes() {
    if (!spatial.bbox) return;
    setSearching(true); setSearchError(null); setResults(null);
    try {
      const body = await api.searchScenes({ bbox: spatial.bbox.join(","), start: new Date(acq.start).toISOString(),
        end: new Date(`${acq.end}T23:59:59Z`).toISOString(), source: acq.mission, product_type: acq.product, top: 20 });
      setResults(body);
    } catch (e) { setSearchError(e.message || "search failed"); }
    finally { setSearching(false); }
  }
  function clearSearch() { setResults(null); setSearchError(null); setSelectedScene(null); setSpatial({ mode: "draw", bbox: null, zoneId: "", aoiId: "" }); setDrawPoints([]); }
  function selectScene(s) {
    setSelectedScene(s);
    if (s.bbox) {
      if (surface === "globe") setSurface("map");
      else flyTo(s.bbox);
    }
  }
  async function onUploadGeojson(file) {
    try {
      const g = JSON.parse(await file.text());
      const b = polygonBbox(g);
      if (!b) throw new Error("no polygon found in that file");
      setSpatial((s) => ({ ...s, bbox: b, polygon: g })); flyTo(b);
    } catch (e) { toast(`GeoJSON: ${e.message}`); }
  }
  useEffect(() => {
    if (spatial.mode === "zone" && spatial.zoneId) {
      api.getZone(spatial.zoneId).then((z) => { if (z.bbox) { setSpatial((s) => ({ ...s, bbox: z.bbox })); flyTo(z.bbox); } }).catch(() => {});
    }
    if (spatial.mode === "aoi" && spatial.aoiId) {
      const a = (aoisQ || []).find((x) => x.id === spatial.aoiId);
      if (a?.bbox) { setSpatial((s) => ({ ...s, bbox: a.bbox })); flyTo(a.bbox); }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spatial.mode, spatial.zoneId, spatial.aoiId]);
  const drawActive = stageId === "acquisition" && spatial.mode === "draw" && surface === "map";
  const addDrawPoint = (pt) => {
    setDrawPoints((pts) => {
      const next = [...pts, pt];
      if (next.length === 2) {
        const [a, b] = next;
        setSpatial((s) => ({ ...s, bbox: [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])] }));
        return [];
      }
      return next;
    });
  };
  async function onCreateIncident(force) {
    if (!runId) return;
    setBusy("incident"); setIncidentError(null);
    try { await api.createAutoIncident(runId, force); await reloadRun(); await reloadIncident(); toast("incident created", "ok"); }
    catch (e) { setIncidentError(typeof e.message === "string" ? e.message : "the gate refused"); }
    finally { setBusy(null); }
  }
  async function onDecision(verdict, note) {
    if (!runId) return;
    setBusy("decision");
    try { await api.recordDecision(runId, { verdict, note: note || null, mmsi: selectedMmsi || null, actor: user?.email || "analyst" }); await reloadDecisions(); }
    catch (e) { toast(e.message || "could not record"); }
    finally { setBusy(null); }
  }
  async function onComposeReport() {
    if (!runId) return;
    setBusy("report"); setReportError(null);
    try { await api.composeReport({ run_id: runId }); await reloadReports(); }
    catch (e) { setReportError(e.message || "could not compose"); }
    finally { setBusy(null); }
  }
  async function onSubmitReport(id) { setBusy("report"); try { await api.submitReport(id); await reloadReports(); } catch (e) { setReportError(e.message); } finally { setBusy(null); } }
  async function onPublishReport(id) { setBusy("report"); try { await api.publishReport(id); await reloadReports(); } catch (e) { setReportError(e.message); } finally { setBusy(null); } }
  function searchArea(text) {
    const c = parseCoordinate(text);
    if (c) { flyTo([c.lon, c.lat]); return; }
    const z = zones.find((x) => x.name.toLowerCase().includes(text.toLowerCase()));
    if (z) { api.getZone(z.id).then((full) => full.bbox && flyTo(full.bbox)).catch(() => {}); return; }
    toast(`Could not read "${text}" as a coordinate or a zone name`, "warn");
  }

  /* --------------------------------------------------------- rendering -- */
  const mode = invId || runId ? "analysis" : "acquisition";
  const footprints = useMemo(() => {
    const out = [];
    for (const h of results?.scenes || []) out.push({ id: h.product_id, bbox: h.bbox, label: h.product_id, acquired_utc: h.acquired_utc, status: h.cached_path ? "cached" : "not downloaded", selected: selectedScene?.key === `hit:${h.product_id}` });
    for (const c of catalogue) if (c.available) out.push({ id: c.id, bbox: c.bbox, label: c.label, acquired_utc: c.acquired_utc, status: "cached", selected: selectedScene?.key === `local:${c.id}` });
    return out;
  }, [results, catalogue, selectedScene]);

  const overall = !runId ? "NEW"
    : (status?.state === "cancelled" || runRow?.status === "cancelled" || stageRows.some((s) => s.status === "cancelled")) ? "CANCELLED"
      : runState === "running" ? "RUNNING" : stageRows.some((s) => s.status === "failed") ? "FAILED-PARTIAL" : stageRows.length ? "COMPLETE" : "NEW";

  const headline = (() => {
    if (cineFrame) return { title: cine.beat.title, sub: cine.hold && cine.hold.need !== "flight" ? `Waiting for ${cine.hold.need} — ${cine.hold.why}` : cineFrame.hud.line, live: true };
    if (stageId === "acquisition") return { title: stage.title, sub: stage.sub };
    if (stageId === "ais") return { title: "Validated slick", ok: "Proceeding with AIS trajectory correlation" };
    if (stageId === "attribution") return { title: "Vessel attribution", ok: judged.attribution.state === "done" ? "Analysis complete" : null };
    if (stageId === "validation") return { title: stage.title, ok: judged.validation.state === "done" ? "Segmentation complete" : null };
    if (stageId === "geometry") return { title: stage.title, ok: judged.geometry.state === "done" ? "Segmentation validated" : null };
    return { title: stage.title, sub: stageId === "scene" ? stage.sub : null };
  })();

  const aisProv = runRow?.manifest?.ais?.data_source || layers.suspects?.source;
  const liveAis = aisBadgeFor(aisStatus?.stream);
  const scenesOnRail = null;

  /* Is the slick somewhere a slick can be? A geometry that fails is not
   * drawn at all; one that meets the coastline is drawn where the run put it
   * and flagged. The workspace never relocates anything. */
  const land = useLandGeometry();
  const geo = useMemo(() => validateSlick(layers.slick, layers.scene_meta?.bbox), [layers.slick, layers.scene_meta]);
  const onLand = useMemo(() => (geo.ok ? landShare(land, layers.slick) : null), [geo.ok, land, layers.slick]);
  const slickForMap = geo.ok ? layers.slick : null;

  /* callout anchors */
  const project = (lonlat) => { try { return viewport && lonlat ? viewport.project(lonlat) : null; } catch { return null; } };
  /* The map key can be minimised to its title bar; the choice is remembered
   * per browser (a viewer convenience, so localStorage, guarded). */
  const [legendOpen, setLegendOpen] = useState(() => { try { return window.localStorage.getItem("ot.ws.legend") !== "min"; } catch { return true; } });
  const toggleLegend = () => setLegendOpen((o) => { try { window.localStorage.setItem("ot.ws.legend", o ? "min" : "open"); } catch { /* private window */ } return !o; });
  const centroidPx = project(slickP?.centroid);
  const originPx = project(est0?.center);
  const tilePx = selectedTile ? { nw: project([selectedTile.bbox[0], selectedTile.bbox[3]]), se: project([selectedTile.bbox[2], selectedTile.bbox[1]]) } : null;
  const scenePx = sceneBbox ? { nw: project([sceneBbox[0], sceneBbox[3]]), se: project([sceneBbox[2], sceneBbox[1]]) } : null;
  /* During the presentation the callout and the tile frame appear when their
   * beat reaches them (frames 09-11); otherwise they belong to the stage. */
  const calloutBeat = !cineFrame || (beatId === "detect" && cine.t >= 0.5) || (beatId === "segment" && cine.t >= 0.9) || beatId === "validate";
  const tileBeat = !cineFrame || (beatId === "tiling" && cine.t >= 0.55) || ["scan", "detect", "segment", "validate"].includes(beatId);
  const showCallout = ["detection", "validation"].includes(stageId) && slickP && centroidPx && judged.detection.state === "done" && calloutBeat;
  const showTileFrame = ["tiling", "detection", "validation"].includes(stageId) && tilePx?.nw && tilePx?.se && tileBeat;
  const scanPx = tilePx?.nw && tilePx?.se ? tilePx : scenePx?.nw && scenePx?.se ? scenePx : null;
  const timeShown = cineFrame?.timeMs ?? timeMs;
  const canPlay = Boolean(runId) && Boolean(layers.scene_meta || hasScene);

  const cineCtx = cine.active ? { active: true, beat: cine.beat, t: cine.t, reveal: cineFrame.reveal, hold: cine.hold, timeMs: cineFrame.timeMs } : { active: false };

  /* The case in one line: only facts the run has actually produced. */
  const caseFacts = useMemo(() => {
    const out = [];
    const sp = slickForMap?.features?.[0]?.properties;
    const areas = (slickForMap?.features || []).map((f) => f.properties?.area_km2).filter((v) => v != null);
    if (areas.length) {
      const total = areas.reduce((a, b) => a + b, 0);
      out.push({ id: "area", k: "Spill area", v: `${total.toFixed(1)} km²`,
        title: areas.length > 1 ? `${areas.length} segmented regions, largest ${Math.max(...areas).toFixed(1)} km²` : undefined });
    }
    if (sp?.age_hours_estimate != null) {
      out.push({ id: "age", k: "Age", v: `≈ ${Math.round(sp.age_hours_estimate)} h`,
        title: `Confidence: ${(sp.age_confidence_label || "low").toUpperCase()} — ${sp.age_method || "damping heuristic"}` });
    }
    const est = originEstimate(layers.origin_cloud);
    if (est?.center) {
      out.push({ id: "origin", k: "Origin", mono: true,
        v: `${est.center[1].toFixed(3)}°, ${est.center[0].toFixed(3)}°${est.radiusKm ? ` ± ${est.radiusKm.toFixed(2)} km` : ""}`,
        title: est.radiusBasis || undefined });
    } else if (judged.drift?.state === "done") {
      out.push({ id: "origin", k: "Origin", v: "not localised" });
    }
    const top = (layers.suspects?.suspects || [])[0];
    if (top) {
      out.push({ id: "candidate", k: "Top candidate", mono: true,
        v: `${top.name || top.mmsi}${top.score != null ? ` · ${Number(top.score).toFixed(2)}` : ""}`,
        title: "Highest-ranked candidate, not a confirmed culprit" });
    }
    return out;
  }, [slickForMap, layers.origin_cloud, layers.suspects, judged.drift?.state]);

  const ctx = {
    stage: stageId, panel: stage.panel, judged, layers, runRow, status, runId, inv, tilesInfo, tileGrid: grid, selectedTile,
    funnel, forcing, models, autoPreview, incident, decisions, reports, verify, aisStatus, selectedScene, sceneT0,
    selectedMmsi, onSelectMmsi: setSelectedMmsi, dossier, errors: layerErr, loaded: !runId, show, onShow,
    canRun, canPublish, busy, zones, users, incidentError, reportError, cine: cineCtx,
    sub: subs[stageId] || null, forcingState, geo, onLand, runState,
    onCreateIncident, onDecision, onComposeReport, onSubmitReport, onPublishReport,
    actions: { loadScene, run: () => run({ present: true }), go, flyTo, play,
      sub: (st, id) => { cine.stop(); if (st !== stageId) gotoStage(st); setSubs((m) => ({ ...m, [st]: id })); } },
  };

  return (
    <div className={`ws ${fullscreen ? "ws-full" : ""} ${cine.active ? "ws-live" : ""} ${leftOff ? "l-off" : ""} ${rightOff ? "r-off" : ""}`} data-testid="workspace" data-stage={stageId}
      data-beat={beatId || (cine.finished ? "finished" : "")}>
      {/* --------------------------------------------------- left panel --- */}
      <aside className={`ws-left ${leftOff ? "off" : ""}`} data-testid="ws-left" data-collapsed={leftOff ? "true" : "false"}>
        <button className="ws-collapse ws-collapse-l" onClick={() => setLeftOff((v) => !v)} data-testid="ws-left-collapse"
          aria-expanded={!leftOff} title={leftOff ? "Show the scene controls" : "Hide the scene controls: the map takes the space"}>
          {leftOff ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
        </button>
        <div className="ws-side-body" hidden={leftOff}>
        {mode === "acquisition" ? (
          <AcquisitionPanel q={q} onQ={setQ} params={acq} onParams={setAcq}
            spatial={spatial} onSpatial={setSpatial} zones={zones} aois={aoisQ || []}
            onSearch={searchScenes} searching={searching} onClear={clearSearch} results={results}
            catalogue={catalogue} selected={selectedScene} onSelect={selectScene} searchError={searchError}
            onUploadGeojson={onUploadGeojson} drawActive={drawActive} canCreate={canRun} />
        ) : (
          <AnalysisPanel sceneMeta={layers.scene_meta} inv={inv} runId={runId} show={show} onShow={onShow}
            aisSource={aisProv} onFlyTo={flyTo} onSearchArea={searchArea} running={running || runState === "running"} job={liveJob}
            cancelling={cancelling} replayMode={replayMode} onReplayMode={setReplayMode} onRun={() => run()}
            onCancel={cancelRun} onRerun={rerunLast} canRun={canRun} status={status} stageId={stageId}
            onClearAll={() => { setShowOverride({}); setSelectedMmsi(null); setMeasurePoints([]); }}
            invs={invs} invId={invId} onPickInv={(id) => { touched.current = false; setReplayRunId(null); setJob(null); setParams({ inv: id }); }}
            onNew={() => { setParams({ new: "1" }); setSelectedScene(null); setResults(null); }} liveAis={liveAis} />
        )}
        </div>
      </aside>

      {/* ------------------------------------------------------ centre ---- */}
      <section className="ws-centre">
        <header className="ws-headline" data-testid="ws-header">
          <div className="ws-headline-main">
            <h1 className="ws-title" data-testid="ws-title">{headline.title}</h1>
            {headline.live && <span className="ws-ok running" data-testid="ws-live"><i className="ws-livedot" /> {cine.hold && cine.hold.need !== "flight" ? "Waiting on the pipeline" : "Analysing"}</span>}
            {headline.ok && <span className="ws-ok"><CheckCircle2 size={16} /> {headline.ok}</span>}
            {runState === "running" && (
              <span className="ws-ok running" data-testid="run-progress">
                <Loader2 size={14} className="ws-spin" />
                {progress.current ? `${progress.current} · ` : ""}stage {Math.min(progress.done + 1, progress.total)} of {progress.total}
              </span>
            )}
            {statusMisses >= 2 && (
              <span className="ws-ok ws-lost" data-testid="status-lost" role="status"
                title="The status request has failed repeatedly. What is on screen is the last answer the server gave.">
                <AlertTriangle size={13} /> Lost contact with the server — retrying. Figures shown are from the last answer.
              </span>
            )}
            {runState === "running" && (
              <span className="ws-leave" data-testid="run-leave-note"
                title="The run executes on the server. It keeps going when you close this tab.">
                You can leave this page — OceanTrace will notify you when it finishes.
              </span>
            )}
            {runId && <span className={`badge ${overall === "COMPLETE" ? "badge-ok" : overall === "RUNNING" ? "badge-warn" : overall === "FAILED-PARTIAL" ? "badge-danger" : overall === "CANCELLED" ? "badge-warn" : "badge-neutral"}`} data-testid="overall-status">{overall}</span>}
            {isDemoRun(runId) && <span className="badge badge-accent" data-testid="demo-badge" title="The canonical acceptance run, chosen for the walkthrough. Every figure is the pipeline's own.">DEMO CASE</span>}
            {runRow && !runRow.investigation_id && <span className="badge badge-neutral" data-testid="unfiled-run" title="Produced outside the API and reconciled from its sealed manifest">UNFILED RUN</span>}
            {layers.scene_meta?.source && <span className={`badge badge-${sourceBadge(layers.scene_meta.source).tone}`} data-testid="scene-source-badge">scene {sourceBadge(layers.scene_meta.source).label}</span>}
          </div>
          {headline.sub && <div className="ws-sub">{headline.sub}</div>}
          {!headline.sub && runId && <div className="ws-sub mono" data-testid="scene-line">{layers.scene_meta?.scene_id ?? inv?.scene_id ?? runId}{layers.scene_meta?.acquired_utc && ` · ${fmtUtc(Date.parse(layers.scene_meta.acquired_utc))}`}</div>}
          {runId && caseFacts.length > 0 && (
            /* The case in one line, from whichever artefacts have landed. A
               fact the run has not produced is absent rather than dashed in:
               the analyst reads this strip to know where the case stands. */
            <dl className="ws-facts" data-testid="case-facts">
              {caseFacts.map((f) => (
                <div key={f.k} className="ws-fact" title={f.title} data-testid={`fact-${f.id}`}>
                  <dt>{f.k}</dt><dd className={f.mono ? "mono" : ""}>{f.v}</dd>
                </div>
              ))}
              <button type="button" className="ws-fact-more" data-testid="open-brief"
                onClick={() => { setRightOff(false); setSubs((m) => ({ ...m, [stageId]: "brief" })); }}>
                Case brief
              </button>
            </dl>
          )}
        </header>

        <div className="ws-map" data-testid="ws-map">
          {/* One map. "Globe" and "Map" are the same surface at different
              zooms -- MapLibre morphs the globe into a flat chart as the view
              closes in -- so there is no second canvas to cross-fade to and
              no camera to keep in sync. */}
          <div className="ws-surface" data-testid="globe-surface" data-shown="true">
            <WorkspaceMap
              view={view} onViewChange={(e) => setView(e.viewState)}
              show={show} layers={{ sceneMeta: layers.scene_meta, slick: slickForMap, origin: layers.origin_cloud,
                forecast: layers.forecast, vessels: cineFrame ? layers.vessels : vesselsShown, suspects: layers.suspects, detect: layers.detect }}
              timeMs={timeShown} sceneT0={sceneT0} runId={runId} selectedMmsi={selectedMmsi}
              onSelect={(mmsi) => { setSelectedMmsi(mmsi); if (mmsi != null && !["ais", "attribution"].includes(stageId) && layers.suspects) go("attribution"); }}
              onHover={setHover} maxStep={maxStep} forcing={forcing?.run_id === runId ? forcing : null}
              measure={{ active: measuring, points: measurePoints, onAddPoint: (p) => setMeasurePoints((pts) => [...pts, p]) }}
              basemap={basemap} onCursor={setCursor} onViewport={setViewport}
              tiles={{ grid, selected: selectedTile }} aoi={spatial.bbox ? { bbox: spatial.bbox } : null}
              footprints={mode === "acquisition" ? footprints : null}
              draw={{ active: drawActive, points: drawPoints, cursor: drawCursor, onAddPoint: addDrawPoint, onCursor: setDrawCursor }}
              candidateLabels={cineFrame ? BEAT_INDEX[beatId] >= BEAT_INDEX.ais : ["ais", "attribution", "evidence"].includes(stageId)}
              dimOthers={cineFrame ? cineFrame.reveal.dimOthers : stageId === "attribution"}
              reveal={cineFrame?.reveal || null}
              onClickMap={(i) => {
                if (spatial.mode === "draw" && stageId === "acquisition" && i?.coordinate) { addDrawPoint(i.coordinate); return; }
                if (i?.footprint) { const f = footprints.find((x) => x.id === i.footprint.id); const src = (results?.scenes || []).find((h) => h.product_id === f?.id); const loc = catalogue.find((c) => c.id === f?.id); if (src) selectScene({ key: `hit:${src.product_id}`, kind: "hit", ...src }); else if (loc) selectScene({ key: `local:${loc.id}`, kind: "local", ...loc }); }
              }}
            />
          </div>

          <MapChrome view={view} onZoom={(d) => setView((v) => ({ ...stripEcho(v), zoom: Math.max(0.5, Math.min(18, (v.zoom || 5) + d)), transitionDuration: 250 }))}
            onReset={() => { setView((v) => ({ ...stripEcho(v), bearing: 0, pitch: 0, transitionDuration: 400 })); flyTo("scene"); }}
            fullscreen={fullscreen} onFullscreen={() => setFullscreen((f) => !f)}
            basemap={basemap} onBasemap={(b) => { setBasemapOverride(b); if (b === "environmental") { onShow("wind", true); onShow("currents", true); } }}
            surface={surface} onSurface={(s) => {
              setSurface(s);
              // The same map, pulled out until MapLibre draws it as a globe,
              // or brought back down to the scene.
              const b = layers.scene_meta?.bbox || selectedScene?.bbox || spatial.bbox;
              const c = b ? [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] : null;
              if (s === "globe") setView((v) => ({ ...stripEcho(v), ...(c ? { longitude: c[0], latitude: c[1] } : {}), zoom: 2.6, ...FLY }));
              else flyTo("scene");
            }}
            cursor={cursor} layersOpen={layersOpen} onLayers={() => setLayersOpen((o) => !o)} showBasemap />

          {/* the presentation's narration: what the system is doing, on the map */}
          {cineFrame && (
            <div className={`ws-cine-hud ${cine.hold && cine.hold.need !== "flight" ? "hold" : ""}`} data-testid="cine-hud">
              <div className="ws-cine-hud-top">
                <span className="ws-cine-dot" />
                <span className="ws-cine-title">{cine.beat.title}</span>
                {cineFrame.hud.clock && <span className="ws-cine-clock mono" data-testid="cine-clock">{cineFrame.hud.clock}</span>}
              </div>
              <div className="ws-cine-line" data-testid="cine-line">
                {cine.hold && cine.hold.need !== "flight" ? <><Loader2 size={12} className="ws-spin" /> Waiting for {cine.hold.need} — {cine.hold.why}</> : cineFrame.hud.line}
              </div>
              <div className="ws-cine-bar"><span style={{ width: `${Math.round(cine.t * 100)}%` }} /></div>
            </div>
          )}

          {/* frame 08 → 09: the scanning sweep over the tile (or the scene) */}
          {beatId === "scan" && scanPx && (
            <div className="ws-scan" data-testid="scan-overlay"
              style={{ left: scanPx.nw[0], top: scanPx.nw[1], width: Math.max(40, scanPx.se[0] - scanPx.nw[0]), height: Math.max(40, scanPx.se[1] - scanPx.nw[1]) }}>
              <div className="ws-scan-band" style={{ top: `${4 + cine.t * 92}%` }} />
              {/* The sweep is the presentation's own clock, so it says what it
                  is. It used to render that clock as "SCANNING · 73%", which
                  read as detector progress the backend never reports. */}
              <div className="ws-scan-label mono">SCANNING SAR SCENE</div>
            </div>
          )}

          {/* frame 08/09: the selected tile carries its label and a state bar */}
          {showTileFrame && (
            <>
              <div className="ws-tile-tag mono" style={{ left: tilePx.nw[0] + 6, top: tilePx.nw[1] + 6 }}>{fmtTile(selectedTile.index).replace("TILE", "Tile")}</div>
              <div className="ws-tile-bar" style={{ left: tilePx.nw[0], top: tilePx.se[1], width: Math.max(160, tilePx.se[0] - tilePx.nw[0]) }} data-testid="tile-bar">
                <span>{beatId === "scan" ? "SCANNING…" : beatId === "detect" ? "CANDIDATE FOUND" : beatId === "segment" && cine.t < 0.95 ? "SEGMENTING…"
                  : judged.detection.state === "done" ? (stageId === "validation" ? "READY FOR VALIDATION" : "SEGMENTATION COMPLETE") : judged.detection.state === "running" ? "SEGMENTING…" : "READY FOR SEGMENTATION"}</span>
                <Info size={13} />
              </div>
            </>
          )}

          {/* frame 10: the candidate callout, anchored to the analysed slick */}
          {showCallout && (
            <>
              <svg className="ws-leader" width="100%" height="100%">
                <line x1={centroidPx[0]} y1={centroidPx[1]} x2={Math.min(centroidPx[0] + 120, (viewport?.width || 0) - 250)} y2={centroidPx[1] - 40} />
                <circle cx={centroidPx[0]} cy={centroidPx[1]} r={7} />
              </svg>
              <div className="ws-callout" style={{ left: Math.min(centroidPx[0] + 120, (viewport?.width || 0) - 250), top: Math.max(8, centroidPx[1] - 40) }} data-testid="slick-callout">
                <div className="ws-callout-title">{stageId === "validation" ? "Validation" : "Candidate slick"}</div>
                {stageId === "validation" ? (
                  <>
                    <div>Backscatter: <b>{slickP.damping_ratio != null ? `${Number(slickP.damping_ratio).toFixed(1)} dB damping` : "—"}</b></div>
                    <div>Shape: <b>{slickP.major_axis_m && slickP.minor_axis_m ? (slickP.major_axis_m / slickP.minor_axis_m >= 3 ? "Elongated" : slickP.major_axis_m / slickP.minor_axis_m >= 1.5 ? "Oblong" : "Compact") : "—"}</b></div>
                    <div>Age confidence: <b>{slickP.age_confidence_label ? String(slickP.age_confidence_label).toUpperCase() : "not recorded"}</b></div>
                  </>
                ) : (
                  <>
                    <div>Area: <b>{Number(slickP.area_km2).toFixed(2)} km²</b></div>
                    <div>Confidence: <b>{(Number(slickP.confidence) * 100).toFixed(0)}%</b></div>
                    <div className="ws-callout-sep" />
                    <div>Tile: <b>{selectedTile ? String(selectedTile.index).padStart(2, "0") : "—"}</b></div>
                    <div>Lat: <b>{Math.abs(slickP.centroid[1]).toFixed(4)}° {slickP.centroid[1] >= 0 ? "N" : "S"}</b></div>
                    <div>Lon: <b>{Math.abs(slickP.centroid[0]).toFixed(4)}° {slickP.centroid[0] >= 0 ? "E" : "W"}</b></div>
                  </>
                )}
              </div>
            </>
          )}

          {/* The stage's headline, big, on the map (from Incident Replay), and a
              soft ping on the estimated origin wherever the origin is the subject.
              The presentation has its own HUD, so these stand down while it runs. */}
          {!cineFrame && originPx && ["drift", "attribution"].includes(stageId) && (
            <span className="ws-ping" style={{ left: originPx[0], top: originPx[1] }} aria-hidden="true" />
          )}
          <MapHud hidden={Boolean(cineFrame)} stageId={stageId} sub={subs[stageId]} slick={layers.slick} detect={layers.detect}
            est={est0} originMeta={layers.origin_cloud?.metadata} forecast={layers.forecast} counts={counts}
            suspects={layers.suspects} selectedMmsi={selectedMmsi} onSelect={setSelectedMmsi} />

          {/* frame 12: scene facts; frame 15: legend */}
          {["ais", "drift"].includes(stageId) && layers.scene_meta && (
            <div className="ws-infobox mono" data-testid="scene-infobox">
              <div>Scene: <b>{sceneFact(layers.scene_meta, "mission")} (SAR)</b></div>
              <div>Acquisition: <b>{fmtUtc(sceneT0)}</b></div>
              <div>Polarization: <b>{layers.scene_meta.polarisation || "—"}</b></div>
              <div>Product: <b>{sceneFact(layers.scene_meta, "product")}</b></div>
            </div>
          )}
          {["attribution", "evidence", "report"].includes(stageId) && layers.slick && (
            <div className={`ws-maplegend ${legendOpen ? "" : "min"}`} data-testid="map-legend-box" data-open={legendOpen ? "true" : "false"}>
              <button type="button" className="ws-maplegend-title ws-maplegend-toggle" onClick={toggleLegend} data-testid="map-legend-toggle"
                aria-expanded={legendOpen} title={legendOpen ? "Minimise the legend" : "Show the legend"}>
                <span>Legend</span>{legendOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
              </button>
              {legendOpen && <>
              <div><i className="lg-slick" /> Detected oil slick (Sentinel-1)</div>
              <div><i className="lg-origin" /> Estimated origin (uncertainty)</div>
              <div><i className="lg-sel" /> Selected vessel track (AIS)</div>
              <div><i className="lg-ship lg-ship-cand" /> Ranked candidate, at the time shown</div>
              <div><i className="lg-ship" /> Other traffic (when switched on)</div>
              <div><i className="lg-ring" /> A vessel working one area</div>
              <div className="ws-maplegend-sub">Short trails, not whole tracks. Looping; pause below.</div>
              {/synthetic|mock/i.test(String(aisProv || "")) && (
                <div className="ws-maplegend-note" data-testid="legend-ais-simulated" title="The run recorded its AIS as synthetic. Tracks that loop in one place are the generator's fishing pattern.">Simulated AIS: no real archive covers this origin. These are not observed vessels.</div>
              )}
              </>}
            </div>
          )}
          {["ais", "attribution"].includes(stageId) && !layers.suspects?.suspects?.length && judged.attribution.state === "done" && (
            <div className="ws-banner warn" data-testid="no-candidates-banner">No vessel passed the spatial, temporal and trajectory gates for this origin window. Tracks shown are the AIS traffic that was considered.</div>
          )}
          {stageId === "ais" && liveAis && liveAis.tone !== "ok" && (
            <div className="ws-banner" data-testid="live-ais-banner">LIVE AIS — {aisStatus?.stream?.state === "not_configured" ? "not configured" : "no coverage in current area"}; tracks are {sourceBadge(aisProv).label === "—" ? "of unrecorded provenance" : sourceBadge(aisProv).label.toLowerCase() === "real" ? "historical archive" : sourceBadge(aisProv).label.toLowerCase()}.</div>
          )}

          {!geo.ok && (
            <div className="ws-banner danger" data-testid="location-unavailable"><AlertTriangle size={13} /> LOCATION DATA UNAVAILABLE: {geo.reason}. The slick is not drawn rather than placed somewhere it was not measured.</div>
          )}
          {geo.ok && onLand && (onLand.centroidOnLand || onLand.share > 0.5) && show.slick && (
            <div className="ws-banner warn low" data-testid="slick-on-land"><AlertTriangle size={13} /> {Math.round(onLand.share * 100)}% of this slick outline intersects the coastline dataset{onLand.centroidOnLand ? ", including its centroid" : ""}. It is drawn where the run recorded it{layers.scene_meta?.source ? ` (scene ${sourceBadge(layers.scene_meta.source).label})` : ""}; check the georeferencing before relying on it.</div>
          )}

          {layersOpen && (
            <div className="ws-layers panel" data-testid="layers-popover">
              <div className="ws-panel-title">Layers</div>
              <LayerPanel show={show} present={status?.layers_present} stages={status?.stages} onToggle={onShow} />
            </div>
          )}
          {measuring && (
            <div className="ws-measure">
              <MeasureTool points={measurePoints} onUndo={() => setMeasurePoints((pts) => pts.slice(0, -1))} onClear={() => setMeasurePoints([])} />
            </div>
          )}

          <AnimatePresence>
            {hover && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                className="ws-hover panel" data-testid="hover-box">
                <div className="ws-hover-title"><Info size={12} /> {hover.title}</div>
                {hover.rows.map(([k, v]) => (
                  <div key={k} className="ws-hover-row"><span className="muted">{k}</span><span className="mono">{String(v)}</span></div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {stageId === "tiling" && selectedTile && (
          <div className="ws-tilestrip" data-testid="tile-strip">
            <div className="ws-tilestrip-cell"><b>BEFORE</b><span className="mono">{layers.scene_meta?.acquired_utc ? fmtUtc(sceneT0) : "—"}</span><span className="mono">{layers.scene_meta?.polarisation || "—"}</span></div>
            <div className="ws-tilestrip-cell thumb">{runId && <img src={`/api/runs/${runId}/scene_png?size=256`} alt="" onError={(e) => { e.currentTarget.style.display = "none"; }} />}<span className="mono">{fmtTile(selectedTile.index)}</span></div>
            <div className="ws-tilestrip-cell grow"><b>{fmtTile(selectedTile.index)}</b><span className="tone-accent">{judged.detection.state === "done" ? "SEGMENTED" : "READY FOR SEGMENTATION"}</span></div>
            <div className="ws-tilestrip-cell"><b className="tone-accent">COORDS</b><span className="mono">Lat: {Math.abs(slickP.centroid[1]).toFixed(3)}° {slickP.centroid[1] >= 0 ? "N" : "S"}</span><span className="mono">Lon: {Math.abs(slickP.centroid[0]).toFixed(3)}° {slickP.centroid[0] >= 0 ? "E" : "W"}</span></div>
          </div>
        )}

        <StageTimeline stageId={stageId} judged={judged} onStage={go} clocks={clocks} domain={domain} value={timeShown}
          onChange={setTimeMs} playing={playing} onPlaying={setPlaying} speed={speed} onSpeed={setSpeed} sceneT0={sceneT0}
          title={stageId === "acquisition" || stageId === "scene" ? "Scene timeline" : "Scene analysis"} scenes={scenesOnRail}
          cine={cine} canPlay={canPlay}
          onCine={{ start: play, stop: () => cine.stop(), skip: cine.skip, jump: (id) => { setPlaying(false); cine.jump(id); }, setSpeed: cine.setSpeed }} />
      </section>

      {/* ------------------------------------------------- right panel ---- */}
      <aside className={`ws-right ${rightOff ? "off" : ""}`} data-testid="ws-right" data-collapsed={rightOff ? "true" : "false"}>
        <button className="ws-collapse ws-collapse-r" onClick={() => setRightOff((v) => !v)} data-testid="ws-right-collapse"
          aria-expanded={!rightOff} title={rightOff ? "Show the analysis panel" : "Hide the analysis panel: the map takes the space"}>
          {rightOff ? <ChevronLeft size={15} /> : <ChevronRight size={15} />}
        </button>
        {rightOff && <span className="ws-side-label">{stage.label}</span>}
        <div className="ws-side-body" hidden={rightOff}><RightPanel ctx={ctx} /></div>
      </aside>

      <div className="ws-toasts">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div key={t.id} initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className={`panel ws-toast ws-toast-${t.tone}`} data-testid="toast">{t.text}</motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
