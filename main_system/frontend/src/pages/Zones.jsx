/* Zone Management -- geographic ownership, shown geographically.
 *
 * The Bay of Bengal is the monitoring theatre, so it is drawn as one: a globe
 * carrying the real polygons, with the administrative detail beside it. A
 * theatre reduced to a grid of small cards loses the only thing that makes a
 * zone meaningful, which is where it is and what it touches.
 *
 * Boundaries are DRAWN on the Global View; they are ADMINISTERED here --
 * status, jurisdiction, officer, incident load, and the history of who moved
 * what. Two surfaces for one dataset because the tasks differ: "divide this
 * water" is spatial, "who is answerable for Zone 03 and when did that change"
 * is not.
 *
 * Three fields carry the most meaning and are the easiest to render
 * dishonestly:
 *   Officer     "unassigned" is a real value and is shown as one. A blank cell
 *               reads as a rendering gap; an unassigned zone routes its alerts
 *               to nobody.
 *   Protected   a jurisdiction boundary, shown as a state, with the server's
 *               own reason for why the caller cannot move it.
 *   Area        geodesic, from the server. Never computed here -- a planar
 *               degree-area is wrong by a factor that varies with latitude.
 */

import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Clock, Crosshair, Globe2, Layers, Lock, Map as MapIcon,
  Pencil, Plus, RefreshCw, Scissors, Shield, Ship, Trash2, UserX, Users,
} from "lucide-react";

import Globe, { GLOBE_INITIAL_VIEW } from "../components/Globe";
import { useGlobeCamera } from "../components/globe/GlobeScene";
import { Compass, ScaleBar } from "../components/globe/GlobeChrome";
import {
  Badge, DataState, KV, Notice, PageHeader, Panel, Segmented, Spinner, Tile,
} from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { useSession } from "../lib/session";
import { useTheme } from "../lib/theme";
import "../globe.css";
import { url } from "../lib/urls";

export default function ZonesPage() {
  const { user } = useSession();
  const { theme } = useTheme();
  const [params, setParams] = useSearchParams();
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const cam = useGlobeCamera(GLOBE_INITIAL_VIEW);

  const zonesQ = useApi(() => api.listZones({ counts: true, kind, status }), [kind, status]);
  const geoQ = useApi(() => api.zonesGeojson({ status: "active" }), []);
  const summaryQ = useApi(() => api.alertsSummary(), [], { interval: 30000 });
  const trafficQ = useApi(() => api.aisZoneSummary({ max_age_minutes: 60 }), [],
                          { interval: 30000 });
  const incidentsQ = useApi(() => api.listIncidents({ limit: 200 }), []);

  const zones = zonesQ.data?.zones || [];
  const selectedId = params.get("zone");
  const selected = zones.find((z) => z.id === selectedId) || null;

  const traffic = useMemo(() => {
    const m = {};
    for (const row of trafficQ.data?.zones || []) m[row.zone_id] = row.vessels;
    return m;
  }, [trafficQ.data]);
  const alertsByZone = summaryQ.data?.by_zone || {};

  const unassigned = zones.filter(
    (z) => z.kind === "operational" && z.status === "active"
      && !(z.officers || []).some((o) => o.is_primary));

  const select = useCallback((id) => {
    const next = new URLSearchParams(params);
    if (id) next.set("zone", id); else next.delete("zone");
    setParams(next, { replace: true });
    const z = zones.find((x) => x.id === id);
    if (z?.bbox) {
      const [w, s, e, n] = z.bbox;
      const span = Math.max(e - w, n - s, 0.5);
      cam.flyTo({ longitude: (w + e) / 2, latitude: (s + n) / 2,
                  zoom: Math.max(2.5, Math.min(8, Math.log2(120 / span) + 1)) }, 1000);
    }
  }, [params, setParams, zones, cam]);

  const totalArea = zones.filter((z) => z.kind === "operational")
    .reduce((a, z) => a + (Number(z.area_km2) || 0), 0);

  if (zonesQ.loading && !zonesQ.data) {
    return <div className="page"><DataState kind="loading" title="Loading zones" /></div>;
  }
  if (zonesQ.error) {
    return (
      <div className="page">
        <PageHeader icon={<MapIcon size={17} />} kicker="Intelligence" title="Zones" />
        <Panel><DataState kind="error" title="Zones unavailable" error={zonesQ.error} /></Panel>
      </div>
    );
  }

  return (
    <div className="page" data-testid="zones-page">
      <PageHeader icon={<MapIcon size={17} />} kicker="Intelligence" title="Zone Management"
        sub="Operational divisions of the monitoring theatre, and who answers for each."
        actions={<>
          <button className="btn btn-sm" onClick={() => { zonesQ.reload(); geoQ.reload(); }}>
            <RefreshCw size={12} /> Refresh
          </button>
          <Link className="btn btn-sm" to={url.map({ new: 1 })} data-testid="zone-new"
            title="Draw a new zone on the Live Map. It is saved as a sub-zone of the one you pick there.">
            <Plus size={12} /> New zone
          </Link>
          <Link className="btn btn-primary btn-sm" to={url.map()}>
            <Pencil size={12} /> Draw on globe
          </Link>
        </>} />

      <div className="grid grid-5 mb-3">
        <Tile label="Zones" value={zones.length} tone="accent"
          sub={`${zones.filter((z) => z.kind === "operational").length} operational`} />
        <Tile label="Theatre area" value={totalArea ? `${Math.round(totalArea).toLocaleString()}` : "—"}
          sub="km² across operational zones" />
        <Tile label="Your role" value={user?.role || "—"}
          sub={zonesQ.data?.assigned_zone_ids?.length
            ? `${zonesQ.data.assigned_zone_ids.length} assigned` : "no zone assignment"} />
        <Tile label="Open alerts" value={summaryQ.data?.open ?? "—"}
          tone={summaryQ.data?.unrouted ? "warn" : undefined}
          sub={summaryQ.data?.unrouted ? `${summaryQ.data.unrouted} unrouted` : "all routed"} />
        <Tile label="Live vessels in zones"
          value={Object.values(traffic).reduce((a, b) => a + b, 0)}
          sub={trafficQ.data ? `+${trafficQ.data.outside_all_zones} outside all zones` : "—"} />
      </div>

      {/* An unassigned operational zone routes its alerts to nobody. */}
      {unassigned.length > 0 && (
        <Notice tone="warn" style={{ marginBottom: 12 }} testid="unassigned-banner"
          icon={<UserX size={13} />}>
          <strong>{unassigned.length} active zone(s) have no primary officer</strong> — detections
          there route by escalation to a parent zone&apos;s officer, or not at all:{" "}
          {unassigned.map((z) => z.name).join(", ")}.{" "}
          <Link to="/officers">Assign officers <ArrowRight size={11} /></Link>
        </Notice>
      )}

      {/* ------------------------------------------------------- the map --- */}
      <div className="zone-map mb-3">
        <Globe
          viewState={cam.viewState} onViewStateChange={cam.onViewStateChange}
          onPointerMove={cam.onPointerMove} onPointerLeave={cam.onPointerLeave}
          basemap="canvas" theme={theme}
          layersOn={{ zones: true, zoneLabels: true, incidents: true, vessels: false,
                      graticule: true }}
          zones={geoQ.data}
          incidents={(incidentsQ.data?.items || []).map((i) => {
            const c = i.geometry?.type === "Point" ? i.geometry.coordinates : null;
            return c ? { ...i, lon: c[0], lat: c[1] } : { ...i, lon: null, lat: null };
          })}
          selected={selectedId ? { kind: "zone", id: selectedId } : null}
          onSelect={(layerId, obj) => {
            if (layerId === "globe-zones") select(obj.properties?.id);
          }}
        />
        <Compass bearing={cam.viewState.bearing || 0} className="zone-compass"
          onReset={() => cam.flyTo({ bearing: 0, pitch: 0 }, 500)} />
        <ScaleBar zoom={cam.viewState.zoom} latitude={cam.viewState.latitude} className="zone-scale" />
        <div className="zone-map-legend map-panel">
          <div className="legend-row">
            <span className="legend-swatch" style={{ background: "var(--c-protected)" }} />
            Jurisdiction (protected)
          </div>
          <div className="legend-row">
            <span className="legend-swatch" style={{ background: "var(--c-zone)" }} />
            Operational division
          </div>
          <div className="legend-row">
            <span className="legend-swatch" style={{ background: "var(--c-zone-mine)" }} />
            Assigned to you
          </div>
          <div className="legend-row">
            <span className="legend-swatch" style={{ background: "var(--c-incident)" }} />
            Incident
          </div>
        </div>
      </div>

      {/* ---------------------------------------------------- the table --- */}
      <Panel title="Zones" icon={<Layers size={12} />} flush
        right={<>
          <Segmented value={kind} onChange={setKind} testidPrefix="zone-kind" items={[
            { id: "", label: "All" }, { id: "operational", label: "Operational" },
            { id: "jurisdiction", label: "Jurisdiction" },
          ]} />
          <Segmented value={status} onChange={setStatus} testidPrefix="zone-status" items={[
            { id: "", label: "Any" }, { id: "active", label: "Active" },
            { id: "inactive", label: "Inactive" },
          ]} />
        </>}>
        {zones.length === 0 ? (
          <DataState kind="empty" title="No zones match"
            hint="No zone matches these filters. Clear them, or draw a zone on the globe." />
        ) : (
          <div className="table-wrap">
            <table className="ot-table" data-testid="zone-table">
              <thead>
                <tr>
                  <th>Zone</th><th>Kind</th><th>Jurisdiction</th><th>Status</th><th>Officer</th>
                  <th className="num">Area km²</th><th className="num">Incidents</th>
                  <th className="num">Alerts</th><th className="num">Vessels</th><th className="num">Rev</th>
                </tr>
              </thead>
              <tbody>
                {zones.map((z) => {
                  const primary = (z.officers || []).find((o) => o.is_primary);
                  const on = selectedId === z.id;
                  return (
                    <>
                      <tr key={z.id} data-clickable="true" className={on ? "row-on" : ""}
                        onClick={() => select(on ? null : z.id)} data-testid={`zone-tr-${z.id}`}>
                        <td>
                          <div className="row gap-2">
                            {z.protected && <Lock size={11} className="dim" />}
                            <span>{z.name}</span>
                            {z.mine && <Badge tone="teal">YOURS</Badge>}
                          </div>
                          <div className="sub mono">{z.id}</div>
                        </td>
                        <td className="tiny">{z.kind}</td>
                        <td className="tiny mono">{z.jurisdiction || "--"}</td>
                        <td><Badge tone={z.status === "active" ? "ok" : "neutral"}>{z.status}</Badge></td>
                        <td className="tiny">
                          {primary ? (primary.display_name || primary.email)
                            /* A real state, not a blank. */
                            : <Badge tone="warn">unassigned</Badge>}
                        </td>
                        <td className="num mono">
                          {z.area_km2 == null ? "--"
                            : Number(z.area_km2).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </td>
                        <td className="num mono">
                          {z.open_incident_count ?? 0}
                          <span className="dim">/{z.incident_count ?? 0}</span>
                        </td>
                        <td className="num mono">{alertsByZone[z.id] ?? 0}</td>
                        <td className="num mono">
                          {/* "--" when the live layer has no reading for this
                              zone at all, 0 when it genuinely saw none. */}
                          {trafficQ.data ? (traffic[z.id] ?? 0) : "--"}
                        </td>
                        <td className="num mono">{z.revision}</td>
                      </tr>
                      {on && (
                        <tr key={`${z.id}-detail`} className="tr-expand">
                          <td colSpan={10}><ZoneDetail zone={z} onChanged={() => { zonesQ.reload(); geoQ.reload(); }} onDeleted={() => { select(null); zonesQ.reload(); geoQ.reload(); }} /></td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <style>{`
        .zone-map { position: relative; height: 42vh; min-height: 300px; border: 1px solid var(--line);
          border-radius: var(--r-md); overflow: hidden; background: var(--bg-0); }
        .zone-compass { position: absolute; top: 12px; right: 12px; z-index: 4; }
        .zone-manage { padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--r-sm); }
        .zone-manage-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 10px; margin: 6px 0 8px; }
        .zone-manage-grid label { display: grid; gap: 3px; font-size: 11px; color: var(--ink-2); }
        .zone-manage input, .zone-manage select { padding: 6px 8px; background: var(--bg-0); border: 1px solid var(--line-bright); border-radius: var(--r-sm); color: var(--ink-0); font: inherit; font-size: 12px; }
        .zone-manage-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 6px; }
        .zone-danger { color: var(--danger, #f87171); border-color: rgba(248,113,113,.45); }
        .zone-msg { font-size: 11.5px; }
        .zone-msg-ok { color: var(--ok, #34d399); }
        .zone-msg-danger { color: var(--danger, #f87171); }
        .zone-scale { position: absolute; bottom: 10px; left: 12px; z-index: 4; }
        .zone-map-legend { position: absolute; bottom: 10px; right: 12px; z-index: 4; padding: 8px 10px; }
      `}</style>
    </div>
  );
}

/** Everything an administrator can change about one zone, in one place:
 *  name, notes, active/inactive, who answers for it, split it, delete it.
 *  Every write is the existing zones API; a refusal (the public evaluator view
 *  may not assign or delete; a zone with incidents cannot be deleted) is shown
 *  in the server's own words. */
function ZoneManage({ zone, onChanged, onDeleted }) {
  const usersQ = useApi(() => api.listUsers({ active: true }).catch(() => null), []);
  const officers = (usersQ.data?.users || usersQ.data || []).filter?.((u) => u.is_active !== false) || [];
  const [name, setName] = useState(zone.name || "");
  const [notes, setNotes] = useState(zone.notes || "");
  const [pick, setPick] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const run = async (fn, ok, after = onChanged) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ tone: "ok", text: ok }); after?.(); }
    catch (e) { setMsg({ tone: "danger", text: e.message }); }
    finally { setBusy(false); }
  };
  const dirty = name.trim() !== (zone.name || "") || notes !== (zone.notes || "");
  const inactive = zone.status !== "active";
  return (
    <div className="zone-manage" data-testid={`zone-manage-${zone.id}`}>
      <div className="section-label"><Pencil size={11} /> Manage this zone</div>
      <div className="zone-manage-grid">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} data-testid="zone-name" /></label>
        <label>Notes<input value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="zone-notes" /></label>
      </div>
      <div className="zone-manage-row">
        <button className="btn btn-sm btn-primary" disabled={busy || !dirty || !name.trim()} data-testid="zone-save"
          onClick={() => run(() => api.updateZone(zone.id, { name: name.trim(), notes }), "Saved.")}>Save</button>
        <button className="btn btn-sm" disabled={busy} data-testid="zone-toggle"
          onClick={() => run(() => api.updateZone(zone.id, { status: inactive ? "active" : "inactive" }), inactive ? "Zone is active again." : "Zone set inactive: it keeps its history and stops receiving detections.")}>
          {inactive ? "Reactivate" : "Set inactive"}</button>
        <Link className="btn btn-sm" to={url.map({ zone: zone.id })}><Pencil size={12} /> Edit boundary</Link>
        <Link className="btn btn-sm" to={url.map({ zone: zone.id, new: 1 })} data-testid="zone-split"
          title="Draw a sub-zone inside this one on the Live Map"><Scissors size={12} /> Split: add a sub-zone</Link>
      </div>
      <div className="zone-manage-row">
        {usersQ.data == null && !usersQ.loading
          ? <span className="tiny dim">The officer list is visible to administrators only.</span>
          : <>
            <select value={pick} onChange={(e) => setPick(e.target.value)} data-testid="zone-officer-pick">
              <option value="">Assign an officer…</option>
              {officers.map((u) => <option key={u.id} value={u.id}>{u.display_name || u.email} · {u.role}</option>)}
            </select>
            <button className="btn btn-sm" disabled={busy || !pick} data-testid="zone-assign"
              onClick={() => run(() => api.assignZoneOfficer(zone.id, Number(pick), true), "Officer assigned as primary.")}>Assign as primary</button>
          </>}
        {(zone.officers || []).map((o) => (
          <button key={o.user_id} className="btn btn-sm" disabled={busy} data-testid={`zone-unassign-${o.user_id}`}
            onClick={() => run(() => api.unassignZoneOfficer(zone.id, o.user_id), "Officer removed from this zone.")}>
            <UserX size={12} /> Remove {o.display_name || o.email}</button>
        ))}
      </div>
      <div className="zone-manage-row">
        {!confirmDel
          ? <button className="btn btn-sm zone-danger" disabled={busy} onClick={() => setConfirmDel(true)} data-testid="zone-delete"><Trash2 size={12} /> Delete zone</button>
          : <>
            <span className="tiny">Delete <b>{zone.name}</b> permanently?</span>
            <button className="btn btn-sm zone-danger" disabled={busy} data-testid="zone-delete-confirm"
              onClick={() => run(() => api.deleteZone(zone.id), "Zone deleted.", onDeleted)}>Yes, delete</button>
            <button className="btn btn-sm" onClick={() => setConfirmDel(false)}>Cancel</button>
          </>}
        {msg && <span className={`zone-msg zone-msg-${msg.tone}`} data-testid="zone-msg">{msg.text}</span>}
      </div>
    </div>
  );
}

function ZoneDetail({ zone, onChanged, onDeleted }) {
  const revisionsQ = useApi(() => api.zoneRevisions(zone.id, 20), [zone.id]);
  const revisions = revisionsQ.data?.revisions || [];

  return (
    <div className="grid grid-2" style={{ gap: 18 }}>
      <div>
        <div className="section-label"><Users size={11} /> Officers</div>
        {(zone.officers || []).length === 0 ? (
          <div className="tiny">
            <Badge tone="warn">unassigned</Badge>{" "}
            Detections here route by escalation or not at all.
          </div>
        ) : zone.officers.map((o) => (
          <div key={o.user_id} className="kv">
            <span className="kv-k">
              {o.display_name || o.email}
              {o.is_primary && <Badge tone="ok" className="ml-auto">PRIMARY</Badge>}
            </span>
            <span className="kv-v tiny">{o.role}</span>
          </div>
        ))}

        <div className="kv-dense mt-3">
          <KV k="Area (geodesic)" v={zone.area_km2 == null ? "--"
            : `${Number(zone.area_km2).toLocaleString()} km²`} />
          <KV k="Parent" v={zone.parent_id || "—"} />
          <KV k="Created" v={fmt.utc(zone.created_utc)} />
          <KV k="Updated" v={fmt.ago(zone.updated_utc)} />
          <KV k="Source" v={zone.source || "—"} />
        </div>

        {zone.notes && (
          /* The seeded disclaimer. Displayed, because an operational tasking
             boundary being mistaken for a jurisdictional claim is the single
             most consequential misreading of this page. */
          <Notice style={{ marginTop: 10 }} icon={<Shield size={11} />}
            testid={`notes-${zone.id}`}>{zone.notes}</Notice>
        )}
        {!zone.can_edit && zone.cannot_edit_reason && (
          <Notice style={{ marginTop: 8 }}>{zone.cannot_edit_reason}</Notice>
        )}

        <div className="globe-actions">
          <Link className="btn btn-sm" to={url.map({ zone: zone.id })}>
            <Globe2 size={12} /> Open on globe
          </Link>
          {zone.can_edit && (
            <Link className="btn btn-sm" to={url.map({ zone: zone.id })}>
              <Pencil size={12} /> Edit boundary
            </Link>
          )}
          <Link className="btn btn-sm" to="/officers"><Users size={12} /> Assign</Link>
        </div>
      </div>

      <div>
        <ZoneManage key={zone.id} zone={zone} onChanged={() => { revisionsQ.reload?.(); onChanged?.(); }} onDeleted={onDeleted} />
        <div className="section-label" style={{ marginTop: 14 }}><Clock size={11} /> Boundary history</div>
        {revisionsQ.loading ? <Spinner />
          : revisions.length === 0 ? <div className="tiny dim">No recorded changes.</div>
            : revisions.map((r) => (
              <div key={r.id} className="kv">
                <span className="kv-k tiny">
                  <span className="mono">r{r.revision}</span> {r.change}
                  {r.area_delta_km2 != null && (
                    <span className="dim">
                      {" "}({r.area_delta_km2 > 0 ? "+" : ""}
                      {Number(r.area_delta_km2).toFixed(1)} km²)
                    </span>
                  )}
                </span>
                <span className="kv-v tiny">{r.actor || "system"}</span>
              </div>
            ))}
      </div>
    </div>
  );
}
