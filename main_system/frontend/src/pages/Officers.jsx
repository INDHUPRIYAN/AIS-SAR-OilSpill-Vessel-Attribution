/* Officer Assignment: accounts, and which water each one answers for.
 *
 * Separate from Zone Management because the task is different. That page asks
 * "what does the operational map look like"; this one asks "who is on duty,
 * and is anybody's zone uncovered".
 *
 * The rules this page renders are enforced server-side, and the page shows the
 * server's own reasons rather than inventing its own:
 *
 *   - only a super_admin may grant a role, so the role selector is disabled
 *     for an admin and says why;
 *   - an admin may not create or modify an admin or a super_admin;
 *   - deactivating an officer unassigns their zones, and the response names
 *     the zones that are now unrouted — displayed, because a zone silently
 *     losing its officer is the failure the whole routing model exists to
 *     avoid.
 *
 * Nothing here hides a control as its only protection. A disabled button is a
 * courtesy; the 403 is the rule.
 */

import { useState } from "react";
import {
  AlertTriangle, Check, Plus, ShieldAlert, UserCheck, UserPlus, UserX, X,
} from "lucide-react";

import { Card, Empty, Spinner } from "../components/ui";
import { api, useApi } from "../lib/api";
import { useSession } from "../lib/session";
import "../globe.css";

const MIN_PASSWORD = 12;

export default function OfficersPage() {
  const { user } = useSession();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [creating, setCreating] = useState(false);

  const usersQ = useApi(() => api.listUsers(), []);
  const zonesQ = useApi(() => api.listZones({ counts: true }), []);
  const rolesQ = useApi(() => api.roles(), []);

  const users = usersQ.data?.users || [];
  const zones = (zonesQ.data?.zones || []);
  const operational = zones.filter((z) => z.kind === "operational");
  const assignable = usersQ.data?.admin_assignable_roles || [];
  const isSuper = user?.role === "super_admin";

  const act = async (fn, describe) => {
    setBusy(true);
    setMessage(null);
    try {
      const out = await fn();
      // The server's warning is the message. It knows which zones went
      // unrouted; this page does not.
      setMessage({ ok: out?.warning ? null : describe,
                   warn: out?.warning || null });
      await Promise.all([usersQ.reload(), zonesQ.reload()]);
    } catch (e) {
      setMessage({ error: e.message });
    } finally {
      setBusy(false);
    }
  };

  if (usersQ.error) {
    return (
      <div className="page">
        <Empty icon={<ShieldAlert size={22} />} title="Not available"
          hint={`${usersQ.error.message} — the account register is `
                + `administrator-and-above.`} />
      </div>
    );
  }
  if (usersQ.loading) {
    return <div className="page"><Spinner label="loading accounts" /></div>;
  }

  const unassignedOfficers = users.filter((u) => u.unassigned_officer);
  const uncoveredZones = operational.filter(
    (z) => z.status === "active"
      && !(z.officers || []).some((o) => o.is_primary));

  return (
    <div className="page" data-testid="officers-page">
      {(unassignedOfficers.length > 0 || uncoveredZones.length > 0) && (
        <div className="globe-warn" style={{ marginBottom: 15 }}>
          <AlertTriangle size={13} />
          <span>
            {uncoveredZones.length > 0 && (
              <>
                <strong>{uncoveredZones.length} active zone(s) with no primary
                officer</strong> ({uncoveredZones.map((z) => z.id).join(", ")}).
                Detections there route by escalation or not at all.{" "}
              </>
            )}
            {unassignedOfficers.length > 0 && (
              <>
                <strong>{unassignedOfficers.length} officer account(s) with no
                zone</strong> — they can see everything and act nowhere.
              </>
            )}
          </span>
        </div>
      )}

      {message?.error && (
        <div className="globe-inline-error" style={{ marginBottom: 12 }}
          data-testid="officer-error">
          <AlertTriangle size={12} /> {message.error}
        </div>
      )}
      {message?.warn && (
        <div className="globe-warn" style={{ marginBottom: 12 }}
          data-testid="officer-warning">
          <AlertTriangle size={12} /> {message.warn}
        </div>
      )}
      {message?.ok && (
        <div className="globe-ok" style={{ marginBottom: 12 }}
          data-testid="officer-ok">
          <Check size={12} /> {message.ok}
        </div>
      )}

      <Card title="Accounts" right={
        <button className="btn btn-sm btn-primary"
          onClick={() => setCreating((c) => !c)}
          data-testid="new-account-toggle">
          <UserPlus size={12} /> New account
        </button>
      } bodyStyle={{ padding: 0 }}>
        {creating && (
          <NewAccount assignable={isSuper
            ? (rolesQ.data?.roles || []).map((r) => r.role)
            : assignable}
            isSuper={isSuper}
            minPassword={usersQ.data?.min_password_length || MIN_PASSWORD}
            busy={busy}
            onCreate={(body) => act(() => api.createUser(body),
                                    `${body.email} created`)
              .then(() => setCreating(false))}
            onCancel={() => setCreating(false)} />
        )}

        <table className="ot-table" data-testid="account-table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Role</th>
              <th>Status</th>
              <th>Zones</th>
              <th>Last sign-in</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} data-testid={`account-${u.id}`}>
                <td>
                  <div>{u.display_name || u.email}</div>
                  <div className="tiny mono muted">{u.email}</div>
                </td>
                <td>
                  {/* Disabled for an admin, with the reason from the server's
                      own vocabulary. Hiding it would leave an administrator
                      wondering why nothing happens. */}
                  <select className="btn btn-sm" value={u.role}
                    disabled={!isSuper || busy || u.id === user?.id}
                    title={!isSuper
                      ? "Only a super_admin may change an account's role"
                      : u.id === user?.id
                        ? "You cannot change your own role"
                        : "Change role"}
                    onChange={(e) => act(
                      () => api.updateUser(u.id, { role: e.target.value }),
                      `${u.email} is now ${e.target.value}`)}
                    data-testid={`role-${u.id}`}>
                    {(rolesQ.data?.roles || [{ role: u.role }]).map((r) => (
                      <option key={r.role} value={r.role}>{r.role}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <span className={`badge badge-${u.active ? "ok" : "neutral"}`}>
                    {u.active ? "active" : "deactivated"}
                  </span>
                  {!u.can_sign_in_with_password && (
                    <span className="badge badge-warn"
                      title="No password set; this account cannot sign in with one">
                      no password
                    </span>
                  )}
                </td>
                <td className="tiny">
                  {u.zones?.length
                    ? u.zones.map((z) => (
                      <div key={z.zone_id}>
                        <span className="mono">{z.zone_id}</span>
                        {z.is_primary && <span className="badge badge-ok">P</span>}
                      </div>
                    ))
                    : u.role === "zone_officer"
                      ? <span className="badge badge-warn">none</span>
                      : <span className="muted">—</span>}
                </td>
                <td className="tiny mono muted">
                  {u.last_login_utc
                    ? String(u.last_login_utc).slice(0, 16).replace("T", " ")
                    /* Never signed in is different from signed in long ago. */
                    : "never"}
                </td>
                <td>
                  <div style={{ display: "flex", gap: 5,
                                justifyContent: "flex-end" }}>
                    <AssignControl user={u} zones={operational} busy={busy}
                      onAssign={(zoneId) => act(
                        () => api.assignZoneOfficer(zoneId, u.id, true),
                        `${u.email} assigned to ${zoneId}`)} />
                    <button className="btn btn-sm"
                      disabled={busy || u.id === user?.id}
                      title={u.id === user?.id
                        ? "You cannot deactivate your own account"
                        : (u.active ? "Deactivate" : "Reactivate")}
                      onClick={() => act(
                        () => api.updateUser(u.id, { active: !u.active }),
                        `${u.email} ${u.active ? "deactivated" : "reactivated"}`)}
                      data-testid={`toggle-${u.id}`}>
                      {u.active ? <UserX size={12} /> : <UserCheck size={12} />}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Zone coverage" style={{ marginTop: 15 }}
        bodyStyle={{ padding: 0 }}>
        <table className="ot-table" data-testid="coverage-table">
          <thead>
            <tr>
              <th>Zone</th>
              <th>Primary officer</th>
              <th>Deputies</th>
              <th className="num">Open incidents</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {operational.map((z) => {
              const primary = (z.officers || []).find((o) => o.is_primary);
              const deputies = (z.officers || []).filter((o) => !o.is_primary);
              return (
                <tr key={z.id}>
                  <td>
                    <div>{z.name}</div>
                    <div className="tiny mono muted">{z.id}</div>
                  </td>
                  <td className="tiny">
                    {primary
                      ? (primary.display_name || primary.email)
                      : <span className="badge badge-warn">unassigned</span>}
                  </td>
                  <td className="tiny">
                    {deputies.length
                      ? deputies.map((d) => d.display_name || d.email).join(", ")
                      : <span className="muted">—</span>}
                  </td>
                  <td className="num mono">{z.open_incident_count ?? 0}</td>
                  <td>
                    {primary && (
                      <button className="btn btn-sm" disabled={busy}
                        title="Remove the primary officer from this zone"
                        onClick={() => act(
                          () => api.unassignZoneOfficer(z.id, primary.user_id),
                          `${primary.email} removed from ${z.id}`)}>
                        <X size={12} />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function AssignControl({ user, zones, busy, onAssign }) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button className="btn btn-sm" disabled={busy || !user.active}
        title={user.active ? "Assign to a zone"
                           : "A deactivated account cannot be a zone's officer"}
        onClick={() => setOpen(true)}
        data-testid={`assign-${user.id}`}>
        <Plus size={12} />
      </button>
    );
  }
  return (
    <select className="btn btn-sm" defaultValue="" autoFocus
      onChange={(e) => { if (e.target.value) onAssign(e.target.value);
                         setOpen(false); }}
      onBlur={() => setOpen(false)}>
      <option value="">assign to…</option>
      {zones.map((z) => (
        <option key={z.id} value={z.id}>{z.name}</option>
      ))}
    </select>
  );
}

function NewAccount({ assignable, isSuper, minPassword, busy, onCreate,
                      onCancel }) {
  const [form, setForm] = useState({
    email: "", password: "", display_name: "", role: "zone_officer",
  });
  const short = form.password.length > 0 && form.password.length < minPassword;

  return (
    <div style={{ padding: 14, borderBottom: "1px solid var(--line)",
                  background: "var(--bg-1)" }}>
      <div className="grid grid-4" style={{ gap: 10 }}>
        <label className="globe-field">
          <span>Email</span>
          <input value={form.email} autoFocus
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            placeholder="officer.07@agency.example"
            data-testid="new-email" />
        </label>
        <label className="globe-field">
          <span>Display name</span>
          <input value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            placeholder="Officer 07" />
        </label>
        <label className="globe-field">
          <span>Role</span>
          <select value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}
            data-testid="new-role">
            {assignable.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="globe-field">
          <span>Password (min {minPassword})</span>
          <input type="password" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            data-testid="new-password" />
        </label>
      </div>

      {!isSuper && (
        <div className="globe-note tiny">
          You may create {assignable.join(", ")}. Only a super_admin may create
          an administrator — granting privileged roles is an escalation and is
          refused by the server, not just hidden here.
        </div>
      )}
      {short && (
        <div className="globe-inline-error tiny">
          <AlertTriangle size={11} /> At least {minPassword} characters. Length
          is what resists guessing; there are no composition rules.
        </div>
      )}

      <div className="globe-actions">
        <button className="btn btn-primary btn-sm"
          disabled={busy || !form.email.trim() || short
                    || form.password.length < minPassword}
          onClick={() => onCreate(form)}
          data-testid="new-submit">
          <UserPlus size={12} /> Create
        </button>
        <button className="btn btn-sm" onClick={onCancel}>
          <X size={12} /> Cancel
        </button>
      </div>
    </div>
  );
}
