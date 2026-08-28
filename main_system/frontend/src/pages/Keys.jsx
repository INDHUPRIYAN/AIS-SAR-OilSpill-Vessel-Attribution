/* API key management (admin).
 *
 * The server never returns a plaintext credential -- only the last four
 * characters -- so this page cannot display one even by accident. Values are
 * write-only from the browser's point of view.
 *
 * If encryption at rest is unavailable, that is shown as a prominent warning
 * rather than hidden. A key store that quietly downgrades to plaintext is
 * worse than one that refuses, because nobody finds out.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import { KeyRound, Lock, LockOpen, Save, ShieldCheck, History, AlertTriangle } from "lucide-react";

import { Badge, Card, Spinner } from "../components/ui";
import { api, fmt, getAdminToken, setAdminToken, useApi } from "../lib/api";

export default function Keys() {
  const [token, setToken] = useState(getAdminToken());
  const [authed, setAuthed] = useState(Boolean(getAdminToken()));

  if (!authed) return <TokenGate token={token} setToken={setToken} onOk={() => setAuthed(true)} />;
  return <KeyManager onLogout={() => { setAdminToken(""); setAuthed(false); }} />;
}

function TokenGate({ token, setToken, onOk }) {
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setError(null);
    setAdminToken(token);
    try { await api.listKeys(); onOk(); }
    catch (err) {
      setError(err.status === 401 ? "Token rejected." : err.message);
      setAdminToken("");
    } finally { setBusy(false); }
  }

  return (
    <div className="page" style={{ display: "grid", placeItems: "center" }}>
      <motion.form initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}
        onSubmit={submit} className="card" style={{ width: 400 }}>
        <div className="card-head">
          <KeyRound size={14} color="var(--accent)" />
          <span className="card-title">Admin authentication</span>
        </div>
        <div className="card-body">
          <p className="tiny muted" style={{ marginTop: 0, lineHeight: 1.55 }}>
            Key management requires the admin token. It is set as{" "}
            <span className="mono">ADMIN_TOKEN</span> in <span className="mono">.env</span>,
            or printed once at server startup if unset.
          </p>
          <input type="password" value={token} placeholder="X-Admin-Token"
            onChange={(e) => setToken(e.target.value)} autoFocus />
          {error && (
            <div className="tiny" style={{ color: "var(--danger)", marginTop: 8 }}>{error}</div>
          )}
          <button className="btn btn-primary" type="submit" disabled={busy || !token}
            style={{ width: "100%", justifyContent: "center", marginTop: 12 }}>
            {busy ? <Spinner /> : <ShieldCheck size={13} />} Authenticate
          </button>
        </div>
      </motion.form>
    </div>
  );
}

function KeyManager({ onLogout }) {
  const { data, loading, reload } = useApi(() => api.listKeys(), []);
  const { data: audit, reload: reloadAudit } = useApi(() => api.keyAudit(), []);
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(null);
  const [tested, setTested] = useState({});

  const enc = data?.encryption;
  const keys = data?.keys || [];
  const byProvider = keys.reduce((acc, k) => {
    (acc[k.provider] = acc[k.provider] || []).push(k);
    return acc;
  }, {});

  async function save(provider, field) {
    const value = edits[`${provider}.${field}`];
    if (!value) return;
    setSaving(`${provider}.${field}`);
    try {
      await api.setKey({ provider, field, value });
      setEdits((e) => ({ ...e, [`${provider}.${field}`]: "" }));
      await reload(); await reloadAudit();
    } finally { setSaving(null); }
  }

  async function test(provider) {
    setTested((t) => ({ ...t, [provider]: { busy: true } }));
    try {
      const r = await api.testKey(provider);
      setTested((t) => ({ ...t, [provider]: r }));
    } catch (e) {
      setTested((t) => ({ ...t, [provider]: { ok: false, detail: e.message } }));
    }
  }

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <KeyRound size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>API Key Management</div>
          <div className="tiny muted">
            Credentials are write-only here — the server returns only the last four characters
          </div>
        </div>
        <button className="btn" style={{ marginLeft: "auto" }} onClick={onLogout}>Sign out</button>
      </div>

      {enc && !enc.available && (
        <div className="card" style={{ marginBottom: 16, borderColor: "rgba(245,158,11,.45)" }}>
          <div className="card-body" style={{ display: "flex", gap: 11, alignItems: "flex-start" }}>
            <AlertTriangle size={17} color="var(--warn)" style={{ flexShrink: 0, marginTop: 1 }} />
            <div>
              <div style={{ fontWeight: 600, color: "var(--warn)" }}>
                Credentials are NOT encrypted at rest
              </div>
              <div className="tiny muted" style={{ marginTop: 3 }}>
                {enc.method}. Anything saved here is stored in the database without
                encryption. Set <span className="mono">SECRET_KEY</span> in{" "}
                <span className="mono">.env</span> and restart to fix.
              </div>
            </div>
          </div>
        </div>
      )}

      {enc?.available && (
        <div className="tiny" style={{ display: "flex", alignItems: "center", gap: 7,
          marginBottom: 16, color: "var(--ok)" }}>
          <Lock size={12} /> Encrypted at rest — {enc.method}
        </div>
      )}

      {loading && <Card><Spinner label="loading credentials…" /></Card>}

      <div className="grid grid-2">
        {Object.entries(byProvider).map(([provider, fields]) => {
          const result = tested[provider];
          return (
            <Card key={provider} title={provider}
              right={<>
                {result && !result.busy && (
                  <Badge status={result.ok ? "WORKING" : "FAILED"}>
                    {result.ok ? "verified" : result.error_class || "failed"}
                  </Badge>
                )}
                <button className="btn btn-sm" onClick={() => test(provider)}
                  disabled={result?.busy}>
                  {result?.busy ? <Spinner /> : "Test connection"}
                </button>
              </>}>
              {fields.map((k) => {
                const id = `${k.provider}.${k.field}`;
                return (
                  <div key={id} style={{ marginBottom: 13 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 5 }}>
                      <span className="mono tiny" style={{ color: "var(--ink-1)" }}>{k.field}</span>
                      {k.configured
                        ? <span className="badge badge-ok">{k.masked}</span>
                        : <span className="badge badge-neutral">unset</span>}
                      <span className="tiny muted" style={{ marginLeft: "auto" }}>
                        {k.source}
                        {k.encrypted === false && k.source === "database" && (
                          <span style={{ color: "var(--warn)" }}> · plaintext</span>
                        )}
                      </span>
                    </div>
                    <div style={{ display: "flex", gap: 7 }}>
                      <input type="password" placeholder="new value"
                        value={edits[id] || ""}
                        onChange={(e) => setEdits((s) => ({ ...s, [id]: e.target.value }))} />
                      <button className="btn btn-sm" disabled={!edits[id] || saving === id}
                        onClick={() => save(k.provider, k.field)}>
                        {saving === id ? <Spinner /> : <Save size={12} />}
                      </button>
                    </div>
                  </div>
                );
              })}
              {result && !result.busy && result.detail && (
                <div className="tiny" style={{
                  marginTop: 4, color: result.ok ? "var(--ok)" : "var(--danger)",
                }}>{result.detail}</div>
              )}
            </Card>
          );
        })}
      </div>

      <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
        <History size={13} /> Audit log
      </span>} style={{ marginTop: 16 }} bodyStyle={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>When</th><th>Action</th><th>Provider</th><th>Field</th><th>Actor</th></tr>
          </thead>
          <tbody>
            {(audit || []).map((a, i) => (
              <tr key={i}>
                <td className="tiny mono muted">{fmt.utc(a.occurred_utc)}</td>
                <td className="tiny mono">{a.action}</td>
                <td className="tiny">{a.provider}</td>
                <td className="tiny mono muted">{a.field}</td>
                <td className="tiny muted">{a.actor}</td>
              </tr>
            ))}
            {!audit?.length && (
              <tr><td colSpan={5} className="tiny muted" style={{ padding: 16 }}>
                No credential changes recorded. The log stores which field changed, never its value.
              </td></tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
