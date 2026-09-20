/* Hindcast · Monitoring Engines.
 *
 * A control-room view of the BAYES-TRACK hindcast: seven engines, one tile
 * each, in stage order, live over a WebSocket. A hindcast answers the question
 * the forward pipeline cannot -- where and WHEN did this oil enter the water --
 * as a posterior with a credible region, not a single back-trajectory.
 *
 * Two ways to start one: from the slick an OceanTrace run segmented (real scene,
 * forcing from the metocean cache), or the synthetic demo, whose origin is known
 * and which therefore proves recovery end to end. Both are investigator/analyst
 * actions; every role can watch. */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Cpu, FlaskConical, Loader2, Play, Radio, RefreshCw } from "lucide-react";
import EngineTile, { StatusPill } from "../components/hindcast/EngineTile";
import EngineDrawer from "../components/hindcast/EngineDrawer";
import HindcastResult from "../components/hindcast/HindcastResult";
import { Notice, PageHeader } from "../components/ui";
import { api, fmt } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { useEngineFeed } from "../lib/useEngineFeed";
import "../hindcast.css";

const LINK = {
  live: ["LIVE · WebSocket", "he-link-live"],
  polling: ["POLLING · 2 s", "he-link-polling"],
  idle: ["NO JOB", "he-link-idle"],
};

export default function HindcastEngines() {
  const { user } = useSession();
  const canStart = hasRole(user, "investigator", "analyst");
  const [params, setParams] = useSearchParams();
  const jobId = params.get("job") || "";
  const setJobId = useCallback((id) => setParams(id ? { job: id } : {}, { replace: true }), [setParams]);

  const [engines, setEngines] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [runs, setRunsList] = useState([]);
  const [runId, setRunId] = useState("");
  const [loadError, setLoadError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [starting, setStarting] = useState(null);      // "demo" | "run" | null
  const [openEngine, setOpenEngine] = useState(null);
  const [result, setResult] = useState(null);

  const { job, runs: engineRuns, transport } = useEngineFeed(jobId);
  const jobStatus = job?.status;

  const loadJobs = useCallback(async () => {
    const list = await api.hindcastJobs();
    setJobs(list);
    return list;
  }, []);

  useEffect(() => {
    Promise.all([api.hindcastEngines(), loadJobs()])
      .then(([registry, list]) => { setEngines(registry); if (!jobId && list.length) setJobId(list[0].id); })
      .catch((e) => setLoadError(e.message));
    // Runs that have a slick to hindcast: completed, with a measured area.
    api.listRunsPaged({ status: "complete", limit: 100 })
      .then((page) => setRunsList((page.items || []).filter((r) => r.slick_area_km2 > 0 && !r.stages_mock)))
      .catch(() => setRunsList([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The posterior arrives with the job record, once, when the job succeeds.
  useEffect(() => {
    setResult(null);
    if (!jobId || jobStatus !== "succeeded") return undefined;
    let alive = true;
    api.hindcastJob(jobId).then((j) => alive && setResult(j.result)).catch(() => {});
    return () => { alive = false; };
  }, [jobId, jobStatus]);

  useEffect(() => {
    if (jobStatus === "succeeded" || jobStatus === "failed") loadJobs().catch(() => {});
  }, [jobStatus, loadJobs]);

  async function start(kind) {
    setStarting(kind); setActionError(null);
    try {
      const created = kind === "demo" ? await api.hindcastDemo() : await api.hindcastFromRun(runId);
      await loadJobs();
      setJobId(created.job_id);
    } catch (e) { setActionError(e.message); } finally { setStarting(null); }
  }

  const ordered = useMemo(() => [...engines].sort((a, b) => a.order - b.order), [engines]);
  const done = ordered.filter((e) => engineRuns[e.id]?.status === "succeeded").length;
  const runningEngine = ordered.find((e) => engineRuns[e.id]?.status === "running");
  const busy = jobStatus === "running" || jobStatus === "pending";
  const [linkText, linkClass] = LINK[transport];
  const startTitle = !canStart ? "Starting a hindcast needs the investigator or analyst role"
    : busy ? "A hindcast is running; wait for it to finish" : undefined;

  return (
    <div className="he-root" data-testid="hindcast-page">
      <div className="page">
        <PageHeader icon={<Cpu size={17} />} kicker="Hindcast · BAYES-TRACK" title="Monitoring Engines"
          sub="Seven engines take a slick back to where and when it entered the water, as a posterior with a credible region."
          actions={(
            <div className="he-bar">
              <span className={`he-link ${linkClass}`} data-testid="hindcast-transport" data-transport={transport}><Radio size={12} /> {linkText}</span>
              <span className="he-bar-k">Overall</span>
              <span data-testid="hindcast-job-status"><StatusPill status={jobStatus || "pending"} /></span>
              <span className="mono tiny dim">{done}/{ordered.length || 7}</span>
            </div>
          )} />

        <div className="he-bar" style={{ marginBottom: 12 }}>
          <span className="he-bar-k">Hindcast a run</span>
          <select value={runId} onChange={(e) => setRunId(e.target.value)} data-testid="hindcast-run-select"
            aria-label="OceanTrace run to hindcast" disabled={!canStart}>
            <option value="">{runs.length ? "choose a completed run with a slick…" : "no completed run has a slick"}</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>{r.run_id} · {Number(r.slick_area_km2).toFixed(1)} km²</option>
            ))}
          </select>
          <button className="btn btn-primary btn-sm" disabled={!canStart || busy || !runId || starting != null}
            onClick={() => start("run")} title={startTitle || "Hindcast the largest slick of this run with its cached forcing"}
            data-testid="hindcast-run-start">
            {starting === "run" ? <Loader2 size={12} className="ws-spin" /> : <Play size={12} />} Run hindcast
          </button>
          <button className="btn btn-sm" disabled={!canStart || busy || starting != null} onClick={() => start("demo")}
            title={startTitle || "SYNTHETIC: release oil at a known point and time, then ask the engines to find it again"}
            data-testid="hindcast-demo-start">
            {starting === "demo" ? <Loader2 size={12} className="ws-spin" /> : <FlaskConical size={12} />} Run demo job
          </button>

          <span className="he-bar-k" style={{ marginLeft: "auto" }}>Job</span>
          <select value={jobId} onChange={(e) => setJobId(e.target.value)} data-testid="hindcast-job-select" aria-label="Hindcast job">
            {jobs.length === 0 && <option value="">no jobs yet</option>}
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.id} · {j.id === jobId && jobStatus ? jobStatus : j.status} · {fmt.utc(j.created_at).slice(5, 16)}Z
              </option>
            ))}
          </select>
          <button className="btn btn-sm btn-icon" onClick={() => loadJobs().catch((e) => setActionError(e.message))}
            title="Refresh the job list" aria-label="Refresh the job list"><RefreshCw size={12} /></button>
        </div>

        {(job?.label || runningEngine || job?.error) && (
          <div className="he-sub">
            {job?.label && <b>{job.label}</b>}
            {job?.scene_time && <span>scene T = {fmt.utc(job.scene_time)}</span>}
            {job?.created_by && <span>by {job.created_by}</span>}
            {runningEngine && <span className="he-now">▸ {runningEngine.name}</span>}
            {job?.error && <span className="he-err" data-testid="hindcast-job-error">{job.error}</span>}
          </div>
        )}

        {loadError && <Notice tone="danger" testid="hindcast-load-error">Cannot load the hindcast engines: {loadError}</Notice>}
        {actionError && <Notice tone="danger" testid="hindcast-action-error">{actionError}</Notice>}

        <div className="he-grid" data-testid="hindcast-grid">
          {ordered.map((engine, i) => (
            <EngineTile key={engine.id} engine={engine} run={engineRuns[engine.id]} index={i} total={ordered.length}
              prevStatus={i > 0 ? engineRuns[ordered[i - 1].id]?.status : null}
              nextStatus={ordered[i + 1] ? engineRuns[ordered[i + 1].id]?.status : null} onOpen={setOpenEngine} />
          ))}
        </div>

        {!jobId && ordered.length > 0 && (
          <p className="tiny" style={{ marginTop: 18, color: "var(--ink-2)", textAlign: "center" }}>
            No hindcast has run yet. Choose a run above, or start the demo job: it releases synthetic oil at a known
            point and time, then asks the seven engines to find it again.
          </p>
        )}

        <HindcastResult result={result} source={job?.source} />
      </div>

      {openEngine && (
        <EngineDrawer engine={openEngine} run={engineRuns[openEngine.id]} jobId={jobId} onClose={() => setOpenEngine(null)} />
      )}
    </div>
  );
}
