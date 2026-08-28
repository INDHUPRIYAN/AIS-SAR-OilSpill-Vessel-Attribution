/* API monitoring.
 *
 * Every row is a real external dependency with its measured status, its
 * fallback chain, and which chain member is actually serving right now.
 *
 * UNCONFIGURED is deliberately distinct from FAILED. They look similar on a
 * dashboard and are completely different problems: one is a missing key the
 * operator can fix on the Keys page, the other is an outage they cannot.
 * Collapsing them sends people hunting for the wrong thing.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import {
  Activity, RefreshCw, Satellite, Waves, Wind, Ship, ChevronRight, ShieldAlert,
} from "lucide-react";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Cell,
} from "recharts";

import { Badge, Card, Dot, Stat, Spinner, useThemeColors } from "../components/ui";
import { api, fmt, statusTone, useApi } from "../lib/api";

const KIND_ICON = {
  satellite: Satellite, currents: Waves, wind: Wind, ais: Ship,
};

export default function Monitoring() {
  const { data, loading, reload } = useApi(() => api.apiStatus(), [], { interval: 15000 });
  const tc = useThemeColors();
  const [testing, setTesting] = useState(false);
  const [selected, setSelected] = useState(null);

  const providers = data?.providers || [];
  const counts = providers.reduce((acc, p) => {
    const t = statusTone(p.status);
    acc[t] = (acc[t] || 0) + 1;
    return acc;
  }, {});

  const byKind = providers.reduce((acc, p) => {
    (acc[p.kind] = acc[p.kind] || []).push(p);
    return acc;
  }, {});

  async function testAll() {
    setTesting(true);
    try { await api.testAll(); await reload(); } finally { setTesting(false); }
  }

  const latency = providers
    .filter((p) => p.last_latency_ms != null)
    .map((p) => ({ name: p.provider, ms: p.last_latency_ms, status: p.status }));

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <Activity size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>API Monitoring</div>
          <div className="tiny muted">
            Live probes of every external dependency · refreshed every 15s
          </div>
        </div>
        <button className="btn" style={{ marginLeft: "auto" }} onClick={testAll} disabled={testing}>
          {testing ? <Spinner /> : <RefreshCw size={13} />} Test all now
        </button>
      </div>

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Card><Stat label="Working" value={counts.ok || 0} tone="ok"
          sub={`of ${providers.length} providers`} /></Card>
        <Card><Stat label="Degraded" value={counts.warn || 0} tone="warn"
          sub="reachable but impaired" /></Card>
        <Card><Stat label="Failed / unconfigured" value={counts.danger || 0}
          tone={counts.danger ? "danger" : undefined} sub="needs attention" /></Card>
        <Card><Stat label="Chains complete" value={chainsComplete(providers)}
          sub="primary → fallback → guaranteed" /></Card>
      </div>

      {latency.length > 0 && (
        <Card title="Last probe latency" style={{ marginBottom: 18 }}>
          <div style={{ height: 168 }}>
            <ResponsiveContainer>
              <BarChart data={latency} margin={{ top: 4, right: 8, left: -18, bottom: 4 }}>
                <XAxis dataKey="name" tick={{ fill: tc.ink2, fontSize: 10 }}
                  axisLine={{ stroke: tc.line }} tickLine={false} />
                <YAxis tick={{ fill: tc.ink2, fontSize: 10 }}
                  axisLine={false} tickLine={false} unit="ms" />
                <Tooltip
                  contentStyle={{ background: tc.bg1, border: `1px solid ${tc.line}`,
                    borderRadius: 8, fontSize: 12 }}
                  cursor={{ fill: "rgba(56,189,248,.06)" }}
                  formatter={(v) => [`${v} ms`, "latency"]} />
                <Bar dataKey="ms" radius={[4, 4, 0, 0]}>
                  {latency.map((d, i) => (
                    <Cell key={i} fill={
                      statusTone(d.status) === "ok" ? "#10b981"
                        : statusTone(d.status) === "warn" ? "#f59e0b" : "#f43f5e"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      {loading && !providers.length && <Card><Spinner label="probing providers…" /></Card>}

      {Object.entries(byKind).map(([kind, rows]) => {
        const Icon = KIND_ICON[kind] || Activity;
        return (
          <Card key={kind} style={{ marginBottom: 15 }}
            title={<span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <Icon size={13} /> {kind}
            </span>}
            bodyStyle={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 34 }}></th>
                  <th>Provider</th><th>Purpose</th><th>Status</th>
                  <th>Latency</th><th>Last success</th><th>Serving now</th><th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <ProviderRow key={p.provider} p={p}
                    onSelect={() => setSelected(selected === p.provider ? null : p.provider)}
                    expanded={selected === p.provider} onRetest={reload} />
                ))}
              </tbody>
            </table>
          </Card>
        );
      })}
    </div>
  );
}

function ProviderRow({ p, onSelect, expanded, onRetest }) {
  const [busy, setBusy] = useState(false);
  const unconfigured = p.status === "UNCONFIGURED";

  async function test(e) {
    e.stopPropagation();
    setBusy(true);
    try { await api.testProvider(p.provider); await onRetest(); } finally { setBusy(false); }
  }

  return (
    <>
      <tr className="clickable" onClick={onSelect}>
        <td><Dot status={p.status} pulsing={p.status === "WORKING"} /></td>
        <td><span style={{ fontWeight: 600 }}>{p.provider}</span>
          <div className="tiny muted">{p.owner}</div></td>
        <td className="tiny muted">{p.purpose}</td>
        <td>
          <Badge status={p.status} />
          {unconfigured && (
            <div className="tiny" style={{ color: "var(--warn)", marginTop: 3,
              display: "flex", alignItems: "center", gap: 4 }}>
              <ShieldAlert size={10} /> key needed, not an outage
            </div>
          )}
        </td>
        <td className="mono tiny">{fmt.ms(p.last_latency_ms)}</td>
        <td className="tiny muted">{fmt.ago(p.last_success_utc)}</td>
        <td>
          <span className="mono tiny" style={{
            color: p.active_provider === p.provider ? "var(--ok)" : "var(--ink-2)",
          }}>{p.active_provider}</span>
        </td>
        <td>
          <button className="btn btn-sm" onClick={test} disabled={busy}>
            {busy ? <Spinner /> : "Test"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} style={{ background: "var(--bg-0)", padding: 14 }}>
            <div style={{ display: "flex", gap: 26, flexWrap: "wrap" }}>
              <div>
                <div className="stat-label" style={{ marginBottom: 6 }}>Fallback chain</div>
                <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                  {(p.chain || []).map((m, i) => (
                    <span key={m} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                      {i > 0 && <ChevronRight size={11} color="var(--ink-3)" />}
                      <span className={`badge ${m === p.active_provider
                        ? "badge-ok" : "badge-neutral"}`}>{m}</span>
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="stat-label" style={{ marginBottom: 6 }}>Last error</div>
                <div className="tiny mono">{p.last_error_class || "NONE"}</div>
                <div className="tiny muted">{fmt.ago(p.last_failure_utc)}</div>
              </div>
              <div>
                <div className="stat-label" style={{ marginBottom: 6 }}>Recent calls</div>
                <div className="tiny mono">
                  {p.recent_calls} logged
                  {p.recent_success_rate != null &&
                    ` · ${fmt.pct(p.recent_success_rate, 0)} ok`}
                </div>
              </div>
              {p.circuit_open && (
                <div>
                  <div className="stat-label" style={{ marginBottom: 6 }}>Circuit breaker</div>
                  <Badge status="FAILED">OPEN — skipping during cooldown</Badge>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** A chain is complete when at least one member of it is serving. */
function chainsComplete(providers) {
  const chains = new Set(providers.map((p) => (p.chain || []).join("→")));
  let ok = 0;
  for (const c of chains) {
    if (!c) continue;
    const members = c.split("→");
    if (members.some((m) =>
      providers.find((p) => p.provider === m && p.status === "WORKING"))) ok += 1;
  }
  return `${ok}/${chains.size}`;
}
