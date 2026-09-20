/* The investigation register.
 *
 * Pins that the register is a JOIN and not a guess: a run filed by an
 * investigation shows that investigation's name, its incident's zone and
 * detection time, and its top-ranked candidate as a ranking (MMSI + score);
 * a run nobody filed is marked UNFILED and its empty joins read as "—" and
 * "no candidate", never as invented values. Also pins that the primary
 * action is enabled for an investigator, and that a 403 on /api/users does
 * not take the page down.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listRunsPaged = vi.fn();
const listInvestigations = vi.fn();
const listIncidents = vi.fn();
const listZones = vi.fn();
const localScenes = vi.fn();
const listUsers = vi.fn();
const listReports = vi.fn();

vi.mock("../src/lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    api: {
      ...real.api,
      listRunsPaged: (...a) => listRunsPaged(...a),
      listInvestigations: (...a) => listInvestigations(...a),
      listIncidents: (...a) => listIncidents(...a),
      listZones: (...a) => listZones(...a),
      localScenes: (...a) => localScenes(...a),
      listUsers: (...a) => listUsers(...a),
      listReports: (...a) => listReports(...a),
    },
  };
});

const session = { user: { id: "u1", role: "investigator" } };
vi.mock("../src/lib/session", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, useSession: () => session };
});

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, useNavigate: () => mockNavigate };
});

import Investigations from "../src/pages/Investigations";

const FILED = {
  run_id: "inv-gulf-flagship-20230108-2day", investigation_id: "inv-01", incident_id: "inc-01",
  scene_id: "S1A_IW_GRDH_1SDV_20230108T001008", status: "complete",
  started_utc: "2023-01-08T01:00:00Z", finished_utc: "2023-01-08T01:07:00Z", seconds: 420,
  detect_engine: "ml", stages_total: 5, stages_real: 5, stages_mock: 0, stages_failed: 0,
  top_suspect_mmsi: 367653160, top_score: 0.6691, slick_area_km2: 12.821, region: null,
  registry_source: "api", archived: false, error: null,
};
const UNFILED = {
  run_id: "cli-reconciled-20230110", investigation_id: null, incident_id: null,
  scene_id: "S1B_IW_GRDH_1SDV_20230110T120000", status: "failed",
  started_utc: "2023-01-10T12:00:00Z", finished_utc: null, seconds: null,
  detect_engine: "threshold", stages_total: 5, stages_real: 2, stages_mock: 1, stages_failed: 1,
  top_suspect_mmsi: null, top_score: null, slick_area_km2: null, region: null,
  registry_source: "reconciled", archived: false, error: "correlate: no AIS in window",
};

function forbidden() {
  const e = new Error("forbidden");
  e.status = 403;
  return Promise.reject(e);
}

beforeEach(() => {
  vi.clearAllMocks();
  listRunsPaged.mockResolvedValue({ total: 2, items: [FILED, UNFILED] });
  listInvestigations.mockResolvedValue([
    { id: "inv-01", name: "Gulf flagship — 8 Jan", scene_id: FILED.scene_id,
      created_utc: "2023-01-08T00:50:00Z", runs: [FILED.run_id] },
  ]);
  listIncidents.mockResolvedValue({ items: [
    { id: "inc-01", status: "investigating", zone_id: "z-gom", zone_path: "gom", severity: "high",
      assignee_id: "u7", source_run_id: FILED.run_id, area_km2: 12.821,
      detected_utc: "2023-01-08T00:10:08Z",
      geometry: { type: "Point", coordinates: [-90.19, 27.81] } },
    { id: "inc-02", status: "closed", zone_id: null, severity: "low", assignee_id: null,
      source_run_id: "some-other-run", area_km2: 1.0, detected_utc: "2022-12-01T00:00:00Z",
      geometry: null },
  ] });
  listZones.mockResolvedValue({ zones: [{ id: "z-gom", name: "Gulf of Mexico North" }] });
  localScenes.mockResolvedValue({ scenes: [
    { id: "gulf-2day", label: "Gulf of Mexico, 8 Jan 2023", scene_id: FILED.scene_id,
      bbox: [-91, 27, -89, 28.5] },
  ] });
  listUsers.mockImplementation(forbidden);
  listReports.mockResolvedValue({ items: [{ id: "r1", run_id: FILED.run_id, status: "draft" }] });
});

function renderPage() {
  return render(<MemoryRouter><Investigations /></MemoryRouter>);
}

/* Rows come back in the register's own order (CREATED desc), so pick them by
 * run id rather than by position: [filed, unfiled]. */
async function rows() {
  await waitFor(() => expect(screen.getAllByTestId("record-row")).toHaveLength(2));
  const all = screen.getAllByTestId("record-row");
  const byId = (id) => all.find((r) => r.getAttribute("data-run-id") === id);
  return [byId(FILED.run_id), byId(UNFILED.run_id)];
}

describe("Investigations register", () => {
  it("renders both records from the API, with the joined zone and the UNFILED mark", async () => {
    renderPage();
    const [first, second] = await rows();
    // Newest first: the unfiled 10 Jan run sorts above the 8 Jan flagship.
    expect(screen.getAllByTestId("record-row")[0]).toBe(second);

    expect(first).toHaveTextContent(FILED.run_id);
    expect(first).toHaveTextContent("Gulf flagship — 8 Jan");
    expect(first).toHaveTextContent("Gulf of Mexico North");
    expect(first).not.toHaveTextContent("UNFILED");

    expect(second).toHaveTextContent(UNFILED.run_id);
    expect(second).toHaveTextContent("UNFILED");
    expect(second).toHaveTextContent("no candidate");
  });

  it("shows the top candidate as MMSI and score, and never a verdict", async () => {
    renderPage();
    const [first] = await rows();
    expect(first).toHaveTextContent("MMSI 367653160 · 0.67");
    expect(first).not.toHaveTextContent(/guilty|responsible/i);
  });

  it("reads the location from the scene catalogue and the detection time from the incident", async () => {
    renderPage();
    const [first, second] = await rows();
    expect(first).toHaveTextContent("Gulf of Mexico, 8 Jan 2023");
    expect(first).toHaveTextContent("2023-01-08 00:10:08Z");
    expect(first).toHaveTextContent("12.82 km²");
    // The unfiled run has no incident: no invented time, area or zone.
    const cells = within(second).getAllByRole("cell");
    expect(cells[3]).toHaveTextContent("—");
    expect(cells[4]).toHaveTextContent("—");
    expect(cells[5]).toHaveTextContent("—");
  });

  it("marks provenance per row: 5/5 REAL, and MOCK when a stage was mocked", async () => {
    renderPage();
    const [first, second] = await rows();
    expect(first).toHaveTextContent("5/5 REAL");
    expect(first).not.toHaveTextContent("MOCK");
    expect(second).toHaveTextContent("2/5 REAL");
    expect(second).toHaveTextContent("MOCK");
  });

  it("keeps the column order the register promises", async () => {
    renderPage();
    await rows();
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent.trim().toUpperCase());
    expect(heads).toEqual([
      "INVESTIGATION ID", "STATUS", "LOCATION", "DETECTION TIME", "AREA", "ZONE",
      "TOP CANDIDATE", "CREATED", "UPDATED",
    ]);
  });

  it("enables NEW INVESTIGATION for an investigator and navigates to the workspace", async () => {
    renderPage();
    await rows();
    const btn = screen.getByTestId("new-investigation");
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    expect(mockNavigate).toHaveBeenCalledWith("/investigation?new=1");
  });

  it("opens a record in the workspace on row click", async () => {
    renderPage();
    const [first] = await rows();
    fireEvent.click(first);
    expect(mockNavigate).toHaveBeenCalledWith(`/investigation?run=${FILED.run_id}`);
  });

  it("survives a 403 on /api/users and labels the officer by id", async () => {
    renderPage();
    await rows();
    expect(listUsers).toHaveBeenCalled();
    const officer = screen.getByTestId("f-assignee");
    expect(within(officer).getByRole("option", { name: "officer #u7" })).toBeInTheDocument();
  });

  it("counts the tiles from the data, not from a constant", async () => {
    renderPage();
    await rows();
    await waitFor(() => expect(screen.getByTestId("tile-reports")).toHaveTextContent("1"));
    expect(screen.getByTestId("tile-records")).toHaveTextContent("2");
    expect(screen.getByTestId("tile-open")).toHaveTextContent("1");
    expect(screen.getByTestId("tile-ranked")).toHaveTextContent("1");
    expect(screen.getByTestId("inv-foot")).toHaveTextContent("Showing 2 of 2 records");
  });

  it("filters by status client-side and clears", async () => {
    renderPage();
    await rows();
    fireEvent.change(screen.getByTestId("f-status"), { target: { value: "failed" } });
    await waitFor(() => expect(screen.getAllByTestId("record-row")).toHaveLength(1));
    expect(screen.getByTestId("inv-foot")).toHaveTextContent("Showing 1 of 2 records");
    fireEvent.click(screen.getByTestId("f-clear"));
    await waitFor(() => expect(screen.getAllByTestId("record-row")).toHaveLength(2));
  });

  it("shows the empty state when the API holds no runs", async () => {
    listRunsPaged.mockResolvedValue({ total: 0, items: [] });
    renderPage();
    await waitFor(() => expect(screen.getByText("No investigation records")).toBeInTheDocument());
    expect(screen.getByText("Start a new investigation to create the first record.")).toBeInTheDocument();
  });

  it("disables NEW INVESTIGATION for a role that cannot open one", async () => {
    session.user = { id: "u2", role: "viewer" };
    try {
      renderPage();
      await rows();
      const btn = screen.getByTestId("new-investigation");
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute("title", "Your role cannot open investigations");
    } finally {
      session.user = { id: "u1", role: "investigator" };
    }
  });
});
