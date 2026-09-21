/* Investigations landing page.
 *
 * Each run shows how much of it was real. A run that leaned on mocks is not
 * hidden or ranked lower -- it is labelled, because the ratio is the single
 * most useful thing to know before opening one.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { FolderOpen, Plus, PlayCircle, WifiOff, Layers, FileCheck } from "lucide-react";

import { Badge, Card, DataState, Dot, PageHeader, Spinner, Stat, Empty } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { url } from "../lib/urls";

export default function Dashboard() {
  const nav = useNavigate();
  const { data: runs, reload, error: runsError, loading: runsLoading } = useApi(() => api.listRuns(), [], { interval: 6000 });
  const { data: replay } = useApi(() => api.replayRuns(), []);
  const { data: invs, reload: reloadInvs } = useApi(() => api.listInvestigations(), []);
  const { data: catalog } = useApi(() => api.localScenes(), []);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  // Null until the catalog arrives, then the server's declared default -- a
  // real acquisition. The mock raster used to be hardcoded here, which made
  // every run started from this page a 1-of-5-real smoke test (audit N-14).
  const [sceneId, setSceneId] = useState(null);

  const scenes = (catalog?.scenes || []).filter((s) => s.available);
  const chosen = scenes.find((s) => s.id === (sceneId ?? catalog?.default_id)) || null;

  const complete = (runs || []).filter((r) => r.status === "complete");
  const fullyReal = complete.filter((r) => r.stages_real === r.stages_total && !r.stages_mock);

  async function createAndRun() {
    if (!chosen) return;
    setCreating(true);
    try {
      const inv = await api.createInvestigation({
        name, scene_meta_path: chosen.scene_meta_path,
      });
      const started = await api.startRun(inv.id, { engine: "auto" });
      await reloadInvs();
      nav(url.workspace({ run: started.run_id }));
    } finally { setCreating(false); }
  }

  return (
    <div className="page">
      <PageHeader icon={<FolderOpen size={17} />} kicker="Analysis" title="Investigations"
        sub="Every pipeline run, with the provenance of what produced it." />

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Card><Stat label="Investigations" value={invs?.length ?? "—"} /></Card>
        <Card><Stat label="Completed runs" value={complete.length} /></Card>
        <Card><Stat label="Fully real runs" value={fullyReal.length} tone="ok"
          sub="no mocked stages" /></Card>
        <Card><Stat label="Replayable offline" value={replay?.count ?? "—"} tone="ok"
          sub="no network required" /></Card>
      </div>

      <Card title="New investigation" style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 220px" }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Name</div>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div style={{ flex: "1 1 260px" }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Scene</div>
            <select
              value={chosen?.id || ""}
              onChange={(e) => setSceneId(e.target.value)}
              disabled={!scenes.length}
              style={{ width: "100%" }}
            >
              {scenes.map((s) => (
                <option key={s.id} value={s.id}>{s.label} — {s.source}</option>
              ))}
            </select>
          </div>
          <button className="btn btn-primary" onClick={createAndRun}
                  disabled={creating || !name || !chosen}>
            {creating ? <Spinner /> : <Plus size={13} />} Create &amp; run
          </button>
        </div>

        {chosen && (
          <div style={{ marginTop: 11 }}>
            <div style={{ display: "flex", gap: 7, alignItems: "center", flexWrap: "wrap" }}>
              {/* Reuses the shared status vocabulary: a training scene is
                  evidentially DEGRADED, the mock raster is MOCK. */}
              <Badge status={chosen.provenance === "mock" ? "MOCK"
                           : chosen.provenance === "corpus_train" ? "DEGRADED" : "OK"}>
                {chosen.source}
              </Badge>
              <span className="tiny muted">
                {chosen.scene_id} · {fmt.utc(chosen.acquired_utc)}
                {chosen.time_basis === "assigned" && " · time assigned, not measured"}
              </span>
            </div>
            {/* Caveats are rendered, never summarised away: a scene the model
                trained on cannot be read as evidence of accuracy. */}
            {chosen.caveats?.map((c) => (
              <div key={c} className="tiny muted" style={{ marginTop: 6, lineHeight: 1.55 }}>⚠ {c}</div>
            ))}
          </div>
        )}

        <div className="tiny muted" style={{ marginTop: 9 }}>
          Runs the full pipeline: detect → characterise → hindcast → forecast → attribute.
          Typically ~30 seconds.
        </div>
      </Card>

      <Card
        title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
          <WifiOff size={13} color="var(--ok)" /> Offline replay
        </span>}
        style={{ marginBottom: 18 }}
      >
        <div className="tiny muted" style={{ marginBottom: 11, lineHeight: 1.6 }}>
          These runs render entirely from contract files already on disk. If every
          provider is unreachable and the GPU is missing, opening one still shows the
          full investigation — the detection, the drift cloud and the ranked suspects.
          This is the demo&apos;s last line of defence, not a mock.
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>
          {(replay?.runs || []).slice(0, 12).map((r) => (
            <motion.button
              key={r.run_id}
              whileHover={{ y: -1 }}
              className="btn btn-sm"
              onClick={() => nav(url.workspace({ run: r.run_id }))}
              title={`${r.files.length} contract files · ${r.stages_real}/${r.stages_total} stages real`}
            >
              <FileCheck size={11} color="var(--ok)" />
              <span className="mono">{r.run_id}</span>
              <span className="muted">{r.stages_real}/{r.stages_total}</span>
            </motion.button>
          ))}
          {!replay?.runs?.length && (
            <span className="tiny muted">No replayable runs yet.</span>
          )}
        </div>
      </Card>

      <Card title="Runs" bodyStyle={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th style={{ width: 32 }}></th>
              <th>Run</th><th>Scene</th><th>Status</th><th>Provenance</th>
              <th>Engine</th><th>Duration</th><th>Started</th><th></th>
            </tr>
          </thead>
          <tbody>
            {(runs || []).map((r) => (
              <tr key={r.run_id} className="clickable"
                onClick={() => nav(url.workspace({ run: r.run_id }))}>
                <td><Dot status={r.status} pulsing={r.status === "running"} /></td>
                <td className="mono tiny">{r.run_id}</td>
                <td className="tiny muted">{r.scene_id || "—"}</td>
                <td><Badge status={r.status} /></td>
                <td><ProvenanceBar r={r} /></td>
                <td>
                  {r.detect_engine && (
                    <span className={`badge ${r.detect_engine === "ml"
                      ? "badge-ok" : "badge-warn"}`}>{r.detect_engine}</span>
                  )}
                </td>
                <td className="mono tiny">{r.seconds ? `${r.seconds.toFixed(1)}s` : "—"}</td>
                <td className="tiny muted">{fmt.ago(r.started_utc)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn-sm btn-primary" title="Incident replay"
                    style={{ marginRight: 6 }}
                    onClick={(e) => { e.stopPropagation(); nav(url.replay(r.run_id)); }}>
                    <PlayCircle size={11} /> Replay
                  </button>
                  <button className="btn btn-sm"
                    onClick={(e) => { e.stopPropagation(); nav(url.workspace({ run: r.run_id })); }}>
                    Open
                  </button>
                </td>
              </tr>
            ))}
            {/* Four states, not one: a registry that failed to answer used to
                read as a registry with nothing in it. */}
            {!runs?.length && (
              <tr><td colSpan={9} style={{ padding: 32 }}>
                {runsError ? (
                  <DataState kind="error" compact title="The run registry did not answer"
                    error={runsError} testid="registry-error">
                    <button className="btn btn-sm" onClick={reload}>Retry</button>
                  </DataState>
                ) : runsLoading ? (
                  <DataState kind="loading" compact title="Reading the run registry" />
                ) : (
                  <Empty icon={<Layers size={24} color="var(--ink-3)" />}
                    title="No runs yet"
                    hint="Create an investigation above to run the pipeline end to end." />
                )}
              </td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

/** Stacked provenance bar: real / fallback vs mock vs failed, at a glance. */
function ProvenanceBar({ r }) {
  const total = r.stages_total || 1;
  const seg = [
    { n: (r.stages_real || 0) - 0, c: "var(--ok)", label: "real" },
    { n: r.stages_mock || 0, c: "var(--mock)", label: "mock" },
    { n: r.stages_failed || 0, c: "var(--danger)", label: "failed" },
  ].filter((s) => s.n > 0);

  return (
    <span title={seg.map((s) => `${s.n} ${s.label}`).join(" · ")}
      style={{ display: "flex", alignItems: "center", gap: 7 }}>
      <span style={{ display: "flex", width: 66, height: 6, borderRadius: 3,
        overflow: "hidden", background: "var(--bg-3)" }}>
        {seg.map((s, i) => (
          <span key={i} style={{ width: `${(s.n / total) * 100}%`, background: s.c }} />
        ))}
      </span>
      <span className="tiny mono muted">{r.stages_real}/{total}</span>
    </span>
  );
}
