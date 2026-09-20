/* The incident report document.
 *
 * What is pinned: the eight numbered sections render in order under their
 * exact headings; the rank-1 candidate is labelled "Highest-Ranked
 * Candidate" and the page never uses the word "guilty"; an age estimate is
 * always stated with "Confidence: LOW"; a synthetic AIS run says SYNTHETIC
 * loudly and a replayed archive says HISTORICAL; and anything the run did
 * not produce is stated as such rather than filled in.
 *
 * Fixture values mirror the flagship's artefacts (window 11:10 -> 00:10,
 * uncertainty 0.3658 km, peak at the acquisition instant).
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import IncidentReport from "../src/components/report/IncidentReport";

const T0 = "2023-01-08T00:10:08Z";
const hoursBack = (h) => new Date(Date.parse(T0) - h * 3.6e6).toISOString().replace(".000", "");

const ellipses = Array.from({ length: 25 }, (_, h) => ({
  type: "Feature",
  geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
  properties: {
    feature_type: "ellipse", step_index: h, t_utc: hoursBack(h),
    center: [-90 - h * 0.01, 27.8], confidence_level: 0.9,
    semi_major_m: 1000 + h * 100, semi_minor_m: 500,
  },
}));

const origin = {
  type: "FeatureCollection",
  features: ellipses,
  metadata: {
    origin_window_start_utc: "2023-01-07T11:10:08Z",
    origin_window_end_utc: T0,
    origin_window_method: "cloud_convergence",
    origin_peak_utc: T0,
    origin_uncertainty_km: 0.3658,
    origin_uncertainty_coverage: 0.908,
    origin_uncertainty_method: "ellipse_90",
    backtrack_hours: 24, n_particles: 7500, timestep_minutes: 60,
    source: "real",
    forcing: {
      currents: { provider: "Copernicus Marine Service (CMEMS)", fallback: null },
      wind: { provider: "ECMWF ERA5 Reanalysis (CDS API)", fallback: "climatology" },
      windage: 0.03, engine: "euler",
      ml_residual: { model: null, applied: false },
    },
  },
};

const forecast = {
  type: "FeatureCollection",
  metadata: {
    issued_utc: T0, horizons_h: [6, 12],
    weathering: { model: "fingas+mackay", confidence: "low", oil_type_assumed: "medium_crude", states: [] },
  },
  features: [
    { properties: { horizon_h: 6, valid_utc: "2023-01-08T06:10:08Z", confidence_level: 0.5, area_km2: 3.0, source: "real" } },
    { properties: { horizon_h: 12, valid_utc: "2023-01-08T12:10:08Z", confidence_level: 0.9, area_km2: 21.4, source: "real" } },
  ],
};

const slick = {
  type: "FeatureCollection",
  metadata: { model_version: "unet-r34-fullcorpus-e48+yolo-screen", acquired_utc: T0 },
  features: [
    { properties: { slick_id: "COG_slick_01", area_km2: 6.826, perimeter_km: 30.1,
      centroid: [-90.19, 27.81], major_axis_m: 9000, minor_axis_m: 1200, orientation_deg: 41.2,
      confidence: 0.6882, damping_ratio: 0.42,
      age_hours_estimate: 23.7, age_method: "damping+fay", age_confidence_label: "low",
      engine: "ml", source: "real" } },
    { properties: { slick_id: "COG_slick_02", area_km2: 5.995, confidence: 0.6882 } },
  ],
};

const detect = {
  engine: "ml", model_version: "yolo-screen-v3", confidence: 0.71,
  candidates: [
    { class: "oil", score: 0.71, bbox: [-90.3, 27.7, -90.1, 27.9] },
    { class: "lookalike", score: 0.4, bbox: [-89.5, 27.0, -89.3, 27.2] },
  ],
};

const sceneMeta = {
  scene_id: "S1A_IW_GRDH_1SDV_20230108T001008", acquired_utc: T0,
  bbox: [-90.6, 27.4, -89.6, 28.2], polarisation: "VV", pixel_spacing_m: 10,
  provider_used: "Copernicus Data Space Ecosystem", source: "real", crs: "EPSG:4326",
};

const suspects = {
  source: "real", total_vessels_considered: 32,
  weights: { proximity: 0.3, temporal: 0.2, trajectory: 0.2, behaviour: 0.1, ais_gap: 0.1, vessel_prior: 0.1 },
  filtered_out: [
    { mmsi: 1, reason: "outside window", filter_reason: "outside time window" },
    { mmsi: 2, reason: "outside region", filter_reason: "outside origin region" },
  ],
  suspects: [
    { rank: 1, mmsi: 367653160, vessel_name: "CAPE ANN", vessel_type: "tanker", total_score: 0.6691,
      sub_scores: { proximity: 0.9, temporal: 0.7, trajectory: 0.5, behaviour: 0.4, ais_gap: 0.6, vessel_prior: 0.5 },
      reason: "Passed through the 90% origin region during the window.",
      evidence: { closest_approach_km: 0.4, time_in_origin_window_min: 55, ais_gap_minutes: 38,
                  course_delta_deg: 12, min_sog_kn: 1.2, track_points_in_cloud: 9 }, source: "real" },
    { rank: 2, mmsi: 311000123, vessel_name: null, vessel_type: "cargo", total_score: 0.4102,
      sub_scores: { proximity: 0.5, temporal: 0.4, trajectory: 0.3, behaviour: 0.3, ais_gap: 0.2, vessel_prior: 0.6 },
      reason: "Transited the outer cloud; no stop or gap.",
      evidence: { closest_approach_km: 3.1, ais_gap_minutes: 0 }, source: "real" },
  ],
};

const funnel = {
  found: 210, indexed: 32, after_spatial: 12, after_temporal: 6, after_trajectory: 2,
  candidates: 2, filtered: 30,
  reasons_histogram: { "outside origin region": 20, "outside time window": 10 },
};

const run = (aisSource) => ({
  run_id: "run-abc123", scene_id: sceneMeta.scene_id, incident_id: "INC-0007",
  detect_engine: "ml", finished_utc: "2023-01-08T01:00:00Z",
  manifest: {
    code_git_sha: "deadbeefcafe0123", artefact_digest: "0123456789abcdef0123456789abcdef",
    ais: { data_source: aisSource, detail: "MarineCadastre 2023-01-07 archive", file: "vessels.csv" },
    stages: [
      { stage: "detect", status: "ok", data_source: "sensor", source: "real", engine_used: "ml" },
      { stage: "characterise", status: "ok", data_source: "sensor", source: "real" },
      { stage: "drift_hindcast", status: "ok", data_source: "sensor", source: "real", engine_used: "euler" },
      { stage: "drift_forecast", status: "fallback", data_source: "cached", source: "fallback" },
      { stage: "attribution", status: "ok", data_source: aisSource, source: "real" },
    ],
  },
});

const incident = {
  id: "INC-0007", status: "under_investigation", zone_id: "gom-1", severity: "high",
  detected_utc: T0, area_km2: 6.8, region: "Gulf of Mexico", origin: "auto",
};

const report = {
  id: "RPT-0042", version: 2, status: "in_review", created_utc: "2023-01-08T02:00:00Z",
  published_utc: null, artefact_digest: "0123456789abcdef0123456789abcdef",
};

const HEADINGS = [
  "1. INCIDENT OVERVIEW",
  "2. SATELLITE EVIDENCE (SENTINEL-1 SAR)",
  "3. SLICK CHARACTERIZATION",
  "4. ESTIMATED ORIGIN & DRIFT ANALYSIS",
  "5. AIS CORRELATION",
  "6. CANDIDATE VESSEL RANKING",
  "7. DATA SOURCES & PROVENANCE",
  "8. DISCLAIMER",
];

const full = (aisSource = "real", extra = {}) => ({
  runId: "run-abc123", run: run(aisSource), sceneMeta, slick, detect, origin, forecast,
  suspects, funnel, incident, report, generatedUtc: "2023-01-09T10:00:00Z", ...extra,
});

describe("IncidentReport", () => {
  it("renders the eight sections in order under their exact headings", () => {
    const { container } = render(<IncidentReport {...full()} />);
    const hs = [...container.querySelectorAll("h2.ir-h")].map((h) => h.textContent.trim());
    expect(hs).toEqual(HEADINGS);
  });

  it("labels the rank-1 row and never speaks of guilt", () => {
    const { container } = render(<IncidentReport {...full()} />);
    expect(screen.getByText("Highest-Ranked Candidate")).toBeInTheDocument();
    expect(screen.getByText("Potential Source Vessel")).toBeInTheDocument();
    const body = container.textContent.toLowerCase();
    expect(body).not.toContain("guilty");
    expect(body).not.toContain("responsible vessel");
    expect(body).not.toContain("confirmed culprit");
    expect(body).toContain("This does not establish responsibility.".toLowerCase());
    // Rows carry the rank test ids, the score to 3 dp and the reason.
    const top = screen.getByTestId("report-suspect-1");
    expect(top).toHaveTextContent("367653160");
    expect(top).toHaveTextContent("0.669");
    expect(top).toHaveTextContent("38 min");
    expect(top).toHaveTextContent("0.4 km");
    const firstSuspect = container.querySelector(".rp-suspect");
    expect(firstSuspect).toHaveTextContent("Passed through the 90% origin region");
    expect(screen.getByTestId("report-suspect-2")).toHaveTextContent("311000123");
  });

  it("shows the weights the run used, to two decimals", () => {
    const { container } = render(<IncidentReport {...full()} />);
    const w = container.querySelector(".rp-weights");
    for (const v of Object.values(suspects.weights)) expect(w).toHaveTextContent(v.toFixed(2));
    expect(w).toHaveTextContent("vessel prior");
  });

  it("states LOW confidence whenever an age exists", () => {
    render(<IncidentReport {...full()} />);
    expect(screen.getByTestId("ir-age")).toHaveTextContent("23.7 h");
    expect(screen.getByTestId("ir-age")).toHaveTextContent("Confidence: LOW");
  });

  it("says SYNTHETIC loudly when the manifest's AIS source is synthetic", () => {
    const { container } = render(<IncidentReport {...full("synthetic")} />);
    const badge = screen.getByTestId("ir-ais-source");
    expect(badge).toHaveTextContent("SYNTHETIC");
    expect(badge.className).toContain("ir-tone-mock");
    expect(within(screen.getByTestId("ir-uncertainties")).getByText(/SYNTHETIC/)).toBeInTheDocument();
    expect(container.textContent).toContain("synthetic AIS");
  });

  it("treats a replayed archive ('real') as HISTORICAL, not live", () => {
    render(<IncidentReport {...full("real")} />);
    const badge = screen.getByTestId("ir-ais-source");
    expect(badge).toHaveTextContent("HISTORICAL");
    expect(badge).not.toHaveTextContent("REAL");
    expect(screen.getByText(/historical archive/)).toBeInTheDocument();
  });

  it("reads the origin, window, hindcast, forecast and forcing from the artefacts", () => {
    const { container } = render(<IncidentReport {...full()} />);
    const body = container.textContent;
    expect(body).toContain("± 0.37 km");
    expect(body).toContain("2023-01-07 11:10:08Z → 2023-01-08 00:10:08Z (13.0 h)");
    expect(body).toContain("cloud_convergence");
    expect(body).toContain("24 h backtrack · 7500 particles · 60 min step");
    expect(body).toContain("+6 h → 2023-01-08 06:10:08Z: 50% 3.0 km²");
    expect(body).toContain("Copernicus Marine Service (CMEMS)");
    expect(body).toContain("FALLBACK (climatology)");
    // The weak-window caveat: the peak is the image time.
    expect(screen.getByTestId("ir-weak-window")).toBeInTheDocument();
  });

  it("derives the shape from the axes and states the ratio", () => {
    const { container } = render(<IncidentReport {...full()} />);
    expect(container.textContent).toContain("Elongated (axis ratio 7.5:1)");
    expect(container.textContent).toContain("OIL — inside a region the screening model confirmed");
  });

  it("carries the funnel, the incident, the report record and the header ids", () => {
    const { container } = render(<IncidentReport {...full()} />);
    const body = container.textContent;
    expect(body).toContain("REPORT ID: RPT-0042");
    expect(body).toContain("GENERATED ON: 2023-01-09 10:00:00Z");
    expect(body).toContain("INC-0007");
    expect(screen.getByTestId("ir-incident-status")).toHaveTextContent("under investigation");
    expect(screen.getByTestId("ir-report-status")).toHaveTextContent("IN REVIEW");
    expect(body).toContain("outside origin region");
    expect(body).toContain("210");
    expect(body).toContain("build deadbeef");
    expect(body).toContain("0123456789ab");
    expect(body).toContain("Gulf of Mexico");
    expect(body).toContain("OCEANTRACE | MARITIME INTELLIGENCE FOR A CLEANER OCEAN");
    expect(body).toContain("Page 1 of 1");
    // Fallback stages are listed among the stated uncertainties.
    expect(screen.getByTestId("ir-uncertainties")).toHaveTextContent("drift_forecast");
    // Figure and mask come from the run's PNG endpoints.
    expect(container.querySelector("img.ir-figure-scene").getAttribute("src")).toBe("/api/runs/run-abc123/scene_png");
    expect(container.querySelector("img.ir-figure-mask").getAttribute("src")).toBe("/api/runs/run-abc123/mask_png");
  });

  it("states what was not produced instead of inventing it", () => {
    const { container } = render(<IncidentReport
      runId="run-empty" run={null} sceneMeta={null} slick={null} detect={null}
      origin={null} forecast={null} suspects={null} funnel={null} incident={null}
      report={null} generatedUtc="2023-01-09T10:00:00Z" />);
    const body = container.textContent;
    const hs = [...container.querySelectorAll("h2.ir-h")].map((h) => h.textContent.trim());
    expect(hs).toEqual(HEADINGS);
    expect(body).toContain("not opened — auto-incident gate not passed / not run");
    expect(body).toContain("REPORT ID: OT-IR-run-empty");
    expect(body).toContain("DRAFT — not reviewed");
    expect(screen.getByTestId("ir-watermark")).toBeInTheDocument();
    expect(body).toContain("Attribution was not produced for this run");
    expect(body).toContain("No slick characterisation was produced");
    expect(body).toContain("manifest not available for this run");
    expect(body).toContain("provider not recorded");
    expect(screen.getByTestId("ir-ais-source")).toHaveTextContent("NOT RECORDED");
    expect(screen.queryByTestId("ir-age")).toBeNull();
    expect(screen.queryByText("Highest-Ranked Candidate")).toBeNull();
    expect(body.toLowerCase()).not.toContain("guilty");
  });

  it("compact mode is single column and skips the figure when there is no run", () => {
    const { container } = render(<IncidentReport {...full("real", { compact: true, runId: null })} />);
    expect(container.querySelector(".ir-doc").className).toContain("ir-compact");
    expect(container.querySelector("img.ir-figure-scene")).toBeNull();
    expect(container.textContent).toContain("figure not available");
    // The preview never carries the watermark: the page's review strip does.
    expect(screen.queryByTestId("ir-watermark")).toBeNull();
  });
});
