/* Zone Management: the operational map as a table.
 *
 * The globe is where boundaries are DRAWN; this is where they are
 * administered — status, jurisdiction, officer, incident load, and the history
 * of who moved what. Two surfaces for the same data because the tasks are
 * different: "divide this water" is spatial, "who is answerable for Zone 03
 * and when did that change" is not.
 *
 * Every column is a field from /api/zones. The three that carry the most
 * meaning and are easiest to render dishonestly:
 *
 *   Officer         "unassigned" is a real value and is shown as one. A blank
 *                   cell would read as a rendering gap; an unassigned zone
 *                   routes its alerts to nobody.
 *   Protected       a jurisdiction boundary. Shown as a state, with the
 *                   server's own reason for why the caller cannot move it.
 *   Area            geodesic, from the server. Never computed here — a planar
 *                   degree-area would be wrong by a factor that varies with
 *                   latitude.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Clock, Globe2, Lock, MapPin, Shield, UserX,
} from "lucide-react";

import { Card, Empty, Spinner } from "../components/ui";
import { api, useApi } from "../lib/api";
import { useSession } from "../lib/session";
import "../globe.css";

export default function ZonesPage() {
  const { user } = useSession();
  const [expanded, setExpanded] = useState(null);
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");

  const zonesQ = useApi(() => api.listZones({ counts: true, kind, status }),
                        [kind, status]);
  const summaryQ = useApi(() => api.alertsSummary(), [], { interval: 30000 });
  const trafficQ = useApi(() => api.aisZoneSummary({ max_age_minutes: 60 }),
                          [], { interval: 30000 });

  const zones = zonesQ.data?.zones || [];
  const traffic = useMemo(() => {
    const map = {};
    for (const row of trafficQ.data?.zones || []) map[row.zone_id] = row.vessels;
    return map;
  }, [trafficQ.data]);
  const alertsByZone = summaryQ.data?.by_zone || {};

  const unassigned = zones.filter(
    (z) => z.kind === "operational" && z.status === "active"
      && !(z.officers || []).some((o) => o.is_primary));

  if (zonesQ.loading) return <div className="page"><Spinner label="loading zones" /></div>;
  if (zonesQ.error) {
    return (
      <div className="page">
        <Empty icon={<AlertTriangle size={22} />} title="Zones unavailable"
          hint={zonesQ.error.message} />
      </div>
    );
  }

  return (
    <div className="page" data-testid="zones-page">
      <div className="grid grid-4" style={{ marginBottom: 15 }}>
        <Stat label="Zones" value={zones.length}
          sub={`${zones.filter((z) => z.kind === "operational").length} operational`} />
        <Stat label="Your role" value={user?.role || "--"}
          sub={zonesQ.data?.assigned_zone_ids?.length
            ? `${zonesQ.data.assigned_zone_ids.length} assigned`
            : "no zone assignment"} />
        <Stat label="Open alerts" value={summaryQ.data?.open ?? "--"}
          sub={summaryQ.data?.unrouted
            ? `${summaryQ.data.unrouted} unrouted`
            : "all routed"}
          tone={summaryQ.data?.unrouted ? "warn" : undefined} />
        <Stat label="Live vessels"
          value={Object.values(traffic).reduce((a, b) => a + b, 0)}
          sub={trafficQ.data
            ? `+${trafficQ.data.outside_all_zones} outside all zones`
            : "--"} />
      </div>

      {/* An unassigned operational zone routes its alerts to nobody. Surfaced
          at the top rather than left to be noticed in a column. */}
      {unassigned.length > 0 && (
        <div className="globe-warn" style={{ marginBottom: 15 }}
          data-testid="unassigned-banner">
          <UserX size={13} />
          <span>
            <strong>{unassigned.length} active zone(s) have no primary
            officer</strong> — detections there route by escalation to a parent
            zone's officer, or not at all:{" "}
            {unassigned.map((z) => z.name).join(", ")}.{" "}
            <Link to="/officers">Assign officers <ArrowRight size={11} /></Link>
          </span>
        </div>
      )}

      <Card title="Operational zones" right={
        <div style={{ display: "flex", gap: 6 }}>
          <select className="btn btn-sm" value={kind}
            onChange={(e) => setKind(e.target.value)}
            aria-label="Filter by kind">
            <option value="">all kinds</option>
            <option value="jurisdiction">jurisdiction</option>
            <option value="operational">operational</option>
          </select>
          <select className="btn btn-sm" value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status">
            <option value="">all statuses</option>
            <option value="active">active</option>
            <option value="inactive">inactive</option>
          </select>
          <Link className="btn btn-sm" to="/globe">
            <Globe2 size={12} /> Draw on globe
          </Link>
        </div>
      } bodyStyle={{ padding: 0 }}>
        {zones.length === 0 ? (
          <Empty icon={<MapPin size={22} />} title="No zones match"
            hint="No zone matches these filters. Clear them, or draw a zone on the globe." />
        ) : (
          <table className="ot-table" data-testid="zone-table">
            <thead>
              <tr>
                <th>Zone</th>
                <th>Kind</th>
                <th>Jurisdiction</th>
                <th>Status</th>
                <th>Officer</th>
                <th className="num">Area km²</th>
                <th className="num">Incidents</th>
                <th className="num">Alerts</th>
                <th className="num">Vessels</th>
                <th className="num">Rev</th>
              </tr>
            </thead>
            <tbody>
              {zones.map((z) => {
                const primary = (z.officers || []).find((o) => o.is_primary);
                return (
                  <>
                    <tr key={z.id}
                      onClick={() => setExpanded(expanded === z.id ? null : z.id)}
                      style={{ cursor: "pointer" }}
                      data-testid={`zone-tr-${z.id}`}>
                      <td>
                        <div style={{ display: "flex", alignItems: "center",
                                      gap: 6 }}>
                          {z.protected && <Lock size={11} className="muted" />}
                          <span>{z.name}</span>
                          {z.mine && <span className="badge badge-ok">YOURS</span>}
                        </div>
                        <div className="tiny mono muted">{z.id}</div>
                      </td>
                      <td className="tiny">{z.kind}</td>
                      <td className="tiny mono">{z.jurisdiction || "--"}</td>
                      <td>
                        <span className={`badge badge-${z.status === "active"
                          ? "ok" : "neutral"}`}>{z.status}</span>
                      </td>
                      <td className="tiny">
                        {primary
                          ? (primary.display_name || primary.email)
                          /* A real state, not a blank. */
                          : <span className="badge badge-warn">unassigned</span>}
                      </td>
                      <td className="num mono">
                        {z.area_km2 == null ? "--"
                          : Number(z.area_km2).toLocaleString(undefined,
                                                              { maximumFractionDigits: 0 })}
                      </td>
                      <td className="num mono">
                        {z.open_incident_count ?? 0}
                        <span className="muted">/{z.incident_count ?? 0}</span>
                      </td>
                      <td className="num mono">{alertsByZone[z.id] ?? 0}</td>
                      <td className="num mono">
                        {/* "--" when the live layer has no reading for this
                            zone at all, 0 when it genuinely saw none. */}
                        {trafficQ.data ? (traffic[z.id] ?? 0) : "--"}
                      </td>
                      <td className="num mono">{z.revision}</td>
                    </tr>
                    {expanded === z.id && (
                      <tr key={`${z.id}-detail`}>
                        <td colSpan={10} style={{ background: "var(--bg-1)" }}>
                          <ZoneDetail zone={z} />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value, sub, tone }) {
  return (
    <div className="card" style={{ padding: 13 }}>
      <div className="tiny muted" style={{ letterSpacing: 0.6 }}>
        {label.toUpperCase()}
      </div>
      <div className="mono" style={{ fontSize: 22, marginTop: 4,
                                     color: tone === "warn" ? "var(--warn)"
                                       : "var(--ink-0)" }}>
        {value}
      </div>
      {sub && <div className="tiny muted">{sub}</div>}
    </div>
  );
}

function ZoneDetail({ zone }) {
  const revisionsQ = useApi(() => api.zoneRevisions(zone.id, 20), [zone.id]);
  const revisions = revisionsQ.data?.revisions || [];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18,
                  padding: "12px 4px" }}>
      <div>
        <div className="tiny muted" style={{ letterSpacing: 0.6,
                                             marginBottom: 6 }}>
          OFFICERS
        </div>
        {(zone.officers || []).length === 0 ? (
          <div className="tiny">
            <span className="badge badge-warn">unassigned</span>{" "}
            Detections here route by escalation or not at all.
          </div>
        ) : zone.officers.map((o) => (
          <div key={o.user_id} className="globe-row">
            <span className="globe-row-k">
              {o.display_name || o.email}
              {o.is_primary && (
                <span className="badge badge-ok"
                  style={{ marginLeft: 6 }}>PRIMARY</span>
              )}
            </span>
            <span className="globe-row-v tiny mono">{o.role}</span>
          </div>
        ))}

        {zone.notes && (
          /* The seeded disclaimer. Displayed, because an operational tasking
             boundary being mistaken for a jurisdictional claim is the single
             most consequential misreading of this page. */
          <div className="globe-note tiny" data-testid={`notes-${zone.id}`}>
            <Shield size={11} /> {zone.notes}
          </div>
        )}
        {!zone.can_edit && zone.cannot_edit_reason && (
          <div className="globe-note tiny">{zone.cannot_edit_reason}</div>
        )}
      </div>

      <div>
        <div className="tiny muted" style={{ letterSpacing: 0.6,
                                             marginBottom: 6 }}>
          <Clock size={10} /> BOUNDARY HISTORY
        </div>
        {revisionsQ.loading ? <Spinner />
          : revisions.length === 0 ? (
            <div className="tiny muted">No recorded changes.</div>
          ) : revisions.map((r) => (
            <div key={r.id} className="globe-row">
              <span className="globe-row-k tiny">
                <span className="mono">r{r.revision}</span> {r.change}
                {r.area_delta_km2 != null && (
                  <span className="muted">
                    {" "}({r.area_delta_km2 > 0 ? "+" : ""}
                    {Number(r.area_delta_km2).toFixed(1)} km²)
                  </span>
                )}
              </span>
              <span className="globe-row-v tiny mono">
                {r.actor || "system"}
              </span>
            </div>
          ))}
      </div>
    </div>
  );
}
