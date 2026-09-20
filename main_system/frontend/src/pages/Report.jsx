/* Printable investigation report — a browser-print-friendly document for one
 * run. The page owns the data loading (one useApi per artefact, a missing
 * layer resolves to null so a partial run yields a partial, honest report)
 * and hands everything to <IncidentReport/>, which renders the numbered
 * eight-section paper layout. Every number is read from the run's contract
 * files; nothing is recomputed or invented here.
 *
 * Opened from the workspace as /report?run=<run_id>; the browser's own
 * print dialog produces the PDF (no server-side rendering needed).
 */

import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { Printer } from "lucide-react";

import IncidentReport from "../components/report/IncidentReport";
import ReportReview from "../components/ReportReview";
import { api, useApi } from "../lib/api";
import "../report.css";

export default function Report() {
  // The run is the address: /reports/print/:run.
  const { run: runId } = useParams();

  // One useApi per artefact; a missing layer resolves to null instead of
  // erroring, so a partial run still yields a partial (honest) report.
  const opt = (name, opts) =>
    (runId ? api.layer(runId, name, opts).catch(() => null)
           : Promise.resolve(null));
  const { data: run } = useApi(
    () => (runId ? api.getRun(runId).catch(() => null) : Promise.resolve(null)),
    [runId]);
  const { data: sceneMeta } = useApi(() => opt("scene_meta"), [runId]);
  const { data: slick } = useApi(() => opt("slick"), [runId]);
  const { data: suspects } = useApi(() => opt("suspects"), [runId]);
  const { data: detect } = useApi(() => opt("detect"), [runId]);
  const { data: origin } = useApi(() => opt("origin_cloud", { lite: true }), [runId]);
  const { data: forecast } = useApi(() => opt("forecast"), [runId]);
  const { data: funnel } = useApi(
    () => (runId ? api.runFunnel(runId).catch(() => null) : Promise.resolve(null)),
    [runId]);
  // The incident is only known once the run row is in; a run that never
  // passed the auto-incident gate has no incident_id and renders as such.
  const incidentId = run?.incident_id ?? null;
  const { data: incident } = useApi(
    () => (incidentId ? api.getIncident(incidentId).catch(() => null) : Promise.resolve(null)),
    [incidentId]);
  const { data: reports } = useApi(
    () => (runId ? api.listReports({ run: runId }).catch(() => []) : Promise.resolve([])),
    [runId]);
  // Newest version wins, which is the first row unless the API returns
  // versions out of order.
  const report = useMemo(() => {
    const list = Array.isArray(reports) ? reports : [];
    if (!list.length) return null;
    return list.reduce((a, b) => ((b?.version ?? 0) > (a?.version ?? 0) ? b : a), list[0]);
  }, [reports]);
  // Stamped once per page load, not per render, so the header does not tick.
  const generatedUtc = useMemo(() => new Date().toISOString(), []);

  if (!runId) {
    return <div className="page rp-missing">No run selected — open the
      report from an investigation (Report button in the Run panel).</div>;
  }

  return (
    <div className="page rp-page">
      <div className="rp-sheet">
        <div className="rp-actions rp-noprint">
          <button className="btn btn-primary btn-sm" onClick={() => window.print()}
            data-testid="report-print">
            <Printer size={13} /> Print / save as PDF
          </button>
        </div>

        <IncidentReport
          runId={runId}
          run={run ?? null}
          sceneMeta={sceneMeta ?? null}
          slick={slick ?? null}
          detect={detect ?? null}
          origin={origin ?? null}
          forecast={forecast ?? null}
          suspects={suspects ?? null}
          funnel={funnel ?? null}
          incident={incident ?? null}
          report={report}
          generatedUtc={generatedUtc}
        />

        <ReportReview runId={runId} />
      </div>
    </div>
  );
}
