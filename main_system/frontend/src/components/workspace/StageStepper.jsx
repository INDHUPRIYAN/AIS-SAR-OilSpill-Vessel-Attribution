/* The investigation pipeline, as a bar.
 *
 * The product's workflow is longer than the pipeline's stage list, and the two
 * are NOT the same thing. Six stages are actually executed and reported by the
 * server (`detect`, `characterise`, `drift_hindcast`, `drift_forecast`, the
 * AIS substep, `attribution`). The other steps an analyst thinks in -- the
 * scene arriving, the origin being identified, vessels being gated, evidence
 * being sealed -- are OUTPUTS of those stages, not separate executions.
 *
 * So the bar renders two kinds of node and never blurs them:
 *
 *   EXECUTED   a real stage row from the server, with its status, the engine
 *              that ran, its data source and its wall time.
 *   DERIVED    a fact read from an artefact that landed (an origin window, a
 *              filtered-out list, a sealed digest). It shows what it found, and
 *              it never invents a "status" for something that was not run.
 *
 * Fallbacks get an amber badge and are never hidden; a failed stage shows red
 * with its error class, and the page keeps rendering every layer that did land.
 */

import {
  AlertTriangle, Check, ChevronRight, Crosshair, FileCheck, Filter, Loader2, Satellite,
  Scale, Ship, Waves, Wind, X,
} from "lucide-react";

import { FALLBACK_LABELS, sourceBadge } from "./palette";

/* The executed stages, in pipeline order. `key` matches the server's own
 * stage name, which the E2E suite also asserts on. */
const EXECUTED = [
  { key: "detect", label: "Detection", icon: Satellite },
  { key: "characterise", label: "Characterisation", icon: Crosshair },
  { key: "drift_hindcast", label: "Hindcast", icon: Waves },
  { key: "drift_forecast", label: "Forecast", icon: Wind },
  { key: "ais", label: "AIS", icon: Ship },
  { key: "attribution", label: "Attribution", icon: Scale },
];

const STATUS_CLASS = {
  ok: "pipe-ok", fallback: "pipe-fallback", mock: "pipe-mock",
  failed: "pipe-failed", running: "pipe-running", cancelled: "pipe-cancelled",
};

function StatusIcon({ status }) {
  if (status === "ok" || status === "fallback") return <Check size={10} />;
  if (status === "failed") return <X size={10} />;
  if (status === "mock") return <AlertTriangle size={10} />;
  if (status === "running") return <Loader2 size={10} className="ws-spin" />;
  return <span className="ws-dot" />;
}

/**
 * @param {Array}  stages   rows from /investigations/{id}/status
 * @param {object} layers   the contract payloads that have landed
 * @param {object} runRow   the run's registry row (for the sealed digest)
 * @param {number} seconds  total wall time
 * @param {string} selected currently focused step id
 */
export default function StageStepper({ stages, layers = {}, runRow, seconds, selected,
                                       onSelect }) {
  const byName = Object.fromEntries((stages || []).map((s) => [s.stage, s]));

  /* "Reconstructing AIS" is a substep of attribution in the pipeline; show it
   * as running while attribution runs, done when attribution is done. Its
   * SOURCE is the interesting part -- synthetic AIS is the loudest fact a run
   * can carry. */
  const attribution = byName.attribution;
  byName.ais = attribution && {
    stage: "ais",
    status: attribution.status === "running" ? "running" : attribution.status,
    engine_used: attribution.source === "synthetic" ? "fallback" : attribution.engine_used,
    data_source: attribution.source,
    source: attribution.source,
    warnings: (attribution.warnings || []).filter((w) => /AIS|vessel/i.test(w)),
    seconds: null,
    error_class: null,
  };

  const slick = layers.slick?.features?.[0]?.properties;
  const originMd = layers.origin?.metadata;
  const suspects = layers.suspects;
  const forecastCount = layers.forecast?.features?.length;

  /* Derived nodes: each states the evidence it read, or says it is absent.
   * None of them fabricates a status for work that was not executed. */
  const derived = [
    {
      id: "scene", label: "Satellite", icon: Satellite,
      present: Boolean(layers.sceneMeta),
      value: layers.sceneMeta?.scene_id
        ? `${String(layers.sceneMeta.scene_id).slice(0, 18)}…` : null,
      detail: layers.sceneMeta?.acquired_utc
        ? `acquired ${String(layers.sceneMeta.acquired_utc).replace("T", " ").slice(0, 16)}Z`
        : "no scene metadata",
      source: layers.sceneMeta?.source,
    },
    {
      id: "origin", label: "Origin", icon: Crosshair,
      present: Boolean(originMd?.origin_window_start_utc),
      value: originMd?.origin_window_start_utc
        ? `${String(originMd.origin_window_start_utc).slice(11, 16)}–${String(originMd.origin_window_end_utc || "").slice(11, 16)}Z`
        : null,
      detail: originMd?.backtrack_hours != null
        ? `${originMd.backtrack_hours} h backtrack · ${originMd.n_particles ?? "?"} particles`
        : "no origin window produced",
    },
    {
      id: "filtering", label: "Vessel filtering", icon: Filter,
      present: Boolean(suspects),
      value: suspects
        ? `${(suspects.filtered_out || []).length} excluded`
        : null,
      detail: suspects?.total_vessels_considered != null
        ? `${suspects.suspects?.length ?? 0} ranked of ${suspects.total_vessels_considered} considered`
        : "attribution has not run",
    },
    {
      id: "evidence", label: "Evidence", icon: FileCheck,
      present: Boolean(runRow?.manifest?.artefact_digest),
      value: runRow?.manifest?.artefact_digest
        ? `⌗ ${String(runRow.manifest.artefact_digest).slice(0, 8)}` : null,
      detail: runRow?.manifest
        ? `${runRow.stages_real}/${runRow.stages_total} stages real`
        : "artefacts not sealed",
    },
  ];

  return (
    <div className="ws-pipeline" data-testid="stage-stepper">
      <div className="pipe">
        {/* --- satellite, before the first executed stage --------------- */}
        <DerivedNode node={derived[0]} selected={selected} onSelect={onSelect} />
        <Sep />

        {EXECUTED.slice(0, 4).map((s, i) => (
          <ExecNode key={s.key} spec={s} row={byName[s.key]} selected={selected}
            onSelect={onSelect} after={i < 3} />
        ))}

        <DerivedNode node={derived[1]} selected={selected} onSelect={onSelect} />
        <Sep />

        {EXECUTED.slice(4).map((s, i) => (
          <ExecNode key={s.key} spec={s} row={byName[s.key]} selected={selected}
            onSelect={onSelect} after={i < 1} />
        ))}

        <DerivedNode node={derived[2]} selected={selected} onSelect={onSelect} />
        <Sep />
        <DerivedNode node={derived[3]} selected={selected} onSelect={onSelect} />
      </div>

      <div className="ws-pipeline-foot">
        <span className="tiny dim">
          Solid nodes are stages the pipeline executed. Outlined nodes are facts read from the
          artefacts they produced — not separate runs.
        </span>
        {seconds != null && (
          <span className="tiny mono dim ml-auto">total {seconds.toFixed(1)}s</span>
        )}
      </div>
    </div>
  );
}

const Sep = () => <span className="pipe-sep"><ChevronRight size={11} /></span>;

function ExecNode({ spec, row, selected, onSelect, after }) {
  const status = row?.status ?? "pending";
  const Icon = spec.icon;
  const fallback = row?.engine_used === "fallback";
  const src = row?.data_source || row?.source;
  const badge = src ? sourceBadge(src) : null;

  return (
    <>
      <button className={`pipe-step ${STATUS_CLASS[status] || ""} clickable ${selected === spec.key ? "on" : ""}`}
        data-testid={`stage-${spec.key}`} data-status={status}
        onClick={() => onSelect?.(spec.key)}
        title={row?.detail || row?.warnings?.join(" · ") || spec.label}>
        <span className="pipe-name">
          <span className="pipe-ico"><StatusIcon status={status} /></span>
          <Icon size={10} /> {spec.label}
        </span>
        <span className="pipe-meta">
          {fallback && (
            <span className="badge badge-warn" data-testid={`fallback-${spec.key}`}>
              {FALLBACK_LABELS[spec.key === "ais" ? "attribution" : spec.key] ?? "fallback"}
            </span>
          )}
          {!fallback && badge && badge.label !== "—" && (
            <span className={`badge badge-${badge.tone}`} data-testid={`source-${spec.key}`}>
              {badge.label}
            </span>
          )}
          {status === "failed" && (
            <span className="badge badge-danger" title={row?.detail}>
              {row?.error_class || "FAILED"}
            </span>
          )}
          {row?.seconds > 0 && <span className="mono">{row.seconds.toFixed(1)}s</span>}
          {status === "pending" && <span className="dim">pending</span>}
        </span>
      </button>
      {after && <Sep />}
    </>
  );
}

function DerivedNode({ node, selected, onSelect }) {
  const Icon = node.icon;
  const badge = node.source ? sourceBadge(node.source) : null;
  return (
    <button className={`pipe-step pipe-derived clickable ${node.present ? "has" : ""} ${selected === node.id ? "on" : ""}`}
      data-testid={`derived-${node.id}`} data-present={String(node.present)}
      onClick={() => onSelect?.(node.id)} title={node.detail}>
      <span className="pipe-name"><Icon size={10} /> {node.label}</span>
      <span className="pipe-meta">
        {node.present
          ? <>
            <span className="mono">{node.value}</span>
            {badge && badge.label !== "—" && (
              <span className={`badge badge-${badge.tone}`}>{badge.label}</span>
            )}
          </>
          : <span className="dim">not produced</span>}
      </span>
    </button>
  );
}
