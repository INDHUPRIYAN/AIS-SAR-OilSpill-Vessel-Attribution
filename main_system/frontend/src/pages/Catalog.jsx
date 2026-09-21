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

import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import {
  AlertTriangle, ArrowRight, Cpu, Database, ExternalLink, HardDrive, KeyRound, Layers, Server, ShieldQuestion,
} from "lucide-react";

import { Badge, Card, DataState, Spinner } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { useUrlTab } from "../lib/urls";

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

/* Where an operator goes to get (or replace) access to each provider. These
 * are the providers' own public pages, not data: the button opens the real
 * site, the operator registers there and pastes what they are given into the
 * form below it. Providers that run on this host have no site to visit. */
const PROVIDER_SITE = {
  CDSE: { url: "https://dataspace.copernicus.eu/", how: "Register, then Dashboard > OAuth clients for a client id and secret." },
  ASF: { url: "https://urs.earthdata.nasa.gov/users/new", how: "A free NASA Earthdata login serves ASF downloads." },
  CMEMS: { url: "https://data.marine.copernicus.eu/register", how: "A free Copernicus Marine account; the username and password are the credential." },
  ERA5: { url: "https://cds.climate.copernicus.eu/how-to-api", how: "Sign in to the Climate Data Store, accept the ERA5 licence, copy the API key from your profile." },
  OpenMeteo: { url: "https://open-meteo.com/en/docs", how: "No key is needed for the free tier." },
  HYCOM: { url: "https://www.hycom.org/dataserver", how: "Open OPeNDAP server; no key." },
  DMA: { url: "https://dma.dk/safety-at-sea/navigational-information/ais-data", how: "Open daily AIS archive for Danish waters; no key." },
  MarineCadastre: { url: "https://hub.marinecadastre.gov/pages/vesseltraffic", how: "Open AIS archive for US waters; no key." },
  AISStream: { url: "https://aisstream.io/authenticate", how: "Sign in with GitHub and create an API key." },
  Sentinel2: { url: "https://dataspace.copernicus.eu/", how: "Served by the same Copernicus Data Space account as Sentinel-1." },
};

/** The fallback ladder a provider belongs to, with the rung in use marked. */
function Chain({ chain, name, byName }) {
  if (!chain?.length) return <span className="cat-dim">—</span>;
  return (
    <div className="cat-chain" data-testid={`chain-${name}`}>
      {chain.map((c, i) => (
        <Fragment key={c}>
          {i > 0 && <ArrowRight size={10} className="cat-chain-a" />}
          <span className={`cat-chain-i ${c === name ? "me" : ""}`} title={byName[c]?.status || "bundled on this host"}>{c}</span>
        </Fragment>
      ))}
    </div>
  );
}

/** Get a key from the provider, enter it here, test it for real. The form
 *  writes through the existing admin key store (PUT /api/keys, encrypted at
 *  rest, audited) and the test is the backend's authenticated probe. */
function KeyForm({ provider, fields, onDone }) {
  const [values, setValues] = useState({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const site = PROVIDER_SITE[provider];
  async function save() {
    setBusy(true); setMsg(null);
    try {
      const filled = fields.filter((f) => (values[f.field] || "").trim());
      if (!filled.length) { setMsg({ tone: "warn", text: "Nothing entered." }); return; }
      for (const f of filled) await api.setKey({ provider, field: f.field, value: values[f.field].trim() });
      const t = await api.testKey(provider);
      setMsg(t.ok ? { tone: "ok", text: `Saved and verified: ${provider} answered an authenticated request.` }
        : { tone: "danger", text: `Saved, but the provider refused it: ${t.error_class || "failed"}${t.detail ? ` - ${t.detail}` : ""}` });
      setValues({}); onDone?.();
    } catch (e) {
      setMsg({ tone: "danger", text: /403|forbid|401/i.test(e.message) ? "Only a signed-in administrator can change credentials. The public evaluator view is read-only here - use Login, top right." : e.message });
    } finally { setBusy(false); }
  }
  async function test() {
    setBusy(true); setMsg(null);
    try {
      const t = await api.testKey(provider);
      setMsg(t.ok ? { tone: "ok", text: `${provider} answered an authenticated request${t.latency_ms ? ` in ${t.latency_ms} ms` : ""}.` }
        : { tone: "danger", text: `${t.error_class || "failed"}${t.detail ? ` - ${t.detail}` : ""}` });
      onDone?.();
    } catch (e) { setMsg({ tone: "danger", text: e.message }); } finally { setBusy(false); }
  }
  return (
    <div className="cat-keyform" data-testid={`keyform-${provider}`}>
      {site && <p className="cat-dim">{site.how}</p>}
      {fields.length === 0 && <p className="cat-dim">This provider takes no credentials: there is nothing to enter.</p>}
      <div className="cat-keygrid">
        {fields.map((f) => (
          <label key={f.field}>
            <span className="mono">{f.field}</span>
            <input type="password" autoComplete="off" placeholder={f.configured ? `set (${f.masked}) - type to replace` : "not set"}
              value={values[f.field] || ""} onChange={(e) => setValues((v) => ({ ...v, [f.field]: e.target.value }))} data-testid={`key-${f.field}`} />
            <i className="cat-dim">{f.configured ? `from ${f.source}` : "missing"}</i>
          </label>
        ))}
      </div>
      <div className="cat-keyacts">
        {fields.length > 0 && <button className="btn btn-sm btn-primary" onClick={save} disabled={busy} data-testid={`key-save-${provider}`}>Save and test</button>}
        <button className="btn btn-sm" onClick={test} disabled={busy} data-testid={`key-test-${provider}`}>Test now</button>
        {msg && <span className={`cat-keymsg cat-keymsg-${msg.tone}`} data-testid={`key-msg-${provider}`}>{msg.text}</span>}
      </div>
    </div>
  );
}

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
  const [tab, setTab] = useUrlTab(["providers", "models", "health"]);
  const { data: catalog, loading: l1, error: catalogError, reload: reloadCatalog } = useApi(() => api.catalog(), []);
  const { data: models } = useApi(() => api.models(), []);
  const { data: keys, reload: reloadKeys } = useApi(() => api.listKeys().catch(() => ({ keys: [] })), []);
  const [openKey, setOpenKey] = useState(null);
  const byName = Object.fromEntries((catalog?.providers ?? []).map((x) => [x.name, x]));
  const fieldsOf = (name) => (keys?.keys || []).filter((k) => k.provider === name);
  const { data: sys } = useApi(() => api.systemHealth(), [],
    { interval: 20000 });

  // The catalogue is the page; models and health fill in around it. The old
  // guard (`l1 && l2 && l3`) showed a spinner only while all three were still
  // loading, so the usual case -- one slow, two back -- rendered blank rows.
  if (l1 && !catalog) return <div className="page"><Spinner /></div>;
  if (catalogError && !catalog) {
    return (
      <div className="page">
        <DataState kind="error" title="The data catalogue did not load" error={catalogError} testid="catalog-error">
          <button className="btn btn-sm" onClick={reloadCatalog}>Retry</button>
        </DataState>
      </div>
    );
  }

  const vocab = catalog?.vocabulary ?? {};
  const credVocab = catalog?.credentials_vocabulary ?? {};

  return (
    <div className="page cat-page">
      <div className="cat-top">
        <Link className="btn btn-sm" to="/system/api-monitor" data-testid="to-api-monitor">
          Probe these providers →
        </Link>
      </div>
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
                  <th>falls back to</th><th>probe</th><th>credentials</th><th>coverage</th><th>access</th>
                </tr>
              </thead>
              <tbody>
                {(catalog?.providers ?? []).map((p) => (
                  <Fragment key={p.name}>
                  <tr
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
                    <td><Chain chain={p.chain} name={p.name} byName={byName} /></td>
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
                    <td className="cat-access">
                      {PROVIDER_SITE[p.name]
                        ? <a className="btn btn-sm" href={PROVIDER_SITE[p.name].url} target="_blank" rel="noreferrer noopener" data-testid={`site-${p.name}`}
                            title={PROVIDER_SITE[p.name].url}><ExternalLink size={11} /> {fieldsOf(p.name).length ? "Get a key" : "Provider site"}</a>
                        : <span className="cat-dim">on this host</span>}
                      {(fieldsOf(p.name).length > 0 || p.credentials !== "n_a") && (
                        <button className="btn btn-sm" onClick={() => setOpenKey(openKey === p.name ? null : p.name)} data-testid={`enter-key-${p.name}`}>
                          <KeyRound size={11} /> {p.credentials === "configured" ? "Change key" : "Enter key"}
                        </button>
                      )}
                    </td>
                  </tr>
                  {openKey === p.name && (
                    <tr className="cat-keyrow"><td colSpan={8}>
                      <KeyForm provider={p.name} fields={fieldsOf(p.name)} onDone={() => { reloadKeys(); reloadCatalog(); }} />
                    </td></tr>
                  )}
                  </Fragment>
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
