/* The review lifecycle and provenance annex for one run's report.
 *
 * The printable body is still rendered by Report.jsx from the run's layers —
 * that layout works and rewriting it would risk the one page most likely to be
 * screenshotted. What was missing is everything that makes a report a document
 * rather than a view:
 *
 *   - a version and a review state (draft → in_review → published);
 *   - a DRAFT watermark, so an unapproved page can never be mistaken for a
 *     signed-off one after it leaves the screen as a PDF;
 *   - the Data Provenance Annex, whose hashes are the manifest's own, so the
 *     table in the report is exactly what /verify re-checks;
 *   - a stale-digest warning, because a report composed from bytes that have
 *     since been re-sealed is a document about a run that no longer exists.
 *
 * Every action here is role-gated by the API. The buttons are shown to
 * everyone and the server refuses what the role may not do — hiding a control
 * would teach an operator the capability does not exist, rather than that they
 * lack the role for it.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, FileCheck, Send, ShieldCheck } from "lucide-react";

import { api, fmt } from "../lib/api";

const STATUS_LABEL = {
  draft: "DRAFT — not reviewed",
  in_review: "IN REVIEW — awaiting approval",
  published: "PUBLISHED — immutable",
};

export default function ReportReview({ runId }) {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!runId) return;
    try {
      const list = await api.listReports({ run: runId });
      if (!list.length) { setReport(null); return; }
      // Newest version wins; earlier published versions stay readable by id.
      const newest = list.reduce((a, b) => (b.version > a.version ? b : a));
      setReport(await api.getReport(newest.id));
    } catch (e) {
      setError(e.message);
    }
  }, [runId]);

  useEffect(() => { load(); }, [load]);

  async function act(fn) {
    setBusy(true); setError(null);
    try { await fn(); await load(); }
    catch (e) { setError(e.message || "action failed"); }
    finally { setBusy(false); }
  }

  if (!runId) return null;

  /* ------------------------------------------------------ not composed -- */
  if (!report) {
    return (
      <section className="rp-section rp-noprint">
        <h2>Report record</h2>
        <p className="rp-note">
          This run has no composed report yet. Composing one snapshots the
          document from the run’s sealed artefacts so it can be versioned and
          reviewed.
        </p>
        {error && <p className="rp-warn">{error}</p>}
        <button className="btn btn-primary btn-sm" disabled={busy}
          data-testid="report-compose"
          onClick={() => act(() => api.composeReport({ run_id: runId }))}>
          <FileCheck size={13} /> Compose report
        </button>
      </section>
    );
  }

  const annex = (report.body?.sections || [])
    .find((s) => s.kind === "provenance_annex");
  const published = report.status === "published";

  return (
    <>
      {!published && (
        <div className="rp-watermark" aria-hidden="true"
          data-testid="report-watermark">DRAFT</div>
      )}

      <section className="rp-section">
        <h2>Report record</h2>
        <div className="rp-review rp-noprint">
          <span className={`rp-badge rp-badge-${report.status}`}
            data-testid="report-status">
            {STATUS_LABEL[report.status] || report.status}
          </span>
          <span className="mono">v{report.version}</span>
          {report.published_utc && (
            <span className="mono">published {fmt.utc(report.published_utc)}</span>
          )}
        </div>

        {report.digest_matches_run === false && (
          <p className="rp-warn" data-testid="report-stale">
            <AlertTriangle size={12} /> This report was composed from a
            different set of artefacts than the run currently holds. It
            describes bytes that have since been re-sealed — compose a new
            version before relying on it.
          </p>
        )}

        {error && <p className="rp-warn">{error}</p>}

        <div className="rp-review rp-noprint">
          {report.status === "draft" && (
            <button className="btn btn-sm" disabled={busy} data-testid="report-submit"
              onClick={() => act(() => api.submitReport(report.id))}>
              <Send size={12} /> Submit for review
            </button>
          )}
          {report.status === "in_review" && (
            <button className="btn btn-primary btn-sm" disabled={busy}
              data-testid="report-publish"
              onClick={() => act(() => api.publishReport(report.id))}>
              <ShieldCheck size={12} /> Approve and publish
            </button>
          )}
          {published && (
            <button className="btn btn-sm" disabled={busy} data-testid="report-revise"
              onClick={() => act(() => api.reviseReport(report.id))}>
              <CheckCircle2 size={12} /> Start version {report.version + 1}
            </button>
          )}
          <a className="btn btn-sm" href={`/api/reports/${report.id}/export.csv`}
            data-testid="report-csv">Export CSV</a>
          <a className="btn btn-sm" href={`/api/reports/${report.id}/export.json`}>
            Export JSON
          </a>
        </div>

        {published && (
          <p className="rp-note">
            Published reports are immutable. Revising this one composes version
            {" "}{report.version + 1} as a draft and leaves this version exactly
            as approved.
          </p>
        )}
      </section>

      {/* ------------------------------------------ data provenance annex -- */}
      {annex && (
        <section className="rp-section" data-testid="report-annex">
          <h2>{annex.title}</h2>
          <p className="rp-note">{annex.note}</p>

          <dl className="rp-kv">
            <dt>Run</dt><dd className="mono">{annex.run_id}</dd>
            <dt>Artefact digest</dt><dd className="mono">{annex.artefact_digest}</dd>
            <dt>Code</dt><dd className="mono">{annex.code_git_sha}</dd>
            <dt>Sealed</dt><dd className="mono">{annex.generated_utc}</dd>
          </dl>

          <h3>Artefacts</h3>
          <table className="rp-table">
            <thead><tr><th>file</th><th>bytes</th><th>sha256</th></tr></thead>
            <tbody>
              {(annex.artefacts || []).map((a) => (
                <tr key={a.name || a.path}>
                  <td className="mono">{a.name || a.path}</td>
                  <td className="mono">{fmt.int(a.bytes)}</td>
                  <td className="mono rp-hash">{a.sha256}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Sources</h3>
          <table className="rp-table">
            <thead><tr><th>layer</th><th>provider</th><th>data source</th></tr></thead>
            <tbody>
              {(annex.sources || []).map((s) => (
                <tr key={s.layer}>
                  <td>{s.layer}</td>
                  <td>{s.provider || "—"}</td>
                  <td className="mono">{s.data_source || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Models</h3>
          <table className="rp-table">
            <thead><tr><th>kind</th><th>name</th><th>sha256</th></tr></thead>
            <tbody>
              {(annex.models || []).map((m) => (
                <tr key={m.kind}>
                  <td>{m.kind}</td>
                  <td className="mono">{m.name}</td>
                  <td className="mono rp-hash">{m.sha256}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
