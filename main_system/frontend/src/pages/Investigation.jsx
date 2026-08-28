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
  Activity, Crosshair, Droplets, Info, Layers as LayersIcon, Play, Zap,
} from "lucide-react";

import WorkspaceMap from "../components/workspace/WorkspaceMap";
import StageStepper from "../components/workspace/StageStepper";
import LayerPanel from "../components/workspace/LayerPanel";
import SpillPanel from "../components/workspace/SpillPanel";
import SuspectsPanel from "../components/workspace/SuspectsPanel";
import TimeSlider from "../components/workspace/TimeSlider";
import { sourceBadge } from "../components/workspace/palette";
import { Spinner, Empty } from "../components/ui";
import { api, useApi } from "../lib/api";
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
    hindcast: true, origin: true, vessels: true,
  });
  const [view, setView] = useState({
    longitude: 80.32, latitude: 13.05, zoom: 9.6, pitch: 0, bearing: 0,
  });

  const runId = replayRunId || status?.run_id || params.get("run")
    || inv?.latest_run_id || null;
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
        const st = await api.invStatus(invId, replayRunId);
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
  }, [invId, running, fetchLayer, replayRunId]);

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

  const overall = !invId ? "NEW"
    : status?.state === "running" ? "RUNNING"
    : (status?.stages ?? []).some((s) => s.status === "failed") ? "FAILED-PARTIAL"
    : (status?.stages ?? []).length ? "COMPLETE" : "NEW";

  const { data: health } = useApi(() => api.apiStatus(), [], { interval: 30000 });
  const providers = health?.providers ?? [];
  const workingN = providers.filter((p) => p.status === "WORKING").length;

  const region = layers.scene_meta?.bbox
    ? guessPlace([(layers.scene_meta.bbox[0] + layers.scene_meta.bbox[2]) / 2,
                  (layers.scene_meta.bbox[1] + layers.scene_meta.bbox[3]) / 2])
    : "—";

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
        await api.startRun(invId, { engine: "auto" });
      }
    } catch (e) {
      setRunning(false);
      toast(e.message || "run failed");
    }
  }

  async function createInv() {
    const created = await api.createInvestigation({
      name: "New investigation",
      scene_meta_path: "contracts/mocks/scene_meta.json",
    });
    await reloadInvs();
    setParams({ inv: created.id });
  }

  /* ----------------------------------------------------------- rendering -- */
  if (invs && !invs.length) {
    return (
      <div className="ws-empty-page" data-testid="no-investigation">
        <Empty icon={<Crosshair size={30} color="var(--ink-3)" />}
          title="No investigation selected"
          hint="Create an investigation to begin." />
        <button className="btn btn-primary" onClick={createInv}
          data-testid="create-investigation">
          <Zap size={13} /> Create investigation
        </button>
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
        }}
        timeMs={timeMs} sceneT0={sceneT0} runId={runId}
        selectedMmsi={selectedMmsi} onSelect={flyToVessel}
        onHover={setHover} maxStep={maxStep}
      />

      {/* ------------------------------------------------------- header --- */}
      <div className="map-overlay ws-header panel" data-testid="ws-header">
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span className="ws-title">{inv?.name ?? "Investigation"}</span>
            <span className={`badge ${
              overall === "COMPLETE" ? "badge-ok" :
              overall === "RUNNING" ? "badge-warn" :
              overall === "FAILED-PARTIAL" ? "badge-danger" : "badge-neutral"}`}
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
        </div>
      </div>

      {/* --------------------------------------------------- left column --- */}
      <div className="map-overlay ws-left">
        <motion.div initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }}
          className="panel" style={{ padding: 13 }}>
          <div className="ws-panel-title"><Zap size={13} /> Run</div>
          <div style={{ display: "flex", gap: 8 }}>
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
        </motion.div>

        <motion.div initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.05 }} className="panel" style={{ padding: 13 }}>
          <div className="ws-panel-title"><Activity size={13} /> Pipeline</div>
          <StageStepper stages={status?.stages} />
        </motion.div>

        <motion.div initial={{ opacity: 0, x: -14 }} animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.1 }} className="panel"
          style={{ padding: 13, overflowY: "auto", minHeight: 0 }}>
          <div className="ws-panel-title"><LayersIcon size={13} /> Layers</div>
          <LayerPanel show={show} present={status?.layers_present}
            stages={status?.stages}
            onToggle={(k, v) => setShow((s) => ({ ...s, [k]: v }))} />
        </motion.div>
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
    </div>
  );
}
