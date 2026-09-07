/* The live-progress hook, and the thing it must never do: go quiet.
 *
 * `useRunEvents` prefers SSE and falls back to polling. The failure worth
 * guarding against is not "SSE broke" -- that happens, behind proxies that
 * buffer text/event-stream and in browsers without EventSource. It is a UI that
 * stops updating without saying so, which looks identical to a pipeline that
 * has hung. So the hook reports its `transport`, and these tests pin that it
 * switches rather than stalls.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useRunEvents } from "../src/lib/useRunEvents";

/* A controllable EventSource stand-in. */
class FakeEventSource {
  static instances = [];
  static CLOSED = 2;

  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.listeners = {};
    FakeEventSource.instances.push(this);
  }

  addEventListener(name, fn) {
    (this.listeners[name] ||= []).push(fn);
  }

  emit(name, data) {
    for (const fn of this.listeners[name] || []) {
      fn({ data: JSON.stringify(data) });
    }
  }

  fail() {
    this.readyState = FakeEventSource.CLOSED;
    for (const fn of this.listeners.error || []) fn({});
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }
}

afterEach(() => {
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useRunEvents", () => {
  it("collects one row per stage and ends on the terminal event", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const onEnd = vi.fn();

    const { result } = renderHook(() => useRunEvents("run-1", { onEnd }));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/events/runs/run-1");

    act(() => {
      source.emit("open", {});
      for (const stage of ["detect", "characterise", "drift_hindcast",
        "drift_forecast", "attribution"]) {
        source.emit("stage", { stage, status: "ok" });
      }
    });

    await waitFor(() => expect(result.current.stages).toHaveLength(5));
    expect(result.current.transport).toBe("sse");

    act(() => source.emit("end", { run_status: "complete" }));
    await waitFor(() => expect(result.current.runStatus).toBe("complete"));
    expect(onEnd).toHaveBeenCalledWith("complete");
  });

  it("replaces a stage rather than appending it twice", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { result } = renderHook(() => useRunEvents("run-2"));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.emit("stage", { stage: "detect", status: "running" });
      source.emit("stage", { stage: "detect", status: "ok" });
    });

    await waitFor(() => expect(result.current.stages).toHaveLength(1));
    expect(result.current.stages[0].status).toBe("ok");
  });

  it("falls back to polling and says so when the stream closes", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { result } = renderHook(() => useRunEvents("run-3"));

    act(() => FakeEventSource.instances[0].fail());

    await waitFor(() => expect(result.current.transport).toBe("poll"));
    // The point of the test: the operator is told, not left guessing.
    expect(result.current.error).toMatch(/polling/i);
  });

  it("falls back when EventSource does not exist at all", async () => {
    vi.stubGlobal("EventSource", undefined);
    const { result } = renderHook(() => useRunEvents("run-4"));
    await waitFor(() => expect(result.current.transport).toBe("poll"));
  });

  it("does nothing without a run id", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const { result } = renderHook(() => useRunEvents(null));
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(result.current.stages).toEqual([]);
    expect(result.current.transport).toBeNull();
  });
});
