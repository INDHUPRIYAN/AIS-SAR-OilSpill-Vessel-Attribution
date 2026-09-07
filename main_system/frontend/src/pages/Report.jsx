/* Printable investigation report — a browser-print-friendly summary of one
 * run: metadata, provenance per stage, slick geometry, origin window, the
 * factor weights actually used, and the ranked suspects with the raw
 * evidence behind every score. Every number is read from the run's contract
 * files; nothing is recomputed or invented here.
 *
 * Opened from the workspace as /report?run=<run_id>; the browser's own
 * print dialog produces the PDF (no server-side rendering needed).
 */

import { useSearchParams } from "react-router-dom";
import { Printer } from "lucide-react";

import ReportReview from "../components/ReportReview";
import { api, useApi, fmt } from "../lib/api";
import "../report.css";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v))
  ? "—" : Number(v).toFixed(d));

const EVIDENCE = [
  ["closest_approach_km", "Closest approach", (v) => `${num(v, 1)} km`],
  ["time_in_origin_window_min", "In origin window", (v) => `${num(v, 0)} min`],
  ["ais_gap_minutes", "AIS gap", (v) => `${num(v, 0)} min`],
  ["course_delta_deg", "Course change", (v) => `${num(v, 0)}°`],
  ["min_sog_kn", "Slowest speed", (v) => `${num(v, 1)} kn`],
  ["track_points_in_cloud", "Points in cloud", (v) => `${Math.round(v)}`],
];

export default function Report() {
  const [params] = useSearchParams();
  const runId = params.get("run");

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

  if (!runId) {
    return <div className="page rp-missing">No run selected — open the
      report from an investigation (Report button in the Run panel).</div>;
  }

  const stages = run?.manifest?.stages ?? [];
  const sp = slick?.features?.[0]?.properties ?? {};
  const md = origin?.metadata ?? {};
  const weights = suspects?.weights ?? null;
  const lookalikes = (detect?.candidates ?? [])
    .filter((c) => c.class === "lookalike");
  const filtered = suspects?.filtered_out ?? [];

  return (
    <div className="page rp-page">
      <div className="rp-sheet">
        <div className="rp-actions rp-noprint">
          <button className="btn btn-primary btn-sm" onClick={() => window.print()}
            data-testid="report-print">
            <Printer size={13} /> Print / save as PDF
          </button>
        </div>

        <header className="rp-head">
          <div>
            <h1>OceanTrace — Investigation report</h1>
            <div className="rp-sub mono">
              run {runId} · scene {run?.scene_id ?? sceneMeta?.scene_id ?? "—"}
            </div>
          </div>
          <div className="rp-meta mono">
            <div>generated {fmt.utc(new Date().toISOString())}</div>
            <div>pipeline finished {fmt.utc(run?.finished_utc)}</div>
            <div>engine {run?.detect_engine || "—"}</div>
          </div>
        </header>

        <p className="rp-disclaimer">
          Attribution likelihood — investigative support, not proof of guilt.
          Every value below is read verbatim from the run's contract files.
        </p>

        {/* ---------------------------------------------------- provenance */}
        <section>
          <h2>Provenance</h2>
          <table className="rp-table">
            <thead><tr><th>Stage</th><th>Status</th><th>Source</th><th>Detail</th></tr></thead>
            <tbody>
              {stages.map((s) => (
                <tr key={s.stage}>
                  <td>{s.stage}</td>
                  <td><span className={`rp-badge rp-${s.status}`}>{s.status}</span></td>
                  <td><span className={`rp-badge rp-src-${(s.source || "").toLowerCase() || "none"}`}>
                    {(s.source || "—").toUpperCase()}</span></td>
                  <td className="rp-detail">{s.detail || "—"}</td>
                </tr>
              ))}
              {!stages.length && <tr><td colSpan={4}>manifest not available for this run</td></tr>}
            </tbody>
          </table>
        </section>

        {/* -------------------------------------------------------- scene */}
        <section>
          <h2>Scene & slick</h2>
          <div className="rp-grid">
            <Row k="Scene acquired" v={fmt.utc(sceneMeta?.acquired_utc)} />
            <Row k="Scene source" v={(sceneMeta?.source || "—").toUpperCase()} />
            <Row k="Slick area" v={`${num(sp.area_km2)} km²`} />
            <Row k="Perimeter" v={`${num(sp.perimeter_km)} km`} />
            <Row k="Centroid" v={sp.centroid
              ? `${num(sp.centroid[1], 4)}, ${num(sp.centroid[0], 4)}` : "—"} />
            <Row k="Major / minor axis" v={`${num((sp.major_axis_m ?? 0) / 1000)} / ${num((sp.minor_axis_m ?? 0) / 1000)} km`} />
            <Row k="Orientation" v={`${num(sp.orientation_deg, 1)}°`} />
            <Row k="Detection confidence" v={`${num((sp.confidence ?? 0) * 100, 1)}%`} />
            <Row k="Origin window" v={md.origin_window_start_utc
              ? `${fmt.utc(md.origin_window_start_utc)} → ${fmt.utc(md.origin_window_end_utc)}`
              : "—"} />
            <Row k="Look-alikes" v={lookalikes.length
              ? `${lookalikes.length} reported (${lookalikes.map((c) => c.phenomenon || "unclassified").join(", ")}) — not counted as oil`
              : "none reported"} />
          </div>
        </section>

        {/* ------------------------------------------------------ weights */}
        {weights && (
          <section>
            <h2>Scoring weights</h2>
            <p className="rp-note">total_score = Σ weight × factor. The weights
              below are the ones this run actually used (they sum to 1.0).</p>
            <table className="rp-table rp-weights">
              <thead><tr>{Object.keys(weights).map((k) => (
                <th key={k}>{k.replace(/_/g, " ")}</th>))}</tr></thead>
              <tbody><tr>{Object.values(weights).map((w, i) => (
                <td key={i} className="mono">{Number(w).toFixed(2)}</td>))}</tr></tbody>
            </table>
          </section>
        )}

        {/* ----------------------------------------------------- suspects */}
        <section>
          <h2>Ranked suspects
            {suspects?.total_vessels_considered != null &&
              ` (${suspects.suspects?.length ?? 0} ranked of ${suspects.total_vessels_considered} considered)`}
          </h2>
          {(suspects?.suspects ?? []).map((s) => (
            <div key={s.mmsi} className="rp-suspect">
              <div className="rp-suspect-head">
                <span className="rp-rank mono">#{s.rank}</span>
                <span className="rp-name">{s.vessel_name || `MMSI ${s.mmsi}`}</span>
                <span className="mono rp-mmsi">{s.mmsi} · {s.vessel_type}</span>
                <span className="mono rp-score">{Number(s.total_score).toFixed(3)}</span>
              </div>
              <div className="rp-factors">
                {Object.entries(s.sub_scores || {}).map(([k, v]) => (
                  <span key={k} className="rp-factor mono">
                    {k.replace(/_/g, " ")} {Number(v).toFixed(2)}
                    {weights?.[k] != null && <em> ×{Number(weights[k]).toFixed(2)}</em>}
                  </span>
                ))}
              </div>
              <div className="rp-evidence mono">
                {EVIDENCE.map(([key, label, f]) =>
                  s.evidence?.[key] == null ? null : (
                    <span key={key}>{label}: <b>{f(s.evidence[key])}</b></span>
                  ))}
              </div>
              <p className="rp-reason">{s.reason}</p>
            </div>
          ))}
          {!suspects?.suspects?.length && (
            <p className="rp-note">No vessel passed the spatial, temporal and
              trajectory gates for this origin window.</p>
          )}
        </section>

        {/* ----------------------------------------------------- filtered */}
        {filtered.length > 0 && (
          <section>
            <h2>Filtered out ({filtered.length})</h2>
            <table className="rp-table">
              <thead><tr><th>MMSI</th><th>Reason excluded</th></tr></thead>
              <tbody>
                {filtered.map((f) => (
                  <tr key={f.mmsi}>
                    <td className="mono">{f.mmsi}</td>
                    <td>{f.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <ReportReview runId={runId} />

        <footer className="rp-foot">
          OceanTrace · SIH 2026 · PS26143 — SAR oil-spill detection, drift
          modelling and AIS vessel attribution. Suspect source flag:{" "}
          <b>{(suspects?.source || "—").toUpperCase()}</b>.
        </footer>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="rp-row">
      <span className="rp-k">{k}</span>
      <span className="rp-v mono">{v}</span>
    </div>
  );
}
