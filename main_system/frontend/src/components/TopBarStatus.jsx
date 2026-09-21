/* The top bar's honesty strip: the run in context, what produced it, and the
 * time in ZULU.
 *
 * The chips describe **the run being looked at**, never global configuration
 * (UX spec §1.1.4). That distinction is the whole point: a system whose
 * providers are all healthy can still be showing you a run whose AIS was
 * synthetic, and a green top bar over that run is a lie told by omission.
 *
 * Every chip is read out of `GET /api/runs/{id}` and its sealed manifest.
 * Nothing here is derived from what the UI hopes was true: a manifest that
 * does not record a fact produces a chip saying it was not recorded, never a
 * cheerful default. With no run open there is one ghost chip and nothing else.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { X } from "lucide-react";

import { api, useApi } from "../lib/api";
import { useShell } from "../lib/shell";
import { url } from "../lib/urls";

const IST_OFFSET_MS = 5.5 * 3600 * 1000;

/** `HH:MM:SSZ`, ticking. Click toggles a second line in IST.
 *
 *  Both readings are computed from the epoch rather than from the browser's
 *  locale: a workstation left on the wrong timezone would otherwise print a
 *  confident local time under a Z suffix, and every timestamp in this system
 *  — scene acquisition, origin window, AIS fixes — is UTC. */
export function ZuluClock() {
  const [now, setNow] = useState(() => new Date());
  const [showIst, setShowIst] = useState(false);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const zulu = `${now.toISOString().slice(11, 19)}Z`;
  const ist = `${new Date(now.getTime() + IST_OFFSET_MS).toISOString().slice(11, 19)} IST`;

  return (
    <button className="zulu" onClick={() => setShowIst((v) => !v)}
      title="System time in UTC (ZULU). Click to show IST as well."
      data-testid="zulu-clock">
      <span className="zulu-utc mono" data-testid="zulu-utc">{zulu}</span>
      {showIst && <span className="zulu-ist mono" data-testid="zulu-ist">{ist}</span>}
    </button>
  );
}

function Chip({ tone, label, value, title, testid }) {
  return (
    <span className={`pchip pchip-${tone}`} title={title} data-testid={testid}>
      <span className="pchip-k">{label}</span>
      <span className="pchip-v">{value}</span>
    </span>
  );
}

/* Stage source -> chip tone and word. Same vocabulary as everywhere else:
 * REAL / SYNTHETIC / CACHED / FALLBACK / NOT DEPLOYED / EXPERIMENTAL. */
const SOURCE_TONE = { real: "real", cache: "cache", cached: "cache",
                      fallback: "fallback", mock: "mock", synthetic: "synth" };

function sourceWord(source) {
  const s = String(source || "").toLowerCase();
  if (s === "cached") return "CACHE";
  return s ? s.toUpperCase() : "UNRECORDED";
}

/** The chip row. Reflects the run in context; collapses to one ghost chip
 *  when there is none. */
export function ProvenanceChips() {
  const { runId, clearRun } = useShell();
  const { data: run, error } = useApi(
    () => (runId ? api.getRun(runId) : Promise.resolve(null)), [runId]);

  if (!runId) {
    return (
      <span className="pchips" data-testid="provenance-chips">
        <Chip tone="ghost" label="context" value="NO RUN"
          testid="chip-no-run"
          title="No run is open. Open an investigation to see what produced it." />
      </span>
    );
  }

  if (error) {
    return (
      <span className="pchips" data-testid="provenance-chips">
        <Chip tone="mock" label="run" value="UNREADABLE" testid="chip-run-error"
          title={`${runId} could not be read: ${error.message}. No provenance is claimed.`} />
      </span>
    );
  }

  if (!run) {
    return (
      <span className="pchips" data-testid="provenance-chips">
        <Chip tone="ghost" label="run" value="LOADING" testid="chip-run-loading"
          title={`Reading ${runId}`} />
      </span>
    );
  }

  const manifest = run.manifest;
  const stages = manifest?.stages || [];
  const notReal = stages.filter((s) => (s.source || "").toLowerCase() !== "real");
  const bySource = {};
  for (const s of notReal) {
    const key = (s.source || "unrecorded").toLowerCase();
    (bySource[key] ||= []).push(s);
  }
  const ais = manifest?.ais;
  const digest = manifest?.artefact_digest;

  return (
    <span className="pchips" data-testid="provenance-chips">
      <Link className="pchip pchip-ctx mono" to={url.workspace({ run: run.run_id })}
        title={`Run in context: ${run.run_id}`} data-testid="chip-run">
        {run.incident_id ? `${run.incident_id} ▸ ` : ""}{run.run_id}
      </Link>

      {/* AIS is the chip that matters most. Synthetic AIS is loud by design;
        * an unrecorded provenance says so rather than defaulting to real. */}
      {ais
        ? (
          <Chip
            tone={ais.data_source === "real" ? "real" : "synth"}
            label="AIS"
            value={ais.data_source === "real" ? "REAL" : "SYNTHETIC"}
            testid="chip-ais"
            title={ais.detail
              || (ais.data_source === "real"
                ? "A real AIS archive supplied the vessel tracks for this run."
                : "Synthetic AIS. Every vessel below is generated, not observed.")} />
        )
        : (
          <Chip tone="ghost" label="AIS" value="UNRECORDED" testid="chip-ais"
            title="This run's manifest records no AIS provenance. It is not being claimed as real." />
        )}

      {/* The count, then a chip for every source that was not real. A count
        * on its own would let one mocked stage hide inside "4/5". */}
      {manifest
        ? (
          <Chip tone={notReal.length ? "fallback" : "real"} label="stages"
            value={`${run.stages_real}/${run.stages_total} REAL`}
            testid="chip-stages"
            title={stages.map((s) => `${s.stage}: ${sourceWord(s.source)}`).join(" · ")} />
        )
        : (
          <Chip tone="ghost" label="stages" value="NO MANIFEST" testid="chip-stages"
            title="No sealed manifest for this run, so no stage provenance can be shown." />
        )}

      {/* Grouped by source, one chip each, matching the spec's four-slot chip
        * row (§1.1.4). A chip per stage overflowed the bar on a run with three
        * synthetic stages, and a provenance chip pushed off the right edge is
        * exactly the defect §8.7 names. The word stays loud; which stages it
        * covers is in the chip's own tooltip. */}
      {Object.entries(bySource).map(([source, group]) => (
        <Chip key={source} tone={SOURCE_TONE[source] || "mock"}
          label={sourceWord(source)}
          value={group.length === 1 ? group[0].stage : `${group.length} stages`}
          testid={`chip-source-${source}`}
          title={group.map((s) => `${s.stage}${s.detail ? `: ${s.detail}` : ""}`)
            .join(" · ")} />
      ))}

      {/* How this index row came to exist. A row rebuilt from a sealed
        * manifest after the fact is not the same kind of record as one the API
        * watched being made, and the difference is exactly the sort of thing
        * that should be visible rather than inferred. The artefacts are the
        * evidence either way -- this describes the index entry, not the run. */}
      {run.registry_source === "reconciled" && (
        <Chip tone="ghost" label="index" value="RECONCILED"
          testid="chip-registry-source"
          title="This registry row was rebuilt from the run's sealed manifest after the run finished, not recorded by the API as it happened. The artefacts and their digest are unchanged; fields the API alone could have known (investigation, incident) are absent rather than guessed." />
      )}

      {run.detect_engine && (
        <Chip tone="neutral" label="engine" value={run.detect_engine}
          testid="chip-engine"
          title={(manifest?.models || [])
            .map((m) => `${m.kind}: ${m.name}`).join(" · ")
            || "Detection engine recorded on the run."} />
      )}

      {digest && (
        <button className="pchip pchip-hash mono" data-testid="chip-digest"
          title={`Artefact digest ${digest} — click to copy`}
          onClick={() => navigator.clipboard?.writeText(digest)}>
          ⌗ {String(digest).slice(0, 8)}
        </button>
      )}

      <button className="pchip pchip-close" onClick={clearRun}
        title="Close the run context" data-testid="chip-clear" aria-label="Close run context">
        <X size={11} />
      </button>
    </span>
  );
}
