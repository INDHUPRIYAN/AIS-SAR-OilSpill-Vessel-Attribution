/* The origin estimate, the drift panel and the case brief.
 *
 * What is pinned: the map ring, the drift panel and the brief all read the
 * origin the run PUBLISHED (the window and origin_uncertainty_km), not a point
 * picked by the UI; the brief says "not produced" for anything whose artefact
 * is missing; and the uncertainty list is built from what the artefacts say
 * about themselves -- a synthetic AIS source is stated, a LOW age is stated,
 * a weak window is stated.
 *
 * Fixture values mirror the flagship's artefacts (window 11:10 -> 00:10,
 * uncertainty 0.3658 km, peak at the acquisition instant).
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import CaseBrief from "../src/components/workspace/CaseBrief";
import DriftPanel from "../src/components/workspace/DriftPanel";
import { originEstimate, screenVerdict, vectorArrows } from "../src/lib/drift";
import SpillPanel from "../src/components/workspace/SpillPanel";

const T0 = "2023-01-08T00:10:08Z";
const hoursBack = (h) => new Date(Date.parse(T0) - h * 3.6e6).toISOString().replace(".000", "");

/* One ellipse per hour back, each centred a little further west so the
 * chosen step is visible in the centre it returns. */
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
    backtrack_hours: 24, n_particles: 7500, timestep_minutes: 60,
    source: "real",
    forcing: {
      currents: { provider: "Copernicus Marine Service (CMEMS)", variables: ["uo", "vo"], fallback: null },
      wind: { provider: "ECMWF ERA5 Reanalysis (CDS API)", variables: ["u10", "v10"], fallback: null },
      windage: 0.03, engine: "euler",
      ml_residual: { model: null, applied: false },
    },
  },
};

const forecast = {
  type: "FeatureCollection",
  metadata: {
    issued_utc: T0, horizons_h: [6, 12],
    weathering: { model: "fingas+mackay", confidence: "low", oil_type_assumed: "medium_crude",
                  states: [{ hours: 6, evaporated_fraction: 0.093, water_fraction: 0.40 }] },
  },
  features: [
    { properties: { horizon_h: 6, valid_utc: "2023-01-08T06:10:08Z", confidence_level: 0.5, area_km2: 3.0 } },
    { properties: { horizon_h: 12, valid_utc: "2023-01-08T12:10:08Z", confidence_level: 0.9, area_km2: 21.4 } },
  ],
};

const slick = {
  type: "FeatureCollection",
  metadata: { model_version: "unet-r34-fullcorpus-e48+yolo-screen", acquired_utc: T0 },
  features: [
    { properties: { slick_id: "COG_slick_01", area_km2: 6.826, perimeter_km: 30.1,
      centroid: [-90.19, 27.81], major_axis_m: 9000, minor_axis_m: 1200, confidence: 0.6882,
      age_hours_estimate: 23.7, age_method: "damping+fay", source: "real" } },
    { properties: { slick_id: "COG_slick_02", area_km2: 5.995, confidence: 0.6882 } },
  ],
};

const suspects = (source) => ({
  source, total_vessels_considered: 32,
  filtered_out: Array.from({ length: 28 }, (_, i) => ({ mmsi: i, reason: "outside window" })),
  suspects: [{ rank: 1, mmsi: 367653160, vessel_name: null, total_score: 0.6691,
               reason: "Passed through the 90% origin region." }],
});

describe("originEstimate", () => {
  it("centres on the ellipse nearest the middle of the published window", () => {
    const est = originEstimate(origin);
    // Window 11:10 -> 00:10 is 13 h wide; its midpoint is 6.5 h back, so the
    // nearest hourly ellipse is step 6 or 7 -- never the backtrack's T-12.
    expect([6, 7]).toContain(est.stepIndex);
    expect(est.windowHours).toBeCloseTo(13, 5);
  });

  it("uses the published uncertainty as the radius, not a UI constant", () => {
    const est = originEstimate(origin);
    expect(est.radiusKm).toBeCloseTo(0.3658, 4);
    expect(est.radiusBasis).toBe("origin_uncertainty_km");
  });

  it("falls back to the ellipse axis only when no uncertainty was published, and says so", () => {
    const md = { ...origin.metadata };
    delete md.origin_uncertainty_km;
    const est = originEstimate({ ...origin, metadata: md });
    expect(est.radiusBasis).toBe("ellipse semi-major axis");
  });

  it("flags a window whose convergence peak is the image time", () => {
    expect(originEstimate(origin).weak).toBe(true);
  });

  it("returns null rather than guessing when there is no window", () => {
    expect(originEstimate(null)).toBeNull();
    expect(originEstimate({ ...origin, metadata: {} })).toBeNull();
  });
});

describe("DriftPanel", () => {
  it("shows hindcast and forecast as separate, labelled directions", () => {
    render(<DriftPanel origin={origin} forecast={forecast} />);
    expect(screen.getByTestId("drift-hindcast")).toHaveTextContent(/back to origin/i);
    expect(screen.getByTestId("drift-forecast")).toHaveTextContent(/where it is going/i);
    expect(screen.getByTestId("drift-uncertainty")).toHaveTextContent("0.37 km");
    expect(screen.getByTestId("forecast-table")).toHaveTextContent("21.4 km²");
  });

  it("names the forcing providers and says the ML residual was off", () => {
    render(<DriftPanel origin={origin} forecast={forecast} />);
    expect(screen.getByTestId("forcing-currents")).toHaveTextContent("CMEMS");
    expect(screen.getByTestId("forcing-wind")).toHaveTextContent("ERA5");
    expect(screen.getByTestId("drift-panel")).toHaveTextContent(/not applied — physics only/);
  });

  it("warns that a peak at the image time is a window, not a discharge time", () => {
    render(<DriftPanel origin={origin} forecast={forecast} />);
    expect(screen.getByTestId("drift-weak-window")).toHaveTextContent(/not a\s+discharge time/);
  });

  it("marks weathering LOW confidence", () => {
    render(<DriftPanel origin={origin} forecast={forecast} />);
    expect(screen.getByTestId("drift-weathering")).toHaveTextContent("LOW");
  });

  it("says drift is not produced yet instead of rendering zeros", () => {
    render(<DriftPanel origin={null} forecast={null} />);
    expect(screen.getByTestId("drift-empty")).toBeInTheDocument();
  });
});

describe("CaseBrief", () => {
  const full = {
    sceneMeta: { scene_id: "S1A_IW_GRDH_X", acquired_utc: T0, source: "real", polarisation: "VV", pixel_spacing_m: 10 },
    slick, detect: { candidates: [{ class: "lookalike" }] }, origin, forecast,
    runRow: { manifest: { ais: { data_source: "real", detail: "a real AIS archive holds reports inside the computed origin window" }, stages: [] } },
  };

  it("answers every question from the artefacts", () => {
    render(<CaseBrief {...full} suspects={suspects("real")} />);
    expect(screen.getByTestId("cb-what")).toHaveTextContent("2 slick regions");
    expect(screen.getByTestId("cb-what")).toHaveTextContent("COG_slick_01");
    expect(screen.getByTestId("cb-where")).toHaveTextContent("27.8100° N");
    expect(screen.getByTestId("cb-when")).toHaveTextContent("LOW confidence");
    expect(screen.getByTestId("cb-origin")).toHaveTextContent("0.37 km");
    expect(screen.getByTestId("cb-forecast")).toHaveTextContent("+12 h");
    expect(screen.getByTestId("cb-vessels")).toHaveTextContent("32 vessels reconstructed");
    expect(screen.getByTestId("cb-top-suspect")).toHaveTextContent("Rank #1 · MMSI 367653160 · score 0.67");
  });

  it("names data sources with their provenance", () => {
    render(<CaseBrief {...full} suspects={suspects("real")} />);
    expect(screen.getByTestId("src-satellite")).toHaveTextContent("Sentinel-1 SAR");
    expect(screen.getByTestId("src-currents")).toHaveTextContent("CMEMS");
    expect(screen.getByTestId("src-ais")).toHaveTextContent("REAL");
  });

  it("states what is uncertain, from the artefacts' own flags", () => {
    render(<CaseBrief {...full} suspects={suspects("real")} />);
    const u = screen.getByTestId("cb-uncertain");
    expect(u).toHaveTextContent(/LOW confidence/);
    expect(u).toHaveTextContent(/window, not a discharge time/);
    expect(u).toHaveTextContent(/1 of 1 ranked vessel\(s\) have no name/);
    expect(u).toHaveTextContent(/not a finding of responsibility/);
    expect(u).not.toHaveTextContent(/SYNTHETIC/);
  });

  it("says loudly when AIS is synthetic", () => {
    const row = { manifest: { ais: { data_source: "synthetic" }, stages: [] } };
    render(<CaseBrief {...full} runRow={row} suspects={suspects("synthetic")} />);
    expect(screen.getByTestId("cb-uncertain")).toHaveTextContent(/AIS for this run is SYNTHETIC/);
    expect(screen.getByTestId("src-ais")).toHaveTextContent("SYNTHETIC");
  });

  it("reports a fallback stage as a caveat", () => {
    const row = { manifest: { stages: [{ stage: "detect", status: "ok", engine_used: "fallback", detail: "threshold" }] } };
    render(<CaseBrief {...full} runRow={row} suspects={suspects("real")} />);
    expect(screen.getByTestId("cb-uncertain")).toHaveTextContent(/detect used its fallback engine/);
  });

  it("says 'not produced' for every question with no artefact", () => {
    render(<CaseBrief />);
    for (const id of ["cb-what", "cb-where", "cb-origin", "cb-forecast", "cb-vessels"]) {
      expect(screen.getByTestId(id)).toHaveTextContent("not produced");
    }
  });
});

describe("screen verdict on the analysed slick", () => {
  const box = (cls, b) => ({ class: cls, bbox: b, score: 0.7 });
  const props = { centroid: [-90.19, 27.81], slick_id: "COG_slick_01" };

  it("reads look-alike when only a rejected box contains the slick (the flagship case)", () => {
    const detect = { candidates: [box("lookalike", [-90.194, 27.776, -90.177, 27.860])] };
    expect(screenVerdict(props, detect)).toBe("lookalike");
  });

  it("lets a confirming oil box win over an overlapping rejected one", () => {
    const detect = { candidates: [
      box("lookalike", [-90.3, 27.7, -90.1, 27.9]), box("oil", [-90.2, 27.8, -90.18, 27.82])] };
    expect(screenVerdict(props, detect)).toBe("oil");
  });

  it("returns null rather than guessing when no box contains the slick", () => {
    expect(screenVerdict(props, { candidates: [box("oil", [0, 0, 1, 1])] })).toBeNull();
    expect(screenVerdict(props, null)).toBeNull();
  });

  it("puts a rejected analysed slick in the spill panel and the brief's caveats", () => {
    const detect = { candidates: [box("lookalike", [-90.194, 27.776, -90.177, 27.860])] };
    const { unmount } = render(<SpillPanel slick={slick} detect={detect} />);
    expect(screen.getByTestId("spill-screen-verdict")).toHaveTextContent("LOOK-ALIKE");
    expect(screen.getByTestId("spill-screen-lookalike")).toBeInTheDocument();
    unmount();
    render(<CaseBrief slick={slick} detect={detect} />);
    expect(screen.getByTestId("cb-uncertain")).toHaveTextContent(/REJECTED as a look-alike/);
  });

  it("says nothing alarming when the slick is screen-confirmed oil", () => {
    const detect = { candidates: [box("oil", [-90.194, 27.776, -90.177, 27.860])] };
    render(<SpillPanel slick={slick} detect={detect} />);
    expect(screen.getByTestId("spill-screen-verdict")).toHaveTextContent("OIL");
    expect(screen.queryByTestId("spill-screen-lookalike")).not.toBeInTheDocument();
  });
});

describe("vectorArrows", () => {
  const field = {
    times_utc: ["2023-01-08T00:00:00Z", "2023-01-08T06:00:00Z"],
    lats: [27.0, 27.5], lons: [-91.0, -90.5],
    u: [[[1, 0], [0, 0]], [[0, 0], [0, 2]]],
    v: [[[0, 1], [0, 0]], [[0, 0], [0, 0]]],
  };

  it("draws the timestep nearest the clock", () => {
    const early = vectorArrows(field, Date.parse("2023-01-08T01:00:00Z"));
    expect(early.timeUtc).toBe("2023-01-08T00:00:00Z");
    expect(early.segments).toHaveLength(4);          // two cells, shaft + head each
    const late = vectorArrows(field, Date.parse("2023-01-08T05:00:00Z"));
    expect(late.timeUtc).toBe("2023-01-08T06:00:00Z");
    expect(late.segments).toHaveLength(2);
  });

  it("points where the flow goes: +u is east (90°), +v is north (0°)", () => {
    const { segments } = vectorArrows(field, Date.parse("2023-01-08T00:00:00Z"));
    const dirs = segments.filter((_, i) => i % 2 === 0).map((s) => Math.round(s.toDeg));
    expect(dirs.sort()).toEqual([0, 90]);
  });

  it("keeps the absolute speed for the hover while lengths are relative", () => {
    const { segments } = vectorArrows(field, Date.parse("2023-01-08T06:00:00Z"));
    expect(segments[0].speed).toBe(2);
    const [[x0], [x1]] = segments[0].path;
    expect(x1 - x0).toBeGreaterThan(0);               // eastward
  });

  it("draws nothing for an absent or calm grid", () => {
    expect(vectorArrows(null, 0).segments).toEqual([]);
    expect(vectorArrows({ ...field, u: [[[0, 0], [0, 0]]], v: [[[0, 0], [0, 0]]] }, 0).segments).toEqual([]);
  });
});

describe("forcing provenance in the brief", () => {
  it("shows the hindcast stage's data source (CACHED), not the engine's REAL flag", () => {
    const row = { manifest: { stages: [{ stage: "drift_hindcast", status: "ok", source: "real", data_source: "cached" }] } };
    render(<CaseBrief origin={origin} runRow={row} />);
    expect(screen.getByTestId("src-currents")).toHaveTextContent("CACHED");
    expect(screen.getByTestId("src-wind")).not.toHaveTextContent("REAL");
  });
});

describe("loading is not absence", () => {
  it("says loading for a layer still in flight, not 'not produced'", () => {
    render(<CaseBrief slick={slick} loaded={false} errors={{}} />);
    expect(screen.getByTestId("cb-origin")).toHaveTextContent("loading…");
    expect(screen.getByTestId("cb-origin")).not.toHaveTextContent("not produced");
  });

  it("says not produced once the server answered 404 for it", () => {
    render(<CaseBrief slick={slick} loaded={false} errors={{ origin_cloud: { status: 404 } }} />);
    expect(screen.getByTestId("cb-origin")).toHaveTextContent("not produced by this run");
    expect(screen.getByTestId("cb-forecast")).toHaveTextContent("loading…");
  });

  it("the drift panel makes the same distinction", () => {
    const { unmount } = render(<DriftPanel origin={null} forecast={forecast} loaded={false} errors={{}} />);
    expect(screen.getByTestId("drift-hindcast-missing")).toHaveTextContent(/Loading hindcast/);
    unmount();
    render(<DriftPanel origin={null} forecast={forecast} loaded={false} errors={{ origin_cloud: {} }} />);
    expect(screen.getByTestId("drift-hindcast-missing")).toHaveTextContent(/not produced/);
  });
});

describe("synthetic answers carry their label inline", () => {
  it("tags a mock origin, forecast and ranking SYNTHETIC in the answer itself", () => {
    const mockOrigin = { ...origin, metadata: { ...origin.metadata, source: "synthetic" } };
    const mockForecast = { ...forecast, features: forecast.features.map((f) => ({ properties: { ...f.properties, source: "synthetic" } })) };
    render(<CaseBrief slick={slick} origin={mockOrigin} forecast={mockForecast} suspects={suspects("synthetic")} />);
    for (const id of ["cb-origin", "cb-forecast", "cb-vessels"]) {
      expect(screen.getByTestId(id)).toHaveTextContent("SYNTHETIC");
    }
  });

  it("adds no tag to real answers", () => {
    render(<CaseBrief slick={slick} origin={origin} forecast={forecast} suspects={suspects("real")} />);
    for (const id of ["cb-origin", "cb-forecast", "cb-vessels"]) {
      expect(screen.getByTestId(id)).not.toHaveTextContent("SYNTHETIC");
    }
  });
});

describe("a run where no vessel passed the gates", () => {
  // inv-gulf-screened-2day-20230108: real AIS, 30 vessels considered, 0 ranked.
  const none = {
    source: "real", total_vessels_considered: 30, suspects: [],
    filtered_out: Array.from({ length: 30 }, (_, i) => ({ mmsi: i, reason: "outside origin region" })),
  };

  it("states the null result instead of naming a rank #1", () => {
    render(<CaseBrief slick={slick} origin={origin} suspects={none} />);
    const v = screen.getByTestId("cb-vessels");
    expect(v).toHaveTextContent("30 vessels reconstructed");
    expect(v).toHaveTextContent("30 excluded by the gates; 0 ranked");
    expect(screen.queryByTestId("cb-top-suspect")).not.toBeInTheDocument();
    expect(screen.getByTestId("cb-uncertain")).not.toHaveTextContent(/weighted ordering/);
  });
});
