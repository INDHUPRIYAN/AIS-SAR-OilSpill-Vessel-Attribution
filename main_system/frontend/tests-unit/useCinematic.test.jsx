/* The presentation clock's honesty rules, driven frame by frame.
 *
 * useCinematic decides only how far along the storytelling is. These pin the
 * three things it must do with the run's real state: advance while a beat's
 * inputs are ready, hold (not fake) while the pipeline is still producing
 * them, and stop on a failed stage with the failure's own words -- while a
 * missing OPTIONAL input (a forcing grid) is skipped and remembered. */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BEATS, BEAT_INDEX } from "../src/lib/cinematic";
import { useCinematic } from "../src/lib/useCinematic";

/* A hand-cranked requestAnimationFrame: each `frame(ms)` runs the queued
 * callbacks once with a clock that advanced by `ms`. */
let queue = [];
let now = 0;
function frame(ms = 50) {
  now += ms;
  const cbs = queue; queue = [];
  act(() => { for (const cb of cbs) cb(now); });
}

const READY = Object.fromEntries(["scene", "flight", "detect", "slick", "forcing", "hindcast", "forecast", "vessels", "suspects"].map((k) => [k, { s: "ready" }]));

beforeEach(() => {
  queue = []; now = 0;
  vi.stubGlobal("requestAnimationFrame", (cb) => { queue.push(cb); return queue.length; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  vi.spyOn(performance, "now").mockImplementation(() => now);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("useCinematic", () => {
  it("advances a ready beat by wall clock over its duration, then moves on", () => {
    const { result } = renderHook(() => useCinematic({ readiness: READY }));
    act(() => result.current.start("scan"));
    expect(result.current.active).toBe(true);
    expect(result.current.beat.id).toBe("scan");
    frame(0);                                    // first frame: dt = 0
    frame(BEATS[BEAT_INDEX.scan].dur * 1000 / 2 + 10);   // dt is clamped to 100 ms per frame
    // many small frames add up to half the beat
    for (let i = 0; i < 22; i++) frame(100);
    expect(result.current.beat.id).toBe("scan");
    expect(result.current.t).toBeGreaterThan(0.4);
    for (let i = 0; i < 30; i++) frame(100);            // 5.3 s in all: 0.8 s into the next beat
    expect(result.current.beat.id).toBe("detect");
    expect(result.current.t).toBeCloseTo(0.8 / BEATS[BEAT_INDEX.detect].dur, 1);
  });

  it("holds on a beat whose input the run is still producing, and says which", () => {
    const readiness = { ...READY, detect: { s: "wait", why: "running" }, slick: { s: "wait", why: "running" } };
    const { result, rerender } = renderHook(({ r }) => useCinematic({ readiness: r }), { initialProps: { r: readiness } });
    act(() => result.current.start("scan"));
    frame(0); for (let i = 0; i < 20; i++) frame(100);
    expect(result.current.active).toBe(true);
    expect(result.current.beat.id).toBe("scan");
    expect(result.current.t).toBe(0);
    expect(result.current.hold).toEqual({ need: "detect", why: "running" });
    // the stage lands: the hold lifts and progress resumes
    rerender({ r: READY });
    for (let i = 0; i < 5; i++) frame(100);
    expect(result.current.hold).toBeNull();
    expect(result.current.t).toBeGreaterThan(0);
  });

  it("stops on a failed stage with the failure's detail, never inventing the beat", () => {
    const readiness = { ...READY, detect: { s: "failed", why: "no scene raster" } };
    const { result } = renderHook(() => useCinematic({ readiness }));
    act(() => result.current.start("preprocess"));
    frame(0); frame(50);
    expect(result.current.active).toBe(false);
    expect(result.current.stopped).toContain("detect failed");
    expect(result.current.stopped).toContain("no scene raster");
    expect(result.current.beat.id).toBe("preprocess");
  });

  it("skips a soft beat whose optional input is missing, and remembers why", () => {
    const readiness = { ...READY, forcing: { s: "missing", why: "no forcing grid recorded for this run" } };
    const { result } = renderHook(() => useCinematic({ readiness }));
    act(() => result.current.start("wind"));
    frame(0); frame(50);
    expect(result.current.active).toBe(true);
    expect(result.current.beat.id).toBe("hindcast");        // wind and currents both skipped
    expect(result.current.skipped.wind).toContain("no forcing grid");
    expect(result.current.skipped.currents).toContain("no forcing grid");
  });

  it("gives a soft beat a bounded wait for an input that is still loading", () => {
    const readiness = { ...READY, forcing: { s: "wait", why: "loading the forcing grids" } };
    const { result } = renderHook(() => useCinematic({ readiness }));
    act(() => result.current.start("wind"));
    frame(0); for (let i = 0; i < 50; i++) frame(100);      // 5 s: still waiting
    expect(result.current.beat.id).toBe("wind");
    expect(result.current.hold?.need).toBe("forcing");
    for (let i = 0; i < 60; i++) frame(100);                // past 10 s: moves on, honestly
    expect(result.current.beat.id).toBe("hindcast");
    expect(result.current.skipped.wind).toContain("still loading");
  });

  it("stop hands the workspace back; jump and skip scrub the beats", () => {
    const { result } = renderHook(() => useCinematic({ readiness: READY }));
    act(() => result.current.start("globe"));
    act(() => result.current.jump("hindcast"));
    expect(result.current.beat.id).toBe("hindcast");
    act(() => result.current.skip());
    expect(result.current.beat.id).toBe("origin");
    act(() => result.current.stop());
    expect(result.current.active).toBe(false);
    expect(result.current.stopped).toBe("stopped");
  });
});
