/* Investigation Records -- the register of every run as a reproducible record.
 *
 * A record is a RUN: one scene through the pipeline, sealed under a manifest.
 * The register joins each run to the investigation that filed it (when one
 * did -- a CLI run reconciled from its manifest is UNFILED), to the incident
 * it produced or was promoted into, and to the zone that incident sits in.
 *
 * Every cell is read from the API; where the API does not carry a value the
 * cell says so ("—", "no candidate", "unzoned") instead of inferring one.
 * The run row does not carry the acquisition time -- that lives in the run's
 * scene_meta -- so DETECTION TIME is the incident's detected_utc or nothing.
 *
 * Role gating on NEW INVESTIGATION is presentational only; the server refuses
 * the POST for a role that may not open one.
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, ArrowUpDown, FolderSearch, Plus, RefreshCw, X } from "lucide-react";

import { Badge, DataState, PageHeader, Panel, Tile } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { guessPlace } from "../lib/replay";
import { hasRole, useSession } from "../lib/session";
import "../investigations.css";

const STATUSES = ["complete", "running", "failed", "cancelled", "pending"];
const SEVERITIES = ["critical", "high", "medium", "low"];
const OPEN_STATUSES = new Set(["open", "investigating"]);

const EMPTY_FILTERS = {
  status: "", zone: "", from: "", to: "", satellite: "", severity: "", assignee: "",
};

/* Sort keys the header exposes. Missing values sort last in either order. */
const SORTERS = {
  created: (r) => (r.started_utc ? Date.parse(r.started_utc) : null),
  area: (r) => (r.slick_area_km2 == null ? null : Number(r.slick_area_km2)),
  status: (r) => (r.status ? String(r.status) : null),
};

function compare(a, b, dir) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const c = typeof a === "string" ? a.localeCompare(b) : a - b;
  return dir === "asc" ? c : -c;
}

/* `/api/reports` has returned both a bare array and {items}; accept either. */
function itemsOf(data) {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  return data.items || [];
}

/** LOCATION: the run's own region, else the catalogue label for its scene,
 *  else a place name for the incident's point. Nothing else is invented. */
function locationOf(run, scene, incident) {
  if (run.region) return run.region;
  if (scene?.geo_basis === "synthetic") return "No place (synthetic scene)";
  if (scene?.label && !["REAL", "REFERENCE", "SYNTHETIC", "UPLOADED", "UNVERIFIED"].includes(scene.label)) return scene.label;
  if (Array.isArray(scene?.center)) return guessPlace(scene.center);
  const g = incident?.geometry;
  if (g?.type === "Point" && Array.isArray(g.coordinates) && g.coordinates.length >= 2) {
    const [lon, lat] = g.coordinates;
    if (Number.isFinite(lon) && Number.isFinite(lat)) return guessPlace([lon, lat]);
  }
  return null;
}

export default function Investigations() {
  const navigate = useNavigate();
  const { user } = useSession();
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [sort, setSort] = useState({ key: "created", dir: "desc" });

  const runsQ = useApi(
    () => api.listRunsPaged({ limit: 200, sort: "started_utc", order: "desc" }), []);
  const invQ = useApi(() => api.listInvestigations(), []);
  const incQ = useApi(() => api.listIncidents({ limit: 200 }), []);
  const zonesQ = useApi(() => api.listZones(), []);
  const scenesQ = useApi(() => api.localScenes(), []);
  /* Every scene this host holds, with its centre and the BASIS of its time and
   * place (measured / assigned / user-supplied / synthetic). A record is only
   * as real as the scene under it, so the register says which it is. */
  const sarQ = useApi(() => api.sarScenes().catch(() => null), []);
  const reportsQ = useApi(() => api.listReports(), []);
  /* `/api/users` is admin-only. A 403 here is the expected answer for most
   * roles, and the register does not need it -- assignee ids are labelled
   * "officer #id" instead. Never let it fail the page. */
  const usersQ = useApi(() => api.listUsers().catch(() => null), []);

  const canOpen = hasRole(user, "investigator", "analyst");

  const runs = runsQ.data?.items || [];
  const total = runsQ.data?.total ?? runs.length;

  const investigationsById = useMemo(() => {
    const m = new Map();
    for (const inv of itemsOf(invQ.data)) m.set(inv.id, inv);
    return m;
  }, [invQ.data]);

  const incidents = useMemo(() => itemsOf(incQ.data), [incQ.data]);
  const incidentByRun = useMemo(() => {
    const bySource = new Map();
    const byId = new Map();
    for (const inc of incidents) {
      if (inc.source_run_id && !bySource.has(inc.source_run_id)) bySource.set(inc.source_run_id, inc);
      byId.set(inc.id, inc);
    }
    return (run) => bySource.get(run.run_id) || (run.incident_id ? byId.get(run.incident_id) : null) || null;
  }, [incidents]);

  const zones = useMemo(() => zonesQ.data?.zones || [], [zonesQ.data]);
  const zoneName = useMemo(() => {
    const m = new Map();
    for (const z of zones) m.set(z.id, z.name || z.id);
    return (id) => (id == null ? null : m.get(id) || null);
  }, [zones]);

  const sceneByScene = useMemo(() => {
    const m = new Map();
    for (const s of sarQ.data?.scenes || []) if (s.scene_id) m.set(s.scene_id, s);
    for (const s of scenesQ.data?.scenes || []) if (s.scene_id) m.set(s.scene_id, { ...m.get(s.scene_id), ...s, label: s.label || m.get(s.scene_id)?.label });
    return m;
  }, [scenesQ.data, sarQ.data]);

  const userLabel = useMemo(() => {
    const m = new Map();
    for (const u of usersQ.data?.users || []) m.set(u.id, u.display_name || u.email || null);
    return (id) => m.get(id) || `officer #${id}`;
  }, [usersQ.data]);

  /* One joined record per run; the table, the filters and the tiles all
   * read this rather than re-joining. */
  const records = useMemo(() => runs.map((run) => {
    const incident = incidentByRun(run);
    const investigation = run.investigation_id ? investigationsById.get(run.investigation_id) : null;
    const scene = run.scene_id ? sceneByScene.get(run.scene_id) : null;
    return {
      run, incident, investigation,
      location: locationOf(run, scene, incident), scene,
      zone: incident?.zone_id ? zoneName(incident.zone_id) : null,
    };
  }), [runs, incidentByRun, investigationsById, sceneByScene, zoneName]);

  const assignees = useMemo(() => {
    const ids = new Set();
    for (const inc of incidents) if (inc.assignee_id != null) ids.add(inc.assignee_id);
    return [...ids].sort((a, b) => String(a).localeCompare(String(b)));
  }, [incidents]);

  const visible = useMemo(() => {
    const f = filters;
    const fromMs = f.from ? Date.parse(`${f.from}T00:00:00Z`) : null;
    const toMs = f.to ? Date.parse(`${f.to}T23:59:59.999Z`) : null;
    const sat = f.satellite.trim().toLowerCase();
    const out = records.filter(({ run, incident }) => {
      if (f.status && run.status !== f.status) return false;
      if (f.zone && String(incident?.zone_id ?? "") !== f.zone) return false;
      if (f.severity && incident?.severity !== f.severity) return false;
      if (f.assignee && String(incident?.assignee_id ?? "") !== f.assignee) return false;
      if (sat && !String(run.scene_id || "").toLowerCase().includes(sat)) return false;
      if (fromMs != null || toMs != null) {
        const t = run.started_utc ? Date.parse(run.started_utc) : NaN;
        if (Number.isNaN(t)) return false;
        if (fromMs != null && t < fromMs) return false;
        if (toMs != null && t > toMs) return false;
      }
      return true;
    });
    const key = SORTERS[sort.key];
    return out.sort((a, b) => compare(key(a.run), key(b.run), sort.dir));
  }, [records, filters, sort]);

  const openIncidents = incidents.filter((i) => OPEN_STATUSES.has(i.status)).length;
  const ranked = runs.filter((r) => r.top_suspect_mmsi != null).length;
  const reportCount = itemsOf(reportsQ.data).length;

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const filtered = Object.values(filters).some(Boolean);

  function toggleSort(key) {
    setSort((s) => (s.key === key
      ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
      : { key, dir: key === "status" ? "asc" : "desc" }));
  }

  function openRecord(runId) {
    navigate(`/investigation?run=${encodeURIComponent(runId)}`);
  }

  return (
    <div className="page inv-page" data-testid="investigations-page">
      <PageHeader icon={<FolderSearch size={17} />} kicker="Analysis" title="Investigation Records"
        sub="Every investigation as a reproducible record: scene, detection, validation, origin, correlation, ranking, evidence, reports."
        actions={<>
          <button className="btn btn-sm" onClick={runsQ.reload} disabled={runsQ.loading}>
            <RefreshCw size={12} /> Refresh
          </button>
          <button className="btn btn-primary btn-sm" data-testid="new-investigation"
            disabled={!canOpen}
            title={canOpen ? "Open a new investigation" : "Your role cannot open investigations"}
            onClick={() => navigate("/investigation?new=1")}>
            <Plus size={12} /> NEW INVESTIGATION
          </button>
        </>} />

      <div className="grid grid-4 mb-3">
        <Tile label="Records" value={runsQ.data ? total : "—"} tone="accent" testid="tile-records" />
        <Tile label="Open incidents" value={incQ.data ? openIncidents : "—"} tone="warn"
          testid="tile-open" />
        <Tile label="Ranked candidates" value={runsQ.data ? ranked : "—"} tone="danger"
          testid="tile-ranked" />
        <Tile label="Reports" value={reportsQ.data ? reportCount : "—"} tone="ok" testid="tile-reports" />
      </div>

      <div className="filters inv-filters mb-3" data-testid="inv-filters">
        <label className="field inv-f-status">
          <span>Status</span>
          <select className="sm" value={filters.status} onChange={set("status")} data-testid="f-status">
            <option value="">all</option>
            {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="field inv-f-zone">
          <span>Zone</span>
          <select className="sm" value={filters.zone} onChange={set("zone")} data-testid="f-zone">
            <option value="">all</option>
            {zones.map((z) => <option key={z.id} value={String(z.id)}>{z.name || z.id}</option>)}
          </select>
        </label>
        <label className="field inv-f-date">
          <span>Date from</span>
          <input className="sm" type="date" value={filters.from} onChange={set("from")} data-testid="f-from" />
        </label>
        <label className="field inv-f-date">
          <span>Date to</span>
          <input className="sm" type="date" value={filters.to} onChange={set("to")} data-testid="f-to" />
        </label>
        <label className="field grow inv-f-sat">
          <span>Satellite</span>
          <input className="sm" type="text" value={filters.satellite} onChange={set("satellite")}
            placeholder="Sentinel-1 scene id…" data-testid="f-satellite" />
        </label>
        <label className="field inv-f-sev">
          <span>Severity</span>
          <select className="sm" value={filters.severity} onChange={set("severity")} data-testid="f-severity">
            <option value="">all</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="field inv-f-officer">
          <span>Assigned officer</span>
          <select className="sm" value={filters.assignee} onChange={set("assignee")} data-testid="f-assignee">
            <option value="">all</option>
            {assignees.map((id) => <option key={id} value={String(id)}>{userLabel(id)}</option>)}
          </select>
        </label>
        <button className="btn btn-sm inv-clear" onClick={() => setFilters(EMPTY_FILTERS)}
          disabled={!filtered} data-testid="f-clear">
          <X size={12} /> Clear
        </button>
      </div>

      <Panel title="Records" icon={<FolderSearch size={12} />} flush className="inv-records-panel"
        testid="inv-records">
        {runsQ.loading && !runsQ.data ? (
          <DataState kind="loading" title="Reading records" />
        ) : runsQ.error ? (
          <DataState kind="error" error={runsQ.error} />
        ) : runs.length === 0 ? (
          <DataState kind="empty" title="No investigation records"
            hint="Start a new investigation to create the first record." />
        ) : (
          <div className="table-wrap inv-records">
            <table>
              <thead>
                <tr>
                  <th>Investigation ID</th>
                  <SortTh label="Status" k="status" sort={sort} onSort={toggleSort} />
                  <th>Location</th>
                  <th>Detection time</th>
                  <SortTh label="Area" k="area" sort={sort} onSort={toggleSort} className="num" />
                  <th>Zone</th>
                  <th>Top candidate</th>
                  <SortTh label="Created" k="created" sort={sort} onSort={toggleSort} />
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {visible.length === 0 ? (
                  <tr className="inv-no-match">
                    <td colSpan={9}>
                      <DataState kind="empty" compact title="No records match these filters"
                        hint="Clear a filter to widen the register." />
                    </td>
                  </tr>
                ) : visible.map(({ run, incident, investigation, location, zone, scene }) => (
                  <RecordRow key={run.run_id} run={run} incident={incident}
                    investigation={investigation} location={location} zone={zone} scene={scene}
                    onOpen={() => openRecord(run.run_id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {runsQ.data && runs.length > 0 && (
          <div className="table-foot inv-foot" data-testid="inv-foot">
            <span>Showing {visible.length} of {total} records</span>
            <span className="inv-foot-key">
              registry source: <span className="mono">api</span> = observed by this server,{" "}
              <span className="mono">reconciled</span> = rebuilt from a sealed manifest
            </span>
          </div>
        )}
      </Panel>
    </div>
  );
}

function SortTh({ label, k, sort, onSort, className = "" }) {
  const on = sort.key === k;
  const Icon = !on ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th className={`inv-sortable ${on ? "on" : ""} ${className}`} onClick={() => onSort(k)}
      aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      data-testid={`sort-${k}`}>
      <span className="inv-th">{label}<Icon size={10} /></span>
    </th>
  );
}

function RecordRow({ run, incident, investigation, location, zone, scene, onOpen }) {
  const real = run.stages_real ?? 0;
  const totalStages = run.stages_total ?? 0;
  const mock = run.stages_mock ?? 0;
  const area = run.slick_area_km2 ?? incident?.area_km2 ?? null;
  const areaFrom = run.slick_area_km2 == null && incident?.area_km2 != null ? "incident" : "run";
  const hasCandidate = run.top_suspect_mmsi != null;
  const updated = run.finished_utc || run.started_utc;
  const partial = mock > 0 || (totalStages > 0 && real < totalStages);

  return (
    <tr data-testid="record-row" data-clickable="true" data-run-id={run.run_id}
      className={run.archived ? "row-dim" : ""} tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}>
      <td className="inv-id">
        <div className="inv-id-row">
          <span className="mono inv-run-id">{run.run_id}</span>
          {run.investigation_id == null && (
            <Badge tone="neutral" title="A CLI run reconciled from its manifest; no investigation filed it">
              UNFILED
            </Badge>
          )}
          {run.registry_source === "reconciled" && (
            <span className="inv-src" title="Rebuilt from a sealed manifest, not observed by this server">
              reconciled
            </span>
          )}
        </div>
        {investigation?.name && <span className="sub inv-inv-name">{investigation.name}</span>}
      </td>
      <td className="inv-status">
        <div className="inv-status-row">
          <Badge status={run.status} title={run.error || undefined} />
          <span className={`chip inv-prov ${partial ? "inv-prov-partial" : ""}`}
            title="Stages produced by the real component, contract-validated, out of the stages run">
            {real}/{totalStages} REAL
          </span>
          {mock > 0 && (
            <Badge tone="mock" title={`${mock} stage(s) served from a mock file`}>MOCK</Badge>
          )}
        </div>
      </td>
      <td className="inv-loc">
        {location ? location : <span className="dim">—</span>}
        {scene?.geo_basis && (
          <span className={`inv-basis inv-basis-${scene.geo_basis}`} title={scene.geo_basis_note} data-testid="record-basis">
            {scene.geo_basis === "measured" ? "REAL · measured" : scene.geo_basis === "assigned" ? "REFERENCE · position assigned"
              : scene.geo_basis === "synthetic" ? "SYNTHETIC" : scene.geo_basis === "raster" ? "UPLOADED · from raster" : "UPLOADED · user-supplied"}
          </span>
        )}
        {Array.isArray(scene?.center) && scene.geo_basis !== "synthetic" && (
          <span className="sub mono" data-testid="record-latlon">{Math.abs(scene.center[1]).toFixed(3)}° {scene.center[1] >= 0 ? "N" : "S"} · {Math.abs(scene.center[0]).toFixed(3)}° {scene.center[0] >= 0 ? "E" : "W"}</span>
        )}
        {run.scene_id && <span className="sub mono ellipsis inv-scene" title={run.scene_id}>{run.scene_id}</span>}
      </td>
      <td className="inv-time mono">
        {incident?.detected_utc
          ? <span title={fmt.utc(incident.detected_utc)}>{fmt.utc(incident.detected_utc)}</span>
          : <span className="dim" title="No incident is joined to this run; acquisition time is recorded in the run's scene_meta">—</span>}
      </td>
      <td className="num mono inv-area">
        {area != null
          ? <span title={areaFrom === "incident" ? "Area from the joined incident; the run row carries none" : "Slick area from the run"}>
              {Number(area).toFixed(2)} km²
            </span>
          : <span className="dim">—</span>}
      </td>
      <td className="inv-zone">
        {zone ? zone
          : incident ? <span className="dim" title="The joined incident has no zone">unzoned</span>
            : <span className="dim">—</span>}
      </td>
      <td className="inv-cand">
        {hasCandidate ? (
          <span className="mono" title="Highest-ranked AIS track by the correlation score; a ranking, not a finding">
            MMSI {run.top_suspect_mmsi}
            {run.top_score != null && <> · {Number(run.top_score).toFixed(2)}</>}
          </span>
        ) : <span className="dim">no candidate</span>}
      </td>
      <td className="inv-when" title={fmt.utc(run.started_utc)}>
        {run.started_utc ? fmt.ago(run.started_utc) : <span className="dim">—</span>}
      </td>
      <td className="inv-when" title={fmt.utc(updated)}>
        {updated ? fmt.ago(updated) : <span className="dim">—</span>}
      </td>
    </tr>
  );
}
