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

import { Badge, Card, Dot, Spinner, Stat, Empty } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

export default function Dashboard() {
  const nav = useNavigate();
  const { data: runs, reload } = useApi(() => api.listRuns(), [], { interval: 6000 });
  const { data: replay } = useApi(() => api.replayRuns(), []);
  const { data: invs, reload: reloadInvs } = useApi(() => api.listInvestigations(), []);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("Chennai / Ennore investigation");

  const complete = (runs || []).filter((r) => r.status === "complete");
  const fullyReal = complete.filter((r) => r.stages_real === r.stages_total && !r.stages_mock);

  async function createAndRun() {
    setCreating(true);
    try {
      const inv = await api.createInvestigation({
        name, scene_meta_path: "contracts/mocks/scene_meta.json",
      });
      const started = await api.startRun(inv.id, { engine: "auto" });
      await reloadInvs();
      nav(`/investigation?run=${started.run_id}`);
    } finally { setCreating(false); }
  }

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <FolderOpen size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>Investigations</div>
          <div className="tiny muted">Every pipeline run, with its provenance</div>
        </div>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Card><Stat label="Investigations" value={invs?.length ?? "—"} /></Card>
        <Card><Stat label="Completed runs" value={complete.length} /></Card>
        <Card><Stat label="Fully real runs" value={fullyReal.length} tone="ok"
          sub="no mocked stages" /></Card>
        <Card><Stat label="Replayable offline" value={replay?.count ?? "—"} tone="ok"
          sub="no network required" /></Card>
      </div>

      <Card title="New investigation" style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
          <div style={{ flex: 1 }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Name</div>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button className="btn btn-primary" onClick={createAndRun} disabled={creating || !name}>
            {creating ? <Spinner /> : <Plus size={13} />} Create &amp; run
          </button>
        </div>
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
              onClick={() => nav(`/investigation?run=${r.run_id}`)}
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
                onClick={() => nav(`/investigation?run=${r.run_id}`)}>
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
                    onClick={(e) => { e.stopPropagation(); nav(`/incident?run=${r.run_id}`); }}>
                    <PlayCircle size={11} /> Replay
                  </button>
                  <button className="btn btn-sm"
                    onClick={(e) => { e.stopPropagation(); nav(`/investigation?run=${r.run_id}`); }}>
                    Open
                  </button>
                </td>
              </tr>
            ))}
            {!runs?.length && (
              <tr><td colSpan={9} style={{ padding: 32 }}>
                <Empty icon={<Layers size={24} color="var(--ink-3)" />}
                  title="No runs yet"
                  hint="Create an investigation above to run the pipeline end to end." />
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
