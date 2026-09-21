/* The system status bar. Every word on it is a measured state from the
 * backend -- provider probes, the AIS stream's own status, the database
 * check -- rendered in the server's vocabulary. Nothing here is hardcoded to
 * "ONLINE": a provider whose functional probe has not succeeded says
 * REACHABLE, UNCONFIGURED, DEGRADED or FAILED, because those are different
 * problems with different fixes. */

import { Link } from "react-router-dom";

import { statusTone } from "../../lib/api";
import { Dot } from "../ui";

import pkg from "../../../package.json";
import { url } from "../../lib/urls";

/* Short display names for the providers the pipeline actually depends on. */
const SHORT = {
  CDSE: "Sentinel-1 · CDSE", ASF: "Sentinel-1 · ASF", CMEMS: "CMEMS", ERA5: "ERA5",
  "Open-Meteo": "Open-Meteo", MarineCadastre: "MarineCadastre", DMA: "DMA AIS",
  AISStream: "AISStream",
};

function aisWord(stream) {
  if (!stream) return null;
  if (stream.state === "not_configured") return { text: "NOT CONFIGURED", tone: "neutral" };
  if (stream.functionally_working) return { text: "LIVE", tone: "ok" };
  if (stream.connected) return { text: "CONNECTED · NO DATA", tone: "warn" };
  if (stream.state === "stopped") return { text: "STOPPED", tone: "neutral" };
  return { text: String(stream.state || "unknown").toUpperCase(), tone: "danger" };
}

export default function StatusBar({ status, ais, health, workers }) {
  const providers = status?.providers || [];
  const working = providers.filter((p) => p.status === "WORKING").length;
  const stream = aisWord(ais?.stream);
  const db = health?.database;
  const alive = (workers?.workers || []).filter((w) => w.alive === true).length;
  const enabled = (workers?.workers || []).filter((w) => w.enabled).length;

  return (
    <footer className="sbar" data-testid="status-bar">
      <span className="sbar-brand">OCEANTRACE v{pkg.version}</span>
      <div className="sbar-items">
        {providers
          // The stream provider is reported from its own status below; the
          // probe row for it would say REACHABLE, which is a weaker claim.
          .filter((p) => p.provider !== "AISStream")
          .map((p) => {
            const tone = statusTone(p.status);
            return (
              <Link key={p.provider} className="sbar-item" to="/monitoring"
                title={`${p.provider}: ${p.status}${p.last_success_utc ? ` · last success ${p.last_success_utc}` : ""}${p.last_error_class ? ` · ${p.last_error_class}` : ""}`}
                data-testid={`sbar-${p.provider}`}>
                <Dot tone={tone} pulsing={p.status === "WORKING"} />
                <span className="sbar-k">{SHORT[p.provider] || p.provider}</span>
                <span className={`sbar-v ${tone}`}>{p.status}</span>
              </Link>
            );
          })}
        {stream && (
          <Link className="sbar-item" to={url.map()} title={ais?.stream?.note || "Live AIS stream"}
            data-testid="sbar-ais-stream">
            <Dot tone={stream.tone} pulsing={stream.tone === "ok"} />
            <span className="sbar-k">AISStream</span>
            <span className={`sbar-v ${stream.tone}`}>{stream.text}</span>
          </Link>
        )}
        {health && (
          <Link className="sbar-item" to="/system" data-testid="sbar-system"
            title={db?.ok ? `database ok · ${db.runs} runs · ${db.url_scheme}` : db?.error || "database check failed"}>
            <Dot tone={db?.ok ? "ok" : "danger"} pulsing={db?.ok} />
            <span className="sbar-k">System</span>
            <span className={`sbar-v ${db?.ok ? "ok" : "danger"}`}>
              {db?.ok ? "OPERATIONAL" : "DEGRADED"}
            </span>
          </Link>
        )}
        {workers && (
          <Link className="sbar-item" to="/system/health?tab=runtime" data-testid="sbar-workers"
            title={workers.model_note}>
            <span className="sbar-k">Workers</span>
            <span className="sbar-v neutral">{alive}/{enabled} ALIVE</span>
          </Link>
        )}
      </div>
      <div className="sbar-right">
        {providers.length > 0 && (
          <span title={`${working} verified by an authenticated or functional request; the rest of the deployed providers answered a reachability probe. Adapters that are not deployed are not counted.`}>
            {providers.filter((p) => p.status === "WORKING" || p.status === "REACHABLE").length}/{providers.filter((p) => p.status !== "NOT_DEPLOYED").length} PROVIDERS UP · {working} VERIFIED
          </span>
        )}
        <span className="sbar-tagline">Maritime intelligence platform</span>
      </div>
    </footer>
  );
}
