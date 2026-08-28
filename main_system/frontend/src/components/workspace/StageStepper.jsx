/* Live pipeline stepper. One row per stage, fed by /investigations/{id}/status
 * every 2 s while running. Fallbacks get an amber badge (never hidden), a
 * failed stage shows red with its error class — and the page keeps rendering
 * every layer that did land. */

import { Check, X, Loader2, AlertTriangle } from "lucide-react";
import { FALLBACK_LABELS } from "./palette";

const ORDER = [
  ["detect", "Detecting"],
  ["characterise", "Characterising"],
  ["drift_hindcast", "Hindcasting"],
  ["drift_forecast", "Forecasting"],
  ["ais", "Reconstructing AIS"],
  ["attribution", "Attributing"],
];

export default function StageStepper({ stages, seconds }) {
  const byName = Object.fromEntries((stages || []).map((s) => [s.stage, s]));
  /* "Reconstructing AIS" is a substep of attribution in the pipeline; show it
   * as running while attribution runs, done when attribution is done. */
  const attribution = byName.attribution;
  const aisVirtual = attribution && {
    stage: "ais",
    status: attribution.status === "running" ? "running" : attribution.status,
    engine_used: attribution.source === "synthetic" ? "fallback" : attribution.engine_used,
    warnings: (attribution.warnings || []).filter((w) => /AIS|vessel/i.test(w)),
    error_class: null,
  };

  return (
    <div className="ws-stepper" data-testid="stage-stepper">
      {ORDER.map(([key, label]) => {
        const s = key === "ais" ? aisVirtual : byName[key];
        const status = s?.status ?? "pending";
        const fallback = s?.engine_used === "fallback" ||
          (key === "ais" && s?.engine_used === "fallback");
        return (
          <div key={key} className={`ws-step ws-step-${status}`}
            data-testid={`stage-${key}`} data-status={status}>
            <span className="ws-step-ico">
              {status === "ok" || status === "fallback" ? <Check size={11} /> :
               status === "failed" ? <X size={11} /> :
               status === "mock" ? <AlertTriangle size={11} /> :
               status === "running" ? <Loader2 size={11} className="ws-spin" /> :
               <span className="ws-dot" />}
            </span>
            <span className="ws-step-label">{label}</span>
            {fallback && (
              <span className="badge badge-warn" data-testid={`fallback-${key}`}>
                {FALLBACK_LABELS[key === "ais" ? "attribution" : key] ?? "fallback"}
              </span>
            )}
            {status === "mock" && !fallback && (
              <span className="badge badge-mock">synthetic</span>
            )}
            {status === "failed" && (
              <span className="badge badge-danger" title={s?.detail}>
                {s?.error_class || "FAILED"}
              </span>
            )}
            {s?.seconds > 0 && (
              <span className="tiny muted mono" style={{ marginLeft: "auto" }}>
                {s.seconds.toFixed(1)}s
              </span>
            )}
          </div>
        );
      })}
      {seconds != null && (
        <div className="tiny muted mono" style={{ textAlign: "right", marginTop: 4 }}>
          total {seconds.toFixed(1)}s
        </div>
      )}
    </div>
  );
}
