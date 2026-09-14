/* Reports -- the evidence register.
 *
 * A report is a DOCUMENT, not a view: it has a version, a review state, and a
 * provenance annex whose hashes are the run manifest's own. This page is the
 * register of those documents and the place their lifecycle is driven;
 * `/report?run=…` renders the printable document itself.
 *
 * Two honesty rules the page enforces visually:
 *
 *   - a DRAFT is never allowed to look approved. Draft rows are marked, and
 *     the printable page carries a watermark that deliberately survives
 *     printing.
 *   - a report composed from artefacts that have since been re-sealed is
 *     STALE: it describes bytes that no longer exist, and that is called out
 *     rather than left for a reader to discover.
 *
 * Role gating is presentational only. Every action here is refused by the
 * server for a role that may not perform it, and a zone officer's report
 * access is scoped server-side to the zones they hold.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, CheckCircle2, Download, FileCheck, FileText, Info, RefreshCw, Send,
  ShieldCheck, Undo2,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, Segmented, Spinner, Tile,
} from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { hasRole, useSession } from "../lib/session";

const STATUS_TONE = { draft: "warn", in_review: "accent", published: "ok" };
const STATUS_LABEL = {
  draft: "DRAFT — not reviewed",
  in_review: "IN REVIEW — awaiting approval",
  published: "PUBLISHED — immutable",
};

export default function Reports() {
  const { user } = useSession();
  const [status, setStatus] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const listQ = useApi(() => api.listReports(status ? { status } : {}), [status],
                       { interval: 45000 });
  const runsQ = useApi(() => api.listRunsPaged({ status: "complete", limit: 40 }), []);

  const reports = listQ.data || [];
  const rows = Array.isArray(reports) ? reports : reports.items || [];
  const selected = rows.find((r) => r.id === selectedId) || rows[0] || null;

  const detailQ = useApi(
    () => (selected ? api.getReport(selected.id) : Promise.resolve(null)), [selected?.id]);

  const canCompose = hasRole(user, "investigator", "analyst");
  const canPublish = hasRole(user, "reviewer");

  /* Runs that have no report yet -- the only runs "Compose" makes sense for. */
  const covered = new Set(rows.map((r) => r.run_id));
  const uncovered = (runsQ.data?.items || []).filter((r) => !covered.has(r.run_id));

  async function act(fn) {
    setBusy(true); setError(null);
    try { await fn(); await listQ.reload(); await detailQ.reload(); }
    catch (e) { setError(e.message || "action failed"); }
    finally { setBusy(false); }
  }

  return (
    <div className="page" data-testid="reports-page">
      <PageHeader icon={<FileText size={17} />} kicker="Analysis" title="Reports"
        sub="Versioned evidence documents composed from a run's sealed artefacts."
        actions={<>
          <button className="btn btn-sm" onClick={listQ.reload}><RefreshCw size={12} /> Refresh</button>
          <Segmented value={status} onChange={setStatus} testidPrefix="report-filter" items={[
            { id: "", label: "All" }, { id: "draft", label: "Draft" },
            { id: "in_review", label: "In review" }, { id: "published", label: "Published" },
          ]} />
        </>} />

      <div className="grid grid-4 mb-3">
        <Tile label="Reports" value={rows.length} tone="accent" />
        <Tile label="Drafts" value={rows.filter((r) => r.status === "draft").length} tone="warn" />
        <Tile label="In review" value={rows.filter((r) => r.status === "in_review").length} />
        <Tile label="Published" value={rows.filter((r) => r.status === "published").length} tone="ok" />
      </div>

      {error && <Notice tone="danger" style={{ marginBottom: 12 }}>{error}</Notice>}

      <div className="split-wide">
        <div className="stack" style={{ gap: 12 }}>
          <Panel title="Report register" icon={<FileCheck size={12} />} flush>
            {listQ.loading && !listQ.data ? <DataState kind="loading" title="Reading reports" />
              : listQ.error ? <DataState kind="error" error={listQ.error} />
                : rows.length === 0 ? (
                  <DataState kind="empty" title="No reports composed"
                    hint="Composing a report snapshots the document from a run's sealed artefacts so it can be versioned and reviewed." />
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Report</th><th>Run</th><th>Status</th>
                          <th className="num">Ver</th><th>Published</th><th />
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r) => (
                          <tr key={r.id} className={selected?.id === r.id ? "row-on" : ""}
                            onClick={() => setSelectedId(r.id)} data-clickable="true"
                            data-testid={`report-${r.id}`}>
                            <td className="mono tiny">{r.id}</td>
                            <td className="mono tiny ellipsis" style={{ maxWidth: 170 }}>{r.run_id}</td>
                            <td><Badge tone={STATUS_TONE[r.status] || "neutral"}>{r.status}</Badge></td>
                            <td className="num mono tiny">v{r.version}</td>
                            <td className="tiny dim">{r.published_utc ? fmt.ago(r.published_utc) : "—"}</td>
                            <td>
                              <Link className="btn btn-xs" to={`/report?run=${r.run_id}`}
                                onClick={(e) => e.stopPropagation()}>Open</Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
          </Panel>

          {canCompose && uncovered.length > 0 && (
            <Panel title={`Runs without a report · ${uncovered.length}`} icon={<Info size={12} />} flush>
              <div className="table-wrap" style={{ maxHeight: 260 }}>
                <table>
                  <thead><tr><th>Run</th><th>Scene</th><th className="num">Real</th><th /></tr></thead>
                  <tbody>
                    {uncovered.slice(0, 12).map((r) => (
                      <tr key={r.run_id}>
                        <td className="mono tiny">{r.run_id}</td>
                        <td className="tiny dim ellipsis" style={{ maxWidth: 150 }}>{r.scene_id || "—"}</td>
                        <td className="num mono tiny">
                          {r.stages_real ?? "?"}/{r.stages_total ?? "?"}
                          {r.stages_mock ? <Badge tone="mock">mock</Badge> : null}
                        </td>
                        <td>
                          <button className="btn btn-xs btn-primary" disabled={busy}
                            onClick={() => act(() => api.composeReport({ run_id: r.run_id }))}
                            data-testid={`compose-${r.run_id}`}>
                            <FileCheck size={11} /> Compose
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}
        </div>

        {/* ------------------------------------------------------ detail --- */}
        {selected ? (
          <ReportDetail report={detailQ.data || selected} busy={busy} act={act}
            canCompose={canCompose} canPublish={canPublish} />
        ) : (
          <Panel title="Document" icon={<FileText size={12} />}>
            <DataState kind="empty" compact title="No report selected" />
          </Panel>
        )}
      </div>
    </div>
  );
}

function ReportDetail({ report: r, busy, act, canCompose, canPublish }) {
  const annex = (r.body?.sections || []).find((s) => s.kind === "provenance_annex");
  const published = r.status === "published";

  return (
    <div className="stack" style={{ gap: 12 }}>
      <Panel title="Document" icon={<FileText size={12} />}
        right={<Badge tone={STATUS_TONE[r.status] || "neutral"} lg>{r.status}</Badge>}>
        <div className="tiny mb-2" style={{ color: "var(--ink-1)" }}>
          {STATUS_LABEL[r.status] || r.status}
        </div>
        <div className="kv-dense">
          <KV k="Report id" v={r.id} wrap />
          <KV k="Run" v={r.run_id} wrap />
          <KV k="Version" v={`v${r.version}`} />
          <KV k="Composed" v={fmt.utc(r.created_utc)} />
          {r.published_utc && <KV k="Published" v={fmt.utc(r.published_utc)} />}
        </div>

        {/* A report composed from bytes that have since been re-sealed is a
            document about a run that no longer exists. */}
        {r.digest_matches_run === false && (
          <Notice tone="danger" testid="report-stale" style={{ marginTop: 10 }}>
            <strong>Stale.</strong> This report was composed from a different set of artefacts
            than the run currently holds. It describes bytes that have since been re-sealed —
            compose a new version before relying on it.
          </Notice>
        )}
        {!published && (
          <Notice tone="warn" style={{ marginTop: 10 }}>
            Not approved. The printable page carries a DRAFT watermark that survives printing, so
            an unapproved page cannot be mistaken for a signed-off one after it leaves the screen.
          </Notice>
        )}

        <div className="globe-actions">
          <Link className="btn btn-primary btn-sm" to={`/report?run=${r.run_id}`}>
            <FileText size={12} /> Open document
          </Link>
          <a className="btn btn-sm" href={`/api/reports/${r.id}/export.csv`}>
            <Download size={12} /> CSV
          </a>
          <a className="btn btn-sm" href={`/api/reports/${r.id}/export.json`}>
            <Download size={12} /> JSON
          </a>
        </div>
      </Panel>

      <Panel title="Review lifecycle" icon={<ShieldCheck size={12} />}>
        <div className="globe-actions" style={{ marginTop: 0 }}>
          {r.status === "draft" && (
            <button className="btn btn-sm" disabled={busy} data-testid="report-submit"
              onClick={() => act(() => api.submitReport(r.id))}>
              <Send size={12} /> Submit for review
            </button>
          )}
          {r.status === "in_review" && (
            <button className="btn btn-primary btn-sm" disabled={busy} data-testid="report-publish"
              onClick={() => act(() => api.publishReport(r.id))}>
              <ShieldCheck size={12} /> Approve and publish
            </button>
          )}
          {published && (
            <button className="btn btn-sm" disabled={busy} data-testid="report-revise"
              onClick={() => act(() => api.reviseReport(r.id))}>
              <Undo2 size={12} /> Start version {r.version + 1}
            </button>
          )}
        </div>
        {published && (
          <div className="tiny dim mt-2" style={{ lineHeight: 1.55 }}>
            Published reports are immutable. Revising composes version {r.version + 1} as a draft
            and leaves this version exactly as approved.
          </div>
        )}
        {!canPublish && r.status === "in_review" && (
          <Notice tone="warn" style={{ marginTop: 8 }}>
            Approving is a reviewer decision. The server refuses it for your role regardless of
            this form.
          </Notice>
        )}
      </Panel>

      {annex && (
        <Panel title="Provenance annex" icon={<CheckCircle2 size={12} />} testid="report-annex">
          <div className="tiny dim mb-2" style={{ lineHeight: 1.55 }}>{annex.note}</div>
          <div className="kv-dense">
            <KV k="Run" v={annex.run_id} wrap />
            <KV k="Artefact digest" v={annex.artefact_digest} wrap />
            <KV k="Code" v={annex.code_git_sha} wrap />
            <KV k="Sealed" v={annex.generated_utc} />
          </div>
          {(annex.sources || []).length > 0 && (
            <>
              <div className="section-label">Sources</div>
              {annex.sources.map((s) => (
                <KV key={s.layer} k={s.layer}
                  v={`${s.provider || "—"} · ${String(s.data_source || "unrecorded").toUpperCase()}`} />
              ))}
            </>
          )}
          {(annex.models || []).length > 0 && (
            <>
              <div className="section-label">Models</div>
              {annex.models.map((m) => (
                <KV key={m.kind} k={m.kind} v={m.name} wrap />
              ))}
            </>
          )}
          <div className="tiny dim mt-2">
            These hashes are the manifest's own, so the table in the report is exactly what{" "}
            <span className="mono">/verify</span> re-checks.
          </div>
        </Panel>
      )}
    </div>
  );
}
