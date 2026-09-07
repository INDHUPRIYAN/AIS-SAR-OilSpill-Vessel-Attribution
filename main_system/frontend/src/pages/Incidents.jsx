/* Incidents registry (P4).
 *
 * Named `Incidents` (plural) because `Incident.jsx` is already the map-first
 * replay workspace for a single run -- a different thing entirely. This page
 * is the case register: which spills are open, who holds them, and what the
 * pipeline has produced against each.
 *
 * The lifecycle control only offers the transitions the signed-in role may
 * actually make. That is presentation, not enforcement: the server rejects a
 * concluding status from an investigator regardless of what the UI shows.
 */

import { useState } from "react";
import { ClipboardList, Filter, Plus } from "lucide-react";

import { Badge, Card, Empty, Spinner, Stat } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { useSession } from "../lib/session";

// Lifecycle order, so the chips read as progress rather than as a set.
const FLOW = ["open", "investigating", "attributed", "closed", "archived"];

// Mirrors INCIDENT_REVIEWER_STATUSES on the server.
const CONCLUDING = ["attributed", "closed"];

function statusFor(status) {
  if (status === "attributed") return "COMPLETE";
  if (status === "closed" || status === "archived") return "OK";
  if (status === "investigating") return "RUNNING";
  return "PENDING";
}

export default function Incidents() {
  const { user } = useSession();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const { data, loading, error, reload } = useApi(
    () => api.listIncidents({ status: status || undefined, q: q || undefined }),
    [status, q],
  );

  const canOpen = ["investigator", "analyst", "admin"].includes(user?.role);
  const canConclude = ["reviewer", "admin"].includes(user?.role);

  async function createIncident() {
    const title = window.prompt("Incident title");
    if (!title) return;
    setBusy(true);
    try {
      await api.createIncident({ title });
      await reload();
    } finally {
      setBusy(false);
    }
  }

  async function setStatusOf(id, next) {
    setBusy(true);
    try {
      await api.patchIncident(id, { status: next });
      await reload();
    } catch (e) {
      window.alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  const items = data?.items || [];
  const counts = FLOW.map((s) => ({ s, n: items.filter((i) => i.status === s).length }));

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <ClipboardList size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>Incidents</div>
          <div className="tiny muted">
            One spill event may span several scenes and runs. The incident is the case;
            runs are its evidence.
          </div>
        </div>
        {canOpen && (
          <button className="btn btn-primary" style={{ marginLeft: "auto" }}
                  onClick={createIncident} disabled={busy}>
            {busy ? <Spinner /> : <Plus size={13} />} New incident
          </button>
        )}
      </div>

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {counts.slice(0, 4).map(({ s, n }) => (
          <Card key={s}><Stat label={s} value={n} /></Card>
        ))}
      </div>

      <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
        <Filter size={13} /> Filters
      </span>} style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div style={{ flex: "1 1 200px" }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Search</div>
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="title or id" style={{ width: "100%" }} />
          </div>
          <div style={{ flex: "0 1 180px" }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Status</div>
            <select value={status} onChange={(e) => setStatus(e.target.value)}
                    style={{ width: "100%" }}>
              <option value="">all</option>
              {FLOW.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        </div>
      </Card>

      {loading && <Card><Spinner /> Loading incidents…</Card>}
      {error && <Card><div className="tiny" style={{ color: "var(--danger)" }}>
        {error.message}
      </div></Card>}

      {!loading && !items.length && (
        <Empty title="No incidents match."
               hint="Alerts you acknowledge, or a run that found something, can be promoted here." />
      )}

      {!!items.length && (
        <Card title={`${data.total} incident${data.total === 1 ? "" : "s"}`}>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Title</th><th>Region</th><th>Detected</th>
                  <th>Status</th><th>Runs</th><th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.id}>
                    <td className="mono">{i.id}</td>
                    <td>{i.title}</td>
                    <td className="tiny muted">{i.region || "—"}</td>
                    <td className="tiny muted">{fmt.utc(i.detected_utc)}</td>
                    <td><Badge status={statusFor(i.status)}>{i.status}</Badge></td>
                    <td>{i.runs}</td>
                    <td>
                      <select
                        value=""
                        disabled={busy}
                        onChange={(e) => e.target.value && setStatusOf(i.id, e.target.value)}
                      >
                        <option value="">change status…</option>
                        {FLOW.filter((s) => s !== i.status)
                          // A concluding status is a reviewer decision; the
                          // server enforces this, the menu just does not
                          // offer an action that would be refused.
                          .filter((s) => canConclude || !CONCLUDING.includes(s))
                          .map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
