/* The vessel dossier: one block on both surfaces, and the AIS silences. */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import VesselIdentity, { VesselHistory } from "../src/components/vessels/VesselIdentity";
import { aisGaps, AIS_GAP_MIN } from "../src/lib/replay";

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);
const MIN = 60_000;

describe("vessel identity", () => {
  it("says a field was not supplied rather than dashing it", () => {
    wrap(<VesselIdentity dossier={{ mmsi: "419", identity_available: false }} mmsi="419" />);
    expect(screen.getByTestId("vi-imo").textContent).toBe("not supplied");
    expect(screen.getByTestId("vi-no-identity")).toBeInTheDocument();
  });

  it("carries no row for a field no provider fills", () => {
    wrap(<VesselIdentity dossier={{ mmsi: "419", name: "Aurora", identity_available: true }} mmsi="419" />);
    const text = document.body.textContent;
    expect(text).not.toMatch(/DWT/i);
    expect(text).not.toMatch(/no vessel image/i);
    expect(screen.queryByTestId("vi-no-identity")).toBeNull();
  });

  it("shows the dimensions when the archive has them, and omits them when it does not", () => {
    const { unmount } = wrap(<VesselIdentity dossier={{ mmsi: "1", length_m: 183.4, width_m: 32.2 }} mmsi="1" />);
    expect(document.body.textContent).toContain("183.4 m × 32.2 m");
    unmount();
    wrap(<VesselIdentity dossier={{ mmsi: "1" }} mmsi="1" />);
    expect(document.body.textContent).not.toMatch(/Size/);
  });
});

describe("what else this vessel appeared in", () => {
  const dossier = { mmsi: "419", appearance_list: [
    { run_id: "run-a", rank: 1, total_score: 0.81 },
    { run_id: "run-b", filtered: true },
  ] };

  it("lists the other runs, and links each to its attribution stage", () => {
    wrap(<VesselHistory dossier={dossier} exceptRun="run-b" />);
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/investigations/run/run-a/attribution");
  });

  it("says so when this is the only run, rather than showing nothing", () => {
    wrap(<VesselHistory dossier={dossier} exceptRun="run-a" />);
    expect(screen.getByTestId("vessel-history")).toBeInTheDocument();
    wrap(<VesselHistory dossier={{ mmsi: "1", appearance_list: [] }} />);
    expect(screen.getByTestId("vessel-history-empty")).toHaveTextContent(/No other run/i);
  });
});

describe("AIS gaps", () => {
  const t0 = Date.UTC(2023, 0, 8, 0, 0);

  it("finds only silences longer than the threshold, between real fixes", () => {
    const track = {
      path: [[0, 0], [1, 1], [2, 2], [3, 3]],
      times: [t0, t0 + 5 * MIN, t0 + 90 * MIN, t0 + 95 * MIN],
    };
    const gaps = aisGaps(track);
    expect(gaps).toHaveLength(1);
    expect(gaps[0].minutes).toBe(85);
    expect(gaps[0].from).toEqual([1, 1]);
    expect(gaps[0].to).toEqual([2, 2]);
  });

  it("skips fixes with no timestamp instead of inventing an interval", () => {
    expect(aisGaps({ path: [[0, 0], [1, 1]], times: [t0, null] })).toEqual([]);
    expect(aisGaps({ path: [], times: [] })).toEqual([]);
    expect(aisGaps(null)).toEqual([]);
  });

  it("uses a threshold longer than a class-A reporting interval", () => {
    expect(AIS_GAP_MIN).toBeGreaterThanOrEqual(15);
  });
});
