/* The incident report document: one printable page, eight numbered sections,
 * every value read verbatim from the run's artefacts.
 *
 * This component is deliberately pure: it takes the layers the page already
 * fetched (run row, scene meta, slick, detect, origin cloud, forecast,
 * suspects, funnel, incident, report record) and renders paper. Nothing is
 * recomputed except shape ratios and sums that are stated as derived. A
 * missing value renders "—" or an explicit "not produced" / "not recorded";
 * it is never invented, because a PDF of this page will outlive the run.
 *
 * Wording rule (project-wide): the ranking is investigative support. The
 * words "guilty", "responsible vessel" and "confirmed culprit" never appear.
 *
 * `compact` renders the same document in a single column with tighter type
 * for a ~340px side-panel preview.
 */

import { fmt } from "../../lib/api";
import { originEstimate, screenVerdict } from "../../lib/drift";
import { guessPlace } from "../../lib/replay";
import { sourceBadge } from "../workspace/palette";

const DASH = "—";
const num = (v, d = 2) => (v == null || Number.isNaN(Number(v))
  ? DASH : Number(v).toFixed(d));
const text = (v) => (v == null || v === "" ? DASH : String(v));
const shortHash = (h) => (h ? String(h).slice(0, 12) : DASH);

const REPORT_STATUS = {
  draft: "DRAFT — not reviewed",
  in_review: "IN REVIEW — awaiting approval",
  published: "PUBLISHED — immutable",
};

/** Where the AIS bytes came from, in the report's own vocabulary.
 *  "real" is the legacy execution flag for a replayed archive file, so it is
 *  stated as HISTORICAL rather than as live traffic. */
function aisSourceBadge(ds) {
  switch ((ds || "").toLowerCase()) {
    case "sensor":
    case "live": return { label: "REAL", tone: "ok", note: "live AIS feed" };
    case "real":
    case "historical":
    case "archive": return { label: "HISTORICAL", tone: "ok", note: "historical archive" };
    case "synthetic":
    case "mock": return { label: "SYNTHETIC", tone: "mock",
      note: "synthetic AIS — generated traffic, not real vessels" };
    case "cached": return { label: "CACHED", tone: "warn", note: "cached archive" };
    default: return { label: "NOT RECORDED", tone: "neutral", note: "AIS source not recorded" };
  }
}

function shapeOf(majorM, minorM) {
  if (!(majorM > 0) || !(minorM > 0)) return null;
  const ratio = majorM / minorM;
  const word = ratio >= 3 ? "Elongated" : ratio >= 1.5 ? "Oblong" : "Compact";
  return `${word} (axis ratio ${ratio.toFixed(1)}:1)`;
}

/* ------------------------------------------------------------ primitives */

function Badge({ tone = "neutral", children, testId }) {
  return (
    <span className={`ir-badge ir-tone-${tone}`} data-testid={testId}>{children}</span>
  );
}

function KV({ rows }) {
  return (
    <table className="ir-kv">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><th scope="row">{k}</th><td>{v}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

function Section({ n, title, children, className = "" }) {
  return (
    <section className={`ir-section ${className}`}>
      <h2 className="ir-h">{n}. {title}</h2>
      {children}
    </section>
  );
}

/* --------------------------------------------------------------- report */

export default function IncidentReport({
  runId, run, sceneMeta, slick, detect, origin, forecast, suspects, funnel,
  incident, report, generatedUtc, compact = false,
}) {
  const manifest = run?.manifest ?? {};
  const stages = manifest.stages ?? [];
  const aisBlock = manifest.ais ?? null;
  const features = slick?.features ?? [];
  // The analysed slick is features[0] (the one drift and attribution trace);
  // the "largest" figure is measured, not assumed.
  const sp = features[0]?.properties ?? {};
  const largest = features.reduce((a, f) =>
    ((f?.properties?.area_km2 ?? -1) > (a?.properties?.area_km2 ?? -1) ? f : a), null)?.properties;
  const combinedArea = features.length
    ? features.reduce((s, f) => s + (Number(f?.properties?.area_km2) || 0), 0) : null;
  const md = origin?.metadata ?? {};
  const est = originEstimate(origin);
  const verdict = screenVerdict(sp, detect);
  const forcing = md.forcing || forecast?.metadata?.forcing || null;
  const envelopes = (forecast?.features ?? []).map((f) => f.properties || {})
    .sort((a, b) => (a.horizon_h - b.horizon_h) || (a.confidence_level - b.confidence_level));
  const horizons = [...new Set(envelopes.map((e) => e.horizon_h))];
  const weights = suspects?.weights ?? null;
  const ranked = suspects?.suspects ?? [];
  const ais = aisSourceBadge(aisBlock?.data_source ?? suspects?.source);
  const synthetic = ais.tone === "mock";
  const fallbackStages = stages.filter((s) =>
    [s.status, s.source, s.engine_used].some((x) => String(x || "").toLowerCase() === "fallback"));
  const sha = manifest.code_git_sha ? String(manifest.code_git_sha).slice(0, 8) : null;
  const modelVersion = slick?.metadata?.model_version ?? detect?.model_version ?? null;
  const engine = sp.engine ?? detect?.engine ?? run?.detect_engine ?? null;
  const reportStatus = report?.status ?? null;
  const draft = !reportStatus || reportStatus === "draft";
  const reportId = report?.id ?? (runId ? `OT-IR-${runId}` : DASH);

  // Analysis area: the slick centroid, else the centre of the scene bbox.
  const bbox = Array.isArray(sceneMeta?.bbox) && sceneMeta.bbox.length === 4 ? sceneMeta.bbox : null;
  const place = Array.isArray(sp.centroid) ? guessPlace(sp.centroid)
    : bbox ? guessPlace([(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]) : DASH;

  // Where the scene's time and place come from. Only a Sentinel-1 product
  // states them; a corpus scene's were assigned and the report must say so.
  const basis = sceneMeta?.basis || null;
  const measured = !basis || basis.geo_basis === "measured";
  const sensorText = basis?.label === "REAL" || !basis ? "Sentinel-1 SAR (GRD)"
    : basis.label === "SYNTHETIC" ? "Synthetic test raster"
    : basis.label === "UPLOADED" ? "Uploaded SAR raster" : "SAR research-corpus scene";
  const sceneSrc = sourceBadge(sceneMeta?.source);
  const slickSrc = sourceBadge(sp.source ?? slick?.metadata?.source);

  const originText = est
    ? `${num(est.center[1], 4)}, ${num(est.center[0], 4)} (± ${num(est.radiusKm)} km)`
    : "not produced";
  const windowText = est
    ? `${fmt.utc(est.windowStartUtc)} → ${fmt.utc(est.windowEndUtc)} (${num(est.windowHours, 1)} h)`
    : md.origin_window_start_utc
      ? `${fmt.utc(md.origin_window_start_utc)} → ${fmt.utc(md.origin_window_end_utc)}`
      : "not produced";

  const forcingText = (f) => {
    if (!f) return "not recorded";
    const base = text(f.provider);
    return f.fallback ? `${base} — FALLBACK (${f.fallback})` : base;
  };

  return (
    <article className={`ir-doc ${compact ? "ir-compact" : ""}`} data-testid="incident-report">
      {!report && !compact && (
        <div className="rp-watermark" aria-hidden="true" data-testid="ir-watermark">DRAFT</div>
      )}

      {/* ----------------------------------------------------------- head */}
      <header className="ir-head">
        <div className="ir-brand">
          <span className="ir-brand-mark" aria-hidden="true" />
          <div>
            <div className="ir-brand-name">OCEANTRACE</div>
            <div className="ir-brand-sub">MARITIME INTELLIGENCE</div>
          </div>
        </div>
        <div className="ir-head-meta mono">
          <div className="ir-head-kind">INCIDENT REPORT</div>
          <div>REPORT ID: {reportId}</div>
          <div>GENERATED ON: {fmt.utc(generatedUtc)}</div>
        </div>
      </header>

      <div className="ir-titleblock">
        <h1 className="ir-title">
          INCIDENT REPORT
          <span className="rp-sr"> — OceanTrace Investigation report</span>
        </h1>
        <div className="ir-subtitle">Oil Spill Detection and Vessel Correlation Analysis</div>
        <div className="rp-sub mono">
          run {text(runId)} · scene {text(run?.scene_id ?? sceneMeta?.scene_id)}
          {run?.finished_utc ? ` · pipeline finished ${fmt.utc(run.finished_utc)}` : ""}
        </div>
      </div>

      {/* ----------------------------------------------- 1. overview */}
      <Section n={1} title="INCIDENT OVERVIEW">
        <div className="ir-cols ir-cols-overview">
          <KV rows={[
            ["Incident ID", incident?.id
              ? <span className="mono">{incident.id}</span>
              : <span className="ir-muted">not opened — auto-incident gate not passed / not run</span>],
            ["Detection Timestamp (UTC)", <span className="mono">{fmt.utc(sceneMeta?.acquired_utc)}</span>],
            ["Detected Area (Slick Polygon)", largest
              ? `${num(largest.area_km2)} km² (largest of ${features.length} region${features.length === 1 ? "" : "s"}; combined ${num(combinedArea)} km²)`
              : "not produced"],
            ["Estimated Origin (Uncertainty)", <span className="mono">{originText}</span>],
            ["Analysis Area", measured ? place
              : <span data-testid="ir-basis">{place} <strong>({basis.geo_basis === "assigned" ? "position and time ASSIGNED, not measured"
                : basis.geo_basis === "synthetic" ? "synthetic scene, depicts no place" : basis.geo_basis.replace(/_/g, " ")})</strong></span>],
            ["Data Source", `${sensorText} · ${sceneMeta?.provider_used || "provider not recorded"}`],
            ["Analysis Product", text(sceneMeta?.polarisation)],
            ["Analyst / System", sha ? <>OceanTrace <span className="mono">build {sha}</span></> : "OceanTrace (code sha not recorded)"],
          ]} />
          <aside className="ir-statusbox">
            <div className="ir-statusbox-head">INCIDENT STATUS</div>
            <div className="ir-statusbox-value" data-testid="ir-incident-status">
              <span className={`ir-dot ir-dot-${incident?.status ? "on" : "off"}`} aria-hidden="true" />
              {incident?.status ? incident.status.replace(/_/g, " ") : "no incident"}
              {incident?.severity ? <span className="ir-muted"> · severity {incident.severity}</span> : null}
            </div>
            <KV rows={[
              ["Report Type", "Oil Spill Detection"],
              ["Generated By", "OceanTrace Analytics"],
              ["Classification", "Decision Support"],
              ["Report Status", <Badge tone={reportStatus === "published" ? "ok" : reportStatus === "in_review" ? "info" : "warn"}
                testId="ir-report-status">
                {reportStatus ? (REPORT_STATUS[reportStatus] || reportStatus) : REPORT_STATUS.draft}
              </Badge>],
              ...(report ? [["Version", <span className="mono">v{text(report.version)}</span>]] : []),
            ]} />
            <p className="ir-statusbox-note">
              {draft ? "DRAFT — not reviewed. " : ""}
              This report is based on satellite, AIS and environmental data analysis.
            </p>
          </aside>
        </div>
      </Section>

      {/* ----------------------------------------- 2. satellite evidence */}
      <Section n={2} title="SATELLITE EVIDENCE (SENTINEL-1 SAR)">
        <div className="ir-cols ir-cols-figure">
          <figure className="ir-figure">
            {runId ? (
              <div className="ir-figure-frame">
                <img className="ir-figure-scene" src={`/api/runs/${runId}/scene_png`}
                  alt="Sentinel-1 SAR scene, grayscale" />
                <img className="ir-figure-mask" src={`/api/runs/${runId}/mask_png`}
                  alt="" aria-hidden="true" />
              </div>
            ) : (
              <div className="ir-figure-frame ir-figure-none">figure not available — no run selected</div>
            )}
            <figcaption>
              Figure 1. Sentinel-1 SAR image showing detected oil slick (grayscale); amber = segmenter mask.
            </figcaption>
          </figure>
          <KV rows={[
            ["Acquisition Time (UTC)", <span className="mono">{fmt.utc(sceneMeta?.acquired_utc)}</span>],
            ["Sensor / Mission", "Sentinel-1"],
            ["Polarization", text(sceneMeta?.polarisation)],
            ["Product Type", text(sceneMeta?.product_type ?? (basis?.label === "REAL" || !basis ? "GRD" : null))],
            ["Spatial Resolution", sceneMeta?.pixel_spacing_m != null
              ? `${num(sceneMeta.pixel_spacing_m, 0)} m / px` : "not recorded"],
            ["Scene ID", <span className="mono ir-break">{text(sceneMeta?.scene_id ?? run?.scene_id)}</span>],
            ["Scene Source", <Badge tone={sceneSrc.tone} testId="ir-scene-source">{sceneSrc.label}</Badge>],
            ...(sceneMeta?.crs ? [["CRS", <span className="mono">{sceneMeta.crs}</span>]] : []),
          ]} />
        </div>
      </Section>

      {/* ------------------------------- 3 + 4: slick and origin, side by side */}
      <div className="ir-cols ir-cols-pair">
        <Section n={3} title="SLICK CHARACTERIZATION">
          {features.length ? (
            <KV rows={[
              ["Area", `${num(sp.area_km2)} km²`],
              ["Length (major axis)", sp.major_axis_m != null ? `${num(sp.major_axis_m / 1000)} km` : DASH],
              ["Width (minor axis)", sp.minor_axis_m != null ? `${num(sp.minor_axis_m / 1000)} km` : DASH],
              ["Orientation", sp.orientation_deg != null ? `${num(sp.orientation_deg, 1)}°` : DASH],
              ["Perimeter", sp.perimeter_km != null ? `${num(sp.perimeter_km)} km` : DASH],
              ["Shape Description", shapeOf(sp.major_axis_m, sp.minor_axis_m) ?? "not derivable (axes not recorded)"],
              ["Detection Confidence", sp.confidence != null ? `${num(sp.confidence * 100, 1)}%` : DASH],
              ["Estimated Age", sp.age_hours_estimate != null
                ? <span data-testid="ir-age">{num(sp.age_hours_estimate, 1)} h · <b>Confidence: LOW</b> ({sp.age_method || "heuristic"})</span>
                : "not produced"],
              ["Screen Verdict", verdict === "oil"
                ? "OIL — inside a region the screening model confirmed"
                : verdict === "lookalike"
                  ? "LOOK-ALIKE — inside the box of a region the screening model rejected"
                  : "not determinable from the candidates"],
              ["Model Version", <span className="mono ir-break">{text(modelVersion)}</span>],
              ["Engine", <>
                <span className="mono">{text(engine)}</span>{" "}
                <Badge tone={slickSrc.tone}>{slickSrc.label}</Badge>
              </>],
              ["Analysed Region", `${text(sp.slick_id)} of ${features.length}`],
            ]} />
          ) : <p className="ir-note">No slick characterisation was produced for this run.</p>}
        </Section>

        <Section n={4} title="ESTIMATED ORIGIN & DRIFT ANALYSIS">
          <KV rows={[
            ["Estimated Origin", <span className="mono">{originText}</span>],
            ["Origin Window", <span className="mono">{windowText}</span>],
            ["Window Method", text(est?.method ?? md.origin_window_method)],
            ["Uncertainty", md.origin_uncertainty_km != null
              ? `± ${num(md.origin_uncertainty_km)} km${md.origin_uncertainty_coverage != null ? ` at ${num(md.origin_uncertainty_coverage * 100, 0)}% coverage` : ""}${md.origin_uncertainty_method ? ` (${md.origin_uncertainty_method})` : ""}`
              : est ? `± ${num(est.radiusKm)} km (${est.radiusBasis})` : "not produced"],
            ["Hindcast", md.backtrack_hours != null || md.n_particles != null
              ? `${text(md.backtrack_hours)} h backtrack · ${text(md.n_particles)} particles · ${text(md.timestep_minutes)} min step`
              : "not produced"],
            ["Forecast", horizons.length
              ? <ul className="ir-list ir-list-tight">
                  {horizons.map((h) => {
                    const rows = envelopes.filter((e) => e.horizon_h === h);
                    return (
                      <li key={h} className="mono">
                        +{h} h → {fmt.utc(rows[0]?.valid_utc)}:{" "}
                        {rows.map((e) => `${e.confidence_level != null ? `${num(e.confidence_level * 100, 0)}%` : "?"} ${num(e.area_km2, 1)} km²`).join(" · ")}
                      </li>
                    );
                  })}
                </ul>
              : "not produced"],
            ["Environmental Data", <>
              Currents: {forcingText(forcing?.currents)}<br />
              Wind: {forcingText(forcing?.wind)}
            </>],
            ["Drift Physics", forcing
              ? `${text(forcing.engine)} integrator · windage ${forcing.windage != null ? `${num(forcing.windage * 100, 1)}%` : DASH} · ML residual ${forcing.ml_residual?.applied ? "applied" : "not applied"}`
              : "not recorded"],
          ]} />
          {est?.weak && (
            <p className="ir-note ir-warn" data-testid="ir-weak-window">
              The convergence peak sits at the image acquisition time: the drift did not
              localise a release earlier than the image. The origin window is a window,
              not a discharge time.
            </p>
          )}
        </Section>
      </div>

      {/* ------------------------------------------------ 5. AIS correlation */}
      <Section n={5} title="AIS CORRELATION">
        <div className="ir-cols ir-cols-pair">
          <KV rows={[
            ["AIS Source", <>
              <Badge tone={ais.tone} testId="ir-ais-source">{ais.label}</Badge>
              <span className="ir-muted"> {ais.note}</span>
              {aisBlock?.detail ? <span className="ir-muted"> · {aisBlock.detail}</span> : null}
            </>],
            ["Time Window", <span className="mono">{windowText}</span>],
            ["Vessels Found", funnel ? text(funnel.found ?? funnel.found_note ?? "not recorded") : "not produced"],
            ["Vessels Indexed", text(funnel?.indexed ?? suspects?.total_vessels_considered)],
            ["After Spatial Gate", funnel ? text(funnel.after_spatial) : "not produced"],
            ["After Temporal Gate", funnel ? text(funnel.after_temporal) : "not produced"],
            ["After Trajectory Gate", funnel ? text(funnel.after_trajectory) : "not produced"],
            ["Candidates", text(funnel?.candidates ?? (suspects ? ranked.length : null))],
            ["Filtered", text(funnel?.filtered ?? (suspects ? (suspects.filtered_out ?? []).length : null))],
          ]} />
          <div>
            <div className="ir-kv-caption">Exclusion reasons</div>
            {funnel?.reasons_histogram && Object.keys(funnel.reasons_histogram).length ? (
              <table className="ir-table">
                <thead><tr><th>Reason</th><th className="ir-num">Vessels</th></tr></thead>
                <tbody>
                  {Object.entries(funnel.reasons_histogram).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td className="ir-num mono">{v}</td></tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="ir-note">{funnel ? "no exclusions recorded" : "funnel not produced for this run"}</p>}
            {funnel?.note && <p className="ir-note">{funnel.note}</p>}
          </div>
        </div>
      </Section>

      {/* ------------------------------------------ 6. candidate ranking */}
      <Section n={6} title="CANDIDATE VESSEL RANKING">
        {ranked.length ? (
          <table className="ir-table ir-suspects">
            <caption className="ir-caption">
              Potential Source Vessel
              {suspects?.total_vessels_considered != null &&
                <span className="ir-muted"> — {ranked.length} ranked of {suspects.total_vessels_considered} considered</span>}
            </caption>
            <thead>
              <tr>
                <th>#</th><th>MMSI</th><th>Vessel type</th>
                <th className="ir-num">Similarity score</th>
                <th className="ir-num">Time difference</th>
                <th className="ir-num">Distance</th>
              </tr>
            </thead>
            {ranked.map((s) => (
              <tbody key={s.mmsi ?? s.rank} className={`rp-suspect ${s.rank === 1 ? "ir-top" : ""}`}>
                <tr data-testid={`report-suspect-${s.rank}`}>
                  <td className="mono">
                    {s.rank}
                    {s.rank === 1 && <div className="ir-top-label">Highest-Ranked Candidate</div>}
                  </td>
                  <td className="mono">
                    {text(s.mmsi)}
                    {s.vessel_name && <div className="ir-muted">{s.vessel_name}</div>}
                  </td>
                  <td>{text(s.vessel_type)}</td>
                  <td className="ir-num mono">{num(s.total_score, 3)}</td>
                  <td className="ir-num mono">{s.evidence?.ais_gap_minutes != null ? `${num(s.evidence.ais_gap_minutes, 0)} min` : DASH}</td>
                  <td className="ir-num mono">{s.evidence?.closest_approach_km != null ? `${num(s.evidence.closest_approach_km, 1)} km` : DASH}</td>
                </tr>
                <tr className="ir-suspect-reason">
                  <td />
                  <td colSpan={5}>
                    {s.sub_scores && (
                      <span className="ir-factors mono">
                        {Object.entries(s.sub_scores).map(([k, v]) => (
                          <span key={k} className="ir-factor">{k.replace(/_/g, " ")} {num(v, 2)}</span>
                        ))}
                      </span>
                    )}
                    <span className="rp-reason">{text(s.reason)}</span>
                  </td>
                </tr>
              </tbody>
            ))}
          </table>
        ) : (
          <p className="ir-note">
            {suspects ? "No vessel passed the spatial, temporal and trajectory gates for this origin window."
                      : "Attribution was not produced for this run."}
          </p>
        )}

        {weights && (
          <div className="ir-weights-wrap">
            <div className="ir-kv-caption">Weights used (total_score = Σ weight × factor)</div>
            <table className="ir-table rp-weights">
              <thead><tr>{Object.keys(weights).map((k) => <th key={k}>{k.replace(/_/g, " ")}</th>)}</tr></thead>
              <tbody><tr>{Object.entries(weights).map(([k, w]) => (
                <td key={k} className="mono">{Number(w).toFixed(2)}</td>))}</tr></tbody>
            </table>
          </div>
        )}
        <p className="ir-note ir-ranking-note">
          Vessels are ranked based on spatio-temporal proximity, trajectory consistency
          and behavioural analysis. This does not establish responsibility.
        </p>
      </Section>

      {/* ------------------------------- 7 + 8: provenance and disclaimer */}
      <div className="ir-cols ir-cols-pair ir-cols-tail">
        <Section n={7} title="DATA SOURCES & PROVENANCE">
          <ul className="ir-list">
            <li>{sensorText} — {sceneMeta?.provider_used || "provider not recorded"}
              {sceneMeta?.source ? <> (<span className="mono">{sceneMeta.source}</span>)</> : null}</li>
            <li>AIS — {aisBlock?.detail || aisBlock?.file || "not recorded in this run's manifest"}
              {" "}(<span className="mono">{aisBlock?.data_source || suspects?.source || "unrecorded"}</span>)</li>
            <li>Ocean currents — {forcingText(forcing?.currents)}</li>
            <li>Wind — {forcingText(forcing?.wind)}</li>
            <li>Models — <span className="mono ir-break">{text(modelVersion)}</span>
              {engine ? <> · engine <span className="mono">{engine}</span></> : null}</li>
          </ul>
          {stages.length ? (
            <table className="ir-table ir-stages">
              <thead><tr><th>Stage</th><th>Status</th><th>Data source</th><th>Source</th></tr></thead>
              <tbody>
                {stages.map((s) => (
                  <tr key={s.stage}>
                    <td className="mono">{s.stage}</td>
                    <td><Badge tone={s.status === "ok" ? "ok" : s.status === "failed" ? "bad" : s.status === "mock" ? "mock" : "warn"}>{text(s.status)}</Badge></td>
                    <td className="mono">{text(s.data_source)}</td>
                    <td><Badge tone={sourceBadge(s.source).tone}>{sourceBadge(s.source).label}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="ir-note">manifest not available for this run</p>}
          <KV rows={[
            ["Artefact Digest", <span className="mono">{shortHash(manifest.artefact_digest)}</span>],
            ...(report ? [
              ["Report", <span className="mono">{text(report.id)} · v{text(report.version)} · {text(report.status)}</span>],
              ["Report Digest", <span className="mono">{shortHash(report.artefact_digest)}</span>],
              ...(report.published_utc ? [["Published", <span className="mono">{fmt.utc(report.published_utc)}</span>]] : []),
            ] : [["Report", <span className="ir-muted">not composed — this is a live view of the run's artefacts</span>]]),
          ]} />
        </Section>

        <Section n={8} title="DISCLAIMER">
          <p className="ir-disclaimer">
            This report provides a decision-support analysis based on available
            satellite, AIS and environmental data. The identification of potential
            source vessels is probabilistic and does not establish legal responsibility.
          </p>
          <div className="ir-kv-caption">Stated uncertainties</div>
          <ul className="ir-list" data-testid="ir-uncertainties">
            {sp.age_hours_estimate != null && (
              <li>Slick age estimate is LOW confidence ({sp.age_method || "heuristic"}).</li>)}
            {synthetic && (
              <li className="ir-warn">AIS traffic in this run is SYNTHETIC; the ranking does not describe real vessels.</li>)}
            {est?.weak && (
              <li>The origin window was not narrowed earlier than the image time (weak convergence).</li>)}
            {verdict === "lookalike" && (
              <li>The analysed slick lies inside a region the screening model classed as a look-alike.</li>)}
            {fallbackStages.length > 0 && (
              <li>Fallback engines or data served: {fallbackStages.map((s) => s.stage).join(", ")}.</li>)}
            {!features.length && <li>No slick characterisation was produced.</li>}
            {!est && <li>No hindcast origin was produced.</li>}
            {!suspects && <li>No AIS attribution was produced.</li>}
            {!sp.age_hours_estimate && !synthetic && !est?.weak && verdict !== "lookalike"
              && !fallbackStages.length && features.length > 0 && est && suspects && (
              <li>No additional caveats were recorded by the run.</li>)}
          </ul>
        </Section>
      </div>

      <footer className="ir-foot">
        <span>OCEANTRACE | MARITIME INTELLIGENCE FOR A CLEANER OCEAN</span>
        <span>Page 1 of 1</span>
      </footer>
    </article>
  );
}
