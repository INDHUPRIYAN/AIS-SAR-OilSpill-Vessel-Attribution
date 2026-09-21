/* The drift chase camera: follow while it moves, ease out once when it is done. */
import { describe, expect, it } from "vitest";

import { buildChase, chaseAt } from "../src/lib/chase";

const H = 3.6e6;
const T0 = Date.parse("2019-06-14T04:12:00Z");
const iso = (ms) => new Date(ms).toISOString().replace(".000", "");

const slick = { features: [{ properties: { centroid: [34.8, 35.57] },
  geometry: { type: "Polygon", coordinates: [[[34.795, 35.565], [34.805, 35.565], [34.805, 35.575], [34.795, 35.575], [34.795, 35.565]]] } }] };
const origin = {
  metadata: { origin_window_start_utc: iso(T0 - 6 * H), origin_window_end_utc: iso(T0 - 5 * H) },
  features: [0, 2, 4, 6, 8].map((k) => ({ properties: { feature_type: "ellipse", t_utc: iso(T0 - k * H), center: [34.8 - 0.004 * k, 35.57 - 0.003 * k] } })),
};
const square = (x, y) => ({ type: "Polygon", coordinates: [[[x - 0.01, y - 0.01], [x + 0.01, y - 0.01], [x + 0.01, y + 0.01], [x - 0.01, y + 0.01], [x - 0.01, y - 0.01]]] });
const forecast = { features: [
  { properties: { horizon_h: 6, confidence_level: 0.9, valid_utc: iso(T0 + 6 * H) }, geometry: square(34.83, 35.6) },
  { properties: { horizon_h: 12, confidence_level: 0.9, valid_utc: iso(T0 + 12 * H) }, geometry: square(34.86, 35.63) },
] };
const chase = buildChase(origin, forecast, slick, T0, { center: [34.776, 35.552] });

describe("drift chase camera", () => {
  it("holds still at the acquisition: nothing is moving yet", () => {
    expect(chaseAt(chase, T0)).toBeNull();
    expect(chaseAt(chase, T0 + 10 * 60 * 1000)).toBeNull();
  });

  it("follows the hindcast close in, centred between the run's own hourly centres", () => {
    const c = chaseAt(chase, T0 - 3 * H);
    expect(c.kind).toBe("follow");
    expect(c.zoom).toBeGreaterThanOrEqual(12);                 // close: a 1 km slick fills the view
    expect(c.center[0]).toBeCloseTo(34.8 - 0.012, 4);          // halfway between the T-2 h and T-4 h centres
    expect(c.center[1]).toBeCloseTo(35.57 - 0.009, 4);
  });

  it("eases out ONCE the hindcast reaches the origin window, with origin and slick in the frame", () => {
    const c = chaseAt(chase, T0 - 7 * H);
    expect(c).toMatchObject({ kind: "frame", key: "origin" });
    const [w, s, e, n] = c.bbox;
    expect(w).toBeLessThan(34.776); expect(s).toBeLessThan(35.552);   // the origin
    expect(e).toBeGreaterThan(34.805); expect(n).toBeGreaterThan(35.575); // the whole slick outline
  });

  it("in the presentation it stays with the travel past the window: the origin beat does the ease-out", () => {
    const c = chaseAt(chase, T0 - 7 * H, { followThrough: true });
    expect(c.kind).toBe("follow");
    expect(c.center[0]).toBeCloseTo(34.8 - 0.004 * 7, 4);
  });

  it("follows the forecast, then frames slick plus every footprint at the last horizon", () => {
    const mid = chaseAt(chase, T0 + 3 * H);
    expect(mid.kind).toBe("follow");
    expect(mid.center[0]).toBeCloseTo(34.815, 3);              // halfway from the slick to the +6 h centroid
    expect(chaseAt(chase, T0 + 12 * H)).toMatchObject({ kind: "frame", key: "forecast" });
  });

  it("does nothing without a slick or an acquisition time", () => {
    expect(buildChase(origin, forecast, null, T0, null)).toBeNull();
    expect(buildChase(origin, forecast, slick, null, null)).toBeNull();
    expect(chaseAt(null, T0)).toBeNull();
    expect(chaseAt(chase, NaN)).toBeNull();
  });
});
