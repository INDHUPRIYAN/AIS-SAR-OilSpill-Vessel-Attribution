/* The audit trail -- who did what, and proof it has not been altered.
 *
 * This is NOT the system log. The log is a bounded in-memory buffer that is
 * lost on restart; this is a durable, append-only, hash-chained record. The
 * distinction matters enough that both pages say it and link to each other.
 *
 * The integrity check is the point of the page, so it is at the top rather
 * than hidden behind a button nobody presses: `/api/audit/verify` recomputes
 * the chain and names the first break. A chain that cannot be verified is
 * reported as exactly that -- an intact system and an altered one must never
 * look the same.
 *
 * There is deliberately no delete and no edit. Append-only is the whole claim.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, Download, FileClock, Link2, RefreshCw, ScrollText, ShieldCheck, ShieldX,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, Spinner, Tile,
} from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

/* Action families -> tone. The vocabulary comes from the server's own
 * `event_types`, so a new event type shows up here without a code change --
 * it simply falls to the neutral tone until it is given one. */
function actionTone(action) {
  const a = String(action || "");
  if (a.startsWith("auth.")) return a.includes("login") ? "accent" : "neutral";
  if (a.startsWith("zone.") || a.startsWith("user.")) return "warn";
  if (a.startsWith("incident.") || a.startsWith("alert.")) return "danger";
  if (a.startsWith("report.")) return "ok";
  if (a.startsWith("key.") || a.startsWith("data.")) return "mock";
  return "neutral";
}

export default function Audit() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [resource, setResource] = useState("");
  const [offset, setOffset] = useState(0);
  const LIMIT = 100;

  const { data, loading, error, reload } = useApi(
    () => api.audit({ actor: actor || undefined, action: action || undefined,
                      resource: resource || undefined, offset, limit: LIMIT }),
    [actor, action, resource, offset]);
  const verifyQ = useApi(() => api.auditVerify(), []);

  const rows = data?.items || [];
  const v = verifyQ.data;

  /* The export carries the same filters as the view, so what you looked at is
   * what you take away. Exporting is itself an audited event. */
  const exportHref = `/api/audit/export?${new URLSearchParams(
    Object.entries({ actor, action, resource }).filter(([, x]) => x)).toString()}`;

  if (error?.status === 403) {
    return (
      <div className="page">
        <PageHeader icon={<ScrollText size={17} />} kicker="System" title="Audit Trail" />
        <Panel>
          <DataState kind="forbidden" title="Not available to your role"
            error={error}
            hint="The audit log is readable by reviewers and auditors. An investigator cannot read it, so the record of their own actions is not theirs to inspect or dispute quietly." />
        </Panel>
      </div>
    );
  }

  return (
    <div className="page" data-testid="audit-page">
      <PageHeader icon={<ScrollText size={17} />} kicker="System" title="Audit Trail"
        sub="Durable, append-only and hash-chained. There is no delete and no edit."
        actions={<>
          <button className="btn btn-sm" onClick={() => { reload(); verifyQ.reload(); }}>
            <RefreshCw size={12} /> Refresh
          </button>
          <a className="btn btn-sm" href={exportHref} data-testid="audit-export">
            <Download size={12} /> Export CSV
          </a>
        </>} />

      {/* ------------------------------------------------------ integrity --- */}
      <Panel title="Chain integrity" icon={<ShieldCheck size={12} />} className="mb-3"
        right={verifyQ.loading ? <Spinner /> : v
          ? <Badge tone={v.ok ? "ok" : "danger"} lg>{v.ok ? "VERIFIED" : "BROKEN"}</Badge>
          : null}>
        {verifyQ.loading && !v ? <DataState kind="loading" compact title="Recomputing the chain" />
          : verifyQ.error ? <DataState kind="error" compact error={verifyQ.error} />
            : v?.ok ? (
              <div className="row gap-3 wrap">
                <span className="row gap-2"><ShieldCheck size={14} color="var(--ok)" />
                  <span>Every row's hash matches the row before it.</span></span>
                <span className="tiny mono dim ml-auto">
                  {v.rows != null ? `${fmt.int(v.rows)} rows checked` : ""}
                </span>
              </div>
            ) : (
              /* The worst available failure for a tamper-evidence store: an
                 intact system and an altered one must not look the same. */
              <Notice tone="danger" icon={<ShieldX size={13} />}>
                <strong>The hash chain does not verify.</strong>{" "}
                {v?.detail || v?.error || "The recomputed chain diverges from the stored one."}
                {v?.first_bad_id != null && <> First divergence at row <span className="mono">#{v.first_bad_id}</span>.</>}
                {" "}Until this is explained, treat the history below as unproven.
              </Notice>
            )}
      </Panel>

      <div className="grid grid-4 mb-3">
        <Tile label="Recorded events" value={data ? fmt.int(data.total) : "—"} tone="accent" />
        <Tile label="Event types" value={data?.event_types?.length ?? "—"} sub="vocabulary from the server" />
        <Tile label="Integrity" value={v ? (v.ok ? "OK" : "BROKEN") : "—"}
          tone={v ? (v.ok ? "ok" : "danger") : undefined} />
        <Tile label="Page" value={data ? `${offset + 1}–${Math.min(offset + LIMIT, data.total)}` : "—"}
          sub={`${LIMIT} per page`} />
      </div>

      <Panel title="History" icon={<FileClock size={12} />} flush
        right={<>
          <span className="search" style={{ height: 24, minWidth: 140 }}>
            <input className="sm" value={actor} onChange={(e) => { setActor(e.target.value); setOffset(0); }}
              placeholder="actor" aria-label="Filter by actor" data-testid="audit-actor" />
          </span>
          <span className="search" style={{ height: 24, minWidth: 140 }}>
            <input className="sm" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }}
              placeholder="action prefix" aria-label="Filter by action" data-testid="audit-action" />
          </span>
          <span className="search" style={{ height: 24, minWidth: 140 }}>
            <input className="sm" value={resource} onChange={(e) => { setResource(e.target.value); setOffset(0); }}
              placeholder="resource" aria-label="Filter by resource" />
          </span>
        </>}>
        {loading && !data ? <DataState kind="loading" title="Reading the audit log" />
          : rows.length === 0 ? (
            <DataState kind="empty" title="No audited events match"
              hint={actor || action || resource
                ? "No row matches these filters."
                : "Nothing has been recorded on this deployment yet. Signing in, changing a zone or publishing a report all write here."} />
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 62 }}>#</th><th>When (UTC)</th><th>Actor</th>
                      <th>Action</th><th>Resource</th><th>Detail</th><th>Hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} data-testid={`audit-${r.id}`}>
                        <td className="mono tiny dim">{r.id}</td>
                        <td className="mono tiny nowrap">{fmt.utc(r.occurred_utc)}</td>
                        <td className="tiny">
                          <div>{r.actor || <span className="dim">system</span>}</div>
                          {r.ip && <div className="sub mono">{r.ip}</div>}
                        </td>
                        <td><Badge tone={actionTone(r.action)}>{r.action}</Badge></td>
                        <td className="mono tiny ellipsis" style={{ maxWidth: 180 }}>
                          {r.resource || "—"}
                          {r.provider && <div className="sub">{r.provider}{r.field ? ` · ${r.field}` : ""}</div>}
                        </td>
                        <td className="tiny dim" style={{ maxWidth: 320, whiteSpace: "normal" }}>
                          {r.detail || "—"}
                        </td>
                        <td className="mono tiny dim" title={r.row_hash}>
                          <Link2 size={9} /> {String(r.row_hash || "").slice(0, 10)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-foot">
                <span className="mono tiny">
                  {offset + 1}–{Math.min(offset + rows.length, data.total)} of {fmt.int(data.total)}
                </span>
                <span className="row gap-2">
                  <button className="btn btn-xs" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Newer</button>
                  <button className="btn btn-xs" disabled={offset + LIMIT >= data.total}
                    onClick={() => setOffset(offset + LIMIT)}>Older</button>
                </span>
              </div>
            </>
          )}
      </Panel>

      <Notice style={{ marginTop: 12 }} icon={<AlertTriangle size={12} />}>
        This is the durable record. The volatile application log — lost on restart, bounded, and
        unable to see <span className="mono">print()</span> — lives on{" "}
        <Link to="/system">System Operations</Link>. Clearing that buffer is itself audited here,
        so a clearing cannot erase the record of the clearing.
      </Notice>
    </div>
  );
}
