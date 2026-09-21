/* The left control panel of the workspace.
 *
 * Two variants of one panel (frames 03-05 vs 06-15):
 *
 *   SCENE ACQUISITION  before a scene is loaded: search the Sentinel-1
 *                      catalogue, choose an area, pick a product.
 *   SCENE ANALYSIS     once an investigation exists: what the scene is, which
 *                      AIS layers are on, run/replay controls and exports.
 *
 * Every select whose options the server does not filter on is applied
 * client-side and says so in its tooltip; every value shown for a loaded
 * scene is read from scene_meta.json. The panel never invents a product.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Calendar, ChevronRight, Download, FileText, Loader2, Menu, Pencil,
  Play, RotateCcw, Search, Square, Upload,
} from "lucide-react";

import { sourceBadge } from "./palette";
import { fmt } from "../../lib/api";
import { provenanceOf } from "../ui";
import { url } from "../../lib/urls";
import { sceneFact } from "../../lib/sceneName";

const MISSIONS = [
  { id: "S1", label: "Sentinel-1" },
  { id: "S2", label: "Sentinel-2 (not deployed)" },
];
const PRODUCTS = ["GRD", "SLC", "OCN"];
const POLS = [["", "Any"], ["VV+VH", "VV + VH"], ["VV", "VV"], ["VH", "VH"], ["HH", "HH"]];
const ORBITS = [["", "Both"], ["ASCENDING", "Ascending"], ["DESCENDING", "Descending"]];

function Head({ title, right }) {
  return (
    <div className="ctl-head">
      <span className="ctl-title">{title}</span>
      <span className="ctl-head-right">{right ?? <Menu size={15} />}</span>
    </div>
  );
}

function Label({ children }) {
  return <div className="ctl-label">{children}</div>;
}

function Toggle({ on, onChange, label, disabled, title, testid }) {
  return (
    <label className={`ctl-toggle ${disabled ? "off" : ""}`} title={title} data-testid={testid}
      onClick={() => !disabled && onChange(!on)}>
      <span className="ctl-toggle-label">{label}</span>
      <span className={`switch-track ${on && !disabled ? "on" : ""}`}><span className="switch-knob" /></span>
    </label>
  );
}

function CheckRow({ on, onChange, label, disabled, title, testid }) {
  return (
    <label className={`ctl-check ${disabled ? "off" : ""}`} title={title} data-testid={testid}>
      <input type="checkbox" checked={Boolean(on)} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

/* ------------------------------------------------------- acquisition ---- */

export function AcquisitionPanel({
  q, onQ, params, onParams, spatial, onSpatial, zones, aois,
  onSearch, searching, onClear, results, catalogue, selected, onSelect, searchError,
  onUploadGeojson, drawActive, canCreate,
}) {
  const p = params;
  const set = (k, v) => onParams({ ...p, [k]: v });
  const hits = results?.scenes || [];
  const s2 = p.mission === "S2";

  const matches = (row) => {
    if (p.polarisation && row.polarisation && !String(row.polarisation).replace(/\s|\+/g, "").includes(p.polarisation.replace("+", ""))) return false;
    if (p.orbit && row.orbit_direction && String(row.orbit_direction).toUpperCase() !== p.orbit) return false;
    if (q && !`${row.product_id || ""} ${row.label || ""} ${row.scene_id || ""}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  };

  return (
    <div className="ctl" data-testid="control-panel" data-mode="acquisition">
      <Head title="Scene Acquisition" />
      <Label>Search</Label>
      <div className="ctl-search">
        <input value={q} onChange={(e) => onQ(e.target.value)} placeholder="Search Sentinel-1 scenes…"
          data-testid="scene-search-input" />
        <Search size={15} />
      </div>

      <Label>Satellite mission</Label>
      <select value={p.mission} onChange={(e) => set("mission", e.target.value)} data-testid="param-mission">
        {MISSIONS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select>
      {s2 && (
        <div className="ctl-note warn">Sentinel-2 / EO is not deployed: the server answers 501 and no optical accuracy has been measured.</div>
      )}

      <Label>Date range</Label>
      <div className="ctl-dates">
        <span className="ctl-date"><input type="date" value={p.start} onChange={(e) => set("start", e.target.value)} data-testid="param-start" /><Calendar size={13} /></span>
        <span className="ctl-date"><input type="date" value={p.end} onChange={(e) => set("end", e.target.value)} data-testid="param-end" /><Calendar size={13} /></span>
      </div>

      <div className="ctl-sep" />
      <Head title="Scene Parameters" />
      <div className="ctl-grid">
        <span>Mission</span>
        <select value={p.mission} onChange={(e) => set("mission", e.target.value)}>
          {MISSIONS.map((m) => <option key={m.id} value={m.id}>{m.label.replace(" (not deployed)", "")}</option>)}
        </select>
        <span>Product</span>
        <select value={p.product} onChange={(e) => set("product", e.target.value)} data-testid="param-product">
          {PRODUCTS.map((x) => <option key={x} value={x}>{x}{x === "GRD" ? " (Ground Range Detected)" : ""}</option>)}
        </select>
        <span>Polarization</span>
        <select value={p.polarisation} onChange={(e) => set("polarisation", e.target.value)} data-testid="param-pol"
          title="Applied to the result list on this side; the catalogue search does not filter by polarisation.">
          {POLS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <span>Orbit Direction</span>
        <select value={p.orbit} onChange={(e) => set("orbit", e.target.value)} data-testid="param-orbit"
          title="Applied to the result list on this side; the catalogue search does not filter by orbit.">
          {ORBITS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>

      <div className="ctl-sep" />
      <Head title="Spatial Analysis" />
      <div className="ctl-radios">
        <label className={`ctl-radio ${spatial.mode === "draw" ? "on" : ""}`}>
          <input type="radio" name="spatial" checked={spatial.mode === "draw"} onChange={() => onSpatial({ ...spatial, mode: "draw" })} />
          <Pencil size={12} /> Draw AOI {spatial.mode === "draw" && <em>{drawActive ? "(click two corners)" : "(Current)"}</em>}
        </label>
        <label className={`ctl-radio ${spatial.mode === "zone" ? "on" : ""}`}>
          <input type="radio" name="spatial" checked={spatial.mode === "zone"} onChange={() => onSpatial({ ...spatial, mode: "zone" })} />
          Select from zone
        </label>
        {spatial.mode === "zone" && (
          <select value={spatial.zoneId || ""} onChange={(e) => onSpatial({ ...spatial, zoneId: e.target.value })} data-testid="spatial-zone">
            <option value="">— choose an operational zone —</option>
            {(zones || []).map((z) => <option key={z.id} value={z.id}>{z.name}{z.protected ? " (protected)" : ""}</option>)}
          </select>
        )}
        <label className={`ctl-radio ${spatial.mode === "upload" ? "on" : ""}`}>
          <input type="radio" name="spatial" checked={spatial.mode === "upload"} onChange={() => onSpatial({ ...spatial, mode: "upload" })} />
          <Upload size={12} /> Upload GeoJSON
        </label>
        {spatial.mode === "upload" && (
          <input type="file" accept=".json,.geojson,application/geo+json,application/json" className="ctl-file"
            onChange={(e) => e.target.files?.[0] && onUploadGeojson(e.target.files[0])} data-testid="spatial-upload" />
        )}
        <label className={`ctl-radio ${spatial.mode === "aoi" ? "on" : ""}`}>
          <input type="radio" name="spatial" checked={spatial.mode === "aoi"} onChange={() => onSpatial({ ...spatial, mode: "aoi" })} />
          Use predefined area
        </label>
        {spatial.mode === "aoi" && (
          <select value={spatial.aoiId || ""} onChange={(e) => onSpatial({ ...spatial, aoiId: e.target.value })} data-testid="spatial-aoi">
            <option value="">— registered AOI —</option>
            {(aois || []).map((a) => <option key={a.id} value={a.id}>{a.name}{a.has_real_ais ? "" : " · AIS synthetic here"}</option>)}
          </select>
        )}
      </div>
      {spatial.bbox && (
        <div className="ctl-aoi mono" data-testid="aoi-readout">
          <b>AOI SELECTED</b>
          <span>Lat: {spatial.bbox[1].toFixed(2)}° – {spatial.bbox[3].toFixed(2)}°</span>
          <span>Lon: {spatial.bbox[0].toFixed(2)}° – {spatial.bbox[2].toFixed(2)}°</span>
          <span>Center: {((spatial.bbox[1] + spatial.bbox[3]) / 2).toFixed(2)}° N, {((spatial.bbox[0] + spatial.bbox[2]) / 2).toFixed(2)}° E</span>
        </div>
      )}

      <div className="ctl-actions">
        <button className="ctl-btn primary" onClick={onSearch} disabled={searching || s2 || !spatial.bbox}
          title={!spatial.bbox ? "Choose an area first" : "Search the CDSE → ASF → local-cache chain"}
          data-testid="search-scenes">
          {searching ? <Loader2 size={14} className="ws-spin" /> : null} Search scenes
        </button>
        <button className="ctl-btn" onClick={onClear} data-testid="clear-search">Clear</button>
      </div>
      {searchError && <div className="ctl-note danger" data-testid="search-error">{searchError}</div>}

      {/* Results: catalogue hits first (with provider), then the scenes this
          host can actually run. A hit that is not on disk is shown and said. */}
      {results && (
        <div className="ctl-results" data-testid="scene-results">
          <div className="ctl-results-head">
            <span>{results.total} result{results.total === 1 ? "" : "s"}</span>
            <span className="mono dim">{results.provider || "no provider answered"} · {results.elapsed_s}s</span>
          </div>
          {results.attempts?.filter((a) => !a.ok && !a.inferred).map((a) => (
            <div key={a.provider} className="ctl-note warn">{a.provider}: {a.error_class || a.result} {a.detail ? `— ${a.detail}` : ""}</div>
          ))}
          {hits.filter(matches).map((h) => (
            <button key={h.product_id} className={`ctl-scene ${selected?.key === `hit:${h.product_id}` ? "on" : ""}`}
              onClick={() => onSelect({ key: `hit:${h.product_id}`, kind: "hit", ...h })} data-testid="scene-hit">
              <span className="ctl-scene-id mono">{h.product_id}</span>
              <span className="ctl-scene-meta">{fmt.utc(h.acquired_utc)} · {h.orbit_direction || "—"} · {h.polarisation || "—"}</span>
              <span className={`badge ${h.cached_path ? "badge-ok" : "badge-neutral"}`}>{h.cached_path ? "cached" : "not downloaded"}</span>
            </button>
          ))}
          {!hits.length && <div className="ctl-note">No catalogue scenes over this area in the window.</div>}
        </div>
      )}
      <div className="ctl-results" data-testid="scene-catalogue">
        <div className="ctl-results-head"><span>Cached scenes on this host</span><span className="dim">{(catalogue || []).filter((c) => c.available).length}</span></div>
        {(catalogue || []).filter((c) => c.available && matches(c)).map((c) => (
          <button key={c.id} className={`ctl-scene ${selected?.key === `local:${c.id}` ? "on" : ""}`}
            onClick={() => onSelect({ key: `local:${c.id}`, kind: "local", ...c })} data-testid="scene-local">
            <span className="ctl-scene-id">{c.label}</span>
            <span className="ctl-scene-meta mono">{c.scene_id?.slice(0, 32)} · {fmt.utc(c.acquired_utc)}</span>
            <span className={`badge badge-${c.provenance === "mock" ? "mock" : c.provenance === "corpus_train" ? "warn" : "ok"}`}>{c.provenance === "mock" ? "MOCK" : c.provenance === "corpus_train" ? "TRAINING IMAGE" : "REAL"}</span>
          </button>
        ))}
      </div>
      {!canCreate && (
        <div className="ctl-note" data-testid="role-note">Your role can view scenes; opening an investigation needs investigator or analyst.</div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------- analysis ---- */

export function AnalysisPanel({
  sceneMeta, runId, show, onShow, aisSource, onFlyTo, onSearchArea,
  running, job, cancelling, replayMode, onReplayMode, onRun, onCancel, onRerun,
  canRun, status, onClearAll, invs, invId, onPickInv, onNew, liveAis,
}) {
  const [area, setArea] = useState("");
  const sm = sceneMeta || {};
  const prov = sm.source ? sourceBadge(sm.source) : null;
  const ais = provenanceOf(aisSource);
  const canSearch = area.trim().length > 0;
  return (
    <div className="ctl" data-testid="control-panel" data-mode="analysis">
      <Head title="Scene Analysis" />
      {(invs?.length > 1 || !runId) && (
        <>
          <Label>Investigation</Label>
          <select value={invId ?? ""} onChange={(e) => onPickInv(e.target.value)} data-testid="inv-select">
            {(invs || []).map((x) => <option key={x.id} value={x.id}>{x.name} · {x.id}</option>)}
          </select>
        </>
      )}

      <Label>Satellite data</Label>
      <div className="ctl-static">
        <span>{sceneFact(sm, "mission")} SAR</span>
        {prov && <span className={`badge badge-${prov.tone}`} data-testid="scene-source">{prov.label}</span>}
      </div>

      <div className="ctl-sep" />
      <div className="ctl-row-title">
        <span className="ctl-title sm">AIS Trajectories</span>
        <span className={`switch-track ${show.vessels ? "on" : ""}`} onClick={() => onShow("vessels", !show.vessels)}
          data-testid="ais-master-toggle"><span className="switch-knob" /></span>
      </div>
      <CheckRow on={show.vessels && show.ranked !== false} disabled={!show.vessels}
        onChange={(v) => onShow("ranked", v)} label="Show vessel tracks (candidates)" testid="ais-ranked" />
      <CheckRow on={show.vessels && show.background !== false} disabled={!show.vessels}
        onChange={(v) => onShow("background", v)} label="Show historical tracks (all AIS in window)" testid="ais-background" />
      <CheckRow on={show.vessels && show.excluded !== false} disabled={!show.vessels}
        onChange={(v) => onShow("excluded", v)} label="Show excluded vessels (dimmed)" testid="ais-excluded" />
      <div className="ctl-ais-src" data-testid="ais-source-line">
        <span className="dim">AIS source</span>
        <span className={`badge badge-${ais.tone}`}>{ais.label}</span>
        {liveAis && <span className={`badge badge-${liveAis.tone}`} title={liveAis.note || ""}>LIVE {liveAis.text}</span>}
      </div>
      <button className="ctl-link" onClick={() => onShow("wind", !show.wind)} data-testid="ais-more">
        <ChevronRight size={13} /> {show.wind ? "Hide" : "Show"} derived wind vectors
      </button>

      <div className="ctl-sep" />
      <Head title="Scene Parameters" />
      <div className="ctl-grid ro">
        <span>Mission</span><div className="ctl-ro">{sceneFact(sm, "mission")}</div>
        <span>Product</span><div className="ctl-ro">{sceneFact(sm, "product")}</div>
        <span>Polarization</span><div className="ctl-ro mono">{sm.polarisation || "—"}</div>
        <span>Acquisition</span><div className="ctl-ro mono">{sm.acquired_utc ? fmt.utc(sm.acquired_utc) : "—"}</div>
        <span>Resolution</span><div className="ctl-ro mono">{sm.pixel_spacing_m ? `${sm.pixel_spacing_m} m` : "—"}</div>
      </div>
      <Link className="ctl-link" to="/system/models"><ChevronRight size={13} /> Detector &amp; references</Link>

      <div className="ctl-sep" />
      <Head title="Spatial Analysis" />
      <form className="ctl-search" onSubmit={(e) => { e.preventDefault(); if (canSearch) onSearchArea(area); }}>
        <input value={area} onChange={(e) => setArea(e.target.value)} placeholder="Search area / coordinates"
          data-testid="area-search" />
        <button type="submit" className="ctl-search-go" disabled={!canSearch}><Search size={15} /></button>
      </form>
      <button className="ctl-link" onClick={() => onFlyTo("scene")}><ChevronRight size={13} /> Fit the scene</button>

      <div className="ctl-sep" />
      <Head title="Run" />
      <div className="ctl-run">
        {running && job?.id && (
          <button className="ctl-btn" onClick={onCancel} disabled={cancelling} data-testid="cancel-btn"
            title="Stops at the next stage boundary. The run will not be sealed.">
            <Square size={12} /> {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
        {!running && (job?.run_id || status?.run_id) && (
          <button className="ctl-btn icon" onClick={onRerun} title="Run again with the same inputs, under a new run id"
            data-testid="rerun-btn"><RotateCcw size={13} /></button>
        )}
        <button className="ctl-btn primary grow" onClick={onRun} disabled={running || !canRun || !invId}
          title={!canRun ? "Your role cannot start or replay runs" : replayMode ? "Render the latest complete run of this scene from disk" : "Execute the full pipeline"}
          data-testid="run-btn">
          {running ? <Loader2 size={14} className="ws-spin" /> : <Play size={13} />}
          {running ? "Running…" : replayMode ? "Replay analysis" : "Analyse scene"}
        </button>
      </div>
      <Toggle on={replayMode} onChange={onReplayMode} label="Replay mode — pre-computed files, no execution" testid="replay-toggle" />
      <div className="ctl-run">
        {runId ? (
          <a className="ctl-btn grow" href={`/api/runs/${runId}/export`} download data-testid="export-bundle"
            title="Download slick, origin cloud, forecast, vessels and suspects as a GeoJSON/contract bundle">
            <Download size={12} /> Export bundle
          </a>
        ) : (
          <button className="ctl-btn grow" disabled data-testid="export-bundle"><Download size={12} /> Export bundle</button>
        )}
        <Link className={`ctl-btn grow ${runId ? "" : "off"}`} to={runId ? url.reportPrint(runId) : "#"} target="_blank"
          data-testid="export-report" title="Printable incident report">
          <FileText size={12} /> Report
        </Link>
      </div>
      <div className="ctl-actions">
        <button className="ctl-btn" onClick={onClearAll} data-testid="clear-all">Clear all</button>
        <button className="ctl-btn" onClick={onNew} data-testid="new-investigation-btn">New investigation</button>
      </div>
    </div>
  );
}
