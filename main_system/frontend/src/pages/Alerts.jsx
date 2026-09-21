/* The alert queue -- what the watcher found and nobody has dealt with yet.
 *
 * Two distinctions this page refuses to collapse, because the backend keeps
 * them apart on purpose:
 *
 *   ROUTED vs ASSIGNED. `routed_to` is what the system decided from the
 *   coordinates; `assigned_to` is what a human then did. Showing one field
 *   would erase the evidence that an alert reached the right desk before
 *   somebody moved it.
 *
 *   UNZONED vs UNASSIGNED. Both mean nobody will see this, and they are shown
 *   separately because the fixes differ: draw a zone, versus assign an officer
 *   to one. Either way the alert is surfaced at the top rather than left to be
 *   noticed in a column.
 *
 * Dismissing requires a typed reason. That friction is the feature: a queue
 * that clears in one unexplained click becomes a queue people clear rather
 * than read, and the record of why nobody acted disappears with it. The server
 * enforces it too -- this is not client-side politeness.
 */

import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowRight, Bell, BellRing, Check, CircleSlash, Clock, Film,
  FolderSearch, Inbox, MapPin, RefreshCw, ShieldAlert, UserPlus,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, Segmented, Spinner, Tile,
} from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { url } from "../lib/urls";

const SEVERITY_TONE = { critical: "danger", warning: "warn", info: "neutral" };
const STATUS_TONE = {
  open: "warn", acknowledged: "accent", assigned: "ok", dismissed: "neutral",
};
/* The two routing outcomes that mean "nobody will see this". */
const UNROUTED = new Set(["unzoned", "unassigned"]);
const ROUTING_COPY = {
  zone: "routed to the zone's officer",
  escalated: "escalated to a parent zone's officer",
  unzoned: "fell outside every declared zone — no officer exists for this water",
  unassigned: "landed in a zone that has no assigned officer",
};

function age(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export default function Alerts() {
  const { user } = useSession();
  const [view, setView] = useState("open");
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [dismissing, setDismissing] = useState(null);
  const [reason, setReason] = useState("");

  const params = view === "dismissed" ? { status: "dismissed" }
    : view === "mine" ? { mine: true } : {};
  const { data, loading, reload } = useApi(() => api.listAlerts({ ...params, limit: 200 }),
                                           [view], { interval: 15000 });
  const summaryQ = useApi(() => api.alertsSummary(), [], { interval: 15000 });

  const alerts = data?.alerts || [];
  const summary = summaryQ.data;
  const unrouted = alerts.filter((a) => UNROUTED.has(a.routing));
  const canAct = hasRole(user, "investigator", "analyst");

  async function act(id, fn) {
    setBusy(id); setError(null);
    try { await fn(); await reload(); await summaryQ.reload(); }
    catch (e) { setError(e.message || "action failed"); }
    finally { setBusy(null); }
  }

  return (
    <div className="page" data-testid="alerts-page">
      <PageHeader icon={<Bell size={17} />} kicker="Operations" title="Alerts"
        sub="The operational queue. Severity first, then age — the oldest critical alert is the one that matters."
        actions={<>
          <button className="btn btn-sm" onClick={reload} disabled={loading}>
            <RefreshCw size={12} /> Refresh
          </button>
          <Segmented value={view} onChange={setView} testidPrefix="alert-view" items={[
            { id: "open", label: "Open" },
            { id: "mine", label: "Mine" },
            { id: "dismissed", label: "Dismissed" },
          ]} />
        </>} />

      <div className="grid grid-5 mb-3">
        <Tile label="Open" value={summary?.open ?? "—"} tone={summary?.open ? "warn" : undefined}
          sub="system-wide" testid="alerts-open" />
        <Tile label="Critical" value={summary?.by_severity?.critical ?? 0}
          tone={summary?.by_severity?.critical ? "danger" : undefined} />
        <Tile label="Mine" value={summary?.mine ?? "—"} tone="accent"
          sub="routed or assigned to you" />
        <Tile label="Unrouted" value={summary?.unrouted ?? 0}
          tone={summary?.unrouted ? "danger" : undefined}
          sub={summary?.unrouted_reasons?.length ? summary.unrouted_reasons.join(" · ") : "all routed"} />
        <Tile label="Oldest open" value={age(summary?.oldest_age_seconds)}
          tone={(summary?.oldest_age_seconds || 0) > 86400 ? "warn" : undefined}
          sub={summary?.oldest_mine_age_seconds != null
            ? `${age(summary.oldest_mine_age_seconds)} oldest of yours` : ""} />
      </div>

      {/* An alert nobody owns is the failure the routing model exists to
          remove, so it is surfaced rather than left in a column. */}
      {unrouted.length > 0 && view !== "dismissed" && (
        <Notice tone="danger" style={{ marginBottom: 12 }} testid="unrouted-banner">
          <strong>{unrouted.length} alert(s) are routed to nobody.</strong>{" "}
          {unrouted.filter((a) => a.routing === "unzoned").length} fell outside every declared
          zone; {unrouted.filter((a) => a.routing === "unassigned").length} landed in a zone with
          no assigned officer. These are in nobody&apos;s queue.{" "}
          <Link to={url.zones()}>Review zones <ArrowRight size={11} /></Link>{" · "}
          <Link to="/officers">assign officers <ArrowRight size={11} /></Link>
        </Notice>
      )}

      {error && <Notice tone="danger" style={{ marginBottom: 12 }}>{error}</Notice>}

      <Panel title={`Queue · ${alerts.length}`} icon={<BellRing size={12} />} flush
        right={data?.scoped_to_you ? <Badge tone="accent">scoped to you</Badge> : null}>
        {loading && !data ? <DataState kind="loading" title="Reading the queue" />
          : alerts.length === 0 ? (
            <DataState kind="empty"
              title={view === "dismissed" ? "No dismissed alerts"
                : view === "mine" ? "Nothing routes to you" : "Queue is clear"}
              hint={view === "dismissed"
                ? "Nothing has been dismissed on this deployment."
                : view === "mine"
                  ? "No open alert is routed to you or to a zone you hold."
                  : "A new Sentinel-1 pass over a watched AOI, or a run that finds a slick, appears here."} />
          ) : (
            <div className="alert-list" data-testid="alerts-list">
              {alerts.map((a) => {
                const unroutedRow = UNROUTED.has(a.routing);
                return (
                  <div key={a.id} className={`alert-row sev-${a.severity}`}
                    data-testid={`alert-${a.id}`}>
                    <span className="alert-rail" />
                    <div className="alert-body">
                      <div className="alert-head">
                        <Badge tone={SEVERITY_TONE[a.severity] || "neutral"}>{a.severity}</Badge>
                        <span className="alert-title">{a.title}</span>
                        <Badge tone={STATUS_TONE[a.status] || "neutral"}>{a.status}</Badge>
                        {unroutedRow && <Badge tone="danger">UNROUTED</Badge>}
                        <span className="alert-age mono ml-auto" title={fmt.utc(a.created_utc)}>
                          <Clock size={10} /> {age(a.age_seconds)}
                        </span>
                      </div>

                      {a.detail && <div className="alert-detail">{a.detail}</div>}

                      <div className="alert-meta mono">
                        <span>{a.kind}</span>
                        <span><MapPin size={10} /> {a.zone_id || "outside all zones"}</span>
                        {/* routed_to is the SYSTEM's decision, assigned_to is a
                            HUMAN's. Both, never one. */}
                        <span title={a.routing_detail || ROUTING_COPY[a.routing] || ""}>
                          routed: {a.routing ? ROUTING_COPY[a.routing] || a.routing : "not recorded"}
                        </span>
                        <span>assigned: {a.assigned_to ? `user ${a.assigned_to}` : "nobody"}</span>
                        {a.scene_id && <span className="ellipsis" style={{ maxWidth: 220 }}>{a.scene_id}</span>}
                      </div>

                      {a.dismiss_reason && (
                        <div className="alert-reason">dismissed: {a.dismiss_reason}</div>
                      )}

                      <div className="alert-actions">
                        {a.incident_id && (
                          <Link className="btn btn-xs btn-primary" to={url.incidents(a.incident_id)}>
                            <FolderSearch size={11} /> Open incident
                          </Link>
                        )}
                        {a.run_id && (
                          <Link className="btn btn-xs" to={url.workspace({ run: a.run_id })}>Workspace</Link>
                        )}
                        {a.run_id && (
                          <Link className="btn btn-xs" to={url.replay(a.run_id)}>
                            <Film size={11} /> Replay
                          </Link>
                        )}
                        {a.status !== "dismissed" && canAct && (
                          <>
                            {a.status === "open" && (
                              <button className="btn btn-xs" disabled={busy === a.id}
                                data-testid={`ack-${a.id}`}
                                onClick={() => act(a.id, () => api.ackAlert(a.id))}>
                                <Check size={11} /> Acknowledge
                              </button>
                            )}
                            <button className="btn btn-xs" disabled={busy === a.id}
                              data-testid={`dismiss-${a.id}`}
                              onClick={() => { setDismissing(a.id); setReason(""); }}>
                              <CircleSlash size={11} /> Dismiss
                            </button>
                          </>
                        )}
                      </div>

                      {dismissing === a.id && (
                        <div className="alert-dismiss" data-testid="dismiss-form">
                          <input value={reason} autoFocus className="sm"
                            onChange={(e) => setReason(e.target.value)}
                            placeholder="why does this need no action?"
                            data-testid="dismiss-reason" />
                          <button className="btn btn-xs btn-primary"
                            disabled={reason.trim().length < 3 || busy === a.id}
                            data-testid="dismiss-confirm"
                            onClick={() => act(a.id, async () => {
                              await api.dismissAlert(a.id, reason.trim());
                              setDismissing(null);
                            })}>
                            Dismiss
                          </button>
                          <button className="btn btn-xs" onClick={() => setDismissing(null)}>
                            Cancel
                          </button>
                          <span className="tiny dim" style={{ flexBasis: "100%" }}>
                            A reason is required. An alert cleared without one erases the record of
                            why nobody acted.
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
      </Panel>

      <style>{`
        .alert-list { display: flex; flex-direction: column; }
        .alert-row { display: flex; border-bottom: 1px solid var(--line); }
        .alert-row:last-child { border-bottom: none; }
        .alert-row:hover { background: var(--accent-soft); }
        .alert-rail { width: 3px; flex-shrink: 0; background: var(--line-bright); }
        .alert-row.sev-critical .alert-rail { background: var(--danger); }
        .alert-row.sev-warning .alert-rail { background: var(--warn); }
        .alert-row.sev-info .alert-rail { background: var(--accent); }
        .alert-body { flex: 1; min-width: 0; padding: 9px 12px; display: flex; flex-direction: column; gap: 4px; }
        .alert-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; min-width: 0; }
        .alert-title { color: var(--ink-0); font-size: var(--fs-md); font-weight: 500; }
        .alert-age { font-size: var(--fs-xs); color: var(--ink-3); display: inline-flex; align-items: center; gap: 4px; }
        .alert-detail { font-size: var(--fs-sm); color: var(--ink-2); line-height: 1.5; }
        .alert-meta { display: flex; gap: 12px; flex-wrap: wrap; font-size: var(--fs-xs); color: var(--ink-3); }
        .alert-meta span { display: inline-flex; align-items: center; gap: 4px; }
        .alert-reason { font-size: var(--fs-xs); color: var(--ink-2); font-style: italic; }
        .alert-actions { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 2px; }
        .alert-dismiss { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-top: 6px;
          padding: 8px; background: var(--bg-0); border: 1px solid var(--line); border-radius: var(--r-sm); }
        .alert-dismiss input { min-width: 240px; flex: 1; }
      `}</style>
    </div>
  );
}

/* The top-bar bell lives in the shell header now; this export is kept because
 * lib/shell's route table and older imports refer to it. Counts come from the
 * server's own summary so the badge and the queue cannot disagree. */
export function AlertBell() {
  const [summary, setSummary] = useState(null);
  const load = useCallback(() => {
    api.alertsSummary().then(setSummary).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(load, 20000);
    return () => clearInterval(id);
  }, [load]);
  const open = summary?.open ?? 0;
  const critical = summary?.by_severity?.critical ?? 0;
  return (
    <span className="hdr-btn" data-testid="alert-bell"
      title={open ? `${open} open alert(s)` : "no open alerts"}>
      <Bell size={14} />
      {open > 0 && <span className={`hdr-count ${critical ? "crit" : ""}`}>{open}</span>}
    </span>
  );
}
