/* Incident command center: the map-first replay experience.
 *
 * Layout — left: modes / layers / legend · centre: the map · right:
 * intelligence panel · bottom: transport + global timeline.
 *
 * The engine is a single rAF loop. `stepT` (0..1 inside the current step)
 * drives every reveal; `simT` (epoch ms) drives every time-aware layer; and
 * `tick` drives continuous effects (radar sweep, pulses, flow particles).
 * Pausing freezes stepT/simT but not tick, so the picture stays alive.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { FlyToInterpolator } from "@deck.gl/core";
import {
  ChevronRight, Crosshair, Layers, Map as MapIcon, Play, Satellite,
  AlertTriangle,
} from "lucide-react";

import { api, useApi } from "../lib/api";
import {
  STEPS, stepIndexById, prepareBundle, stepSimTime, span, easeInOut,
  clamp01, lerp, makeParticles, advectParticles, sampleField,
  MAP_MODES, MODE_PRESETS,
} from "../lib/replay";
import CommandMap, { SEMANTIC } from "../components/CommandMap";
import IntelPanel from "../components/IntelPanel";
import ReplayControls from "../components/ReplayControls";
import { Spinner } from "../components/ui";

/* ------------------------------------------------------------ camera ----- */

function fitZoom(bbox) {
  if (!bbox) return 9;
  const lonSpan = Math.max(bbox[2] - bbox[0], 0.05);
  return Math.log2(360 / lonSpan) + Math.log2(1100 / 256) - 1.05;
}

function cameraFor(stepId, b) {
  const [cx, cy] = b.sceneCenter;
  const [ox, oy] = b.originCenter;
  const z = fitZoom(b.sceneMeta?.bbox);
  const C = {
    location: { longitude: cx, latitude: cy, zoom: z - 0.15, pitch: 0, bearing: 0 },
    radar: { longitude: cx, latitude: cy, zoom: z - 0.35, pitch: 0, bearing: 0 },
    ais: { longitude: cx, latitude: cy, zoom: z - 0.2, pitch: 0, bearing: 0 },
    detect: {
      longitude: b.slickProps?.centroid?.[0] ?? cx,
      latitude: b.slickProps?.centroid?.[1] ?? cy,
      zoom: z + 0.55, pitch: 0, bearing: 0,
    },
    env: { longitude: cx, latitude: cy, zoom: z, pitch: 18, bearing: -8 },
    drift: { longitude: cx, latitude: cy, zoom: z + 0.1, pitch: 32, bearing: -14 },
    hindcast: { longitude: (cx + ox) / 2, latitude: (cy + oy) / 2, zoom: z + 0.15, pitch: 34, bearing: 12 },
    origin: { longitude: ox, latitude: oy, zoom: z + 0.9, pitch: 24, bearing: 0 },
    filter: { longitude: cx, latitude: cy, zoom: z - 0.15, pitch: 0, bearing: 0 },
    attribution: { longitude: ox, latitude: oy, zoom: z + 0.35, pitch: 12, bearing: 0 },
    evidence: { longitude: cx, latitude: cy, zoom: z, pitch: 22, bearing: -6 },
  };
  return C[stepId] ?? C.location;
}

/* Which layers each step needs. User toggles override, mode presets force. */
function stepToggles(stepId) {
  const on = (...k) => Object.fromEntries(k.map((x) => [x, true]));
  switch (stepId) {
    case "location": return {};
    case "radar": return on("vessels");
    case "ais": return on("vessels");
    case "detect": return on("vessels", "slick");
    case "env": return on("vessels", "slick", "wind", "currents");
    case "drift": return on("slick", "hindcast", "wind", "currents");
    case "hindcast": return on("slick", "hindcast", "currents");
    case "origin": return on("slick", "hindcast", "origin", "vessels");
    case "filter": return on("slick", "origin", "vessels");
    case "attribution": return on("slick", "origin", "vessels");
    case "evidence": return on("slick", "origin", "vessels", "forecast");
    default: return {};
  }
}

/* Per-step dimming of background traffic so the subject of the step owns the
 * frame. Never zero: context stays visible. */
const VESSEL_DIM = {
  detect: 0.3, env: 0.35, drift: 0.2, hindcast: 0.15, origin: 0.4,
};

/* ========================================================================= */

/* One incident per SCENE: the latest completed run of each. The demo cycles
 * through spills across the world, each a real end-to-end pipeline run. */
function useIncidents(runs) {
  return useMemo(() => {
    const byScene = new Map();
    for (const r of runs ?? []) {
      if (r.status !== "complete" || !r.scene_id) continue;
      const prev = byScene.get(r.scene_id);
      if (!prev || (r.started_utc ?? "") > (prev.started_utc ?? "")) {
        byScene.set(r.scene_id, r);
      }
    }
    return [...byScene.values()].sort(
      (a, b) => (a.scene_id > b.scene_id ? 1 : -1));
  }, [runs]);
}

export default function Incident() {
  const [params, setParams] = useSearchParams();
  const wantRun = params.get("run");

  /* ------------------------------------------------------- data load ----- */
  const { data: runs } = useApi(() => api.listRuns(), []);
  const incidents = useIncidents(runs);
  const runId = useMemo(() => {
    if (wantRun) return wantRun;
    return incidents[0]?.run_id
      ?? (runs ?? []).find((r) => r.status === "complete")?.run_id ?? null;
  }, [wantRun, runs, incidents]);
  const runMeta = useMemo(
    () => (runs ?? []).find((r) => r.run_id === runId) ?? { run_id: runId },
    [runs, runId]);

  const [raw, setRaw] = useState(null);
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    Promise.all([
      api.layer(runId, "scene_meta").catch(() => null),
      api.layer(runId, "slick").catch(() => null),
      api.layer(runId, "origin_cloud").catch(() => null),
      api.layer(runId, "forecast").catch(() => null),
      api.layer(runId, "suspects").catch(() => null),
      api.vesselsGeojson(runId).catch(() => null),
      api.forcingField(runId).catch(() => null),
    ]).then(([sceneMeta, slick, origin, forecast, suspects, vessels, forcing]) => {
      if (alive) setRaw({ sceneMeta, slick, origin, forecast, suspects, vessels, forcing });
    });
    return () => { alive = false; };
  }, [runId]);

  const bundle = useMemo(
    () => (raw?.sceneMeta ? prepareBundle({ ...raw, runMeta }) : null),
    [raw, runMeta]);

  /* --------------------------------------------------------- engine ------ */
  const [started, setStarted] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [mode, setModeState] = useState("dark");
  const [speed, setSpeed] = useState(1);
  const [playMode, setPlayMode] = useState("replay");   // replay | step
  const [stepIdx, setStepIdx] = useState(0);
  const [userToggles, setUserToggles] = useState({});
  const [selectedMmsi, setSelectedMmsi] = useState(null);
  const [hoverInfo, setHoverInfo] = useState(null);
  const [frame, setFrame] = useState({ stepIdx: 0, stepT: 0, simT: 0, tick: 0 });
  const [appTheme, setAppTheme] = useState(
    () => document.documentElement.getAttribute("data-theme") || "dark");
  useEffect(() => {
    const obs = new MutationObserver(() => setAppTheme(
      document.documentElement.getAttribute("data-theme") || "dark"));
    obs.observe(document.documentElement,
      { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);

  const engine = useRef({ stepT: 0, tick: 0, last: 0, scrubT: null, raf: 0 });
  const stepIdxRef = useRef(0); stepIdxRef.current = stepIdx;
  const playingRef = useRef(false); playingRef.current = playing;
  const speedRef = useRef(1); speedRef.current = speed;
  const modeRef = useRef(playMode); modeRef.current = playMode;

  /* camera */
  const [view, setView] = useState({
    longitude: 78, latitude: 12, zoom: 4.4, pitch: 0, bearing: 0,
  });
  const flyTo = useCallback((cam, ms = 1800) => {
    setView((v) => ({
      ...v, ...cam,
      transitionDuration: ms,
      transitionInterpolator: new FlyToInterpolator({ curve: 1.35 }),
    }));
  }, []);

  const gotoStep = useCallback((i, { fly = true } = {}) => {
    const idx = Math.max(0, Math.min(STEPS.length - 1, i));
    setStepIdx(idx);
    engine.current.stepT = modeRef.current === "step" ? 1 : 0;
    engine.current.scrubT = null;
    if (fly && bundle) flyTo(cameraFor(STEPS[idx].id, bundle));
  }, [bundle, flyTo]);

  /* particle pools */
  const windParts = useRef(null);
  const currentParts = useRef(null);
  const driftFwd = useRef(null);
  useEffect(() => {
    if (!bundle?.sceneMeta?.bbox) return;
    const bb = bundle.sceneMeta.bbox;
    const pad = 0.25 * (bb[2] - bb[0]);
    const big = [bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad];
    windParts.current = makeParticles(big, 380, 11);
    currentParts.current = makeParticles(big, 380, 23);
    const c = bundle.slickProps?.centroid ?? bundle.sceneCenter;
    const r = 0.05;
    driftFwd.current = makeParticles([c[0] - r, c[1] - r, c[0] + r, c[1] + r], 160, 5);
  }, [bundle]);

  /* the loop */
  useEffect(() => {
    if (!bundle) return;
    const eng = engine.current;
    eng.last = performance.now();
    const loop = (now) => {
      const dt = Math.min(0.1, (now - eng.last) / 1000);
      eng.last = now;
      eng.tick += dt * 60;

      let idx = stepIdxRef.current;
      if (playingRef.current && modeRef.current === "replay") {
        eng.stepT += (dt * speedRef.current) / STEPS[idx].dur;
        if (eng.stepT >= 1) {
          if (idx < STEPS.length - 1) {
            idx += 1;
            setStepIdx(idx);
            eng.stepT = 0;
            flyTo(cameraFor(STEPS[idx].id, bundle));
          } else {
            eng.stepT = 1;
            setPlaying(false);
          }
        }
      }

      const p = clamp01(eng.stepT);
      const simT = eng.scrubT ?? stepSimTime(STEPS[idx].id, p, bundle);

      /* advect flow particles (visual pace only; vectors are the real field) */
      if (windParts.current) {
        advectParticles(windParts.current, bundle.wind, simT, dt, 900);
        advectParticles(currentParts.current, bundle.currents, simT, dt, 2600);
        if (driftFwd.current) {
          advectParticles(driftFwd.current, bundle.wind, simT, dt, 2000);
        }
      }

      setFrame({ stepIdx: idx, stepT: p, simT, tick: eng.tick });
      eng.raf = requestAnimationFrame(loop);
    };
    eng.raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(eng.raf);
  }, [bundle, flyTo]);

  /* ------------------------------------------------------- effects ------- */
  const gateOf = useMemo(() => {
    if (!bundle) return () => null;
    const m = new Map();
    bundle.filterStages.forEach((s, i) => s.reasons.forEach((r) => m.set(r, i)));
    return (t) => (t.filtered ? (m.get(t.filterReason) ?? 3) : null);
  }, [bundle]);

  const effects = useMemo(() => {
    if (!bundle) return {};
    const id = STEPS[frame.stepIdx].id;
    const p = frame.stepT;
    const after = (sid) => frame.stepIdx > stepIndexById[sid];
    const at = (sid) => id === sid;
    const modal = (m) => mode === m;

    const filterStageF = at("filter")
      ? clamp01(span(p, 0.06, 0.86)) * (bundle.filterStages.length - 1)
      : after("filter") ? bundle.filterStages.length - 1 : 0;

    const dim = VESSEL_DIM[id] ?? 1;
    const filterFade = (t) => {
      const g = gateOf(t);
      if (g == null) return 0.9 * dim;
      if (!at("filter") && !after("filter")) return 0.8 * dim;
      const gone = clamp01(filterStageF - g + 0.5);
      return lerp(0.8, 0.13, gone) * dim;
    };

    return {
      sarAlpha: at("detect")
        ? span(p, 0, 0.2) * (1 - 0.55 * span(p, 0.88, 1))
        : modal("sar") || modal("oil") ? 0.9
        : after("detect") && modal("hybrid") ? 0.3 : 0,
      scanX: at("detect") && p >= 0.14 && p <= 0.6 ? span(p, 0.14, 0.6) : null,
      maskAlpha: at("detect") ? span(p, 0.38, 0.68) * (1 - span(p, 0.82, 0.98))
        : modal("oil") ? 0.5 : 0,
      slickAlpha: at("detect") ? span(p, 0.55, 0.88) : after("detect") ? 1 : 0,
      slickLock: at("detect") ? span(p, 0.8, 1) : 0,
      slickLabel: after("detect") || (at("detect") && p > 0.92),
      radar: at("radar") ? Math.min(span(p, 0, 0.1), 1 - span(p, 0.93, 1)) : 0,
      envAlpha: at("env") ? span(p, 0, 0.28)
        : at("drift") || at("hindcast") ? 0.7
        : modal("wind") || modal("current") || modal("hybrid") ? 1
        : after("env") ? 0.28 : 0,
      aisGrow: at("ais"),
      hindTime: at("drift") ? bundle.maxStep * (1 - easeInOut(p))
        : at("hindcast") ? bundle.maxStep * easeInOut(p)
        : at("origin") ? bundle.maxStep
        : modal("hindcastM") ? (frame.tick / 3) % (bundle.maxStep + 4)
        : after("origin") ? null : null,
      originAlpha: at("origin") ? span(p, 0.12, 0.5)
        : after("origin") || modal("hindcastM") ? 1 : 0,
      fcAlpha: at("evidence") ? span(p, 0.15, 0.6)
        : modal("forecastM") || modal("hybrid") ? 1 : 0,
      filterStageF, filterFade,
      filterTick: Math.round(filterStageF * 6),
      lockOn: at("attribution") ? span(p, 0.3, 0.72)
        : after("attribution") ? 1 : 0,
      rankReveal: at("attribution") ? span(p, 0.12, 0.8) : 1,
    };
  }, [bundle, frame, mode, gateOf]);

  const toggles = useMemo(() => {
    const base = stepToggles(STEPS[frame.stepIdx].id);
    const preset = MODE_PRESETS[mode];
    return { ...base, ...(preset ?? {}), ...userToggles };
  }, [frame.stepIdx, mode, userToggles]);

  const setMode = (m) => { setModeState(m); setUserToggles({}); };

  /* ------------------------------------------------------ transport ------ */
  const onPlay = () => {
    const first = !started;
    setStarted(true);
    if (playMode === "step") setPlayMode("replay");
    if (frame.stepIdx === STEPS.length - 1 && frame.stepT >= 1) gotoStep(0);
    engine.current.scrubT = null;
    // The opening shot: a long fly-in from orbit to the investigation zone.
    if (first && bundle) flyTo(cameraFor("location", bundle), 4200);
    setPlaying(true);
  };
  const onScrub = (t) => {
    setPlaying(false);
    engine.current.scrubT = t;
  };

  const incidentIdx = incidents.findIndex((x) => x.run_id === runId);
  const nextIncident = incidents.length > 1
    ? incidents[(incidentIdx + 1) % incidents.length] : null;
  const openIncident = useCallback((rid, autoplay = false) => {
    setSelectedMmsi(null);
    setStarted(autoplay);
    setPlaying(false);
    setRaw(null);
    engine.current.stepT = 0;
    setStepIdx(0);
    setParams(autoplay ? { run: rid, autoplay: "1" } : { run: rid });
  }, [setParams]);

  /* arriving with ?autoplay=1 (the play-next flow) starts the show itself */
  useEffect(() => {
    if (bundle && params.get("autoplay") === "1" && !started) {
      setStarted(true);
      flyTo(cameraFor("location", bundle), 4200);
      setPlaying(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundle]);

  if (!bundle) {
    return (
      <div className="incident-loading">
        <Spinner />
        <span className="muted tiny">
          {runId ? `Loading incident ${runId}…` : "No completed runs yet — start one from Investigations."}
        </span>
      </div>
    );
  }

  const step = STEPS[frame.stepIdx];
  const wind = bundle.wind ? sampleField(bundle.wind,
    bundle.sceneCenter[0], bundle.sceneCenter[1], frame.simT) : null;
  const cur = bundle.currents ? sampleField(bundle.currents,
    bundle.sceneCenter[0], bundle.sceneCenter[1], frame.simT) : null;

  return (
    <div className="incident">
      {/* ------------------------------------------------- header strip --- */}
      <div className="incident-head">
        <div className="incident-id">
          <Satellite size={15} color="var(--accent)" />
          {incidents.length > 1 ? (
            <select className="incident-select mono" value={runId}
              onChange={(e) => openIncident(e.target.value)}>
              {incidents.map((x, i) => (
                <option key={x.run_id} value={x.run_id}>
                  {String(i + 1).padStart(2, "0")} - {x.scene_id}
                </option>
              ))}
            </select>
          ) : (
            <span className="mono">INCIDENT {bundle.runMeta?.run_id?.toUpperCase()}</span>
          )}
          {nextIncident && (
            <button className="btn btn-sm" title={"Next: " + nextIncident.scene_id}
              onClick={() => openIncident(nextIncident.run_id)}>
              Next <ChevronRight size={12} />
            </button>
          )}
          <span className="badge badge-warn">OIL SPILL INVESTIGATION</span>
          <span className={`badge ${runMeta?.stages_mock ? "badge-mock" : "badge-ok"}`}>
            {runMeta?.stages_mock ? `${runMeta.stages_real}/${runMeta.stages_total} STAGES REAL`
              : "ANALYSIS COMPLETE"}
          </span>
        </div>
        <div className="incident-env mono">
          {wind && <span>WIND {Math.hypot(...wind).toFixed(1)} m/s</span>}
          {cur && <span>CURRENT {Math.hypot(...cur).toFixed(2)} m/s</span>}
          <span>{bundle.placeName.toUpperCase()}</span>
        </div>
      </div>

      <div className="incident-main">
        {/* ---------------------------------------------------- left rail -- */}
        <div className="rail">
          <div className="rail-section">
            <div className="rail-title"><MapIcon size={11} /> MAP MODE</div>
            {MAP_MODES.map((m) => (
              <button key={m.id}
                className={`rail-item ${mode === m.id ? "on" : ""}`}
                onClick={() => setMode(m.id)}>{m.label}</button>
            ))}
          </div>
          <div className="rail-section">
            <div className="rail-title"><Layers size={11} /> LAYERS</div>
            {[["sar", "SAR imagery"], ["slick", "Detected slick"],
              ["vessels", "AIS vessels"], ["wind", "Wind flow"],
              ["currents", "Ocean current"], ["hindcast", "Hindcast"],
              ["forecast", "Forecast"], ["origin", "Origin"]].map(([k, label]) => (
              <label key={k} className="rail-toggle">
                <input type="checkbox" checked={Boolean(toggles[k])}
                  onChange={(e) => setUserToggles((u) => ({ ...u, [k]: e.target.checked }))} />
                <span>{label}</span>
              </label>
            ))}
          </div>
          <div className="rail-section">
            <div className="rail-title">LEGEND</div>
            <Legend />
          </div>
        </div>

        {/* --------------------------------------------------------- map --- */}
        <div className="stage">
          <CommandMap
            bundle={bundle} frame={frame} effects={effects}
            mode={mode} toggles={toggles}
            view={view}
            onViewChange={({ viewState }) => setView(viewState)}
            windParts={toggles.wind ? windParts.current : null}
            currentParts={toggles.currents ? currentParts.current : null}
            driftFwd={effects.fcAlpha > 0 ? driftFwd.current : null}
            selectedMmsi={selectedMmsi}
            onSelect={(m) => setSelectedMmsi((s) => (s === m ? null : m))}
            onHoverInfo={setHoverInfo}
            runId={runId}
            appTheme={appTheme}
          />

          {/* step banner */}
          <div className="stage-banner">
            <span className="mono stage-banner-n">
              {String(step.n).padStart(2, "0")} / {STEPS.length}
            </span>
            <span className="stage-banner-t">{step.title}</span>
          </div>

          {/* detection callout */}
          <AnimatePresence>
            {step.id === "detect" && frame.stepT > 0.88 && (
              <motion.div className="stage-callout panel"
                initial={{ opacity: 0, scale: 0.92, y: 8 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0 }}>
                <div className="callout-title oil">OIL SPILL DETECTED</div>
                <div className="callout-big mono">
                  {(bundle.slickProps.confidence * 100).toFixed(1)}% confidence
                </div>
                <div className="callout-sub mono">
                  {bundle.slickProps.area_km2} km² estimated area
                </div>
              </motion.div>
            )}
            {step.id === "evidence" && frame.stepT > 0.4 && bundle.top && (
              <motion.div className="stage-callout stage-callout-final panel"
                initial={{ opacity: 0, scale: 0.94 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0 }}>
                <div className="callout-title danger">
                  <Crosshair size={13} /> HIGHEST ATTRIBUTION LIKELIHOOD
                </div>
                <div className="callout-name">
                  {bundle.top.vessel_name ?? bundle.top.mmsi}
                </div>
                <div className="callout-big mono danger">
                  {(bundle.top.total_score * 100).toFixed(0)}%
                </div>
                {nextIncident && frame.stepT >= 1 && !playing && (
                  <button className="btn btn-primary"
                    style={{ marginTop: 12, pointerEvents: "auto" }}
                    onClick={() => openIncident(nextIncident.run_id, true)}>
                    <Play size={13} /> PLAY NEXT INCIDENT -{" "}
                    {nextIncident.scene_id.replace("S1A_IW_GRDH_", "")}
                  </button>
                )}
              </motion.div>
            )}
          </AnimatePresence>

          {/* hover inspector */}
          <AnimatePresence>
            {hoverInfo && (
              <motion.div className="inspector panel"
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}>
                <div className="inspector-title">{hoverInfo.title}</div>
                {hoverInfo.rows.map(([k, v]) => (
                  <div key={k} className="ip-row">
                    <span className="ip-k">{k}</span>
                    <span className="ip-v mono">{v}</span>
                  </div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>

          {/* hero overlay before first play */}
          <AnimatePresence>
            {!started && (
              <motion.div className="hero" exit={{ opacity: 0 }}>
                <motion.div className="hero-card"
                  initial={{ opacity: 0, y: 18 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5 }}>
                  <div className="hero-kicker mono">
                    SIH 26143 · MARITIME INTELLIGENCE
                  </div>
                  <div className="hero-title">
                    INCIDENT {bundle.runMeta?.run_id?.toUpperCase()}
                  </div>
                  <div className="hero-sub">Oil Spill Investigation — {bundle.placeName}</div>
                  <button className="btn btn-primary hero-play" onClick={onPlay}>
                    <Play size={15} /> PLAY FULL INCIDENT REPLAY
                  </button>
                  <button className="btn hero-step"
                    onClick={() => { setStarted(true); setPlayMode("step"); gotoStep(0); }}>
                    Step-by-step investigation
                  </button>
                  {Boolean(runMeta?.stages_mock) && (
                    <div className="hero-flag">
                      <AlertTriangle size={11} />
                      {runMeta.stages_mock} of {runMeta.stages_total} stages used
                      mock data in this run — labelled in the replay
                    </div>
                  )}
                </motion.div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* ------------------------------------------------- intel panel --- */}
        <IntelPanel bundle={bundle} frame={frame} effects={effects}
          selectedMmsi={selectedMmsi} onSelect={setSelectedMmsi} />
      </div>

      {/* --------------------------------------------------- transport ----- */}
      <ReplayControls
        bundle={bundle} frame={frame} playing={playing} speed={speed}
        mode={playMode}
        onPlay={onPlay}
        onPause={() => setPlaying(false)}
        onRestart={() => { gotoStep(0); setPlaying(true); setStarted(true); }}
        onStep={(d) => { setPlaying(false); setPlayMode("step"); gotoStep(stepIdx + d); }}
        onJump={(i) => { setPlaying(false); setPlayMode("step"); gotoStep(i); }}
        onSpeed={setSpeed}
        onMode={(m) => {
          setPlayMode(m);
          if (m === "step") { setPlaying(false); engine.current.stepT = 1; }
        }}
        onScrub={onScrub}
      />
    </div>
  );
}

/* ------------------------------------------------------------- legend ---- */

function Legend() {
  const c = (rgb) => `rgb(${rgb.join(",")})`;
  return (
    <div className="legend">
      <div className="legend-row">
        <span className="lg lg-solid" style={{ background: c(SEMANTIC.slick) }} />
        Observed slick — solid</div>
      <div className="legend-row">
        <span className="lg lg-dash" style={{ borderColor: c(SEMANTIC.hindcast) }} />
        Hindcast — dashed, backwards</div>
      <div className="legend-row">
        <span className="lg lg-ring" style={{ borderColor: c(SEMANTIC.origin) }} />
        Probable origin — pulsing</div>
      <div className="legend-row">
        <span className="lg lg-dot" style={{ borderColor: c(SEMANTIC.forecast) }} />
        Forecast — dotted, translucent</div>
      <div className="legend-row">
        <span className="lg lg-solid" style={{ background: c(SEMANTIC.candidate) }} />
        Attribution candidate</div>
      <div className="legend-row">
        <span className="lg lg-solid" style={{ background: c(SEMANTIC.vessel), opacity: 0.5 }} />
        Background / eliminated vessel</div>
      <div className="legend-row">
        <span className="lg lg-solid" style={{ background: c(SEMANTIC.current) }} />
        Ocean current particles</div>
      <div className="legend-row">
        <span className="lg lg-solid" style={{ background: c(SEMANTIC.wind) }} />
        Wind particles</div>
    </div>
  );
}
