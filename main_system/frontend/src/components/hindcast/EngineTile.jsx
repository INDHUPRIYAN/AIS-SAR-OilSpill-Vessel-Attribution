/* One hindcast engine as a live status tile. Seven of these, in stage order,
 * are the Monitoring Engines page. Everything shown comes from the engine's
 * own run row: the tile computes nothing. */

import * as Icons from "lucide-react";
import { ChevronRight, Clock, ScrollText } from "lucide-react";
import { fmt } from "../../lib/api";

export const ENGINE_STATUS = {
  pending: "IDLE", running: "RUNNING", succeeded: "SUCCEEDED", failed: "FAILED", skipped: "SKIPPED",
};
const FLOW = { succeeded: "var(--ok)", running: "var(--accent)", failed: "var(--danger)", skipped: "var(--warn)" };

export function durationText(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60000)} m ${Math.round((ms % 60000) / 1000)} s`;
}

export function metricText(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") {
    if (Number.isInteger(value)) return value.toLocaleString("en-US");
    return Math.abs(value) >= 100 ? value.toFixed(1) : value.toFixed(2);
  }
  return String(value);
}

export function StatusPill({ status = "pending", small }) {
  return (
    <span className={`he-pill ${small ? "he-pill-sm" : ""}`} data-status={status} data-testid="engine-pill">
      <i />{ENGINE_STATUS[status] || String(status).toUpperCase()}
    </span>
  );
}

const Arrow = () => (
  <svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true">
    <path d="M1 1l4 3.5L1 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

export default function EngineTile({ engine, run, index, total, prevStatus, nextStatus, onOpen }) {
  const status = run?.status || "pending";
  const Icon = Icons[engine.icon] || Icons.Cpu;
  const running = status === "running";
  const blank = status === "pending" || status === "skipped";
  const percent = Math.round(run?.percent ?? 0);
  const metrics = run?.metrics || {};
  const step = status === "failed" ? (run?.error || run?.current_step)
    : run?.current_step || (status === "pending" ? "waiting for a job" : "—");

  return (
    <article className="he-tile" data-status={status} data-testid={`tile-${engine.id}`}
      role="button" tabIndex={0} onClick={() => onOpen(engine)}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen(engine))}
      aria-label={`${engine.name}: ${ENGINE_STATUS[status]}. Open logs and run history.`}>
      {running && <div className="he-sweep" />}

      <header className="he-head">
        <div className="he-icon"><Icon size={18} strokeWidth={1.8} /></div>
        <div className="he-head-main">
          <div className="he-stage"><span>{engine.stage}</span>{String(engine.order).padStart(2, "0")}/{String(total).padStart(2, "0")}</div>
          <div className="he-name" title={engine.name}>{engine.name}</div>
        </div>
        <StatusPill status={status} />
      </header>

      <p className="he-desc">{engine.description}</p>

      <div>
        <div className="he-prog-k"><span>progress</span><b data-testid="engine-percent">{blank ? "—" : `${percent}%`}</b></div>
        <div className="he-prog" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={blank ? 0 : percent}>
          <i style={{ width: `${blank ? 0 : percent}%` }} />
        </div>
      </div>

      <div className="he-step"><ChevronRight size={13} /><span data-testid="engine-step">{step}</span></div>

      <div className={`he-chips ${engine.metric_keys.length > 3 ? "he-chips-4" : ""}`}>
        {engine.metric_keys.map((m) => (
          <div className="he-chip" key={m.key} title={`${m.label}: ${metricText(metrics[m.key])}${m.unit ? ` ${m.unit}` : ""}`}>
            <div className="he-chip-k">{m.label}</div>
            <div className="he-chip-v">{metricText(metrics[m.key])}{metrics[m.key] != null && m.unit ? <em>{m.unit}</em> : null}</div>
          </div>
        ))}
      </div>

      <footer className="he-foot">
        <span><Clock size={11} />
          {run?.finished_at ? `${durationText(run.duration_ms)} · ${fmt.utc(run.finished_at)}`
            : running ? `started ${fmt.utc(run.started_at)}` : "no run yet"}</span>
        <span><ScrollText size={11} /> logs{run?.log_count ? ` (${run.log_count})` : ""}</span>
      </footer>

      {/* A connector takes its colour from the engine it LEAVES, and marches
          while the engine it enters is working. */}
      {index < total - 1 && (
        <span className="he-flow he-flow-exit" aria-hidden="true" style={{ "--he-flow": FLOW[status] }}
          data-live={status === "succeeded" && nextStatus === "running"}><b /><Arrow /></span>
      )}
      {index > 0 && (
        <span className="he-flow he-flow-entry" aria-hidden="true" style={{ "--he-flow": FLOW[prevStatus] }}
          data-live={prevStatus === "succeeded" && running}><b /><Arrow /></span>
      )}
    </article>
  );
}
