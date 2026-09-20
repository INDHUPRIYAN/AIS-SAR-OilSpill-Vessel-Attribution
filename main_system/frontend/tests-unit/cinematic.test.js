/* The presentation script must never claim what the run has not produced.
 *
 * These pin the two honesty rules of lib/cinematic: a beat holds (or stops)
 * on the run's REAL status, and every reveal fraction only slices an
 * artefact -- the clock runs backward through the hindcast and forward
 * through the forecast, gates dim in backend order, candidates light up in
 * rank order, and nothing is drawn before its stage landed. */

import { describe, expect, it } from "vitest";

import {
  BEATS, BEAT_INDEX, beatGate, filterCounts, frameOf, gateOf, readiness,
} from "../src/lib/cinematic";

const judgedAll = (state) => Object.fromEntries(
  ["detection", "validation", "drift", "attribution"].map((k) => [k, { state, row: { status: state === "done" ? "ok" : state }, forecastRow: { status: state === "done" ? "ok" : state } }]));

describe("readiness", () => {
  it("reads a complete run as ready once its artefacts are on screen", () => {
    const r = readiness({
      judged: judgedAll("done"), hasScene: true, runState: "complete", forcingState: "ok",
      layers: { scene_meta: {}, detect: {}, slick: { features: [{}] }, origin_cloud: {}, forecast: { features: [{}] }, vessels: {}, suspects: {} },
    });
    for (const k of Object.keys(r)) expect(r[k].s, k).toBe("ready");
  });

  it("holds, never invents, while a live run is still producing a stage", () => {
    const r = readiness({ judged: { ...judgedAll("pending"), detection: { state: "running" } }, hasScene: true, runState: "running", forcingState: "idle", layers: { scene_meta: {} } });
    expect(r.detect.s).toBe("wait");
    expect(r.hindcast.s).toBe("wait");
    expect(r.vessels.s).toBe("wait");
  });

  it("reads a run whose status has not arrived yet as waiting, not missing", () => {
    const r = readiness({ judged: judgedAll("pending"), hasScene: true, runState: null, forcingState: "idle", layers: {}, awaitingStatus: true });
    expect(r.detect.s).toBe("wait");
    const r2 = readiness({ judged: judgedAll("pending"), hasScene: true, runState: "complete", forcingState: "idle", layers: {}, awaitingStatus: false });
    expect(r2.detect.s).toBe("missing");
  });

  it("carries a failed stage's detail through to the gate", () => {
    const j = judgedAll("done");
    j.detection = { state: "failed", note: "no scene raster" };
    const r = readiness({ judged: j, hasScene: true, runState: "failed", forcingState: "ok", layers: { scene_meta: {} } });
    expect(r.detect).toEqual({ s: "failed", why: "no scene raster" });
    const g = beatGate(BEATS[BEAT_INDEX.scan], r);
    expect(g.s).toBe("failed");
    expect(g.need).toBe("detect");
  });

  it("a soft beat's missing input is reported as missing (the hook skips it)", () => {
    const r = readiness({ judged: judgedAll("done"), hasScene: true, runState: "complete", forcingState: "error", layers: { scene_meta: {}, slick: { features: [{}] } } });
    expect(r.forcing.s).toBe("missing");
    expect(BEATS[BEAT_INDEX.wind].soft).toBe(true);
  });
});

describe("frameOf", () => {
  const D = { t0: 1_700_000_000_000, backtrackH: 24, forecastMaxH: 24, aisStart: 1_700_000_000_000 - 30 * 3.6e6, nRanked: 4, counts: { total: 32, remaining: [32, 14, 8, 4], ranked: 4 } };

  it("runs the clock backward through the hindcast and forward through the forecast", () => {
    expect(frameOf("hindcast", 0, D).timeMs).toBe(D.t0);
    expect(frameOf("hindcast", 1, D).timeMs).toBeCloseTo(D.t0 - 24 * 3.6e6, -3);
    expect(frameOf("forecast", 1, D).timeMs).toBeCloseTo(D.t0 + 24 * 3.6e6, -3);
    expect(frameOf("forecast", 0.5, D).reveal.forecastUpTo).toBeCloseTo(12, 0);
    expect(frameOf("ais", 0, D).timeMs).toBe(D.aisStart);
    expect(frameOf("ais", 1, D).timeMs).toBe(D.t0);
  });

  it("does not draw the SAR raster before the scene loads, and fades it in over the sar beat", () => {
    expect(frameOf("globe", 0.5, D).reveal.sar).toBe(0);
    expect(frameOf("footprint", 0.9, D).reveal.sar).toBe(0);
    expect(frameOf("sar", 0, D).reveal.sar).toBe(0);
    expect(frameOf("sar", 1, D).reveal.sar).toBe(1);
    expect(frameOf("scan", 0.3, D).reveal.sar).toBe(1);
  });

  it("reveals the slick only through the segmentation beat, and the mask is full afterwards", () => {
    expect(frameOf("scan", 0.5, D).show.slick).toBe(false);
    expect(frameOf("segment", 0.4, D).reveal.slick).toBeCloseTo(0.4);
    expect(frameOf("validate", 0.1, D).reveal.slick).toBeNull();
    expect(frameOf("validate", 0.1, D).show.slick).toBe(true);
  });

  it("dims vessels gate by gate and lights candidates in rank order", () => {
    expect(frameOf("ais", 0.9, D).reveal.gate).toBeNull();
    expect(frameOf("ais", 0.9, D).reveal.ranked).toBe(0);
    expect(frameOf("filter", 0.1, D).reveal.gate).toBe(0);
    expect(frameOf("filter", 0.3, D).reveal.gate).toBe(1);
    expect(frameOf("filter", 0.8, D).reveal.gate).toBe(3);
    expect(frameOf("filter", 0.8, D).hud.line).toContain("4 candidates");
    expect(frameOf("ranking", 0.3, D).reveal.ranked).toBe(1);
    expect(frameOf("ranking", 0.99, D).reveal.ranked).toBe(4);
    expect(frameOf("attribution", 0, D).reveal.ranked).toBe(4);
    expect(frameOf("attribution", 0, D).reveal.dimOthers).toBe(true);
  });

  it("grows the origin ring from nothing to its published radius", () => {
    expect(frameOf("hindcast", 0.9, D).show.origin).toBe(false);
    expect(frameOf("origin", 0, D).reveal.origin).toBe(0);
    expect(frameOf("origin", 1, D).reveal.origin).toBe(1);
    expect(frameOf("forecast", 0, D).reveal.origin).toBe(1);
  });
});

describe("AIS gates", () => {
  it("groups backend reasons into the three gates, temporal before spatial", () => {
    expect(gateOf("outside origin region")).toBe(1);
    expect(gateOf("outside time window")).toBe(2);
    expect(gateOf("course incompatible with slick axis")).toBe(3);
    expect(gateOf(null)).toBe(0);
  });

  it("counts the funnel from the vessel layer's own flags", () => {
    const vessels = { features: [
      { properties: { filtered: true, filter_reason: "outside origin region" } },
      { properties: { filtered: true, filter_reason: "outside origin region" } },
      { properties: { filtered: true, filter_reason: "outside time window" } },
      { properties: { filtered: true, filter_reason: "course incompatible with slick axis" } },
      { properties: { rank: 1 } }, { properties: { rank: 2 } },
    ] };
    const c = filterCounts(vessels, { suspects: [{ rank: 1 }, { rank: 2 }] });
    expect(c.total).toBe(6);
    expect(c.remaining).toEqual([6, 4, 3, 2]);
    expect(c.ranked).toBe(2);
  });
});
