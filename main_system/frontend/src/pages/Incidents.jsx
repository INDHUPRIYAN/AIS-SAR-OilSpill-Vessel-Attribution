/* The incident register -- the case list, and the case file beside it.
 *
 * Named `Incidents` (plural) because `Incident.jsx` is the replay workspace
 * for a single run -- a different thing entirely. This page is the register:
 * which spills are open, who holds them, what the pipeline produced against
 * each, and what to do next.
 *
 * Deliberately NOT a CRUD table. An incident is a case, so the page is a
 * list-plus-dossier: a dense severity-ranked list on the left, and the full
 * case file on the right -- location, zone, detection provenance, spill
 * geometry, origin estimate, candidate vessels, and the lifecycle controls.
 * The primary action on a case is OPEN INVESTIGATION, so it is the primary
 * button, not a link buried in a row.
 *
 * The lifecycle control only offers the transitions the signed-in role may
 * actually make. That is presentation, not enforcement: the server rejects a
 * concluding status from an investigator regardless of what the UI shows.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Ban, Bell, CheckCircle2, ClipboardList, Clock, Crosshair,
  FileText, Film, FolderSearch, Map as MapIcon, Plus, Radar, RefreshCw, Satellite, Ship,
  UserCheck,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, Segmented, Spinner, Tile,
} from "../components/ui";
import { fmtLat, fmtLon } from "../components/Globe";
import { api, fmt, useApi } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { url } from "../lib/urls";
import { sceneFact } from "../lib/sceneName";

/* Lifecycle order, so the chips read as progress rather than as a set. */
const FLOW = ["open", "investigating", "attributed", "closed", "archived"];
/* Mirrors INCIDENT_REVIEWER_STATUSES on the server. */
const CONCLUDING = ["attributed", "closed"];

const SEVERITY_TONE = {
  critical: "danger", high: "danger", medium: "warn", low: "neutral", info: "neutral",
};
const STATUS_TONE = {
  open: "warn", investigating: "accent", attributed: "danger",
  closed: "ok", archived: "neutral",
};

function centroidOf(geometry) {
  if (!geometry) return null;
  if (geometry.type === "Point" && Array.isArray(geometry.coordinates)) {
    return { lon: geometry.coordinates[0], lat: geometry.coordinates[1] };
  }
  const ring = geometry.type === "Polygon" ? geometry.coordinates?.[0]
    : geometry.type === "MultiPolygon" ? geometry.coordinates?.[0]?.[0] : null;
  if (!ring?.length) return null;
  const lons = ring.map((c) => c[0]);
  const lats = ring.map((c) => c[1]);
  return { lon: (Math.min(...lons) + Math.max(...lons)) / 2,
           lat: (Math.min(...lats) + Math.max(...lats)) / 2 };
}

export default function Incidents() {
  const { user } = useSession();
  const [params, setParams] = useSearchParams();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState(null);

  const { data, loading, error, reload } = useApi(
    () => api.listIncidents({ status: status || undefined, q: q || undefined, limit: 200 }),
    [status, q], { interval: 45000 });

  const items = data?.items || [];
  const focusId = params.get("focus");
  const selected = items.find((i) => i.id === focusId) || items[0] || null;

  /* The full case file: the list response is a summary, the detail carries
   * the run and investigation lists. */
  const { data: detail } = useApi(
    () => (selected ? api.getIncident(selected.id) : Promise.resolve(null)), [selected?.id]);

  const canOpen = hasRole(user, "investigator", "analyst");
  const canConclude = hasRole(user, "reviewer");

  const counts = useMemo(() => {
    const out = {};
    for (const s of FLOW) out[s] = items.filter((i) => i.status === s).length;
    return out;
  }, [items]);

  const select = useCallback((id) => {
    const next = new URLSearchParams(params);
    next.set("focus", id);
    setParams(next, { replace: true });
  }, [params, setParams]);

  async function createIncident() {
    const title = window.prompt("Incident title");
    if (!title) return;
    setBusy(true); setActionError(null);
    try { await api.createIncident({ title }); await reload(); }
    catch (e) { setActionError(e.message); }
    finally { setBusy(false); }
  }

  async function setStatusOf(id, next) {
    setBusy(true); setActionError(null);
    try { await api.patchIncident(id, { status: next }); await reload(); }
    catch (e) { setActionError(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="page" data-testid="incidents-page">
      <PageHeader icon={<ClipboardList size={17} />} kicker="Operations" title="Incidents"
        sub="One spill event may span several scenes and runs. The incident is the case; runs are its evidence."
        actions={<>
          <button className="btn btn-sm" onClick={reload} disabled={loading}>
            <RefreshCw size={12} /> Refresh
          </button>
          {canOpen && (
            <button className="btn btn-primary btn-sm" onClick={createIncident} disabled={busy}
              data-testid="new-incident">
              {busy ? <Spinner /> : <Plus size={12} />} New incident
            </button>
          )}
        </>} />

      <div className="grid grid-5 mb-3">
        {FLOW.map((s) => (
          <Tile key={s} label={s} value={counts[s] ?? 0}
            tone={s === "open" ? "warn" : s === "investigating" ? "accent"
              : s === "attributed" ? "danger" : s === "closed" ? "ok" : undefined}
            onClick={() => setStatus(status === s ? "" : s)}
            testid={`count-${s}`} />
        ))}
      </div>

      {actionError && <Notice tone="danger" style={{ marginBottom: 12 }}>{actionError}</Notice>}

      <div className="split-wide">
        {/* ------------------------------------------------------- list --- */}
        <Panel title={`Register${data ? ` · ${data.total}` : ""}`} icon={<ClipboardList size={12} />}
          flush
          right={<>
            <span className="search" style={{ height: 24, minWidth: 180 }}>
              <input className="sm" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="title or id" aria-label="Search incidents"
                data-testid="incident-search" />
            </span>
            <Segmented value={status} onChange={setStatus} testidPrefix="filter" items={[
              { id: "", label: "All" },
              { id: "open", label: "Open" },
              { id: "investigating", label: "Active" },
            ]} />
          </>}>
          {loading && !data ? <DataState kind="loading" title="Loading register" />
            : error ? <DataState kind="error" error={error} />
              : items.length === 0 ? (
                <DataState kind="empty" title="No active incidents"
                  hint={q || status
                    ? "No incident matches these filters."
                    : "A run that finds a slick opens a case automatically, and an acknowledged alert can be promoted to one."} />
              ) : (
                <div className="inc-list" data-testid="incident-list">
                  {items.map((i) => {
                    const c = centroidOf(i.geometry);
                    const on = selected?.id === i.id;
                    return (
                      <button key={i.id} className={`inc-row ${on ? "on" : ""}`}
                        onClick={() => select(i.id)} data-testid={`incident-${i.id}`}>
                        <span className={`inc-rail sev-${i.severity || "none"}`} />
                        <span className="inc-main">
                          <span className="inc-top">
                            <span className="inc-id mono">{i.id}</span>
                            {i.severity && (
                              <Badge tone={SEVERITY_TONE[i.severity] || "neutral"}>{i.severity}</Badge>
                            )}
                            <Badge tone={STATUS_TONE[i.status] || "neutral"}>{i.status}</Badge>
                            {i.origin === "auto" && <Badge tone="warn">auto</Badge>}
                            <span className="inc-when mono ml-auto">{fmt.ago(i.detected_utc)}</span>
                          </span>
                          <span className="inc-title">{i.title}</span>
                          <span className="inc-meta mono">
                            <span><MapIcon size={10} /> {i.zone_id || "outside all zones"}</span>
                            {c && <span><Crosshair size={10} /> {c.lat.toFixed(2)}, {c.lon.toFixed(2)}</span>}
                            {i.area_km2 != null && <span>{Number(i.area_km2).toFixed(2)} km²</span>}
                            {i.detection_confidence != null && (
                              <span>{(i.detection_confidence * 100).toFixed(0)}% conf</span>
                            )}
                            <span><Radar size={10} /> {i.runs ?? 0} run{(i.runs ?? 0) === 1 ? "" : "s"}</span>
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
        </Panel>

        {/* ----------------------------------------------------- dossier --- */}
        {selected ? (
          <IncidentDossier incident={detail || selected} summary={selected} busy={busy}
            canConclude={canConclude} onStatus={setStatusOf} />
        ) : (
          <Panel title="Case file" icon={<FileText size={12} />}>
            <DataState kind="empty" compact title="No incident selected"
              hint="Pick a case from the register." />
          </Panel>
        )}
      </div>

      <style>{`
        .inc-list { display: flex; flex-direction: column; max-height: calc(100vh - 340px); overflow-y: auto; }
        .inc-row { display: flex; gap: 0; width: 100%; padding: 0; background: none; border: none;
          border-bottom: 1px solid var(--line); text-align: left; }
        .inc-row:hover { background: var(--accent-soft); }
        .inc-row.on { background: var(--accent-soft); }
        .inc-rail { width: 3px; flex-shrink: 0; background: var(--line-bright); }
        .inc-rail.sev-critical, .inc-rail.sev-high { background: var(--danger); }
        .inc-rail.sev-medium { background: var(--warn); }
        .inc-rail.sev-low { background: var(--ok); }
        .inc-row.on .inc-rail { background: var(--accent); }
        .inc-main { display: flex; flex-direction: column; gap: 3px; padding: 8px 12px; min-width: 0; flex: 1; }
        .inc-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
        .inc-id { font-size: var(--fs-sm); color: var(--accent); }
        .inc-when { font-size: var(--fs-xs); color: var(--ink-3); }
        .inc-title { font-size: var(--fs-md); color: var(--ink-0); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .inc-meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: var(--fs-xs); color: var(--ink-3); }
        .inc-meta span { display: inline-flex; align-items: center; gap: 4px; }
        .inc-stage { display: flex; align-items: center; gap: 6px; padding: 5px 0; font-size: var(--fs-sm); }
        .inc-stage-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--line-bright); flex-shrink: 0; }
        .inc-stage.done .inc-stage-dot { background: var(--ok); }
        .inc-stage.now .inc-stage-dot { background: var(--accent); box-shadow: 0 0 6px var(--accent); }
        .inc-stage-label { color: var(--ink-2); }
        .inc-stage.done .inc-stage-label, .inc-stage.now .inc-stage-label { color: var(--ink-0); }
      `}</style>
    </div>
  );
}

/* ------------------------------------------------------------- dossier --- */

function IncidentDossier({ incident, summary, busy, canConclude, onStatus }) {
  const i = incident || summary;
  const c = centroidOf(i.geometry);
  const runs = i.run_list || [];
  const latestRun = runs[0] || null;
  const runId = i.source_run_id || latestRun?.id || null;

  /* The lifecycle, as a path rather than a dropdown of unrelated words. */
  const stageIndex = FLOW.indexOf(i.status);

  return (
    <div className="stack" style={{ gap: 12 }}>
      <Panel title="Case file" icon={<FileText size={12} />}
        right={<Badge tone={STATUS_TONE[i.status] || "neutral"} lg>{i.status}</Badge>}>
        <div className="row gap-2 wrap mb-3">
          <span className="mono" style={{ color: "var(--accent)", fontSize: "var(--fs-lg)" }}>{i.id}</span>
          {i.severity && <Badge tone={SEVERITY_TONE[i.severity] || "neutral"}>{i.severity}</Badge>}
          {i.origin && <Badge tone={i.origin === "auto" ? "warn" : "ok"}>{i.origin}-opened</Badge>}
        </div>
        <div style={{ fontSize: "var(--fs-lg)", color: "var(--ink-0)", marginBottom: 10 }}>{i.title}</div>

        <div className="kv-dense">
          <KV k="Detected" v={fmt.utc(i.detected_utc)} />
          <KV k="Location" v={c ? `${fmtLat(c.lat)}  ${fmtLon(c.lon)}` : "no geometry recorded"} />
          <KV k="Zone" v={i.zone_id || "outside all zones"} tone={i.zone_id ? "" : "danger"} />
          {i.zone_path && <KV k="Zone path" v={i.zone_path} wrap />}
          <KV k="Region" v={i.region || "—"} />
          <KV k="Satellite source" v={i.scene_id ? `${sceneFact({ scene_id: i.scene_id }, "mission")} (SAR)` : "—"} />
          {i.scene_id && <KV k="Scene" v={i.scene_id} wrap />}
          <KV k="Spill area" v={i.area_km2 != null ? `${Number(i.area_km2).toFixed(2)} km²` : "not characterised"} />
          <KV k="Detection confidence"
            v={i.detection_confidence != null ? `${(i.detection_confidence * 100).toFixed(1)}%` : "—"} />
          <KV k="Assigned officer" v={i.assignee_id ? `user ${i.assignee_id}` : "unassigned"}
            tone={i.assignee_id ? "" : "danger"} />
          <KV k="Runs / investigations" v={`${i.runs ?? runs.length} / ${i.investigations ?? 0}`} />
          <KV k="Opened" v={fmt.utc(i.created_utc)} />
          <KV k="Updated" v={fmt.ago(i.updated_utc)} />
        </div>

        {i.notes && <Notice style={{ marginTop: 10 }}>{i.notes}</Notice>}

        <div className="globe-actions">
          {runId ? (
            <Link className="btn btn-primary btn-sm" to={url.workspace({ run: runId })}
              data-testid="open-investigation">
              <FolderSearch size={12} /> Open investigation
            </Link>
          ) : (
            <Link className="btn btn-primary btn-sm" to="/investigation" data-testid="open-investigation">
              <FolderSearch size={12} /> Open investigation
            </Link>
          )}
          {runId && (
            <Link className="btn btn-sm" to={url.replay(runId)}><Film size={12} /> Replay</Link>
          )}
          {c && (
            <Link className="btn btn-sm" to={url.map({ incident: i.id })}><MapIcon size={12} /> On globe</Link>
          )}
          {runId && (
            <Link className="btn btn-sm" to={url.reportPrint(runId)}><FileText size={12} /> Report</Link>
          )}
        </div>
      </Panel>

      <Panel title="Case lifecycle" icon={<CheckCircle2 size={12} />}>
        {FLOW.map((s, idx) => (
          <div key={s} className={`inc-stage ${idx < stageIndex ? "done" : ""} ${idx === stageIndex ? "now" : ""}`}>
            <span className="inc-stage-dot" />
            <span className="inc-stage-label">{s}</span>
            {idx === stageIndex && <span className="tiny mono dim ml-auto">current</span>}
          </div>
        ))}
        <div className="hr" />
        <div className="field">
          <span>Change status</span>
          <select value="" disabled={busy} data-testid="status-select"
            onChange={(e) => e.target.value && onStatus(i.id, e.target.value)}>
            <option value="">choose a transition…</option>
            {FLOW.filter((s) => s !== i.status)
              /* A concluding status is a reviewer decision; the server
               * enforces this, the menu just does not offer an action that
               * would be refused. */
              .filter((s) => canConclude || !CONCLUDING.includes(s))
              .map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        {!canConclude && (
          <div className="tiny dim mt-2" style={{ lineHeight: 1.5 }}>
            Concluding a case (<span className="mono">attributed</span>,{" "}
            <span className="mono">closed</span>) is a reviewer decision and is refused by the
            server for your role.
          </div>
        )}
      </Panel>

      <Panel title="Evidence" icon={<Radar size={12} />} flush>
        {runs.length === 0 ? (
          <DataState kind="empty" compact title="No runs against this case"
            hint="Open an investigation to produce detection, drift and attribution evidence." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Run</th><th>Scene</th><th>Status</th><th className="num">Real</th><th /></tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id}>
                    <td className="mono tiny">{r.id}</td>
                    <td className="tiny dim ellipsis" style={{ maxWidth: 120 }}>{r.scene_id || "—"}</td>
                    <td><Badge status={r.status}>{r.status}</Badge></td>
                    <td className="num mono tiny">
                      {r.stages_real ?? "?"}/{r.stages_total ?? "?"}
                      {r.stages_mock ? <Badge tone="mock">mock</Badge> : null}
                    </td>
                    <td>
                      <Link className="btn btn-xs" to={url.workspace({ run: r.id })}>Open</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
