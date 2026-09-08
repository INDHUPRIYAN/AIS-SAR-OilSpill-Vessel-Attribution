/* The top bar's honesty strip and its clock.
 *
 * The chip row is the one place in the product that answers "what am I
 * looking at?" without being asked, which makes it the one place where a
 * cheerful default does the most damage: a green strip over a run whose AIS
 * was synthetic is a lie the rest of the page then inherits.
 *
 * So these pin the awkward cases rather than the happy one — a manifest that
 * records nothing, a run that cannot be read, a stage that fell back — and
 * they pin that the strip describes the RUN, never the machine.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useEffect } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProvenanceChips, ZuluClock } from "../src/components/TopBarStatus";
import { ShellProvider, useShell } from "../src/lib/shell";

function SetRun({ runId }) {
  const { setRunId } = useShell();
  useEffect(() => { if (runId) setRunId(runId); }, [runId, setRunId]);
  return null;
}

function renderChips(runId, reply) {
  global.fetch = vi.fn(async (path) => {
    if (String(path).startsWith("/api/runs/")) return reply(String(path));
    return { ok: true, status: 200, json: async () => ({}) };
  });
  return render(
    <MemoryRouter>
      <ShellProvider>
        <SetRun runId={runId} />
        <ProvenanceChips />
      </ShellProvider>
    </MemoryRouter>);
}

const ok = (body) => ({ ok: true, status: 200, json: async () => body });

/* Shaped like the flagship's own payload, trimmed to the fields the strip
 * reads. Real stage sources, real model names, a real digest. */
const FLAGSHIP = {
  run_id: "inv-gulf-flagship-20230108-2day",
  incident_id: null,
  status: "complete",
  detect_engine: "ml",
  stages_total: 5, stages_real: 5,
  manifest: {
    artefact_digest: "fd42e078f8366110",
    models: [{ kind: "segment", name: "unet-r34-fullcorpus-e48" },
             { kind: "screen", name: "yolo11n-screen-dartis-2026-08-24" }],
    ais: { data_source: "real", covers_origin: true,
           detail: "a real AIS archive holds reports inside the computed origin window" },
    stages: [
      { stage: "detect", source: "real", detail: "engine=ml" },
      { stage: "characterise", source: "real" },
      { stage: "drift_hindcast", source: "real" },
      { stage: "drift_forecast", source: "real" },
      { stage: "attribution", source: "real" },
    ],
  },
};

afterEach(() => { vi.restoreAllMocks(); });

// ------------------------------------------------------------------ chips --

describe("provenance chips", () => {
  it("shows one ghost chip when no run is open", async () => {
    renderChips(null, ok);
    await waitFor(() =>
      expect(screen.getByTestId("chip-no-run")).toHaveTextContent("NO RUN"));
    expect(screen.queryByTestId("chip-ais")).not.toBeInTheDocument();
    // and it did not go asking the server about a run that does not exist
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("reads the run in context and names it", async () => {
    renderChips("inv-gulf-flagship-20230108-2day", async () => ok(FLAGSHIP));
    await waitFor(() => expect(screen.getByTestId("chip-run"))
      .toHaveTextContent("inv-gulf-flagship-20230108-2day"));
    expect(global.fetch.mock.calls[0][0])
      .toBe("/api/runs/inv-gulf-flagship-20230108-2day");
  });

  it("says REAL for a run whose AIS came from an archive", async () => {
    renderChips("r", async () => ok(FLAGSHIP));
    await waitFor(() => expect(screen.getByTestId("chip-ais")).toHaveTextContent("REAL"));
    expect(screen.getByTestId("chip-stages")).toHaveTextContent("5/5 REAL");
  });

  it("says SYNTHETIC loudly for a run whose AIS was generated", async () => {
    renderChips("r", async () => ok({
      ...FLAGSHIP,
      manifest: { ...FLAGSHIP.manifest, ais: { data_source: "synthetic" } },
    }));
    const chip = await screen.findByTestId("chip-ais");
    expect(chip).toHaveTextContent("SYNTHETIC");
    // Gold-filled is the loudest chip in the system by design (spec §8.7).
    expect(chip.className).toContain("pchip-synth");
  });

  it("says UNRECORDED rather than assuming real when the manifest is silent", async () => {
    const { ais, ...rest } = FLAGSHIP.manifest;
    renderChips("r", async () => ok({ ...FLAGSHIP, manifest: rest }));
    const chip = await screen.findByTestId("chip-ais");
    expect(chip).toHaveTextContent("UNRECORDED");
    expect(chip).not.toHaveTextContent("REAL");
  });

  it("says NO MANIFEST rather than showing a stage count it cannot support", async () => {
    const { manifest, ...rest } = FLAGSHIP;
    renderChips("r", async () => ok(rest));
    expect(await screen.findByTestId("chip-stages")).toHaveTextContent("NO MANIFEST");
  });

  it("names every source that was not real, instead of hiding it in a count", async () => {
    // 4/5 real reads as a good run. The mocked stage has to be visible.
    renderChips("r", async () => ok({
      ...FLAGSHIP, stages_real: 3,
      manifest: {
        ...FLAGSHIP.manifest,
        stages: [
          { stage: "detect", source: "real" },
          { stage: "characterise", source: "real" },
          { stage: "drift_hindcast", source: "mock", detail: "Engine B unavailable" },
          { stage: "drift_forecast", source: "real" },
          { stage: "attribution", source: "fallback" },
        ],
      },
    }));
    const mock = await screen.findByTestId("chip-source-mock");
    expect(mock).toHaveTextContent("MOCK");
    expect(mock).toHaveTextContent("drift_hindcast");     // one stage: named
    expect(mock).toHaveAttribute("title", expect.stringContaining("Engine B unavailable"));
    expect(screen.getByTestId("chip-source-fallback")).toHaveTextContent("FALLBACK");
    // real stages get no chip of their own; they are the count
    expect(screen.queryByTestId("chip-source-real")).not.toBeInTheDocument();
  });

  it("groups several stages of one source into a chip that still says the word", async () => {
    // Three synthetic stages used to mean three chips, which pushed the later
    // ones off the right edge of the bar -- a provenance chip you cannot see.
    renderChips("r", async () => ok({
      ...FLAGSHIP, stages_real: 2,
      manifest: {
        ...FLAGSHIP.manifest,
        stages: [
          { stage: "detect", source: "real" },
          { stage: "characterise", source: "real" },
          { stage: "drift_hindcast", source: "synthetic" },
          { stage: "drift_forecast", source: "synthetic" },
          { stage: "attribution", source: "synthetic" },
        ],
      },
    }));
    const chip = await screen.findByTestId("chip-source-synthetic");
    expect(chip).toHaveTextContent("SYNTHETIC");
    expect(chip).toHaveTextContent("3 stages");
    // and which three is one hover away, not lost
    expect(chip).toHaveAttribute("title", expect.stringContaining("attribution"));
  });

  it("says when the registry row was reconciled rather than observed", async () => {
    // The flagship's own situation. The artefacts are untouched evidence; the
    // ROW describing them was rebuilt afterwards, and a reader should not have
    // to infer which kind of record they are looking at.
    renderChips("r", async () => ok({ ...FLAGSHIP, registry_source: "reconciled" }));
    const chip = await screen.findByTestId("chip-registry-source");
    expect(chip).toHaveTextContent("RECONCILED");
    expect(chip).toHaveAttribute("title", expect.stringContaining("digest are unchanged"));
  });

  it("shows no such chip for a run the API watched happen", async () => {
    renderChips("r", async () => ok({ ...FLAGSHIP, registry_source: "api" }));
    await screen.findByTestId("chip-ais");
    expect(screen.queryByTestId("chip-registry-source")).not.toBeInTheDocument();
  });

  it("shows the artefact digest, truncated, for the run it describes", async () => {
    renderChips("r", async () => ok(FLAGSHIP));
    expect(await screen.findByTestId("chip-digest")).toHaveTextContent("fd42e078");
  });

  it("claims no provenance at all when the run cannot be read", async () => {
    renderChips("r", async () => ({
      ok: false, status: 404, json: async () => ({ detail: "run not found" }),
    }));
    const chip = await screen.findByTestId("chip-run-error");
    expect(chip).toHaveTextContent("UNREADABLE");
    expect(screen.queryByTestId("chip-ais")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chip-stages")).not.toBeInTheDocument();
  });

  it("can be closed, which empties the strip rather than leaving a stale run", async () => {
    renderChips("r", async () => ok(FLAGSHIP));
    await screen.findByTestId("chip-ais");
    fireEvent.click(screen.getByTestId("chip-clear"));
    await waitFor(() =>
      expect(screen.getByTestId("chip-no-run")).toBeInTheDocument());
    expect(screen.queryByTestId("chip-ais")).not.toBeInTheDocument();
  });
});

// ------------------------------------------------------------------ clock --

describe("ZULU clock", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2023-01-08T00:10:08Z"));
  });
  afterEach(() => { vi.useRealTimers(); });

  it("shows UTC with a Z suffix", () => {
    render(<ZuluClock />);
    expect(screen.getByTestId("zulu-utc")).toHaveTextContent("00:10:08Z");
  });

  it("ticks", () => {
    render(<ZuluClock />);
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByTestId("zulu-utc")).toHaveTextContent("00:10:10Z");
  });

  it("hides IST until asked, then labels it", () => {
    render(<ZuluClock />);
    expect(screen.queryByTestId("zulu-ist")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("zulu-clock"));
    const ist = screen.getByTestId("zulu-ist");
    // UTC+05:30, computed from the epoch rather than from the machine's
    // timezone: a workstation set to the wrong zone must not be able to print
    // a local time under a Z suffix.
    expect(ist).toHaveTextContent("05:40:08 IST");
  });

  it("is not affected by the browser's own timezone", () => {
    // The component formats from ISO, so a machine in any zone reads the same.
    render(<ZuluClock />);
    const shown = within(screen.getByTestId("zulu-clock")).getByTestId("zulu-utc");
    expect(shown.textContent).toBe(`${new Date().toISOString().slice(11, 19)}Z`);
  });
});
