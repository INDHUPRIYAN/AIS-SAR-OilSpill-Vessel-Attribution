/* The bell: what is waiting, where it points, and honest failure. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Notifications, { reasonFor, targetFor } from "../src/components/shell/Notifications";
import { api } from "../src/lib/api";

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("what a notification means and where it goes", () => {
  it("says why, in the reader's terms", () => {
    expect(reasonFor("run_failed")).toBe("a run failed");
    expect(reasonFor("run_complete")).toBe("a run you started finished");
    expect(reasonFor("detection")).toBe("the detector opened a case");
    expect(reasonFor("something_new")).toBe("something new");
  });

  it("opens the thing itself, not the queue, when there is a thing", () => {
    expect(targetFor({ run_id: "r1" })).toBe("/investigations/run/r1");
    expect(targetFor({ incident_id: "INC-1" })).toBe("/operations/incidents?focus=INC-1");
    expect(targetFor({})).toBe("/operations/alerts");
  });
});

describe("the panel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("opens in place instead of navigating away, and lists open alerts", async () => {
    vi.spyOn(api, "listAlerts").mockResolvedValue({ alerts: [
      { id: "a1", kind: "run_failed", severity: "warning", title: "Run failed · r1", run_id: "r1",
        created_utc: "2026-09-21T04:00:00Z", status: "open" },
    ] });
    wrap(<Notifications summary={{ open: 1 }} />);
    fireEvent.click(screen.getByTestId("alert-bell"));
    expect(await screen.findAllByTestId("ntf-row")).toHaveLength(1);
    expect(screen.getByText("Run failed · r1").closest("a").getAttribute("href")).toBe("/investigations/run/r1");
    expect(api.listAlerts).toHaveBeenCalledWith({ status: "open", limit: 8 });
  });

  it("says nothing is open rather than showing an empty box", async () => {
    vi.spyOn(api, "listAlerts").mockResolvedValue({ alerts: [] });
    wrap(<Notifications summary={{ open: 0 }} />);
    fireEvent.click(screen.getByTestId("alert-bell"));
    expect(await screen.findByTestId("ntf-empty")).toBeInTheDocument();
  });

  it("reports a feed that did not answer, with a retry", async () => {
    vi.spyOn(api, "listAlerts").mockRejectedValue(new Error("feed down"));
    wrap(<Notifications summary={{ open: 3 }} />);
    fireEvent.click(screen.getByTestId("alert-bell"));
    expect(await screen.findByTestId("ntf-error")).toHaveTextContent("feed down");
  });

  it("acknowledges through the same endpoint the queue uses", async () => {
    vi.spyOn(api, "listAlerts").mockResolvedValue({ alerts: [
      { id: "a2", kind: "detection", severity: "critical", title: "Oil detected", created_utc: "2026-09-21T04:00:00Z" },
    ] });
    const ack = vi.spyOn(api, "ackAlert").mockResolvedValue({});
    wrap(<Notifications summary={{ open: 1 }} />);
    fireEvent.click(screen.getByTestId("alert-bell"));
    fireEvent.click(await screen.findByTestId("ntf-ack-a2"));
    await waitFor(() => expect(ack).toHaveBeenCalledWith("a2"));
  });

  it("closes on Escape", async () => {
    vi.spyOn(api, "listAlerts").mockResolvedValue({ alerts: [] });
    wrap(<Notifications summary={{ open: 0 }} />);
    fireEvent.click(screen.getByTestId("alert-bell"));
    await screen.findByTestId("notifications");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("notifications")).toBeNull();
  });
});
