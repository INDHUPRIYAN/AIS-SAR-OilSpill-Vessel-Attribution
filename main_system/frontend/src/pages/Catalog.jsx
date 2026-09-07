/* Data Catalog, Models and Health — the three pages that answer "what is this
 * system actually running on?".
 *
 * The design constraint here is narrower than usual: every badge must say what
 * it measured, not how it feels. The status vocabulary is published by the API
 * alongside the data (`/api/catalog` returns `vocabulary`), so this page never
 * invents a meaning for a word — it renders the server's own definition in the
 * tooltip. If the backend's vocabulary changes, this page follows rather than
 * drifting into a second, contradictory account.
 *
 * The distinction that matters most on screen:
 *
 *   WORKING    green   an authenticated/functional probe succeeded
 *   REACHABLE  amber   the host answered a ping — nothing more
 *
 * These used to render identically. A board showing all-green while every
 * fetch failed on authentication is worse than a board showing nothing.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle, Cpu, Database, HardDrive, Layers, Server, ShieldQuestion,
} from "lucide-react";

import { Badge, Card, Spinner } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

const STATUS_TONE = {
  WORKING: "ok",
  REACHABLE: "warn",
  DEGRADED: "warn",
  UNCONFIGURED: "danger",
  FAILED: "danger",
  NOT_DEPLOYED: "neutral",
  UNKNOWN: "neutral",
};

const CRED_LABEL = {
  configured: "configured",
  missing: "MISSING",
  n_a: "n/a",
};

function Coverage({ coverage }) {
  if (!coverage) return <span className="cat-dim">—</span>;
  return (
    <div className="cat-coverage">
      <div className="mono">{coverage.dataset}</div>
      <div className="cat-dim">
        {coverage.temporal}
        {coverage.resolution ? ` · ${coverage.resolution}` : ""}
      </div>
      {coverage.note && <div className="cat-note">{coverage.note}</div>}
    </div>
  );
}

export default function Catalog() {
  const [tab, setTab] = useState("providers");
  const { data: catalog, loading: l1 } = useApi(() => api.catalog(), []);
  const { data: models, loading: l2 } = useApi(() => api.models(), []);
  const { data: sys, loading: l3 } = useApi(() => api.systemHealth(), [],
    { interval: 20000 });

  if (l1 && l2 && l3) return <div className="page"><Spinner /></div>;

  const vocab = catalog?.vocabulary ?? {};
  const credVocab = catalog?.credentials_vocabulary ?? {};

  return (
    <div className="page cat-page">
      <div className="cat-tabs">
        {[["providers", "Data catalog"], ["models", "Models"],
          ["health", "Health"]].map(([id, label]) => (
          <button key={id} className={tab === id ? "on" : ""}
            onClick={() => setTab(id)} data-testid={`cat-tab-${id}`}>
            {label}
          </button>
        ))}
      </div>

      {/* ------------------------------------------------------ providers -- */}
      {tab === "providers" && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
          <Card title="Providers" icon={<Layers size={14} />}>
            <p className="cat-legend">
              <b>WORKING</b> means a functional probe succeeded — we asked for
              something real and got it. <b>REACHABLE</b> means only that the
              host answered an unauthenticated request; it does not prove the
              provider will serve us data.
            </p>
            <table className="cat-table" data-testid="catalog-table">
              <thead>
                <tr>
                  <th>provider</th><th>purpose</th><th>status</th>
                  <th>probe</th><th>credentials</th><th>coverage</th>
                </tr>
              </thead>
              <tbody>
                {(catalog?.providers ?? []).map((p) => (
                  <tr key={p.name}
                    className={p.deployment === "NOT_DEPLOYED" ? "cat-off" : ""}>
                    <td>
                      <div className="mono">{p.name}</div>
                      <div className="cat-dim">{p.kind}</div>
                    </td>
                    <td>
                      {p.purpose}
                      {p.reason && (
                        <div className="cat-reason" data-testid={`reason-${p.name}`}>
                          <AlertTriangle size={11} /> {p.reason}
                        </div>
                      )}
                    </td>
                    <td>
                      <Badge tone={STATUS_TONE[p.status] || "neutral"}
                        title={vocab[p.status] || ""}>
                        {p.status}
                      </Badge>
                    </td>
                    <td className="cat-dim" title={p.probe_proves || ""}>
                      {p.probe}
                      {p.probe === "functional" && p.probe_proves && (
                        <div className="cat-note">proves: {p.probe_proves}</div>
                      )}
                    </td>
                    <td>
                      <span className={`cat-cred cat-cred-${p.credentials}`}
                        title={credVocab[p.credentials] || ""}>
                        {CRED_LABEL[p.credentials] ?? p.credentials}
                      </span>
                    </td>
                    <td><Coverage coverage={p.coverage} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </motion.div>
      )}

      {/* --------------------------------------------------------- models -- */}
      {tab === "models" && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          className="cat-models" data-testid="models-list">
          {(models?.models ?? []).map((m) => (
            <Card key={m.kind}
              title={`${m.kind} — ${m.name || m.file}`}
              icon={m.status === "EXPERIMENTAL"
                ? <ShieldQuestion size={14} /> : <Cpu size={14} />}>
              <div className="cat-model-head">
                <Badge tone={m.status === "DEPLOYED" ? "ok"
                  : m.status === "EXPERIMENTAL" ? "mock" : "danger"}>
                  {m.status}
                </Badge>
                {m.applied === false && (
                  <span className="cat-dim">not applied to any run</span>
                )}
              </div>

              {m.note && <p className="cat-note">{m.note}</p>}
              {m.detail && <p className="cat-legend">{m.detail}</p>}

              {m.sha256 && (
                <dl className="cat-kv">
                  <dt>file</dt><dd className="mono">{m.file}</dd>
                  <dt>sha256</dt><dd className="mono cat-hash">{m.sha256}</dd>
                  <dt>bytes</dt><dd className="mono">{fmt.int(m.bytes)}</dd>
                  {m.config_fingerprint && (
                    <>
                      <dt>config</dt>
                      <dd className="mono cat-hash">{m.config_fingerprint}</dd>
                    </>
                  )}
                </dl>
              )}
            </Card>
          ))}
          <p className="cat-legend">{models?.note}</p>
        </motion.div>
      )}

      {/* --------------------------------------------------------- health -- */}
      {tab === "health" && sys && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
          className="cat-health" data-testid="health-panel">
          <Card title="Host" icon={<Server size={14} />}>
            <dl className="cat-kv">
              <dt>platform</dt><dd className="mono">{sys.host.platform}</dd>
              <dt>python</dt><dd className="mono">{sys.host.python}</dd>
              <dt>uptime</dt><dd className="mono">{fmt.int(sys.host.uptime_seconds)} s</dd>
              {sys.host.measured_by === "psutil" ? (
                <>
                  <dt>cpu</dt><dd className="mono">{sys.host.cpu_percent}%</dd>
                  <dt>memory</dt>
                  <dd className="mono">
                    {sys.host.memory_used_percent}% of {fmt.int(sys.host.memory_total_mb)} MB
                  </dd>
                </>
              ) : (
                <>
                  <dt>cpu</dt>
                  <dd className="cat-dim" data-testid="health-unmeasured">
                    not measured — {sys.host.note}
                  </dd>
                </>
              )}
            </dl>
          </Card>

          <Card title="Storage" icon={<HardDrive size={14} />}>
            <dl className="cat-kv">
              <dt>path</dt><dd className="mono">{sys.disk.path}</dd>
              <dt>free</dt><dd className="mono">{sys.disk.free_gb} GB</dd>
              <dt>total</dt><dd className="mono">{sys.disk.total_gb} GB</dd>
            </dl>
          </Card>

          <Card title="Database" icon={<Database size={14} />}>
            <dl className="cat-kv">
              <dt>status</dt>
              <dd><Badge tone={sys.database.ok ? "ok" : "danger"}>
                {sys.database.ok ? "OK" : "ERROR"}</Badge></dd>
              <dt>runs</dt><dd className="mono">{fmt.int(sys.database.runs)}</dd>
              <dt>engine</dt><dd className="mono">{sys.database.url_scheme}</dd>
            </dl>
          </Card>

          <Card title="Models on disk" icon={<Cpu size={14} />}>
            <dl className="cat-kv">
              {sys.models.map((m) => (
                <div key={m.file} style={{ display: "contents" }}>
                  <dt className="mono">{m.file}</dt>
                  <dd>
                    <Badge tone={m.present ? "ok" : "danger"}>
                      {m.present ? `${fmt.int(m.bytes)} B` : "MISSING"}
                    </Badge>
                  </dd>
                </div>
              ))}
            </dl>
          </Card>

          <Card title="Not reported" icon={<AlertTriangle size={14} />}>
            <p className="cat-legend">
              These are listed rather than shown as empty tiles. A dashboard
              tile for a component this system does not run is a claim, not a
              placeholder.
            </p>
            <ul className="cat-absent" data-testid="health-not-reported">
              {sys.not_reported.map((line) => <li key={line}>{line}</li>)}
            </ul>
          </Card>
        </motion.div>
      )}
    </div>
  );
}
