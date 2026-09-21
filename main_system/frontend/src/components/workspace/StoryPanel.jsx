/* The workspace sidebar in the shape of Incident Replay's: a stage header that
 * says where you are and what this step does, ONE focused card for the stage
 * (a big number, a few rows, a note), and a fixed incident summary.
 *
 * Replay reads a prepared bundle; this reads the workspace's live context, so
 * every row is the run's own artefact or a backend answer: scene_meta,
 * detect_response, slick.geojson, origin_cloud metadata, /runs/{id}/funnel,
 * suspects.json. A value the run does not carry is written "not recorded",
 * never filled in. The attribution total is a score, never a percentage.
 *
 * The detailed panels the workspace always had stay below this, unchanged.
 */
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Anchor, FileCheck, Filter, MapPin, Radar, Satellite, Scale, Ship, Waves, Wind } from "lucide-react";

import { STAGES, STAGE_INDEX } from "../../lib/stages";
import { sceneFact } from "../../lib/sceneName";
import { fmtLat, fmtLon, fmtUtc } from "../../lib/replay";
import { originEstimate } from "../../lib/drift";
import { useOptionalTime } from "../maps/TimeContext";

const NR = "not recorded";
const pct = (v) => (v == null ? NR : `${(Number(v) * 100).toFixed(1)}%`);
const sc = (v) => (v == null ? "—" : Number(v).toFixed(2));
const n2 = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? NR : Number(v).toFixed(d));
const fade = { initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 }, transition: { duration: 0.28 } };

const ICONS = { acquisition: MapPin, scene: MapPin, preprocess: Satellite, tiling: Radar, detection: Satellite, validation: Satellite,
  geometry: Waves, drift: Waves, ais: Filter, attribution: Scale, evidence: FileCheck, report: FileCheck };

/* The header names the step in plain words. (lib/stages titles follow the
 * original frame designs, where two different stages are both "Validated Slick".) */
const TITLES = { acquisition: "Scene acquisition", scene: "Scene under investigation", preprocess: "Radiometric calibration",
  tiling: "Georeferencing & tiling", detection: "Oil-slick detection", validation: "Detection validation", geometry: "Slick characterisation",
  drift: "Drift hindcast & forecast", ais: "Vessel traffic & filtering", attribution: "Vessel attribution", evidence: "Evidence record", report: "Incident report" };

const Row = ({ k, v, tone, testid }) => (
  <div className="sp-row"><span className="sp-k">{k}</span><span className={`sp-v mono ${tone || ""}`} data-testid={testid}>{v ?? NR}</span></div>
);
const Hero = ({ big, sub, tone = "accent", name }) => (
  <div className="sp-hero"><div className={`sp-hero-big mono ${tone}`}>{big}</div>{name && <div className="sp-hero-name">{name}</div>}{sub && <div className="sp-hero-sub">{sub}</div>}</div>
);

/** What this step does, in one sentence built from the run's own numbers. */
function blurb(stageId, c) {
  const p = c.slickP, s = c.suspects, f = c.funnel;
  switch (stageId) {
    case "acquisition": return "Choose a SAR scene: search the catalogues, pick one held on this host, or upload a raster.";
    case "scene": return `The Sentinel-1 scene under investigation${c.meta?.acquired_utc ? `, acquired ${fmtUtc(Date.parse(c.meta.acquired_utc))}` : ""}.`;
    case "preprocess": return "Radiometric calibration to sigma0 dB: the form the detector was trained on.";
    case "tiling": return "The scene is georeferenced and cut into tiles so the detector can scan it piece by piece.";
    case "detection": return "A screening model proposes oil regions; a segmentation model outlines them pixel by pixel.";
    case "validation": return "Each proposed region is checked: oil, or a look-alike such as low wind or biogenic film.";
    case "geometry": return p ? `The confirmed slick measures ${n2(p.area_km2)} km². Its shape and damping give a rough age.` : "Shape, size and age of the confirmed slick.";
    case "drift": return "Thousands of particles are run backwards through wind and current to where the oil came from, then forwards to where it goes.";
    case "ais": return f?.indexed != null ? `${f.indexed} vessels were near. Three gates remove the ones that could not have been at the origin.` : "Vessel tracks near the origin are gated by place, time and course.";
    case "attribution": return s?.length ? `${s.length} vessel${s.length === 1 ? "" : "s"} passed every gate and ${s.length === 1 ? "is" : "are"} ranked by weighted evidence.` : "Vessels that pass every gate are ranked by weighted evidence.";
    case "evidence": return "The evidence behind the highest-ranked candidate, factor by factor.";
    case "report": return "The investigation record: what was found, from what data, with what confidence.";
    default: return "";
  }
}

function buildChecks(ss) {
  const out = [];
  if (ss.proximity > 0.4) out.push("Passed through the probable origin cloud");
  if (ss.temporal > 0.4) out.push("Present within the estimated origin time window");
  if (ss.trajectory > 0.4) out.push("Course compatible with the slick axis");
  if (ss.behaviour > 0.4) out.push("Behaviour anomaly recorded (speed or course change)");
  if (ss.ais_gap > 0.4) out.push("AIS transmission gap overlapping the origin window");
  if (!out.length) out.push("Ranked on weighted factors: see the breakdown below");
  return out;
}

function Evidence({ s, synthetic }) {
  if (!s) return null;
  const ss = s.sub_scores || {};
  return (
    <div className="panel sp-card" data-testid="sp-evidence">
      <div className="sp-topline"><Anchor size={13} /> <span>RANK #{s.rank} · WEIGHTED EVIDENCE SCORE</span></div>
      <Hero big={sc(s.total_score)} name={s.vessel_name || `MMSI ${s.mmsi}`} sub="a score, not a probability" tone="accent" />
      <Row k="MMSI" v={s.mmsi} />
      <Row k="Type" v={s.vessel_type} />
      <Row k="To origin region" v={s.evidence?.closest_approach_km != null ? `${n2(s.evidence.closest_approach_km, 1)} km` : null} />
      <Row k="In origin window" v={s.evidence?.time_in_origin_window_min != null ? `${Math.round(s.evidence.time_in_origin_window_min)} min` : null} />
      <Row k="AIS gap" v={s.evidence?.ais_gap_minutes != null ? `${Math.round(s.evidence.ais_gap_minutes)} min` : null} />
      <div className="sp-section">WHY THIS VESSEL?</div>
      {buildChecks(ss).map((c) => <div key={c} className="sp-why">✓ {c}</div>)}
      <div className="sp-section">SCORE FACTORS</div>
      {Object.entries(ss).map(([k, v], i) => (
        <div key={k} className="sp-factor">
          <span className="sp-factor-n">{k.replace(/_/g, " ")}</span>
          <span className="sp-factor-t"><motion.span className="sp-factor-f" initial={{ width: 0 }} animate={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }}
            transition={{ delay: 0.15 + i * 0.08, duration: 0.5 }} style={{ background: v > 0.65 ? "var(--accent)" : v > 0.35 ? "#d1a054" : "var(--ink-3, #55657c)" }} /></span>
          <span className="sp-factor-v mono">{sc(v)}</span>
        </div>
      ))}
      {s.reason && <div className="sp-note">{s.reason}</div>}
      {synthetic && <div className="sp-flag"><AlertTriangle size={11} /> Simulated AIS: this ranks the method, it names no real vessel.</div>}
    </div>
  );
}

function Ranking({ list, selectedMmsi, onSelect }) {
  return (
    <div className="panel sp-card" data-testid="sp-ranking">
      <div className="sp-section first">CANDIDATE RANKING</div>
      {list.map((s, i) => (
        <motion.button key={s.mmsi} type="button" className={`sp-rankrow ${s.rank === 1 ? "top" : ""} ${String(s.mmsi) === String(selectedMmsi) ? "sel" : ""}`}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.12 }}
          onClick={() => onSelect?.(s.mmsi)} data-testid={`sp-rank-${s.rank}`}>
          <span className="sp-rank-n mono">{String(s.rank).padStart(2, "0")}</span>
          <span className="sp-rank-name">{s.vessel_name || `MMSI ${s.mmsi}`}</span>
          <span className="sp-rank-bar"><motion.span className="sp-rank-fill" initial={{ width: 0 }}
            animate={{ width: `${Math.max(0, Math.min(1, Number(s.total_score) || 0)) * 100}%` }} transition={{ delay: 0.25 + i * 0.12, duration: 0.6 }} /></span>
          <span className="sp-rank-v mono">{sc(s.total_score)}</span>
        </motion.button>
      ))}
    </div>
  );
}

function Funnel({ f }) {
  const rows = [["All vessels indexed", f.indexed], ["Inside the origin region", f.after_spatial], ["Inside the time window", f.after_temporal], ["On a compatible course", f.after_trajectory]]
    .filter(([, v]) => v != null);
  return (
    <div className="panel sp-card" data-testid="sp-funnel">
      <div className="sp-section first">GATING {f.indexed ?? "—"} VESSELS</div>
      {rows.map(([label, v], i) => {
        const removed = i > 0 ? rows[i - 1][1] - v : 0;
        return (
          <motion.div key={label} className={`sp-funnel ${i === rows.length - 1 ? "now" : "on"}`} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 + i * 0.18 }}>
            <span className="sp-funnel-l">{label}</span>
            {removed > 0 && <span className="sp-funnel-r mono">−{removed}</span>}
            <span className="sp-funnel-c mono">{v}</span>
          </motion.div>
        );
      })}
      {f.reasons_histogram && Object.keys(f.reasons_histogram).length > 0 && <>
        <div className="sp-section">WHY VESSELS WERE REMOVED</div>
        {Object.entries(f.reasons_histogram).map(([k, v]) => <Row key={k} k={k} v={v} />)}
      </>}
      <div className="sp-note">{f.gates_are_sequential === false ? "The gates are evaluated together; a vessel may fail more than one. " : ""}Removed vessels can be switched back on in the map layers.</div>
    </div>
  );
}

function StageCard({ stageId, sub, c, time }) {
  const { meta, slickP, slick, detect, est, om, forecast, funnel, suspects, vessels } = c;
  switch (stageId) {
    case "acquisition": case "scene": case "preprocess": case "tiling": {
      const b = meta?.bbox;
      return (
        <div className="panel sp-card">
          <div className="sp-section first">INVESTIGATION ZONE</div>
          <Row k="Scene" v={meta?.scene_id} />
          <Row k="Centre" v={b ? `${fmtLat((b[1] + b[3]) / 2)}  ${fmtLon((b[0] + b[2]) / 2)}` : null} />
          <Row k="Acquired" v={meta?.acquired_utc ? fmtUtc(Date.parse(meta.acquired_utc)) : null} />
          <Row k="Sensor" v={meta ? `${sceneFact(meta, "mission")} · ${meta.polarisation || NR}` : null} />
          <Row k="Pixel spacing" v={meta?.pixel_spacing_m != null ? `${meta.pixel_spacing_m} m` : null} />
          <Row k="Provider" v={meta?.provider_used} />
          {stageId === "tiling" && <Row k="Tiles" v={c.tileCount} />}
          {!meta && <div className="sp-note">No scene is loaded yet.</div>}
        </div>
      );
    }
    case "detection": case "validation": {
      const oil = (detect?.candidates || []).filter((x) => x.class === "oil").length;
      const look = (detect?.candidates || []).filter((x) => x.class === "lookalike").length;
      const conf = detect?.confidence ?? slickP?.confidence;
      return (
        <div className="panel sp-card">
          <div className="sp-section first">DETECTION</div>
          <Row k="Engine" v={detect ? (detect.engine === "ml" ? "screen + segment (ML)" : "threshold fallback") : null} />
          {conf != null ? <Hero big={pct(conf)} sub="detection confidence" tone="oil" /> : <div className="sp-note">No detection result yet.</div>}
          <Row k="Est. area" v={slickP?.area_km2 != null ? `${n2(slickP.area_km2)} km²` : null} />
          <Row k="Screen regions" v={detect ? `${oil} oil · ${look} look-alike` : null} />
          <Row k="Model" v={detect?.model_version} />
        </div>
      );
    }
    case "geometry": return (
      <div className="panel sp-card">
        <div className="sp-section first">SLICK GEOMETRY</div>
        {slickP ? <Hero big={`${n2(slickP.area_km2)} km²`} sub={slick?.features?.length > 1 ? `largest of ${slick.features.length} regions` : "slick area"} tone="oil" /> : <div className="sp-note">No slick was characterised.</div>}
        <Row k="Major axis" v={slickP?.major_axis_m != null ? `${n2(slickP.major_axis_m / 1000)} km @ ${n2(slickP.orientation_deg, 0)}°` : null} />
        <Row k="Minor axis" v={slickP?.minor_axis_m != null ? `${n2(slickP.minor_axis_m / 1000)} km` : null} />
        <Row k="Perimeter" v={slickP?.perimeter_km != null ? `${n2(slickP.perimeter_km)} km` : null} />
        <Row k="Damping" v={slickP?.damping_ratio != null ? `${n2(slickP.damping_ratio, 1)} dB` : null} />
        <Row k="Estimated age" v={slickP?.age_hours_estimate != null ? `≈ ${n2(slickP.age_hours_estimate, 1)} h · ${slickP.age_confidence_label || "low"} confidence` : null} />
      </div>
    );
    case "drift": {
      if (sub === "wind" || sub === "currents") {
        const w = om?.forcing?.wind, cu = om?.forcing?.currents;
        return (
          <div className="panel sp-card">
            <div className="sp-section first">FORCING FIELDS</div>
            <div className="sp-legend"><Wind size={12} /> Wind<span className="mono">{w?.provider || NR}</span></div>
            <div className="sp-legend"><Waves size={12} /> Current<span className="mono">{cu?.provider || NR}</span></div>
            <div className="sp-note">These are the grids the drift engine consumed for this run, from the provider fallback chain.</div>
          </div>
        );
      }
      if (sub === "forecast") {
        const hs = [...new Set((forecast?.features || []).map((f) => f.properties?.horizon_h).filter((h) => h != null))].sort((a, b) => a - b);
        return (
          <div className="panel sp-card">
            <div className="sp-section first">WHERE THE OIL GOES</div>
            {hs.length ? <Hero big={`+${hs[hs.length - 1]} h`} sub="forecast horizon" tone="forecast" /> : <div className="sp-note">No forecast was produced.</div>}
            {hs.map((h) => {
              const fs = forecast.features.filter((f) => f.properties.horizon_h === h);
              const a = (lv) => fs.find((f) => Math.abs((f.properties.confidence_level ?? 0) - lv) < 0.01)?.properties?.area_km2;
              return <Row key={h} k={`+${h} h`} v={`50% ${a(0.5) != null ? n2(a(0.5), 1) : "—"} · 90% ${a(0.9) != null ? n2(a(0.9), 1) : "—"} km²`} />;
            })}
          </div>
        );
      }
      const back = time?.t != null && c.sceneT0 ? Math.max(0, Math.round((c.sceneT0 - time.t) / 3.6e6)) : null;
      return (<>
        <div className="panel sp-card">
          <div className="sp-section first">RECONSTRUCTING SPILL ORIGIN</div>
          <Hero big={back != null && back > 0 ? `−${back} h` : "T 0"} sub={back ? "clock position before acquisition" : "at acquisition"} tone="hind" />
          <Row k="Backtrack" v={om?.backtrack_hours != null ? `${om.backtrack_hours} h` : null} />
          <Row k="Particles" v={om?.n_particles != null ? Number(om.n_particles).toLocaleString() : null} />
          <Row k="Timestep" v={om?.timestep_minutes != null ? `${om.timestep_minutes} min` : null} />
        </div>
        {est && (
          <div className="panel sp-card" data-testid="sp-origin">
            <div className="sp-section first">PROBABLE ORIGIN IDENTIFIED</div>
            <Hero big={<>{fmtLat(est.center[1])}<br />{fmtLon(est.center[0])}</>} tone="origin small" />
            <Row k="Time window" v={om?.origin_window_start_utc ? `${fmtUtc(Date.parse(om.origin_window_start_utc))} → ${String(om.origin_window_end_utc || "").slice(11, 16)}` : null} />
            <Row k="Uncertainty" v={`± ${n2(est.radiusKm, est.radiusKm < 1 ? 2 : 1)} km`} />
            <div className="sp-note">The uncertainty is the model's own: the system does not claim precision it does not have.</div>
          </div>
        )}
      </>);
    }
    case "ais": {
      if (sub === "ranking" && suspects.length) return <Ranking list={suspects} selectedMmsi={c.selectedMmsi} onSelect={c.onSelect} />;
      const fixes = (vessels?.features || []).reduce((a, f) => a + (f.geometry?.coordinates?.length || 0), 0);
      return (<>
        <div className="panel sp-card">
          <div className="sp-section first">TRAFFIC</div>
          <Row k="Vessel tracks" v={vessels?.features?.length} />
          <Row k="Recorded fixes" v={fixes ? fixes.toLocaleString() : null} />
          <Row k="AIS source" v={c.aisSource} tone={c.synthetic ? "warn" : ""} />
        </div>
        {funnel?.indexed != null && <Funnel f={funnel} />}
      </>);
    }
    case "attribution": return (<>
      {suspects.length ? <Ranking list={suspects} selectedMmsi={c.selectedMmsi} onSelect={c.onSelect} /> : <div className="panel sp-card"><div className="sp-note">No vessel passed the gates for this origin window.</div></div>}
      <Evidence s={c.shown} synthetic={c.synthetic} />
    </>);
    case "evidence": case "report": return <Evidence s={c.shown} synthetic={c.synthetic} />;
    default: return null;
  }
}

export function StoryHead({ ctx }) {
  const time = useOptionalTime();
  const stageId = ctx.stage;
  const idx = STAGE_INDEX[stageId] ?? 0;
  const st = STAGES[idx];
  const Icon = ICONS[stageId] || MapPin;
  const L = ctx.layers || {};
  const suspects = L.suspects?.suspects || [];
  const aisSource = ctx.runRow?.manifest?.ais?.data_source || L.suspects?.source || null;
  const c = {
    meta: L.scene_meta, slick: L.slick, slickP: L.slick?.features?.[0]?.properties, detect: L.detect,
    est: originEstimate(L.origin_cloud), om: L.origin_cloud?.metadata, forecast: L.forecast, funnel: ctx.funnel,
    suspects, vessels: L.vessels, sceneT0: ctx.sceneT0, tileCount: ctx.tileGrid?.n ?? null,
    selectedMmsi: ctx.selectedMmsi, onSelect: ctx.onSelectMmsi, aisSource, synthetic: /synthetic|mock/i.test(String(aisSource || "")),
    shown: suspects.find((s) => String(s.mmsi) === String(ctx.selectedMmsi)) || suspects[0] || null,
  };
  const state = ctx.judged?.[stageId]?.state;
  const progress = state === "done" ? 1 : state === "running" ? 0.5 : state === "failed" ? 1 : 0.08;
  return (
    <div className="sp" data-testid="story-panel">
      <div className="panel sp-step">
        <div className="sp-step-head">
          <Icon size={15} color="var(--accent)" />
          <div>
            <div className="sp-step-n mono">{String(idx + 1).padStart(2, "0")} / {STAGES.length}</div>
            <div className="sp-step-title">{TITLES[stageId] || st?.title || stageId}</div>
          </div>
        </div>
        <div className="sp-step-blurb">{blurb(stageId, c)}</div>
        <div className="sp-progress"><div className={`sp-progress-f ${state === "failed" ? "bad" : ""}`} style={{ width: `${progress * 100}%` }} /></div>
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={`${stageId}:${ctx.sub || ""}`} {...fade} className="sp-body">
          <StageCard stageId={stageId} sub={ctx.sub} c={c} time={time} />
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

export function StorySummary({ ctx }) {
  const L = ctx.layers || {};
  const p = L.slick?.features?.[0]?.properties;
  const est = originEstimate(L.origin_cloud);
  const suspects = L.suspects?.suspects || [];
  const f = ctx.funnel;
  const synthetic = /synthetic|mock/i.test(String(ctx.runRow?.manifest?.ais?.data_source || L.suspects?.source || ""));
  if (!L.scene_meta && !ctx.runId) return null;
  return (
    <div className="panel sp-summary" data-testid="story-summary">
      <div className="sp-section first"><Ship size={11} /> INCIDENT SUMMARY</div>
      <Row k="Run" v={ctx.runId} />
      <Row k="Detected" v={L.scene_meta?.acquired_utc ? fmtUtc(Date.parse(L.scene_meta.acquired_utc)) : null} />
      <Row k="Spill area" v={p?.area_km2 != null ? `${n2(p.area_km2)} km² est.` : null} />
      <Row k="Detection conf." v={(L.detect?.confidence ?? p?.confidence) != null ? pct(L.detect?.confidence ?? p?.confidence) : null} />
      <Row k="Origin uncertainty" v={est ? `± ${n2(est.radiusKm, est.radiusKm < 1 ? 2 : 1)} km` : null} />
      <Row k="Vessels considered" v={f?.indexed ?? L.suspects?.total_vessels_considered} />
      <Row k="Ranked" v={suspects.length || (L.suspects ? 0 : null)} />
      <Row k="Rank #1 score" v={suspects[0] ? sc(suspects[0].total_score) : null} tone="accent" />
      {synthetic && <div className="sp-flag"><AlertTriangle size={11} /> AIS is simulated for this run: labelled throughout</div>}
    </div>
  );
}
