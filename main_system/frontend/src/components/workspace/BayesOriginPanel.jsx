/* The Bayesian origin, inside the stage that asks the question.
 *
 * OceanTrace answers "where did this come from?" twice: Engine B integrates a
 * particle cloud backwards and publishes an ellipse and a window, and
 * BAYES-TRACK computes a posterior over place AND age with a credible region.
 * The second one lived on its own page with no way back to the run it came
 * from, so an analyst standing in the Origin stage never saw it.
 *
 * Here it is the same estimate, rendered by the same component, next to the
 * drift's own answer. Nothing is merged and nothing is averaged: two methods
 * that agree are evidence, and two that disagree is something the analyst
 * needs to see rather than have resolved for them.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Cpu, Loader2 } from "lucide-react";

import HindcastResult from "../hindcast/HindcastResult";
import { Row, Section } from "./intel";
import { api } from "../../lib/api";
import { url } from "../../lib/urls";

/** BAYES-TRACK tags a job started from a pipeline run with its run id. */
export const jobsForRun = (jobs, runId) =>
  (jobs || []).filter((j) => j.source === `run:${runId}`);

export default function BayesOriginPanel({ ctx }) {
  const { runId, canRun } = ctx;
  const [state, setState] = useState({ phase: "loading" });
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    if (!runId) { setState({ phase: "norun" }); return; }
    setState((s) => (s.phase === "ready" ? s : { phase: "loading" }));
    try {
      const mine = jobsForRun(await api.hindcastJobs(), runId);
      if (!mine.length) { setState({ phase: "none" }); return; }
      const done = mine.find((j) => j.status === "complete");
      if (!done) { setState({ phase: "running", job: mine[0] }); return; }
      const job = await api.hindcastJob(done.id);
      setState(job?.result ? { phase: "ready", job } : { phase: "none" });
    } catch (e) {
      setState({ phase: "error", message: e.message || "could not read the hindcast jobs" });
    }
  }, [runId]);

  useEffect(() => { load(); }, [load]);

  // A job takes minutes; while one is running this is the only thing that
  // changes, so it is worth asking again.
  useEffect(() => {
    if (state.phase !== "running") return undefined;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [state.phase, load]);

  const start = async () => {
    setStarting(true);
    try { await api.hindcastFromRun(runId); await load(); }
    catch (e) { setState({ phase: "error", message: e.message || "the engine refused this run" }); }
    finally { setStarting(false); }
  };

  return (
    <div className="ip" data-testid="intel-bayes">
      <div className="ip-head"><span className="ip-head-title">Bayesian origin</span>
        <span className="dim tiny">BAYES-TRACK</span></div>
      <Section title="Second opinion">
        <Row k="Method" mono={false} v="Posterior over release place and age, from an ensemble of backward drifts" />
        <Row k="Relationship" mono={false}
          v="Independent of the drift stage's own estimate above. Shown side by side; never merged." />
      </Section>

      {state.phase === "loading" && (
        <div className="ip-note" data-testid="bayes-loading"><Loader2 size={13} className="ws-spin" /> Looking for a hindcast of this run…</div>
      )}
      {state.phase === "norun" && <div className="ip-note">Open a run to see its Bayesian hindcast.</div>}
      {state.phase === "error" && (
        <div className="ip-note ip-note-err" data-testid="bayes-error">
          {state.message}
          <button className="btn btn-sm" onClick={load}>Try again</button>
        </div>
      )}
      {state.phase === "running" && (
        <div className="ip-note" data-testid="bayes-running">
          <Loader2 size={13} className="ws-spin" /> A hindcast of this run is {state.job.status}.
          <Link className="btn btn-sm" to={url.engines(state.job.id)}><Cpu size={12} /> Watch the engines</Link>
        </div>
      )}
      {state.phase === "none" && (
        <div className="ip-note" data-testid="bayes-none">
          No Bayesian hindcast has been run for this run.
          {canRun
            ? <button className="btn btn-sm btn-primary" onClick={start} disabled={starting} data-testid="bayes-start">
              {starting ? <Loader2 size={12} className="ws-spin" /> : <Cpu size={12} />} Run one
            </button>
            : <span className="dim"> Your role cannot start one.</span>}
        </div>
      )}
      {state.phase === "ready" && (
        <>
          <HindcastResult result={state.job.result} source={state.job.source} />
          <div className="ip-note">
            <Link className="btn btn-sm" to={url.engines(state.job.id)}><Cpu size={12} /> Engine detail</Link>
          </div>
        </>
      )}
    </div>
  );
}
