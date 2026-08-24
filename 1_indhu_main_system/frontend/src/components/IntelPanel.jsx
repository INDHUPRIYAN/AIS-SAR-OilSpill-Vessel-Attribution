/* Right-hand intelligence panel: context for the current replay step, the
 * evidence dossier for the selected vessel, and the incident summary.
 *
 * Language discipline: detected / estimated / probable / reconstructed /
 * predicted / highest attribution likelihood. Never "confirmed culprit".
 */

import { AnimatePresence, motion } from "framer-motion";
import {
  Anchor, Crosshair, Radar, Ship, Waves, Wind, Filter, Scale, FileCheck,
  Satellite, MapPin, AlertTriangle,
} from "lucide-react";

import { STEPS } from "../lib/replay";
import {
  fmtLat, fmtLon, fmtUtc, fmtDur, clamp01,
} from "../lib/replay";

const STEP_ICONS = {
  location: MapPin, radar: Radar, ais: Ship, detect: Satellite, env: Wind,
  drift: Waves, hindcast: Waves, origin: Crosshair, filter: Filter,
  attribution: Scale, evidence: FileCheck,
};

const Row = ({ k, v, tone }) => (
  <div className="ip-row">
    <span className="ip-k">{k}</span>
    <span className={`ip-v mono ${tone ?? ""}`}>{v}</span>
  </div>
);

const fade = {
  initial: { opacity: 0, y: 10 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.28 },
};

export default function IntelPanel({ bundle: b, frame, effects, selectedMmsi,
                                     onSelect }) {
  if (!b) return null;
  const step = STEPS[frame.stepIdx];
  const Icon = STEP_ICONS[step.id] ?? MapPin;
  const sel = b.tracks.find((t) => t.mmsi === selectedMmsi)
    ?? b.tracks.find((t) => t.rank === 1);
  const selSuspect = b.suspectsList.find((s) => s.mmsi === sel?.mmsi);

  return (
    <div className="intel">
      {/* ------------------------------------------------ current stage --- */}
      <div className="intel-step panel">
        <div className="intel-step-head">
          <Icon size={15} color="var(--accent)" />
          <div>
            <div className="intel-step-n mono">
              {String(step.n).padStart(2, "0")} / {STEPS.length}
            </div>
            <div className="intel-step-title">{step.title}</div>
          </div>
        </div>
        <div className="intel-step-blurb">{step.blurb(b)}</div>
        <div className="intel-progress">
          <div className="intel-progress-fill"
            style={{ width: `${clamp01(frame.stepT) * 100}%` }} />
        </div>
      </div>

      {/* --------------------------------------------- per-step content --- */}
      <div className="intel-body">
        <AnimatePresence mode="wait">
          <motion.div key={step.id} {...fade}>
            <StepContent b={b} step={step} frame={frame} effects={effects}
              sel={sel} selSuspect={selSuspect} onSelect={onSelect} />
          </motion.div>
        </AnimatePresence>
      </div>

      {/* ------------------------------------------------------ summary --- */}
      <div className="intel-summary panel">
        <div className="ip-section">INCIDENT SUMMARY</div>
        <Row k="Incident" v={b.runMeta?.run_id ?? "—"} />
        <Row k="Detected" v={b.sceneMeta?.acquired_utc?.replace("T", " ").slice(0, 16) + "Z"} />
        <Row k="Spill area" v={`${b.slickProps.area_km2 ?? "—"} km² est.`} />
        <Row k="Detection conf." v={pct(b.slickProps.confidence)} />
        <Row k="Origin conf." v={b.originEllipse?.properties?.confidence_level ?? "—"} />
        <Row k="Vessels analysed" v={b.vesselCount} />
        <Row k="Candidates" v={b.candidateCount} />
        <Row k="Top likelihood" v={pct(b.top?.total_score)} tone="danger" />
        {b.suspects?.source === "synthetic" && (
          <div className="ip-flag">
            <AlertTriangle size={11} />
            AIS reconstruction is synthetic — labelled throughout
          </div>
        )}
      </div>
    </div>
  );
}

/* ======================================================================== */

function StepContent({ b, step, frame, effects, sel, selSuspect, onSelect }) {
  switch (step.id) {
    case "location": return (
      <div className="panel ip-card">
        <div className="ip-section">INVESTIGATION ZONE</div>
        <Row k="Scene" v={b.sceneMeta?.scene_id} />
        <Row k="Centre" v={`${fmtLat(b.sceneCenter[1])}  ${fmtLon(b.sceneCenter[0])}`} />
        <Row k="Acquired" v={b.sceneMeta?.acquired_utc?.replace("T", " ").slice(0, 16) + "Z"} />
        <Row k="Sensor" v={`Sentinel-1 · ${b.sceneMeta?.polarisation ?? "VV"}`} />
        <Row k="Provider" v={b.sceneMeta?.provider_used} />
      </div>);

    case "radar": return (
      <div className="panel ip-card">
        <div className="ip-section">SCAN TELEMETRY</div>
        <Row k="Transponders" v={b.vesselCount} />
        <Row k="AIS window" v={`${fmtUtc(b.aisStart)} → acq.`} />
        <Row k="Source" v={b.suspects?.source === "synthetic"
          ? "synthetic reconstruction" : b.suspects?.source} />
        <div className="ip-note">
          Blips light as the sweep passes each vessel's reconstructed position
          at acquisition time.
        </div>
      </div>);

    case "ais": return (
      <div className="panel ip-card">
        <div className="ip-section">TRAFFIC REPLAY</div>
        <Row k="Window" v={fmtDur(b.aisWindowH)} />
        <Row k="Vessels" v={b.vesselCount} />
        <Row k="Fixes" v={b.tracks.reduce((a, t) => a + t.path.length, 0).toLocaleString()} />
        <div className="ip-note">
          Positions interpolate the recorded fixes — distances shown for any
          vessel are haversine sums along its actual track.
        </div>
      </div>);

    case "detect": {
      const conf = effects.slickAlpha > 0.5 ? b.slickProps.confidence : null;
      return (
        <div className="panel ip-card">
          <div className="ip-section">DETECTION</div>
          <Row k="Engine" v={b.detectEngine === "ml"
            ? "YOLO screen + U-Net segment" : "threshold fallback"} />
          {conf != null ? (<>
            <div className="ip-hero">
              <div className="ip-hero-big oil">{pct(conf)}</div>
              <div className="ip-hero-sub">detection confidence</div>
            </div>
            <Row k="Est. area" v={`${b.slickProps.area_km2} km²`} />
            <Row k="Perimeter" v={`${b.slickProps.perimeter_km} km`} />
            <Row k="Major axis" v={`${(b.slickProps.major_axis_m / 1000).toFixed(1)} km @ ${b.slickProps.orientation_deg}°`} />
            <Row k="Damping" v={`${b.slickProps.damping_ratio} dB`} />
          </>) : (
            <div className="ip-note">Scanning Sentinel-1 scene…</div>
          )}
        </div>);
    }

    case "env": return (
      <div className="panel ip-card">
        <div className="ip-section">FORCING FIELDS</div>
        <div className="ip-legend-line"><span className="sw sw-wind" />Wind
          <span className="mono ip-vv">{b.wind
            ? `${b.wind.mean_speed} m/s mean · ${b.wind.file}` : "unavailable"}</span></div>
        <div className="ip-legend-line"><span className="sw sw-cur" />Current
          <span className="mono ip-vv">{b.currents
            ? `${b.currents.mean_speed} m/s mean · ${b.currents.file}` : "unavailable"}</span></div>
        <div className="ip-note">
          These are the grids the drift engine consumed — resolved from the
          same fallback chain, not decorative animation.
        </div>
      </div>);

    case "drift": return (
      <div className="panel ip-card">
        <div className="ip-section">DRIFT RECONSTRUCTION</div>
        <Row k="Particles" v={b.nParticles?.toLocaleString()} />
        <Row k="Engine" v={b.driftEngine} />
        <Row k="Timestep" v="60 min" />
        <Row k="Forcing" v={[b.currents && "currents", b.wind && "wind"]
          .filter(Boolean).join(" + ") || "—"} />
      </div>);

    case "hindcast": {
      const back = Math.round((b.t0 - frame.simT) / 3.6e6);
      return (
        <div className="panel ip-card">
          <div className="ip-section">RECONSTRUCTING SPILL ORIGIN</div>
          <div className="ip-hero">
            <div className="ip-hero-big accent mono">−{back}h</div>
            <div className="ip-hero-sub">running drift backwards</div>
          </div>
          <Row k="Backtrack" v={`${b.backtrackH} h`} />
          <Row k="Particles" v={b.nParticles?.toLocaleString()} />
        </div>);
    }

    case "origin": return (
      <div className="panel ip-card">
        <div className="ip-section">PROBABLE ORIGIN IDENTIFIED</div>
        <div className="ip-hero">
          <div className="ip-hero-big origin mono" style={{ fontSize: 17 }}>
            {fmtLat(b.originCenter[1])}<br />{fmtLon(b.originCenter[0])}
          </div>
        </div>
        <Row k="Time window" v={`${fmtUtc(b.originStart)} → ${fmtUtc(b.originEnd).slice(11)}`} />
        <Row k="Uncertainty" v={`± ${b.uncertaintyKm?.toFixed(1) ?? "—"} km`} />
        <Row k="Confidence" v={b.originEllipse?.properties?.confidence_level ?? "—"} />
        <div className="ip-note">
          The uncertainty shown is the model's own confidence ellipse — the
          system does not claim precision it does not have.
        </div>
      </div>);

    case "filter": {
      const stage = effects.filterStageF ?? 0;
      return (
        <div className="panel ip-card">
          <div className="ip-section">GATING {b.vesselCount} VESSELS</div>
          {b.filterStages.map((s, i) => {
            const active = stage >= i;
            const now = Math.floor(stage) === i;
            return (
              <div key={s.label}
                className={`funnel ${active ? "on" : ""} ${now ? "now" : ""}`}>
                <span className="funnel-label">{s.label}</span>
                {s.removed > 0 && active &&
                  <span className="funnel-removed mono">−{s.removed}</span>}
                <span className="funnel-count mono">
                  {active ? s.remaining : "·"}
                </span>
              </div>);
          })}
          <div className="ip-note">
            Eliminated vessels stay on the map, dimmed — hover one for the
            exact reason it was excluded.
          </div>
        </div>);
    }

    case "attribution": return (
      <div className="panel ip-card">
        <div className="ip-section">CANDIDATE RANKING</div>
        {b.suspectsList.map((s, i) => {
          const reveal = clamp01((effects.rankReveal ?? 1) * b.suspectsList.length - i);
          return (
            <button key={s.mmsi}
              className={`rankrow ${s.rank === 1 ? "top" : ""}`}
              style={{ opacity: reveal, transform: `translateY(${(1 - reveal) * 8}px)` }}
              onClick={() => onSelect?.(s.mmsi)}>
              <span className="rank-n mono">{String(s.rank).padStart(2, "0")}</span>
              <span className="rank-name">{s.vessel_name ?? s.mmsi}</span>
              <span className="rank-bar">
                <span className="rank-fill"
                  style={{ width: `${s.total_score * 100 * reveal}%` }} />
              </span>
              <span className="rank-pct mono">{pct(s.total_score)}</span>
            </button>);
        })}
      </div>);

    case "evidence": return <Evidence b={b} sel={sel} suspect={selSuspect} />;
    default: return null;
  }
}

/* ------------------------------------------------------------ evidence --- */

function Evidence({ b, sel, suspect }) {
  if (!sel || !suspect) return null;
  const ss = suspect.sub_scores ?? {};
  const checks = buildChecks(suspect, ss);
  return (
    <div className="panel ip-card">
      <div className="ip-topline">
        <Anchor size={13} color="var(--danger)" />
        <span>HIGHEST ATTRIBUTION LIKELIHOOD</span>
      </div>
      <div className="ip-hero">
        <div className="ip-hero-big danger">{pct(suspect.total_score)}</div>
        <div className="ip-hero-name">{suspect.vessel_name ?? sel.mmsi}</div>
      </div>
      <Row k="MMSI" v={sel.mmsi} />
      <Row k="Type" v={suspect.vessel_type ?? "—"} />
      <Row k="Distance travelled" v={`${sel.distanceKm} km`} />
      <Row k="Travel duration" v={fmtDur(sel.durationH)} />
      <Row k="Average speed" v={`${sel.avgKn ?? "—"} kn`} />
      <Row k="Maximum speed" v={`${sel.maxKn ?? "—"} kn`} />

      <div className="ip-section" style={{ marginTop: 12 }}>WHY THIS VESSEL?</div>
      {checks.map((c) => (
        <div key={c} className="why-row">✓ {c}</div>
      ))}

      <div className="ip-section" style={{ marginTop: 12 }}>SCORE FACTORS</div>
      {Object.entries(ss).map(([k, v]) => (
        <div key={k} className="factor">
          <span className="factor-name">{k.replace("_", " ")}</span>
          <span className="factor-track">
            <span className="factor-fill" style={{
              width: `${v * 100}%`,
              background: v > 0.65 ? "var(--danger)" : v > 0.35 ? "var(--warn)" : "var(--ink-3)",
            }} />
          </span>
          <span className="factor-val">{(v * 100).toFixed(0)}</span>
        </div>
      ))}
      <div className="ip-note">{suspect.reason}</div>
    </div>
  );
}

/** Turn the engine's sub-scores into the evidence checklist. Each line is
 *  only shown when the underlying factor actually supports it. */
function buildChecks(s, ss) {
  const out = [];
  if (ss.proximity > 0.4) out.push("Passed through the probable origin region");
  if (ss.temporal > 0.4) out.push("Present within the estimated origin time window");
  if (ss.trajectory > 0.4) out.push("Trajectory compatible with the reconstructed origin");
  if (ss.behaviour > 0.4) out.push("Behaviour anomaly detected (speed / course change)");
  if (ss.ais_gap > 0.4) out.push("AIS transmission gap near the origin window");
  if (!out.length) out.push("Ranked on weighted factors — see score breakdown");
  return out;
}

const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(v >= 0.995 ? 0 : 1)}%`);
