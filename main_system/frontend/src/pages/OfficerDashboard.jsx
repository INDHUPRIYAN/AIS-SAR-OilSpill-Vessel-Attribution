/* Officer Dashboard: what is mine, right now.
 *
 * Every other screen answers a question about the system. This one answers a
 * question about the person looking at it, and that shapes two decisions.
 *
 * FIRST: an officer with no zone gets told so, prominently, instead of an
 * empty dashboard. An empty queue and no authority look identical on screen
 * and mean opposite things — one is a quiet shift, the other is an account
 * that can never receive anything.
 *
 * SECOND: "my zone" narrows the QUEUE, never the PICTURE. The situational
 * panels (traffic, other zones' incidents) stay global, because an officer who
 * cannot see a spill drifting toward their boundary from the next zone is
 * worse at the job, not more secure. Spec §20 is explicit: global visibility,
 * jurisdiction-scoped control. The one thing genuinely scoped is report
 * access, and the server enforces that.
 *
 * Unrouted alerts are surfaced here even though they are, by definition, not
 * this officer's — because an alert nobody owns is the failure the routing
 * model exists to remove, and the person most likely to notice is whoever is
 * on duty.
 */

import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, BellRing, Clock, FileText, Globe2, Inbox,
  Radio, Ship, ShieldAlert,
} from "lucide-react";

import { Card, Empty, Spinner } from "../components/ui";
import { api, useApi } from "../lib/api";
import { useSession } from "../lib/session";
import "../globe.css";

const SEVERITY_TONE = { critical: "danger", warning: "warn", info: "neutral" };

function age(seconds) {
  if (seconds == null) return "--";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export default function OfficerDashboard() {
  const { user } = useSession();

  const mineQ = useApi(() => api.myZones(), []);
  const myAlertsQ = useApi(() => api.listAlerts({ mine: true, limit: 50 }),
                           [], { interval: 20000 });
  const allAlertsQ = useApi(() => api.listAlerts({ limit: 200 }),
                            [], { interval: 20000 });
  const summaryQ = useApi(() => api.alertsSummary(), [], { interval: 20000 });
  const incidentsQ = useApi(() => api.listIncidents({ limit: 100 }), [],
                            { interval: 60000 });
  const trafficQ = useApi(() => api.aisZoneSummary({ max_age_minutes: 60 }),
                          [], { interval: 30000 });
  const aisQ = useApi(() => api.aisStatus(), [], { interval: 30000 });

  const myZones = mineQ.data?.zones || [];
  const myZoneIds = useMemo(() => new Set(myZones.map((z) => z.id)), [myZones]);
  const myAlerts = myAlertsQ.data?.alerts || [];
  const allAlerts = allAlertsQ.data?.alerts || [];
  const incidents = incidentsQ.data?.items || incidentsQ.data?.incidents || [];

  const myIncidents = incidents.filter(
    (i) => myZoneIds.has(i.zone_id) || i.assignee_id === user?.id);
  const elsewhere = incidents.filter(
    (i) => !myZoneIds.has(i.zone_id) && i.assignee_id !== user?.id);
  const unrouted = allAlerts.filter(
    (a) => a.routing === "unzoned" || a.routing === "unassigned");

  const isOfficer = user?.role === "zone_officer";
  const hasScope = myZones.length > 0;

  if (mineQ.loading) {
    return <div className="page"><Spinner label="loading your zones" /></div>;
  }

  return (
    <div className="page" data-testid="officer-dashboard">
      {/* An officer with no zone can see everything and act nowhere. Said
          plainly, because an empty queue would otherwise read as a quiet
          shift. */}
      {isOfficer && !hasScope && (
        <div className="globe-inline-error" style={{ marginBottom: 15 }}
          data-testid="no-scope-banner">
          <ShieldAlert size={14} />
          <span>
            <strong>You have no zone assignment.</strong> You can view every
            zone, incident and vessel in the system, and you cannot act on any
            of them or read any zone's reports. Nothing will be routed to you
            until an administrator assigns you a zone. This is not an empty
            queue.
          </span>
        </div>
      )}

      <div className="grid grid-4" style={{ marginBottom: 15 }}>
        <Tile label="My open alerts" value={myAlerts.length}
          sub={summaryQ.data
            ? `${summaryQ.data.open} open system-wide` : "--"}
          tone={myAlerts.some((a) => a.severity === "critical")
            ? "danger" : undefined}
          to="/alerts" />
        <Tile label="My zones"
          value={myZones.length}
          sub={myZones.length
            ? myZones.map((z) => z.id.replace("zone-bob-", "Z")).join(" ")
            : (isOfficer ? "none assigned" : "you supervise, not hold, zones")}
          to="/zones" />
        <Tile label="My incidents" value={myIncidents.length}
          sub={`${myIncidents.filter((i) => i.status === "open").length} awaiting review`}
          to="/incidents" />
        <Tile label="Oldest in my queue"
          value={age(summaryQ.data?.oldest_mine_age_seconds)}
          sub={summaryQ.data?.oldest_age_seconds != null
            ? `${age(summaryQ.data.oldest_age_seconds)} oldest anywhere`
            : "--"}
          tone={(summaryQ.data?.oldest_mine_age_seconds || 0) > 86400
            ? "warn" : undefined} />
      </div>

      {/* Not mine, and shown anyway. An alert nobody owns is the failure the
          routing model exists to remove. */}
      {unrouted.length > 0 && (
        <div className="globe-warn" style={{ marginBottom: 15 }}
          data-testid="unrouted-banner">
          <AlertTriangle size={13} />
          <span>
            <strong>{unrouted.length} alert(s) are routed to nobody.</strong>{" "}
            {unrouted.filter((a) => a.routing === "unzoned").length} fell
            outside every declared zone;{" "}
            {unrouted.filter((a) => a.routing === "unassigned").length} landed
            in a zone with no assigned officer. These are not in anybody's
            queue. <Link to="/zones">Review zones <ArrowRight size={11} /></Link>
          </span>
        </div>
      )}

      <div className="grid grid-2">
        <Card title={`My alert queue${hasScope ? "" : " (nothing routes to you)"}`}
          right={<Link className="btn btn-sm" to="/alerts">
            <BellRing size={12} /> All alerts
          </Link>}
          bodyStyle={{ padding: 0 }}>
          {myAlerts.length === 0 ? (
            <Empty icon={<Inbox size={20} />}
              title={hasScope ? "Nothing waiting" : "Nothing routes to you"}
              hint={hasScope
                ? "No open alert is routed to you or to a zone you hold."
                : "You hold no zone, so no alert can be routed to you."} />
          ) : (
            <table className="ot-table" data-testid="my-alerts">
              <thead>
                <tr>
                  <th>Alert</th><th>Zone</th><th>Severity</th>
                  <th className="num">Age</th><th />
                </tr>
              </thead>
              <tbody>
                {myAlerts.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <div>{a.title}</div>
                      <div className="tiny mono muted">{a.kind}</div>
                    </td>
                    <td className="tiny mono">{a.zone_id || "—"}</td>
                    <td>
                      <span className={`badge badge-${SEVERITY_TONE[a.severity]
                        || "neutral"}`}>{a.severity}</span>
                    </td>
                    <td className="num mono">{age(a.age_seconds)}</td>
                    <td>
                      {a.incident_id && (
                        <Link className="btn btn-sm"
                          to={`/incidents?focus=${a.incident_id}`}>
                          Open
                        </Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="My incidents" right={
          <Link className="btn btn-sm" to="/incidents">
            <FileText size={12} /> Register
          </Link>
        } bodyStyle={{ padding: 0 }}>
          {myIncidents.length === 0 ? (
            <Empty icon={<FileText size={20} />} title="No incidents"
              hint={hasScope
                ? "No incident is currently assigned to you or to your zones."
                : "Incidents are routed by zone; you hold none."} />
          ) : (
            <table className="ot-table" data-testid="my-incidents">
              <thead>
                <tr>
                  <th>Incident</th><th>Zone</th><th>Severity</th>
                  <th>Status</th><th>Origin</th>
                </tr>
              </thead>
              <tbody>
                {myIncidents.slice(0, 12).map((i) => (
                  <tr key={i.id}>
                    <td>
                      <div className="mono">{i.id}</div>
                      <div className="tiny muted">{i.title}</div>
                    </td>
                    <td className="tiny mono">{i.zone_id || "—"}</td>
                    <td className="tiny">{(i.severity || "--").toUpperCase()}</td>
                    <td><span className="badge badge-neutral">{i.status}</span></td>
                    <td className="tiny">
                      {/* Whether a human looked at this before it became a
                          case. Provenance, not bookkeeping. */}
                      {i.origin === "auto"
                        ? <span className="badge badge-warn">AUTO</span>
                        : i.origin === "manual"
                          ? <span className="badge badge-ok">MANUAL</span>
                          : <span className="muted tiny">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <div className="grid grid-2" style={{ marginTop: 15 }}>
        <Card title="Current vessel activity" right={
          <Link className="btn btn-sm" to="/globe">
            <Globe2 size={12} /> Globe
          </Link>
        }>
          {/* The live layer's honesty note, rendered. Over water AISStream has
              no receivers for, an empty list is a coverage fact and must not
              read as "no traffic". */}
          {aisQ.data?.stream?.note && (
            <div className="globe-note tiny" data-testid="dash-ais-note">
              <Radio size={11} /> {aisQ.data.stream.note}
            </div>
          )}
          {(trafficQ.data?.zones || []).length === 0 ? (
            <Empty icon={<Ship size={20} />} title="No zone traffic"
              hint="No operational zone reported a vessel in the last hour." />
          ) : (
            <table className="ot-table">
              <thead>
                <tr><th>Zone</th><th className="num">Vessels (1 h)</th></tr>
              </thead>
              <tbody>
                {(trafficQ.data.zones || []).slice(0, 8).map((z) => (
                  <tr key={z.zone_id}
                    style={{ fontWeight: myZoneIds.has(z.zone_id) ? 600 : 400 }}>
                    <td className="tiny">
                      {z.name}
                      {myZoneIds.has(z.zone_id) && (
                        <span className="badge badge-ok">MINE</span>
                      )}
                    </td>
                    <td className="num mono">{z.vessels}</td>
                  </tr>
                ))}
                <tr>
                  <td className="tiny muted">outside all zones</td>
                  <td className="num mono muted">
                    {trafficQ.data.outside_all_zones}
                  </td>
                </tr>
              </tbody>
            </table>
          )}
        </Card>

        {/* Global by design. See the header comment. */}
        <Card title="Elsewhere in the region"
          right={<span className="tiny muted">situational awareness</span>}>
          {elsewhere.length === 0 ? (
            <Empty icon={<Globe2 size={20} />} title="Nothing elsewhere"
              hint="No incident outside your zones is currently open." />
          ) : (
            <table className="ot-table">
              <thead>
                <tr><th>Incident</th><th>Zone</th><th>Status</th></tr>
              </thead>
              <tbody>
                {elsewhere.slice(0, 10).map((i) => (
                  <tr key={i.id}>
                    <td className="mono tiny">{i.id}</td>
                    <td className="tiny">{i.region || i.zone_id || "outside all zones"}</td>
                    <td className="tiny">{i.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="globe-note tiny">
            You can see every zone and incident in the system. You may act, and
            read reports, only within{" "}
            {mineQ.data?.report_scope === "all"
              ? "any zone (your role is not zone-scoped)"
              : (myZones.length
                ? myZones.map((z) => z.id).join(", ")
                : "no zone — nothing is assigned to you")}.
          </div>
        </Card>
      </div>
    </div>
  );
}

function Tile({ label, value, sub, tone, to }) {
  const body = (
    <>
      <div className="tiny muted" style={{ letterSpacing: 0.6 }}>
        {label.toUpperCase()}
      </div>
      <div className="mono" style={{
        fontSize: 24, marginTop: 4,
        color: tone === "danger" ? "var(--danger)"
          : tone === "warn" ? "var(--warn)" : "var(--ink-0)",
      }}>
        {value}
      </div>
      {sub && <div className="tiny muted">{sub}</div>}
    </>
  );
  if (!to) return <div className="card" style={{ padding: 13 }}>{body}</div>;
  return (
    <Link className="card" to={to}
      style={{ padding: 13, textDecoration: "none", display: "block" }}>
      {body}
    </Link>
  );
}
