/* The alert queue — what the watcher found and nobody has dealt with yet.
 *
 * The one interaction detail that matters here: dismissing requires typing a
 * reason. That is deliberate friction. A queue that clears with one
 * unexplained click becomes a queue people clear rather than read, and the
 * record of why nobody acted disappears with it. The server enforces it too —
 * this is not client-side politeness.
 *
 * Age is rendered from the server's computed `age_seconds` rather than from a
 * stored timestamp difference, so what the UI shows and what the SLA is judged
 * on are the same number.
 */

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Bell, Check, CircleSlash, Clock, UserPlus } from "lucide-react";

import { Badge, Card, Empty, Spinner } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

const SEVERITY_TONE = { critical: "danger", warning: "warn", info: "neutral" };
const STATUS_TONE = {
  open: "warn", acknowledged: "neutral", assigned: "ok", dismissed: "neutral",
};

function age(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export default function Alerts() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data, loading, reload } = useApi(
    () => api.listAlerts(showDismissed ? { status: "dismissed" } : {}),
    [showDismissed], { interval: 15000 });
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [dismissing, setDismissing] = useState(null);
  const [reason, setReason] = useState("");

  async function act(id, fn) {
    setBusy(id); setError(null);
    try { await fn(); await reload(); }
    catch (e) { setError(e.message || "action failed"); }
    finally { setBusy(null); }
  }

  const alerts = data?.alerts ?? [];

  if (loading && !data) return <div className="page"><Spinner /></div>;

  return (
    <div className="page">
      <Card title={`Alerts (${alerts.length})`} icon={<Bell size={14} />}>
        <div className="al-controls">
          <label className="al-toggle">
            <input type="checkbox" checked={showDismissed}
              onChange={(e) => setShowDismissed(e.target.checked)}
              data-testid="alerts-show-dismissed" />
            show dismissed
          </label>
          {error && <span className="al-err">{error}</span>}
        </div>

        {!alerts.length ? (
          <Empty icon={<Bell size={26} color="var(--ink-3)" />}
            title={showDismissed ? "No dismissed alerts" : "Queue is clear"}
            hint={showDismissed ? "" : "New Sentinel-1 passes over a watched AOI appear here."} />
        ) : (
          <table className="al-table" data-testid="alerts-table">
            <thead>
              <tr>
                <th>severity</th><th>alert</th><th>age</th>
                <th>status</th><th>actions</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <motion.tr key={a.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <td>
                    <Badge tone={SEVERITY_TONE[a.severity] || "neutral"}>
                      {a.severity}
                    </Badge>
                  </td>
                  <td>
                    <div className="al-title">{a.title}</div>
                    {a.detail && <div className="al-detail">{a.detail}</div>}
                    <div className="al-meta mono">
                      {a.kind}
                      {a.scene_id ? ` · ${a.scene_id.slice(0, 40)}` : ""}
                      {a.aoi_id ? ` · ${a.aoi_id}` : ""}
                    </div>
                    {a.dismiss_reason && (
                      <div className="al-reason">
                        dismissed: {a.dismiss_reason}
                      </div>
                    )}
                  </td>
                  <td className="mono" title={fmt.utc(a.created_utc)}>
                    <Clock size={11} /> {age(a.age_seconds)}
                  </td>
                  <td>
                    <Badge tone={STATUS_TONE[a.status] || "neutral"}>
                      {a.status}
                    </Badge>
                  </td>
                  <td>
                    {a.status !== "dismissed" && (
                      <div className="al-actions">
                        {a.status === "open" && (
                          <button className="btn btn-sm" disabled={busy === a.id}
                            data-testid={`ack-${a.id}`}
                            onClick={() => act(a.id, () => api.ackAlert(a.id))}>
                            <Check size={11} /> Ack
                          </button>
                        )}
                        <button className="btn btn-sm" disabled={busy === a.id}
                          data-testid={`dismiss-${a.id}`}
                          onClick={() => { setDismissing(a.id); setReason(""); }}>
                          <CircleSlash size={11} /> Dismiss
                        </button>
                      </div>
                    )}

                    {dismissing === a.id && (
                      <div className="al-dismiss" data-testid="dismiss-form">
                        <input value={reason} autoFocus
                          onChange={(e) => setReason(e.target.value)}
                          placeholder="why does this need no action?"
                          data-testid="dismiss-reason" />
                        <button className="btn btn-sm btn-primary"
                          disabled={reason.trim().length < 3}
                          data-testid="dismiss-confirm"
                          onClick={() => act(a.id, async () => {
                            await api.dismissAlert(a.id, reason.trim());
                            setDismissing(null);
                          })}>
                          Dismiss
                        </button>
                        <button className="btn btn-sm"
                          onClick={() => setDismissing(null)}>Cancel</button>
                        <div className="al-detail">
                          A reason is required. An alert cleared without one
                          erases the record of why nobody acted.
                        </div>
                      </div>
                    )}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

/* The top-bar bell. Counts come from the server's own summary so the badge and
 * the queue can never disagree. */
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
    <span className="al-bell" data-testid="alert-bell"
      title={open ? `${open} open alert(s)` : "no open alerts"}>
      <Bell size={14} />
      {open > 0 && (
        <span className={`al-bell-count ${critical ? "crit" : ""}`}>{open}</span>
      )}
    </span>
  );
}
