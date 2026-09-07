/* The New Investigation wizard.
 *
 * Replaces a button that created an investigation against the demo mock and
 * called it "New investigation" — which meant every case in the list pointed at
 * the same fabricated scene, and the only way to run a real one was the CLI.
 *
 * Four steps, in the order the decisions actually depend on each other:
 *
 *   1. AREA    an existing AOI, or a GeoJSON polygon pasted/uploaded. Validated
 *              server-side (at sea, sane span, lon/lat order) — the refusal
 *              message is shown verbatim rather than being reduced to "invalid".
 *   2. WINDOW  the acquisition window to search. Reversed windows are refused.
 *   3. SCENE   search results from /api/scenes/search. A catalogue hit is NOT a
 *              downloaded scene, so anything not in the local cache is shown as
 *              such and cannot be selected — offering "run this" for bytes
 *              nobody has is how a demo dies on stage.
 *   4. REVIEW  exactly what will be created, then launch.
 *
 * Nothing here invents a value. Every field the review step shows came from a
 * server response or from something the operator typed.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Loader2, Search, X } from "lucide-react";

import { api, fmt } from "../lib/api";

const STEPS = ["Area", "Window", "Scene", "Review"];

function isoDaysAgo(days) {
  const d = new Date(Date.now() - days * 86400000);
  return d.toISOString().slice(0, 16);
}

export default function NewInvestigation({ onClose, onCreated }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [aois, setAois] = useState([]);
  const [aoiId, setAoiId] = useState("");
  const [geojsonText, setGeojsonText] = useState("");
  const [areaError, setAreaError] = useState(null);
  const [start, setStart] = useState(isoDaysAgo(30));
  const [end, setEnd] = useState(isoDaysAgo(0));
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState(null);
  const [searchError, setSearchError] = useState(null);
  const [scene, setScene] = useState(null);
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState(null);

  useEffect(() => {
    api.listAois().then(setAois).catch(() => setAois([]));
  }, []);

  const selectedAoi = useMemo(
    () => aois.find((a) => a.id === aoiId) || null, [aois, aoiId]);

  const drawnGeometry = useMemo(() => {
    if (!geojsonText.trim()) return null;
    try {
      return JSON.parse(geojsonText);
    } catch {
      return "invalid";
    }
  }, [geojsonText]);

  const bbox = useMemo(() => {
    if (selectedAoi) return selectedAoi.bbox;
    return null;
  }, [selectedAoi]);

  const windowValid = useMemo(
    () => Boolean(start && end && new Date(end) > new Date(start)), [start, end]);

  const areaChosen = Boolean(selectedAoi) || (drawnGeometry && drawnGeometry !== "invalid");

  /* ------------------------------------------------------------- search -- */
  const runSearch = useCallback(async () => {
    if (!bbox) {
      setSearchError(
        "Scene search needs a bounding box. Register the drawn area as an AOI " +
        "first, or pick an existing AOI — searching by an unsaved polygon is " +
        "not wired up.");
      return;
    }
    setSearching(true); setSearchError(null); setResults(null);
    try {
      const body = await api.searchScenes({
        bbox: bbox.join(","),
        start: new Date(start).toISOString(),
        end: new Date(end).toISOString(),
        top: 20,
      });
      setResults(body);
    } catch (e) {
      setSearchError(e.message || "search failed");
    } finally {
      setSearching(false);
    }
  }, [bbox, start, end]);

  /* ------------------------------------------------------------- create -- */
  async function create() {
    setBusy(true); setCreateError(null);
    try {
      const body = { name: name.trim() || "Untitled investigation" };
      if (selectedAoi) body.aoi_id = selectedAoi.id;
      else if (drawnGeometry && drawnGeometry !== "invalid") body.aoi = drawnGeometry;
      if (windowValid) {
        body.window_start_utc = new Date(start).toISOString();
        body.window_end_utc = new Date(end).toISOString();
      }
      if (scene?.cached_path) body.scene_product_id = scene.product_id;

      const created = await api.createInvestigation(body);
      onCreated?.(created);
      onClose?.();
    } catch (e) {
      setCreateError(e.message || "could not create the investigation");
    } finally {
      setBusy(false);
    }
  }

  const canAdvance = [areaChosen, windowValid, true, true][step];

  return (
    <div className="wizard-backdrop" role="dialog" aria-modal="true"
      aria-label="New investigation" data-testid="new-investigation">
      <div className="wizard">
        <header className="wizard-head">
          <h2>New investigation</h2>
          <button className="btn btn-icon" onClick={onClose} aria-label="Close">
            <X size={15} />
          </button>
        </header>

        <ol className="wizard-steps">
          {STEPS.map((label, i) => (
            <li key={label} className={i === step ? "on" : i < step ? "done" : ""}>
              <span className="dot">{i < step ? <Check size={11} /> : i + 1}</span>
              {label}
            </li>
          ))}
        </ol>

        <div className="wizard-body">
          {/* ------------------------------------------------------ area -- */}
          {step === 0 && (
            <>
              <label className="field">
                <span>Name</span>
                <input value={name} onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Gulf of Mexico, 8 Jan"
                  data-testid="wizard-name" />
              </label>

              <label className="field">
                <span>Registered AOI</span>
                <select value={aoiId} onChange={(e) => { setAoiId(e.target.value); setGeojsonText(""); }}
                  data-testid="wizard-aoi">
                  <option value="">— none —</option>
                  {aois.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} {a.enabled ? "" : "(disabled)"}
                    </option>
                  ))}
                </select>
              </label>

              <p className="wizard-or">or paste a GeoJSON polygon</p>
              <textarea rows={5} value={geojsonText} spellCheck={false}
                onChange={(e) => { setGeojsonText(e.target.value); setAoiId(""); }}
                placeholder='{"type":"Polygon","coordinates":[[[lon,lat], …]]}'
                data-testid="wizard-geojson" />
              {drawnGeometry === "invalid" && (
                <p className="wizard-err">That is not valid JSON.</p>
              )}
              {areaError && <p className="wizard-err">{areaError}</p>}
              {selectedAoi && (
                <dl className="wizard-facts">
                  <dt>bbox</dt><dd>{selectedAoi.bbox.map((v) => v.toFixed(3)).join(", ")}</dd>
                  <dt>AIS</dt>
                  <dd>{selectedAoi.has_real_ais
                    ? `real (${selectedAoi.ais_region})`
                    : "no public bulk AIS here — traffic will be SYNTHETIC"}</dd>
                </dl>
              )}
            </>
          )}

          {/* ---------------------------------------------------- window -- */}
          {step === 1 && (
            <>
              <label className="field">
                <span>From (UTC)</span>
                <input type="datetime-local" value={start}
                  onChange={(e) => setStart(e.target.value)} data-testid="wizard-start" />
              </label>
              <label className="field">
                <span>To (UTC)</span>
                <input type="datetime-local" value={end}
                  onChange={(e) => setEnd(e.target.value)} data-testid="wizard-end" />
              </label>
              {!windowValid && (
                <p className="wizard-err" data-testid="wizard-window-error">
                  The end must be after the start. A reversed window searches
                  nothing and would report it as “no scenes found”.
                </p>
              )}
            </>
          )}

          {/* ----------------------------------------------------- scene -- */}
          {step === 2 && (
            <>
              <button className="btn" onClick={runSearch} disabled={searching || !bbox}
                data-testid="wizard-search">
                {searching ? <Loader2 size={13} className="spin" /> : <Search size={13} />}
                {searching ? "Searching…" : "Search scenes"}
              </button>

              {searchError && <p className="wizard-err">{searchError}</p>}

              {results && (
                <>
                  <p className="wizard-note">
                    {results.total} result(s) from {results.provider || "no provider"}.
                    A catalogue hit is not a downloaded scene — only cached
                    scenes can be run.
                  </p>
                  <ul className="wizard-scenes" data-testid="wizard-results">
                    {results.scenes.map((s) => {
                      const cached = Boolean(s.cached_path);
                      return (
                        <li key={s.product_id}
                          className={scene?.product_id === s.product_id ? "on" : ""}>
                          <button disabled={!cached}
                            onClick={() => cached && setScene(s)}
                            title={cached ? "" : "not downloaded"}>
                            <code>{s.product_id.slice(0, 46)}</code>
                            <span>{fmt.utc(s.acquired_utc)}</span>
                            <em className={cached ? "ok" : "warn"}>
                              {cached ? "cached" : "not downloaded"}
                            </em>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  {!results.scenes.some((s) => s.cached_path) && (
                    <p className="wizard-note">
                      <AlertTriangle size={12} /> None of these are held locally.
                      You can still create the investigation and attach a scene
                      later.
                    </p>
                  )}
                </>
              )}
            </>
          )}

          {/* ---------------------------------------------------- review -- */}
          {step === 3 && (
            <dl className="wizard-facts" data-testid="wizard-review">
              <dt>Name</dt><dd>{name.trim() || "Untitled investigation"}</dd>
              <dt>Area</dt>
              <dd>{selectedAoi ? `${selectedAoi.name} (${selectedAoi.id})`
                : drawnGeometry && drawnGeometry !== "invalid" ? "drawn polygon"
                  : "none"}</dd>
              <dt>Window</dt>
              <dd>{windowValid
                ? `${new Date(start).toISOString()} → ${new Date(end).toISOString()}`
                : "not set"}</dd>
              <dt>Scene</dt>
              <dd>{scene ? scene.product_id : "none selected — attach one later"}</dd>
              {createError && (
                <>
                  <dt>Refused</dt>
                  <dd className="wizard-err">{createError}</dd>
                </>
              )}
            </dl>
          )}
        </div>

        <footer className="wizard-foot">
          <button className="btn" onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}>
            <ChevronLeft size={13} /> Back
          </button>
          {step < STEPS.length - 1 ? (
            <button className="btn btn-primary" disabled={!canAdvance}
              onClick={() => setStep((s) => s + 1)} data-testid="wizard-next">
              Next <ChevronRight size={13} />
            </button>
          ) : (
            <button className="btn btn-primary" onClick={create} disabled={busy}
              data-testid="wizard-create">
              {busy ? <Loader2 size={13} className="spin" /> : <Check size={13} />}
              Create
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
