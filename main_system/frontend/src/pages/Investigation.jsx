/* The Investigation workspace — the product's main screen AND the end-to-end
 * test of every module. Runs the whole pipeline live (per-stage stepper,
 * layers render the moment their stage completes) or replays a finished run
 * from disk in under five seconds with zero network. The page never goes
 * blank because one stage failed: whatever landed, renders.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity, Crosshair, Download, Droplets, FileText, Info,
  Layers as LayersIcon, Play, Plus, RotateCcw, Ruler, Square, Zap,
} from "lucide-react";

import WorkspaceMap from "../components/workspace/WorkspaceMap";
import StageStepper from "../components/workspace/StageStepper";
import LayerPanel from "../components/workspace/LayerPanel";
import SpillPanel from "../components/workspace/SpillPanel";
import SuspectsPanel from "../components/workspace/SuspectsPanel";
import TimeSlider from "../components/workspace/TimeSlider";
import MeasureTool from "../components/workspace/MeasureTool";
import { sourceBadge } from "../components/workspace/palette";
import NewInvestigation from "../components/NewInvestigation";
import { Spinner, Empty } from "../components/ui";
import { api, useApi } from "../lib/api";
import { useRunEvents } from "../lib/useRunEvents";
import { useRegisterCommands, useRunInContext } from "../lib/shell";
import { guessPlace, fmtUtc } from "../lib/replay";

const LAYER_STAGE = {
  scene_meta: "detect", detect: "detect", slick: "characterise",
  origin_cloud: "drift_hindcast", forecast: "drift_forecast",
  suspects: "attribution", vessels: "attribution",
};

export default function Investigation() {
  const [params, setParams] = useSearchParams();

  /* ------------------------------------------------------ investigation -- */
  const { data: invs, reload: reloadInvs } = useApi(() => api.listInvestigations(), []);
  const invId = params.get("inv") || invs?.[0]?.id || null;
  const { data: inv } = useApi(
    () => (invId ? api.getInvestigation(invId) : Promise.resolve(null)), [invId]);

  const [running, setRunning] = useState(false);
  // The job behind the current run: what makes it cancellable. Null when the
  // page is showing a finished run rather than driving a live one.
  const [job, setJob] = useState(null);
  const [cancelling, setCancelling] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [replayRunId, setReplayRunId] = useState(null);
  const [replayMode, setReplayMode] = useState(true);
  const [status, setStatus] = useState(null);
  const [layers, setLayers] = useState({});
  const [layerErr, setLayerErr] = useState({});
  const [toasts, setToasts] = useState([]);
  const [selectedMmsi, setSelectedMmsi] = useState(null);
  const [hover, setHover] = useState(null);
  const [tab, setTab] = useState("spill");
  const [timeMs, setTimeMs] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [show, setShow] = useState({
    sar: true, slick: true, geometry: true, forecast: true,
    hindcast: true, origin: true, vessels: true, lookalikes: true,
  });
  const [view, setView] = useState({
    longitude: 80.32, latitude: 13.05, zoom: 9.6, pitch: 0, bearing: 0,
  });
  // MAP-mode measure. Points are [lon, lat] straight off the map click, so
  // the numbers are measured on the globe rather than on the projection.
  const [measuring, setMeasuring] = useState(false);
  const [measurePoints, setMeasurePoints] = useState([]);

  /* Precedence matters and is not obvious. A `?run=` in the URL used to lose
   * to `status.run_id`, which is the *selected investigation's* latest run --
   * so a deep link to a specific run silently showed a different one. Every
   * run row in the command palette is such a link, and a link that opens the
   * wrong run is worse than a link that fails. The two ids the page produces
   * itself (a replay, and a run it just started) still win, or starting a run
   * from a deep-linked page would pin the old one forever. */
  const runId = replayRunId || job?.run_id || params.get("run")
    || status?.run_id || inv?.latest_run_id || null;
  const loadedFor = useRef(null);
  const fetched = useRef(new Set());

  const toast = useCallback((text, tone = "danger") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 8000);
  }, []);

  /* ------------------------------------------------ incremental loading -- */
  const fetchLayer = useCallback(async (name, rid) => {
    const target = rid || runId;
    try {
      // A replayed run may belong to a sibling investigation of the same
      // scene, so layers are fetched run-scoped whenever a run id is known.
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
    }
  }, [invId, runId, toast]);

  /* poll status every 2 s while running; render layers as stages land.
   * seq guards against the stale-response race: a request issued before
   * replay was clicked can resolve AFTER the post-replay one and would
   * otherwise clobber real stages with an empty "new" status. */
  const statusSeq = useRef(0);
  useEffect(() => {
    if (!invId) return undefined;
    let alive = true;
    const tick = async () => {
      const mine = ++statusSeq.current;
      try {
        // Status for THE RUN ON SCREEN, not the investigation's latest. With
        // only the replay id passed, a deep-linked run got its title and
        // chips from the URL and its layers from whichever run the selected
        // investigation last produced -- the flagship's header over the
        // Chennai scene's slick (found by the P20 screenshot set).
        const st = await api.invStatus(invId, runId);
        if (!alive || mine !== statusSeq.current) return;
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
      } catch { /* backend briefly away; keep polling */ }
    };
    tick();
    const id = setInterval(tick, running ? 2000 : 10000);
    return () => { alive = false; clearInterval(id); };
  }, [invId, running, fetchLayer, runId]);

  /* full (re)load when the active run changes */
  useEffect(() => {
    if (!runId || loadedFor.current === runId) return;
    loadedFor.current = runId;
    fetched.current = new Set();
    setLayers({}); setLayerErr({}); setSelectedMmsi(null);
    // Light layers first so the stepper, spill panel and slick render
    // instantly; the heavy cloud and tracks follow a beat later without
    // blocking that first paint.
    ["scene_meta", "detect", "slick", "forecast", "suspects"]
      .forEach((n) => fetchLayer(n, runId));
    setTimeout(() => { fetchLayer("origin_cloud", runId);
                       fetchLayer("vessels", runId); }, 250);
  }, [runId, fetchLayer]);

  /* ------------------------------------------------------ derived state -- */
  const sceneT0 = useMemo(
    () => Date.parse(layers.scene_meta?.acquired_utc ?? "") || null,
    [layers.scene_meta]);
  const domain = useMemo(() => {
    if (!sceneT0) return null;
    const winStart = Date.parse(
      layers.origin_cloud?.metadata?.origin_window_start_utc ?? "")
      || sceneT0 - 24 * 3.6e6;
    return [winStart - 6 * 3.6e6, sceneT0 + 24 * 3.6e6];
  }, [sceneT0, layers.origin_cloud]);
  useEffect(() => { if (sceneT0 && timeMs == null) setTimeMs(sceneT0); },
    [sceneT0, timeMs]);

  const maxStep = useMemo(() => {
    let m = 0;
    for (const f of layers.origin_cloud?.features ?? []) {
      m = Math.max(m, f.properties?.step_index ?? 0);
    }
    return m;
  }, [layers.origin_cloud]);

  useEffect(() => {
    const c = layers.slick?.features?.[0]?.properties?.centroid;
    if (Array.isArray(c)) {
      setView((v) => ({ ...v, longitude: c[0], latitude: c[1], zoom: 10.4 }));
    }
  }, [layers.slick]);

  const flyToVessel = useCallback((mmsi) => {
    setSelectedMmsi(mmsi);
    if (mmsi == null) return;
    const f = (layers.vessels?.features ?? [])
      .find((x) => x.properties.mmsi === mmsi);
    const mid = f?.geometry?.coordinates?.[
      Math.floor((f.geometry.coordinates.length || 1) / 2)];
    if (mid) {
      setView((v) => ({ ...v, longitude: mid[0], latitude: mid[1],
                        zoom: Math.max(v.zoom, 10.6) }));
    }
  }, [layers.vessels]);

  /* The run's own registry row. Needed because the page can be showing a run
   * that belongs to no investigation at all (a CLI run, reconciled into the
   * registry afterwards) -- and in that case the investigation dropdown's
   * selection is NOT this run's investigation, so naming it in the header
   * would attribute the run to a case it was never filed under. */
  const { data: runRow } = useApi(
    () => (runId ? api.getRun(runId) : Promise.resolve(null)), [runId]);
  const runIsUnfiled = Boolean(runId && runRow && !runRow.investigation_id);
  const runBelongsElsewhere = Boolean(
    runId && runRow && runRow.investigation_id && invId
    && runRow.investigation_id !== invId);
  const headerNamesTheRun = runIsUnfiled || runBelongsElsewhere;

  const stageList = status?.stages ?? [];
  const overall = !invId ? "NEW"
    // A cancelled run has stages that read `cancelled`; before this it showed
    // COMPLETE because "some stages ran and none failed" was the only test.
    : (status?.state === "cancelled" || runRow?.status === "cancelled"
       || stageList.some((s) => s.status === "cancelled")) ? "CANCELLED"
    : status?.state === "running" ? "RUNNING"
    : stageList.some((s) => s.status === "failed") ? "FAILED-PARTIAL"
    : stageList.length ? "COMPLETE" : "NEW";

  const { data: health } = useApi(() => api.apiStatus(), [], { interval: 30000 });
  const providers = health?.providers ?? [];
  const workingN = providers.filter((p) => p.status === "WORKING").length;

  const region = layers.scene_meta?.bbox
    ? guessPlace([(layers.scene_meta.bbox[0] + layers.scene_meta.bbox[2]) / 2,
                  (layers.scene_meta.bbox[1] + layers.scene_meta.bbox[3]) / 2])
    : "—";

  /* ------------------------------------------- shell context + palette -- */
  /* The run rides in the top bar until it is explicitly closed, so the
   * provenance chips describe what is on screen even after navigating away. */
  useRunInContext(runId);

  /* What this view contributes to ⌘K. Layer toggles and time jumps cannot
   * live in the palette itself -- they are this page's state -- so the page
   * hands them over for as long as it is mounted. */
  useRegisterCommands(() => {
    const cmds = [
      { id: "measure", group: "Map", keys: ["M"], scope: "workspace",
        label: measuring ? "Measure tool — turn off" : "Measure tool — turn on",
        run: () => setMeasuring((v) => !v) },
      { id: "measure-clear", group: "Map", label: "Clear the measurement",
        run: () => setMeasurePoints([]) },
    ];
    for (const [key, on] of Object.entries(show)) {
      cmds.push({
        id: `layer:${key}`, group: "Layer",
        label: `${on ? "Hide" : "Show"} ${key}`,
        run: () => setShow((sh) => ({ ...sh, [key]: !sh[key] })),
      });
    }
    if (sceneT0) {
      cmds.push({
        id: "t:acquisition", group: "Time",
        label: "Jump to scene acquisition time",
        hint: fmtUtc(sceneT0), run: () => setTimeMs(sceneT0),
      });
    }
    const windowStart = Date.parse(
      layers.origin_cloud?.metadata?.origin_window_start_utc ?? "");
    if (windowStart) {
      cmds.push({
        id: "t:window-start", group: "Time",
        label: "Jump to the start of the origin window",
        hint: fmtUtc(windowStart), run: () => setTimeMs(windowStart),
      });
    }
    return cmds;
  }, [measuring, show, sceneT0, layers.origin_cloud]);

  /* ------------------------------------------------------------- actions -- */
  async function run() {
    if (!invId) return;
    fetched.current = new Set();
    setLayers({}); setLayerErr({}); loadedFor.current = null;
    try {
      if (replayMode) {
        const r = await api.invReplay(invId);
        setReplayRunId(r.run_id);
      } else {
        setReplayRunId(null);
        setRunning(true);
        const started = await api.startRun(invId, { engine: "auto" });
        setJob({ id: started.job_id, run_id: started.run_id });
      }
    } catch (e) {
      setRunning(false);
      toast(e.message || "run failed");
    }
  }

  async function cancelRun() {
    if (!job?.id) return;
    setCancelling(true);
    try {
      const result = await api.cancelJob(job.id);
      // Cooperative: the pipeline stops at the next stage boundary, so the
      // button reports what was asked rather than pretending it stopped now.
      toast(result.detail || "cancelling at the next stage boundary");
    } catch (e) {
      toast(e.message || "could not cancel");
      setCancelling(false);
    }
  }

  async function rerunLast() {
    const runId = job?.run_id || status?.run_id;
    if (!runId) return;
    try {
      const started = await api.rerun(runId);
      setJob({ id: started.job_id, run_id: started.run_id });
      setRunning(true);
      toast(`re-running as ${started.run_id}`);
    } catch (e) {
      toast(e.message || "could not re-run");
    }
  }

  /* ----------------------------------------------------------- rendering -- */
  if (invs && !invs.length) {
    return (
      <div className="ws-empty-page" data-testid="no-investigation">
        <Empty icon={<Crosshair size={30} color="var(--ink-3)" />}
          title="No investigation selected"
          hint="Create an investigation to begin." />
        <button className="btn btn-primary" onClick={() => setWizardOpen(true)}
          data-testid="create-investigation">
          <Plus size={13} /> New investigation
        </button>
        {wizardOpen && (
          <NewInvestigation
            onClose={() => setWizardOpen(false)}
            onCreated={async (created) => {
              await reloadInvs();
              setParams({ inv: created.id });
            }} />
        )}
      </div>
    );
  }

  return (
    <div style={{ position: "absolute", inset: 0 }} data-testid="workspace">
      <WorkspaceMap
        view={view} onViewChange={(e) => setView(e.viewState)}
        show={show} layers={{
          sceneMeta: layers.scene_meta, slick: layers.slick,
          origin: layers.origin_cloud, forecast: layers.forecast,
          vessels: layers.vessels, suspects: layers.suspects,
          detect: layers.detect,
        }}
        timeMs={timeMs} sceneT0={sceneT0} runId={runId}
        selectedMmsi={selectedMmsi} onSelect={flyToVessel}
        onHover={setHover} maxStep={maxStep}
        measure={{
          active: measuring,
          points: measurePoints,
          onAddPoint: (p) => setMeasurePoints((pts) => [...pts, p]),
        }}
      />

      {/* ------------------------------------------------------- header --- */}
      <div className="map-overlay ws-header panel" data-testid="ws-header">
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span className="ws-title" data-testid="ws-title">
              {headerNamesTheRun ? runId : (inv?.name ?? "Investigation")}
            </span>
            {runIsUnfiled && (
              <span className="badge badge-neutral" data-testid="unfiled-run"
                title="This run is not filed under an investigation. It was produced outside the API (a CLI run) and its registry row was reconciled from its sealed manifest, which records the scene and the stages but not which case it was opened for.">
                UNFILED RUN
              </span>
            )}
            <span className={`badge ${
              overall === "COMPLETE" ? "badge-ok" :
              overall === "RUNNING" ? "badge-warn" :
              overall === "FAILED-PARTIAL" ? "badge-danger" :
              overall === "CANCELLED" ? "badge-warn" : "badge-neutral"}`}
              data-testid="overall-status">{overall}</span>
          </div>
          <div className="tiny muted mono" data-testid="scene-line">
            {layers.scene_meta?.scene_id ?? inv?.scene_id ?? "—"}
            {layers.scene_meta?.acquired_utc &&
              ` · ${fmtUtc(Date.parse(layers.scene_meta.acquired_utc))}`}
            {` · ${region}`}
          </div>
        </div>
        <div className="ws-header-right">
          {layers.scene_meta?.source && (
            <span className={`badge badge-${sourceBadge(layers.scene_meta.source).tone}`}
              data-testid="scene-source">
              scene {sourceBadge(layers.scene_meta.source).label}
            </span>
          )}
          <Link to="/monitoring" className="tiny ws-health" data-testid="health-strip">
            <Activity size={11} />
            <span className="mono">{workingN}/{providers.length || "—"} APIs</span>
          </Link>
          <select value={invId ?? ""} data-testid="inv-select"
            onChange={(e) => setParams({ inv: e.target.value })}>
            {(invs ?? []).map((x) => (
              <option key={x.id} value={x.id}>{x.name} · {x.id}</option>
            ))}
          </select>
          <button className={`btn btn-icon ${measuring ? "btn-on" : ""}`}
            title="Measure distance on the map (M) — great-circle km and nm"
            aria-pressed={measuring}
            onClick={() => setMeasuring((v) => !v)} data-testid="measure-toggle">
            <Ruler size={14} />
          </button>
          <button className="btn btn-icon" title="New investigation"
            onClick={() => setWizardOpen(true)} data-testid="new-investigation-btn">
            <Plus size={14} />
          </button>
        </div>
      </div>

      {/* --------------------------------------------------- left column --- */}
      <div className="map-overlay ws-left">
        <motion.div initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }}
          className="panel" style={{ padding: 13 }}>
          <div className="ws-panel-title"><Zap size={13} /> Run</div>
          <div style={{ display: "flex", gap: 8 }}>
            {running && job?.id && (
              <button className="btn" style={{ justifyContent: "center" }}
                onClick={cancelRun} disabled={cancelling} data-testid="cancel-btn"
                title="Stops at the next stage boundary. The run will not be sealed.">
                <Square size={12} /> {cancelling ? "Cancelling…" : "Cancel"}
              </button>
            )}
            {!running && (job?.run_id || status?.run_id) && (
              <button className="btn btn-icon" onClick={rerunLast}
                title="Run again with the same inputs, under a new run id"
                data-testid="rerun-btn">
                <RotateCcw size={13} />
              </button>
            )}
            <button className="btn btn-primary" style={{ flex: 1, justifyContent: "center" }}
              onClick={run} disabled={running} data-testid="run-btn">
              {running ? <Spinner /> : <Play size={13} />}
              {running ? "Running…" : replayMode ? "Replay investigation" : "Run investigation"}
            </button>
          </div>
          <label className="switch" style={{ marginTop: 8 }}
            onClick={() => setReplayMode((v) => !v)} data-testid="replay-toggle">
            <span className={`switch-track ${replayMode ? "on" : ""}`}>
              <span className="switch-knob" />
            </span>
            <span className="switch-label">
              Replay mode — pre-computed files, no execution
            </span>
          </label>

          {/* Export: the GeoJSON bundle streams straight from the run dir
            * (contract artefacts only); the report is a printable page. */}
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            {runId ? (
              <a className="btn btn-sm" style={{ flex: 1, justifyContent: "center",
                  textDecoration: "none" }}
                href={`/api/runs/${runId}/export`} download
                data-testid="export-bundle"
                title="Download slick, origin cloud, forecast, vessels and suspects as a GeoJSON/contract bundle">
                <Download size={12} /> Export bundle
              </a>
            ) : (
              <button className="btn btn-sm" disabled
                style={{ flex: 1, justifyContent: "center" }}
                data-testid="export-bundle">
                <Download size={12} /> Export bundle
              </button>
            )}
            <Link className="btn btn-sm" style={{ flex: 1, justifyContent: "center",
                textDecoration: "none",
                pointerEvents: runId ? "auto" : "none",
                opacity: runId ? 1 : 0.45 }}
              to={runId ? `/report?run=${runId}` : "#"} target="_blank"
              data-testid="export-report"
              title="Printable investigation report — run metadata, slick geometry, origin window, suspects with evidence and weights">
              <FileText size={12} /> Report
            </Link>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.05 }} className="panel" style={{ padding: 13 }}>
          <div className="ws-panel-title"><Activity size={13} /> Pipeline</div>
          <StageStepper stages={status?.stages} />
        </motion.div>

        <motion.div initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.1 }} className="panel"
          style={{ padding: 13, overflowY: "auto", minHeight: 120 }}>
          <div className="ws-panel-title"><LayersIcon size={13} /> Layers</div>
          <LayerPanel show={show} present={status?.layers_present}
            stages={status?.stages}
            onToggle={(k, v) => setShow((s) => ({ ...s, [k]: v }))} />
        </motion.div>

        {measuring && (
          <MeasureTool points={measurePoints}
            onUndo={() => setMeasurePoints((pts) => pts.slice(0, -1))}
            onClear={() => setMeasurePoints([])} />
        )}
      </div>

      {/* -------------------------------------------------- right column --- */}
      <div className="map-overlay ws-right">
        <div className="panel" style={{ display: "flex", flexDirection: "column",
          minHeight: 0, flex: 1 }}>
          <div className="ws-tabs">
            <button className={`ws-tab ${tab === "spill" ? "on" : ""}`}
              onClick={() => setTab("spill")} data-testid="tab-spill">
              <Droplets size={12} /> Spill
            </button>
            <button className={`ws-tab ${tab === "suspects" ? "on" : ""}`}
              onClick={() => setTab("suspects")} data-testid="tab-suspects">
              <Crosshair size={12} /> Suspects
              {layers.suspects?.suspects?.length ? (
                <span className="ws-count mono">{layers.suspects.suspects.length}</span>
              ) : null}
            </button>
          </div>
          <div style={{ overflowY: "auto", padding: 13, minHeight: 0 }}>
            {tab === "spill"
              ? <SpillPanel slick={layers.slick} detect={layers.detect} />
              : <SuspectsPanel suspects={layers.suspects}
                  error={layerErr.suspects} tracks={layers.vessels}
                  selectedMmsi={selectedMmsi} onSelect={flyToVessel} />}
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------- bottom --- */}
      <div className="map-overlay ws-bottom">
        <TimeSlider domain={domain} value={timeMs} onChange={setTimeMs}
          playing={playing} onPlaying={setPlaying}
          speed={speed} onSpeed={setSpeed} sceneT0={sceneT0} />
      </div>

      {/* --------------------------------------------------- hover panel --- */}
      <AnimatePresence>
        {hover && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }} className="map-overlay panel ws-hover"
            data-testid="hover-box">
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}>
              <Info size={12} color="var(--accent)" />
              <span style={{ fontSize: 12, fontWeight: 600 }}>{hover.title}</span>
            </div>
            {hover.rows.map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 12, fontSize: 11, padding: "1px 0" }}>
                <span className="muted" style={{ width: 78 }}>{k}</span>
                <span className="mono">{String(v)}</span>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ------------------------------------------------------- toasts --- */}
      <div className="ws-toasts">
        <AnimatePresence>
          {toasts.map((t) => (
            <motion.div key={t.id} initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
              className={`panel ws-toast ws-toast-${t.tone}`} data-testid="toast">
              {t.text}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {wizardOpen && (
        <NewInvestigation
          onClose={() => setWizardOpen(false)}
          onCreated={async (created) => {
            await reloadInvs();
            setParams({ inv: created.id });
          }} />
      )}
    </div>
  );
}
