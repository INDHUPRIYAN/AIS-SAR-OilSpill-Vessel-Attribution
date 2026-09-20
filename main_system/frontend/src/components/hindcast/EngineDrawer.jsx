/* Slide-over for one engine: its full log for the selected job, every metric it
 * reported, and its run history across jobs. The tile only carries the log
 * tail (that is all the live feed sends), so the full log is fetched here and
 * refreshed while the engine is still writing to it. */

import { useEffect, useRef, useState } from "react";
import { Drawer } from "../ui";
import { api, fmt } from "../../lib/api";
import { StatusPill, durationText, metricText } from "./EngineTile";

export default function EngineDrawer({ engine, run, jobId, onClose }) {
  const [logs, setLogs] = useState(null);
  const [history, setHistory] = useState(null);
  const [error, setError] = useState(null);
  const end = useRef(null);
  const status = run?.status;
  const logCount = run?.log_count;

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let alive = true;
    api.engineRuns(engine.id).then((r) => alive && setHistory(r)).catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [engine.id, status]);

  useEffect(() => {
    if (!jobId) { setLogs([]); return undefined; }
    let alive = true;
    api.hindcastJob(jobId)
      .then((job) => alive && setLogs(job.engines.find((e) => e.engine_id === engine.id)?.logs || []))
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [jobId, engine.id, logCount, status]);

  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [logs]);

  const metrics = Object.entries(run?.metrics || {});
  return (
    <Drawer title={engine.name} icon={<StatusPill status={status || "pending"} small />} onClose={onClose}
      width="min(560px, 100%)" testid="engine-drawer">
      <p className="tiny" style={{ color: "var(--ink-2)", marginTop: 0 }}>
        <span className="mono dim">{engine.stage} · {engine.id}</span><br />{engine.description}
      </p>
      {run?.error && <div className="he-drawer-err" data-testid="engine-drawer-error">{run.error}</div>}

      <section className="he-drawer-sec">
        <div className="he-drawer-k">Metrics · this job</div>
        {metrics.length === 0 ? <div className="tiny dim">No metrics reported yet.</div> : (
          <dl className="he-metrics">
            {metrics.map(([k, v]) => <div key={k}><dt>{k}</dt><dd title={metricText(v)}>{metricText(v)}</dd></div>)}
          </dl>
        )}
      </section>

      <section className="he-drawer-sec">
        <div className="he-drawer-k">Log{logs ? ` · ${logs.length} line${logs.length === 1 ? "" : "s"}` : ""}</div>
        <pre className="he-log" data-testid="engine-drawer-logs">
          {logs == null ? "loading…" : logs.length === 0 ? "No log lines for this job yet." : logs.join("\n")}
          <span ref={end} />
        </pre>
      </section>

      <section className="he-drawer-sec">
        <div className="he-drawer-k">Run history · all jobs</div>
        {error && <div className="tiny" style={{ color: "var(--danger)" }}>{error}</div>}
        {history == null ? <div className="tiny dim">loading…</div>
          : history.length === 0 ? <div className="tiny dim">This engine has not run yet.</div> : (
            <table className="he-history" data-testid="engine-history">
              <thead><tr><th>Job</th><th>Status</th><th>Started</th><th>Duration</th></tr></thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.job_id} data-current={h.job_id === jobId}>
                    <td title={h.job_label || ""}>{h.job_id}</td>
                    <td><StatusPill status={h.status} small /></td>
                    <td>{fmt.utc(h.started_at)}</td>
                    <td>{durationText(h.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </section>
    </Drawer>
  );
}
