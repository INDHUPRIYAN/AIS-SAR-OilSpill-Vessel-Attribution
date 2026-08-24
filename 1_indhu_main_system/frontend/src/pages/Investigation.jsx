/* Investigation view: the map, the evidence, and the ranked suspects.
 *
 * Every layer carries the provenance of the stage that produced it. A layer
 * served from a mock is badged MOCK next to its toggle -- if a viewer cannot
 * tell measured data from a placeholder, the whole honesty principle is gone.
 */

import { useEffect, useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play, Layers as LayersIcon, Ship, Crosshair, Waves, Clock, Info, Zap,
} from "lucide-react";

import InvestigationMap, { RGB } from "../components/InvestigationMap";
import { Badge, Card, ProvenanceChip, FactorBar, Spinner, Empty } from "../components/ui";
import { useSearchParams } from "react-router-dom";
import { api, fmt, useApi } from "../lib/api";

const rgbaCss = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export default function Investigation() {
  const [params] = useSearchParams();
  const { data: runs, reload: reloadRuns } = useApi(() => api.listRuns(), [], { interval: 5000 });
  const [runId, setRunId] = useState(params.get("run"));
  const [busy, setBusy] = useState(false);
  const [hover, setHover] = useState(null);
  const [timeStep, setTimeStep] = useState(null);
  const [playing, setPlaying] = useState(false);

  const [show, setShow] = useState({
    slick: true, origin: true, forecast: true, vessels: true,
  });

  const [viewState, setViewState] = useState({
    longitude: 80.32, latitude: 13.05, zoom: 10.2, pitch: 0, bearing: 0,
  });

  const active = runId || runs?.find((r) => r.status === "complete")?.run_id || null;

  const { data: run } = useApi(
    () => (active ? api.getRun(active) : Promise.resolve(null)), [active],
    { interval: busy ? 2000 : 0 }
  );

  const layerData = useLayers(active);
  const stages = run?.manifest?.stages || [];
  const stageOf = (name) => stages.find((s) => s.stage === name) || {};

  const suspects = layerData.suspects?.suspects || [];
  const maxStep = useMemo(() => {
    const f = layerData.origin?.features || [];
    return f.reduce((m, x) => {
      const p = x.properties || {};
      return Math.max(m, Number(p.step_index ?? Math.abs(p.timestep_h ?? 0)) || 0);
    }, 0);
  }, [layerData.origin]);

  /* Recentre on the slick once it loads, so the map opens on the evidence
   * rather than on wherever the previous run happened to leave it. */
  useEffect(() => {
    const c = layerData.slick?.features?.[0]?.properties?.centroid;
    if (Array.isArray(c) && c.length === 2) {
      setViewState((v) => ({ ...v, longitude: c[0], latitude: c[1], zoom: 10.6 }));
    }
  }, [layerData.slick]);

  /* Hindcast playback. Steps run backwards in time, so the animation shows
   * the slick collapsing towards its origin. */
  useEffect(() => {
    if (!playing || !maxStep) return;
    const id = setInterval(() => {
      setTimeStep((t) => {
        const next = (t == null ? 0 : t) + 1;
        if (next > maxStep) { setPlaying(false); return maxStep; }
        return next;
      });
    }, 380);
    return () => clearInterval(id);
  }, [playing, maxStep]);

  async function runPipeline() {
    setBusy(true);
    try {
      const invs = await api.listInvestigations();
      const inv = invs[0] || await api.createInvestigation({
        name: "Chennai / Ennore demo",
        scene_meta_path: "contracts/mocks/scene_meta.json",
      });
      const started = await api.startRun(inv.id, { engine: "auto" });
      setRunId(started.run_id);
      const poll = setInterval(async () => {
        const r = await api.getRun(started.run_id);
        if (r.status === "complete" || r.status === "failed") {
          clearInterval(poll); setBusy(false); reloadRuns();
        }
      }, 2000);
    } catch (e) {
      console.error(e); setBusy(false);
    }
  }

  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <InvestigationMap
        viewState={viewState}
        onViewStateChange={(e) => setViewState(e.viewState)}
        layers={show}
        slick={layerData.slick}
        origin={layerData.origin}
        forecast={layerData.forecast}
        vessels={layerData.vessels}
        suspects={suspects}
        sceneMeta={layerData.scene_meta}
        timeStep={timeStep}
        onHover={setHover}
      />

      {/* ------------------------------------------------- left: controls */}
      <div className="map-overlay" style={{ top: 14, left: 14, width: 268 }}>
        <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }}
          className="panel" style={{ padding: 13 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <LayersIcon size={14} color="var(--accent)" />
            <span className="card-title">Layers</span>
          </div>

          <LayerToggle label="Detected slick" swatch={rgbaCss(RGB.slick)}
            checked={show.slick} stage={stageOf("characterise")}
            onChange={(v) => setShow((s) => ({ ...s, slick: v }))} />
          <LayerToggle label="Origin cloud" swatch={rgbaCss(RGB.origin)}
            checked={show.origin} stage={stageOf("drift_hindcast")}
            onChange={(v) => setShow((s) => ({ ...s, origin: v }))} />
          <LayerToggle label="Forecast spread" swatch={rgbaCss(RGB.forecast)}
            checked={show.forecast} stage={stageOf("drift_forecast")}
            onChange={(v) => setShow((s) => ({ ...s, forecast: v }))} />
          <LayerToggle label="AIS tracks" swatch={rgbaCss(RGB.vessel)}
            checked={show.vessels} stage={stageOf("attribution")}
            onChange={(v) => setShow((s) => ({ ...s, vessels: v }))} />

          <div style={{ borderTop: "1px solid var(--line)", margin: "11px 0", paddingTop: 11 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 7 }}>
              <Clock size={13} color="var(--ink-2)" />
              <span className="tiny muted">Hindcast playback</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button className="btn btn-sm" disabled={!maxStep}
                onClick={() => { setPlaying((p) => !p); if (timeStep == null) setTimeStep(0); }}>
                <Play size={11} />{playing ? "Pause" : "Play"}
              </button>
              <input type="range" min={0} max={maxStep || 1}
                value={timeStep ?? (maxStep || 0)}
                onChange={(e) => { setPlaying(false); setTimeStep(Number(e.target.value)); }}
                style={{ flex: 1, padding: 0 }} />
            </div>
            <div className="tiny muted mono" style={{ marginTop: 5 }}>
              {timeStep == null ? `all ${maxStep} steps` : `T−${timeStep} h of ${maxStep}`}
            </div>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, x: -16 }} animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.06 }}
          className="panel" style={{ padding: 13, marginTop: 11 }}>
          <button className="btn btn-primary" style={{ width: "100%", justifyContent: "center" }}
            onClick={runPipeline} disabled={busy}>
            {busy ? <Spinner /> : <Zap size={13} />}
            {busy ? "Running pipeline…" : "Run pipeline"}
          </button>
          <select value={active || ""} onChange={(e) => setRunId(e.target.value)}
            style={{ marginTop: 9 }}>
            {(runs || []).map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id} · {r.stages_real}/{r.stages_total} real
              </option>
            ))}
          </select>
        </motion.div>
      </div>

      {/* ----------------------------------------------- right: suspects */}
      <div className="map-overlay" style={{ top: 14, right: 14, bottom: 14, width: 330,
        display: "flex", flexDirection: "column", gap: 11 }}>
        <motion.div initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }}
          className="panel" style={{ padding: 13 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Crosshair size={14} color="var(--danger)" />
            <span className="card-title">Ranked suspects</span>
            <span style={{ marginLeft: "auto" }}>
              <ProvenanceChip status={stageOf("attribution").status} />
            </span>
          </div>
          {layerData.slick?.features?.[0] && (
            <SlickSummary p={layerData.slick.features[0].properties} />
          )}
        </motion.div>

        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 9 }}>
          <AnimatePresence>
            {suspects.map((s, i) => (
              <motion.div key={s.mmsi}
                initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                className="panel" style={{
                  padding: 12,
                  borderColor: s.rank === 1 ? "rgba(244,63,94,.45)" : undefined,
                  boxShadow: s.rank === 1 ? "0 0 26px rgba(244,63,94,.16)" : undefined,
                }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="mono" style={{
                    fontSize: 17, fontWeight: 700,
                    color: s.rank === 1 ? "var(--danger)" : "var(--ink-2)",
                  }}>#{s.rank}</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, whiteSpace: "nowrap",
                      overflow: "hidden", textOverflow: "ellipsis" }}>
                      {s.vessel_name || `MMSI ${s.mmsi}`}
                    </div>
                    <div className="tiny muted mono">{s.mmsi} · {s.vessel_type}</div>
                  </div>
                  <span className="mono" style={{
                    marginLeft: "auto", fontSize: 18, fontWeight: 700,
                    color: s.rank === 1 ? "var(--danger)" : "var(--oil)",
                  }}>{Number(s.total_score).toFixed(2)}</span>
                </div>

                <div style={{ marginTop: 9 }}>
                  {Object.entries(s.sub_scores || {}).map(([k, v]) => (
                    <FactorBar key={k} name={k} value={v} />
                  ))}
                </div>

                {s.reason && (
                  <div className="tiny" style={{
                    marginTop: 9, paddingTop: 9, borderTop: "1px solid var(--line)",
                    color: "var(--ink-1)", lineHeight: 1.5,
                  }}>{s.reason}</div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>

          {!suspects.length && (
            <div className="panel" style={{ padding: 22 }}>
              <Empty icon={<Ship size={26} color="var(--ink-3)" />}
                title="No suspects yet"
                hint="Run the pipeline to detect a slick, backtrack it to an origin, and rank the vessels that were there." />
            </div>
          )}
        </div>
      </div>

      {/* ------------------------------------------------ bottom: stages */}
      <div className="map-overlay" style={{ bottom: 14, left: 14, width: 268 }}>
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
          className="panel" style={{ padding: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
            <Waves size={13} color="var(--accent)" />
            <span className="card-title">Pipeline</span>
            {run && (
              <span className="tiny muted mono" style={{ marginLeft: "auto" }}>
                {run.seconds?.toFixed(1)}s
              </span>
            )}
          </div>
          {stages.map((s) => (
            <div key={s.stage} style={{
              display: "flex", alignItems: "center", gap: 8, padding: "4px 0",
            }}>
              <ProvenanceChip status={s.status} />
              <span className="tiny" style={{ color: "var(--ink-1)" }}>
                {s.stage.replace(/_/g, " ")}
              </span>
              <span className="tiny muted mono" style={{ marginLeft: "auto" }}>
                {s.seconds?.toFixed(1)}s
              </span>
            </div>
          ))}
          {!stages.length && <div className="tiny muted">No run selected.</div>}
        </motion.div>
      </div>

      {/* ------------------------------------------------------- hover box */}
      <AnimatePresence>
        {hover && (
          <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="map-overlay panel"
            style={{ bottom: 14, left: "50%", transform: "translateX(-50%)", padding: 11, minWidth: 210 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}>
              <Info size={12} color="var(--accent)" />
              <span style={{ fontSize: 12, fontWeight: 600 }}>{hover.title}</span>
            </div>
            {hover.rows.map(([k, v]) => (
              <div key={k} style={{ display: "flex", gap: 12, fontSize: 11, padding: "1px 0" }}>
                <span className="muted" style={{ width: 74 }}>{k}</span>
                <span className="mono">{String(v)}</span>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/* --------------------------------------------------------------- helpers */

function LayerToggle({ label, swatch, checked, onChange, stage }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
      <label className="switch" style={{ flex: 1 }} onClick={() => onChange(!checked)}>
        <span className={`switch-track ${checked ? "on" : ""}`}>
          <span className="switch-knob" />
        </span>
        <span className="legend-swatch" style={{ background: swatch }} />
        <span className="switch-label">{label}</span>
      </label>
      {stage?.status && <ProvenanceChip status={stage.status} />}
    </div>
  );
}

function SlickSummary({ p }) {
  const cells = [
    ["Area", `${Number(p.area_km2).toFixed(1)} km²`],
    ["Length", `${((p.major_axis_m || 0) / 1000).toFixed(1)} km`],
    ["Bearing", `${Math.round(p.orientation_deg)}°`],
    ["Damping", p.damping_ratio == null ? "—" : `${Number(p.damping_ratio).toFixed(1)} dB`],
  ];
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 11 }}>
      {cells.map(([k, v]) => (
        <div key={k}>
          <div className="stat-label">{k}</div>
          <div className="mono" style={{ fontSize: 15, fontWeight: 600, color: "var(--oil)" }}>{v}</div>
        </div>
      ))}
    </div>
  );
}

/** Load every contract layer for a run. Missing layers resolve to null rather
 *  than rejecting, because a partial run is a normal state here -- the point
 *  of the fallback design is that some stages can be absent. */
function useLayers(runId) {
  const [data, setData] = useState({});
  useEffect(() => {
    if (!runId) { setData({}); return; }
    let alive = true;
    const names = ["scene_meta", "slick", "origin_cloud", "forecast", "suspects"];
    Promise.all([
      ...names.map((n) => api.layer(runId, n).catch(() => null)),
      // Vessels come from a separate endpoint: the contract stores them as
      // parquet, which a browser cannot read, so the server converts to
      // GeoJSON on the way out rather than changing the contract.
      api.vesselsGeojson(runId).catch(() => null),
    ]).then((vals) => {
      if (!alive) return;
      setData({
        scene_meta: vals[0], slick: vals[1], origin: vals[2],
        forecast: vals[3], suspects: vals[4], vessels: vals[5],
      });
    });
    return () => { alive = false; };
  }, [runId]);
  return data;
}
