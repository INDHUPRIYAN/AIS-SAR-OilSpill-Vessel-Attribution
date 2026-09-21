/* The bell, and what is behind it.
 *
 * It used to be a link: clicking it navigated to the alert queue, which is a
 * page, not a notification. An analyst who starts a hindcast and moves on has
 * no reason to visit a queue — they need the system to come to them.
 *
 * Everything here is a server record. The panel reads the same alert feed the
 * queue does (`/api/alerts`), so the badge, this list and the queue cannot
 * disagree, and acknowledging here is the same acknowledgement.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Bell, Check, Loader2 } from "lucide-react";

import { api, fmt } from "../../lib/api";
import { url } from "../../lib/urls";

const TONE = { critical: "danger", warning: "warn", info: "neutral" };

/** Why this alert exists, in the reader's terms rather than the kind's. */
export function reasonFor(kind) {
  return {
    run_failed: "a run failed",
    run_complete: "a run you started finished",
    detection: "the detector opened a case",
    new_scene: "a new scene arrived",
    provider_down: "a data provider stopped answering",
  }[kind] || String(kind || "").replace(/_/g, " ");
}

/** Where this alert is actually about. */
export function targetFor(a) {
  if (a.run_id) return url.workspace({ run: a.run_id });
  if (a.incident_id) return url.incidents(a.incident_id);
  return url.alerts();
}

export default function Notifications({ summary }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState({ phase: "idle", alerts: [] });
  const [busy, setBusy] = useState(null);
  const ref = useRef(null);
  const count = summary?.open ?? 0;
  const critical = (summary?.by_severity?.critical ?? 0) > 0;

  const load = useCallback(async () => {
    setState((s) => ({ ...s, phase: s.alerts.length ? s.phase : "loading" }));
    try {
      const r = await api.listAlerts({ status: "open", limit: 8 });
      setState({ phase: "ready", alerts: r?.alerts || [] });
    } catch (e) {
      setState({ phase: "error", alerts: [], message: e.message || "the alert feed did not answer" });
    }
  }, []);

  useEffect(() => { if (open) load(); }, [open, load]);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("mousedown", onDown); window.removeEventListener("keydown", onKey); };
  }, [open]);

  const ack = async (id) => {
    setBusy(id);
    try { await api.ackAlert(id); await load(); } catch { /* the row stays; the queue is the record */ }
    finally { setBusy(null); }
  };

  return (
    <div className="ntf-wrap" ref={ref}>
      <button className={`hdr-btn ${count ? "on" : ""}`} onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog" aria-expanded={open}
        title={count ? `${count} open alert(s)` : "no open alerts"} data-testid="alert-bell">
        <Bell size={15} />
        {count > 0 && <span className={`hdr-count ${critical ? "crit" : ""}`}>{count}</span>}
      </button>

      {open && (
        <div className="ntf" role="dialog" aria-label="Notifications" data-testid="notifications">
          <div className="ntf-head">
            <span className="label">Notifications</span>
            <Link className="btn btn-xs" to={url.alerts()} onClick={() => setOpen(false)}>Alert queue</Link>
          </div>

          {state.phase === "loading" && (
            <div className="ntf-note"><Loader2 size={12} className="ws-spin" /> Reading the alert feed…</div>
          )}
          {state.phase === "error" && (
            <div className="ntf-note ntf-err" data-testid="ntf-error">
              {state.message}
              <button className="btn btn-xs" onClick={load}>Retry</button>
            </div>
          )}
          {state.phase === "ready" && state.alerts.length === 0 && (
            <div className="ntf-note" data-testid="ntf-empty">
              Nothing open. A finished or failed run, a new scene and an opened case all arrive here.
            </div>
          )}

          {state.alerts.map((a) => (
            <div key={a.id} className="ntf-row" data-testid="ntf-row">
              <span className={`ntf-dot tone-${TONE[a.severity] || "neutral"}`} />
              <Link className="ntf-body" to={targetFor(a)} onClick={() => setOpen(false)}>
                <span className="ntf-title">{a.title}</span>
                <span className="ntf-why">
                  {reasonFor(a.kind)} · {fmt.utc(a.created_utc).slice(5, 16)}Z
                  {a.routing === "unzoned" ? " · routed to nobody" : ""}
                </span>
              </Link>
              <button className="ntf-ack" onClick={() => ack(a.id)} disabled={busy === a.id}
                title="Acknowledge: it stays in the queue, with your name on it"
                data-testid={`ntf-ack-${a.id}`}>
                {busy === a.id ? <Loader2 size={11} className="ws-spin" /> : <Check size={12} />}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
