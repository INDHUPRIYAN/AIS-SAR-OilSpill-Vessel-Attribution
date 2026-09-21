/* The demo case: one real, sealed run an evaluator can walk end to end.
 *
 * Not a fixture and not a mock. It is the canonical acceptance run
 * (`inv-gulf-flagship-20230108-2day`, digest fd42e078f8366110 — DEMO_RUNBOOK.md),
 * whose artefacts on disk are what every stage reads. "Demo" says only that it
 * was chosen for the walkthrough; every number in it is the pipeline's own.
 *
 * `VITE_DEMO_RUN_ID` points a deployment at a different showcase run. Whether
 * the run exists is asked of the server, not assumed: a host without it shows
 * "demo case not installed on this host" instead of a link that would 404.
 */

import { useEffect, useState } from "react";

import { api } from "./api";

// @ts-ignore -- Vite replaces this exact expression at build time.
const ENV = import.meta.env || {};

export const DEMO_RUN_ID = (typeof ENV.VITE_DEMO_RUN_ID === "string" && ENV.VITE_DEMO_RUN_ID.trim())
  || "inv-gulf-flagship-20230108-2day";

export const isDemoRun = (runId) => Boolean(runId) && runId === DEMO_RUN_ID;

/** `{ state: "checking" | "ready" | "missing" | "error", run }` */
export function useDemoRun() {
  const [s, setS] = useState({ state: "checking", run: null });
  useEffect(() => {
    let alive = true;
    api.getRun(DEMO_RUN_ID)
      .then((run) => { if (alive) setS(run ? { state: "ready", run } : { state: "missing", run: null }); })
      .catch((e) => { if (alive) setS({ state: e?.status === 404 ? "missing" : "error", run: null, error: e }); });
    return () => { alive = false; };
  }, []);
  return s;
}
