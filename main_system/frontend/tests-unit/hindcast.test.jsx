/* The Origin stage's two answers, and the honesty rules around them. */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import BayesOriginPanel, { jobsForRun } from "../src/components/workspace/BayesOriginPanel";
import { api } from "../src/lib/api";

const ctx = { runId: "run-7", canRun: true };
const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("finding a run's Bayesian hindcast", () => {
  it("matches only jobs BAYES-TRACK tagged with this run", () => {
    const jobs = [{ id: "a", source: "run:run-7" }, { id: "b", source: "run:run-8" },
      { id: "c", source: "request" }, { id: "d" }];
    expect(jobsForRun(jobs, "run-7").map((j) => j.id)).toEqual(["a"]);
    expect(jobsForRun(null, "run-7")).toEqual([]);
  });
});

describe("the Bayesian panel", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("says plainly when no hindcast has been run, and offers to run one", async () => {
    vi.spyOn(api, "hindcastJobs").mockResolvedValue([{ id: "x", source: "run:other" }]);
    wrap(<BayesOriginPanel ctx={ctx} />);
    await screen.findByTestId("bayes-none");
    expect(screen.getByTestId("bayes-start")).toBeInTheDocument();
  });

  it("refuses to offer the run to a role that cannot start one", async () => {
    vi.spyOn(api, "hindcastJobs").mockResolvedValue([]);
    wrap(<BayesOriginPanel ctx={{ ...ctx, canRun: false }} />);
    await screen.findByTestId("bayes-none");
    expect(screen.queryByTestId("bayes-start")).toBeNull();
  });

  it("reports a job that is still running rather than an empty panel", async () => {
    vi.spyOn(api, "hindcastJobs").mockResolvedValue([{ id: "j1", source: "run:run-7", status: "running" }]);
    wrap(<BayesOriginPanel ctx={ctx} />);
    expect(await screen.findByTestId("bayes-running")).toHaveTextContent(/running/i);
  });

  it("shows the posterior, and never calls it the source", async () => {
    vi.spyOn(api, "hindcastJobs").mockResolvedValue([{ id: "j2", source: "run:run-7", status: "complete" }]);
    vi.spyOn(api, "hindcastJob").mockResolvedValue({
      id: "j2", source: "run:run-7",
      result: {
        map: { lat: 27.8, lon: -90.1, tau_h: 13, release_time: "2023-01-07T11:10:00Z" },
        release_window: { credible_mass: 0.9, tau_lo_h: 9, tau_hi_h: 17,
          earliest: "2023-01-07T07:10:00Z", latest: "2023-01-07T15:10:00Z" },
        hdr90_area_km2: 12.4, p_tau: { tau_hours: [9, 13, 17], probability: [0.2, 0.6, 0.2] },
        modes: [], age_modes: [], multi_modal: false,
      },
    });
    wrap(<BayesOriginPanel ctx={ctx} />);
    await screen.findByTestId("hindcast-result");
    expect(screen.getByText(/MAP origin/i)).toBeInTheDocument();
    expect(screen.getByText(/90 % origin region/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\bthe source\b/i);
    // the two estimates are shown side by side, never merged
    expect(document.body.textContent).toMatch(/never merged/i);
  });

  it("surfaces a failure instead of pretending there is no hindcast", async () => {
    vi.spyOn(api, "hindcastJobs").mockRejectedValue(new Error("engine unreachable"));
    wrap(<BayesOriginPanel ctx={ctx} />);
    expect(await screen.findByTestId("bayes-error")).toHaveTextContent("engine unreachable");
  });
});
