/* System Operations -- the engineering console.
 *
 * Three things an operator actually needs when something is wrong: what is
 * running, what ran, and what the process said while it ran. They are tabs on
 * one page rather than three pages, because diagnosing a stuck run means
 * moving between them constantly.
 *
 * This page renders the backend's own honesty and does not soften it:
 *
 *   - `/api/workers` reports the background THREADS this single process runs,
 *     not a worker fleet. A disabled worker is shown as disabled with the
 *     setting that would enable it, because a deliberately-off scheduler must
 *     not look like a crashed one. GPU reports "not measured" rather than 0%.
 *   - `/api/logs` is a bounded in-memory ring buffer that is lost on restart
 *     and cannot see `print()`. It is NOT the audit trail, and the page says
 *     so with a link to the one that is.
 *   - job durations are absent rather than zero when nothing has finished.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity, AlertTriangle, Ban, Cpu, Database, FileClock, HardDrive, Play, RefreshCw,
  ScrollText, Server, Square, Trash2, Workflow,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, Segmented, Spinner, Tabs, Tile,
} from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { useUrlTab } from "../lib/urls";
import { hasRole, useSession } from "../lib/session";

const LEVEL_TONE = {
  CRITICAL: "danger", ERROR: "danger", WARNING: "warn", WARN: "warn",
  INFO: "neutral", DEBUG: "ghost",
};
const JOB_TONE = {
  running: "accent", pending: "warn", complete: "ok", failed: "danger", cancelled: "neutral",
};

export default function SystemOps() {
  const { user } = useSession();
  const isAdmin = hasRole(user);
  const [tab, setTab] = useUrlTab(["runtime", "jobs", "logs"]);

  const workersQ = useApi(() => api.workers(), [], { interval: 10000 });
  const healthQ = useApi(() => api.systemHealth(), [], { interval: 20000 });

  const w = workersQ.data;
  const h = healthQ.data;

  return (
    <div className="page" data-testid="system-ops-page">
      <PageHeader icon={<Server size={17} />} kicker="System" title="System Operations"
        sub="What is running in this process, what it has run, and what it said while running."
        actions={
          <button className="btn btn-sm" onClick={() => { workersQ.reload(); healthQ.reload(); }}>
            <RefreshCw size={12} /> Refresh
          </button>
        } />

      <div className="grid grid-5 mb-3">
        <Tile label="Deployment" value={w?.model || "—"} tone="accent"
          sub={w ? "single process" : "reading"} />
        <Tile label="Jobs in flight" value={w?.jobs?.in_flight_count ?? "—"}
          tone={w?.jobs?.in_flight_count ? "accent" : undefined}
          sub={w?.jobs?.duration_seconds ? `p50 ${w.jobs.duration_seconds.p50}s` : "no timings yet"} />
        <Tile label="Workers alive"
          value={w ? `${w.workers.filter((x) => x.alive === true).length}/${w.workers.filter((x) => x.enabled).length}` : "—"}
          sub={w ? `${w.workers.length} declared` : ""} />
        <Tile label="Database" value={h?.database?.ok ? "OK" : h ? "ERROR" : "—"}
          tone={h?.database?.ok ? "ok" : h ? "danger" : undefined}
          sub={h?.database ? `${fmt.int(h.database.runs)} runs · ${h.database.url_scheme}` : ""} />
        <Tile label="Disk free" value={h?.disk?.free_gb != null ? `${h.disk.free_gb} GB` : "—"}
          tone={h?.disk?.free_gb != null && h.disk.free_gb < 5 ? "danger" : undefined}
          sub={h?.disk?.total_gb ? `of ${h.disk.total_gb} GB` : ""} />
      </div>

      <Tabs value={tab} onChange={setTab} testidPrefix="sysops-tab" items={[
        { id: "runtime", label: "Runtime & workers", icon: <Cpu size={12} /> },
        { id: "jobs", label: "Jobs", icon: <Workflow size={12} />,
          count: w?.jobs?.in_flight_count || undefined },
        { id: "logs", label: "Logs", icon: <ScrollText size={12} /> },
      ]} />

      <div className="mt-3">
        {tab === "runtime" && <Runtime workersQ={workersQ} healthQ={healthQ} />}
        {tab === "jobs" && <Jobs workersQ={workersQ} />}
        {tab === "logs" && <Logs isAdmin={isAdmin} />}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ runtime --- */

function Runtime({ workersQ, healthQ }) {
  const w = workersQ.data;
  const h = healthQ.data;

  if (workersQ.loading && !w) return <DataState kind="loading" title="Reading process state" />;
  if (workersQ.error) return <DataState kind="error" error={workersQ.error} />;

  const host = w?.host || {};
  const measured = host.measured_by === "psutil";

  return (
    <div className="split">
      <div className="stack" style={{ gap: 12 }}>
        <Panel title="Workers" icon={<Workflow size={12} />} flush
          right={<Badge tone="neutral">{w.model}</Badge>}>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Worker</th><th>State</th><th>Alive</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {(w.workers || []).map((x) => (
                  <tr key={x.name}>
                    <td>
                      <div className="mono">{x.name}</div>
                      <div className="sub" style={{ maxWidth: 420, whiteSpace: "normal" }}>{x.purpose}</div>
                    </td>
                    <td>
                      {x.enabled
                        ? <Badge tone={x.functionally_working === false ? "warn" : "ok"}>
                          {x.state ? String(x.state).toUpperCase() : "ENABLED"}
                        </Badge>
                        : <Badge tone="neutral">DISABLED</Badge>}
                      {x.functionally_working === false && x.enabled && (
                        <div className="tiny tone-warn mt-1">connected, not working</div>
                      )}
                    </td>
                    <td>
                      {/* `alive: null` is "cannot be attributed", which is a
                          different claim from "dead". Never collapsed. */}
                      {x.alive === true ? <Badge tone="ok">YES</Badge>
                        : x.alive === false ? <Badge tone="danger">NO</Badge>
                          : <Badge tone="ghost">NOT ATTRIBUTABLE</Badge>}
                    </td>
                    <td className="tiny dim" style={{ maxWidth: 300, whiteSpace: "normal" }}>
                      {x.disabled_reason || x.alive_note || x.note
                        || (x.interval_seconds ? `every ${x.interval_seconds}s` : "—")}
                      {x.counters && (
                        <div className="mono mt-1">
                          {Object.entries(x.counters).map(([k, v]) => `${k} ${v}`).join(" · ")}
                        </div>
                      )}
                      {x.queue_depth != null && (
                        <div className="mono mt-1">queue {x.queue_depth}/{x.queue_capacity}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="panel-foot tiny dim" style={{ lineHeight: 1.55 }}>{w.model_note}</div>
        </Panel>
      </div>

      <div className="stack" style={{ gap: 12 }}>
        <Panel title="Host" icon={<Cpu size={12} />}>
          <div className="kv-dense">
            <KV k="Hostname" v={host.hostname || "—"} />
            <KV k="Platform" v={host.platform || "—"} wrap />
            <KV k="Python" v={host.python || "—"} />
            <KV k="PID" v={host.pid ?? "—"} />
            <KV k="Threads" v={host.threads_total ?? "—"} />
            {measured ? (<>
              <KV k="CPU (host)" v={`${host.cpu_percent_host}%`} />
              <KV k="CPU (process)" v={`${host.process_cpu_percent}%`} />
              <KV k="Cores" v={host.cpu_count} />
              <KV k="Process RSS" v={`${host.process_rss_mb} MB`} />
              <KV k="Memory" v={`${host.memory_used_percent}% of ${fmt.int(host.memory_total_mb)} MB`} />
            </>) : (
              <KV k="CPU / memory" v="not measured" tone="danger" />
            )}
            <KV k="Uptime" v={h?.host?.uptime_seconds != null ? `${fmt.int(h.host.uptime_seconds)} s` : "—"} />
          </div>
          {!measured && host.note && <Notice tone="warn" style={{ marginTop: 8 }}>{host.note}</Notice>}
        </Panel>

        <Panel title="GPU" icon={<Cpu size={12} />}>
          {/* Never a zero. A "GPU 0%" row would be a measurement nobody took. */}
          <Badge tone={w.gpu?.measured ? "ok" : "ghost"}>
            {w.gpu?.measured ? "MEASURED" : "NOT MEASURED"}
          </Badge>
          <div className="tiny dim mt-2" style={{ lineHeight: 1.55 }}>{w.gpu?.note}</div>
        </Panel>

        <Panel title="Storage & models" icon={<HardDrive size={12} />}>
          <div className="kv-dense">
            <KV k="Data root" v={h?.disk?.path || "—"} wrap />
            <KV k="Free" v={h?.disk?.free_gb != null ? `${h.disk.free_gb} GB` : "—"} />
            <KV k="Total" v={h?.disk?.total_gb != null ? `${h.disk.total_gb} GB` : "—"} />
          </div>
          <div className="section-label">Model files on disk</div>
          {(h?.models || []).map((m) => (
            <div key={m.file} className="kv">
              <span className="kv-k mono">{m.file}</span>
              <span className="kv-v">
                <Badge tone={m.present ? "ok" : "danger"}>
                  {m.present ? `${fmt.int(m.bytes)} B` : "MISSING"}
                </Badge>
              </span>
            </div>
          ))}
        </Panel>

        {h?.not_reported?.length > 0 && (
          <Panel title="Not reported" icon={<Ban size={12} />}>
            <div className="tiny dim mb-2" style={{ lineHeight: 1.55 }}>
              Listed rather than shown as empty tiles. A dashboard tile for a component this
              system does not run is a claim, not a placeholder.
            </div>
            <ul className="cat-absent">{h.not_reported.map((l) => <li key={l}>{l}</li>)}</ul>
          </Panel>
        )}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- jobs --- */

function Jobs({ workersQ }) {
  const w = workersQ.data;
  if (workersQ.loading && !w) return <DataState kind="loading" title="Reading jobs" />;
  if (workersQ.error) return <DataState kind="error" error={workersQ.error} />;
  const jobs = w?.jobs || {};
  const rows = [...(jobs.in_flight || []), ...(jobs.recent || [])]
    .filter((j, i, arr) => arr.findIndex((x) => x.job_id === j.job_id) === i);

  return (
    <div className="stack" style={{ gap: 12 }}>
      <Panel title="Processing time" icon={<Activity size={12} />}>
        {jobs.duration_seconds ? (
          <div className="grid grid-4">
            <Tile label="p50" value={`${jobs.duration_seconds.p50}s`} />
            <Tile label="p90" value={`${jobs.duration_seconds.p90}s`} />
            <Tile label="max" value={`${jobs.duration_seconds.max}s`} />
            <Tile label="sampled" value={jobs.finished_sampled} sub="finished jobs" />
          </div>
        ) : (
          /* Absent rather than zero: a "0.0 s mean" would be a statistic over
             nothing at all. */
          <DataState kind="empty" compact title="No timings yet" hint={jobs.duration_note} />
        )}
      </Panel>

      <Panel title={`Jobs · ${rows.length}`} icon={<Workflow size={12} />} flush
        right={jobs.in_flight_count ? <Badge tone="accent">{jobs.in_flight_count} in flight</Badge> : null}>
        {rows.length === 0 ? (
          <DataState kind="empty" title="No jobs recorded"
            hint="A pipeline run creates a job. Start one from the Workspace." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job</th><th>Run</th><th>Status</th><th>Stage</th>
                  <th className="num">Elapsed</th><th>Started</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((j) => (
                  <tr key={j.job_id}>
                    <td className="mono tiny">{j.job_id}</td>
                    <td className="mono tiny">
                      {j.run_id
                        ? <Link to={`/investigation?run=${j.run_id}`}>{j.run_id}</Link>
                        : <span className="dim">—</span>}
                    </td>
                    <td>
                      <Badge tone={JOB_TONE[j.status] || "neutral"}>{j.status}</Badge>
                      {j.cancel_requested && <Badge tone="warn">cancel requested</Badge>}
                    </td>
                    <td className="tiny">
                      {j.current_stage || "—"}
                      {j.stages_total ? (
                        <span className="dim mono"> {j.stages_done}/{j.stages_total}</span>
                      ) : null}
                    </td>
                    <td className="num mono tiny">
                      {j.elapsed_seconds != null ? `${j.elapsed_seconds}s` : "—"}
                    </td>
                    <td className="tiny dim">{fmt.ago(j.started_utc || j.created_utc)}</td>
                    <td className="tiny tone-danger" style={{ maxWidth: 220, whiteSpace: "normal" }}>
                      {j.error || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

/* --------------------------------------------------------------- logs --- */

const LEVELS = ["", "INFO", "WARNING", "ERROR", "CRITICAL"];

function Logs({ isAdmin }) {
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const { data, loading, error, reload } = useApi(
    () => api.logs({ level: level || undefined, q: q || undefined, limit: 300 }),
    [level, q], { interval: 10000 });

  async function clear() {
    if (!window.confirm("Empty the in-memory log buffer? The audit trail is a separate, hash-chained store and is not affected.")) return;
    setBusy(true);
    try { await api.clearLogs(); await reload(); } finally { setBusy(false); }
  }

  const entries = data?.entries || [];

  return (
    <Panel title={`System logs${data ? ` · ${data.total}` : ""}`} icon={<ScrollText size={12} />} flush
      right={<>
        <span className="search" style={{ height: 24, minWidth: 200 }}>
          <input className="sm" value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="substring" aria-label="Search logs" data-testid="log-search" />
        </span>
        <Segmented value={level} onChange={setLevel} testidPrefix="level" items={[
          { id: "", label: "All" }, { id: "INFO", label: "Info" },
          { id: "WARNING", label: "Warn" }, { id: "ERROR", label: "Error" },
        ]} />
        {isAdmin && (
          <button className="btn btn-sm btn-danger" onClick={clear} disabled={busy}
            title="Empty the buffer (audited)">
            <Trash2 size={11} /> Clear
          </button>
        )}
      </>}>
      {/* The buffer's own limits, stated where the buffer is read. */}
      <div style={{ padding: "8px 12px 0" }}>
        {data && (
          <div className="row gap-2 wrap mb-2 tiny mono dim">
            <span>buffered {data.buffered}/{data.capacity}</span>
            {data.dropped > 0 && <span className="tone-warn">dropped {data.dropped}</span>}
            {Object.entries(data.by_level || {}).map(([lv, n]) => (
              <span key={lv}>{lv} {n}</span>
            ))}
            <span>capturing {String(data.capturing)}</span>
          </div>
        )}</div>
      <div style={{ padding: "0 12px 8px" }}>
        <Notice icon={<FileClock size={11} />}>
          {data?.note || ("A bounded in-memory ring buffer: lost on restart, cannot see print(), "
            + "and filtered by MINIMUM level so asking for WARN cannot hide a CRITICAL.")}
          {" "}This is not the audit trail — that is <Link to="/audit">hash-chained and durable</Link>.
        </Notice>
      </div>

      {loading && !data ? <DataState kind="loading" title="Reading buffer" />
        : error ? <DataState kind="error" error={error} />
          : entries.length === 0 ? (
            <DataState kind="empty" title="No log records match"
              hint={data?.capturing === false
                ? "The log buffer is not installed in this process."
                : "Nothing in the buffer matches these filters."} />
          ) : (
            <div className="log-list" data-testid="log-list">
              {entries.map((e, i) => (
                <div key={e.seq ?? i} className="log-row">
                  <span className="log-time mono">
                    {String(e.utc || "").replace("T", " ").slice(5, 19)}
                  </span>
                  <Badge tone={LEVEL_TONE[String(e.level || "").toUpperCase()] || "neutral"}>
                    {e.level}
                  </Badge>
                  <span className="log-logger mono">{e.logger}</span>
                  <span className="log-msg">{e.message}</span>
                  {(e.run_id || e.job_id || e.incident_id || e.zone_id) && (
                    <span className="log-ctx mono">
                      {e.run_id ? `run ${e.run_id}` : e.job_id ? `job ${e.job_id}`
                        : e.incident_id ? `inc ${e.incident_id}` : `zone ${e.zone_id}`}
                    </span>
                  )}
                  {/* An exception's traceback is the single most useful thing in
                      a log and is never truncated away. */}
                  {e.exception && <pre className="log-exc pre">{e.exception}</pre>}
                </div>
              ))}
            </div>
          )}

      <style>{`
        .log-list { max-height: calc(100vh - 400px); overflow-y: auto; }
        .log-row { display: grid; grid-template-columns: 104px 76px 150px minmax(0,1fr) auto;
          gap: 10px; padding: 4px 12px; border-bottom: 1px solid var(--line);
          font-size: var(--fs-sm); align-items: baseline; }
        .log-row:hover { background: var(--accent-soft); }
        .log-time { color: var(--ink-3); font-size: var(--fs-xs); }
        .log-logger { color: var(--ink-2); font-size: var(--fs-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .log-msg { color: var(--ink-1); word-break: break-word; }
        .log-ctx { color: var(--ink-3); font-size: var(--fs-xs); white-space: nowrap; }
        .log-exc { grid-column: 1 / -1; margin-top: 4px; font-size: var(--fs-xs); max-height: 150px; overflow: auto; }
        @media (max-width: 1200px) { .log-row { grid-template-columns: 90px 70px minmax(0,1fr); }
          .log-logger, .log-ctx { display: none; } }
      `}</style>
    </Panel>
  );
}
