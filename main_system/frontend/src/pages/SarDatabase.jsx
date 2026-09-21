/* SAR Image Database -- the scenes this host holds, as scenes.
 *
 * A SAR scene is a raster AND the facts that make it usable: where, when,
 * which sensor and polarisation, who supplied it, and -- the one a picture
 * viewer never asks -- on what BASIS the where and when are known. Two of
 * the scenes here are Sentinel-1 products whose time and georeference are
 * measured. Most are chips from a research corpus that ships neither: their
 * positions and times were ASSIGNED so the pipeline could be exercised over
 * different seas. Every card and the detail pane say which, in the server's
 * own words (`geo_basis_note`), because the hindcast and the AIS window are
 * built on those two values.
 *
 * Analysis is not a second engine. "Analyse" opens an investigation on the
 * scene through the existing API and starts the existing pipeline; this page
 * shows the detector's and the characteriser's artefacts as they land, and
 * FIND VESSELS hands the same run to the Investigation workspace, which plays
 * it from the globe onward.
 *
 * An upload with missing metadata is answered METADATA REQUIRED and a form;
 * nothing is filled in on the user's behalf.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, CheckCircle2, Database, Info, Loader2, MapPin, Radar, Search, Ship, Upload, X,
} from "lucide-react";

import { Badge, DataState, Notice, PageHeader } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { guessPlace } from "../lib/replay";
import "../sardb.css";
import { url } from "../lib/urls";

const TONE = { REAL: "ok", REFERENCE: "accent", SYNTHETIC: "mock", UPLOADED: "warn", UNVERIFIED: "neutral" };
const BASIS_TONE = { measured: "ok", raster: "ok", assigned: "warn", user_supplied: "warn", synthetic: "mock" };
const BASIS_WORD = { measured: "MEASURED", raster: "FROM RASTER", assigned: "ASSIGNED", user_supplied: "USER-SUPPLIED", synthetic: "SYNTHETIC" };
const DONE = ["ok", "fallback", "mock"];

const lat = (v) => (v == null ? "—" : `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "N" : "S"}`);
const lon = (v) => (v == null ? "—" : `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "E" : "W"}`);
const day = (s) => (s ? s.slice(0, 10) : "—");
const clock = (s) => (s ? `${s.slice(11, 19)} UTC` : "—");
const place = (r) => (r?.geo_basis === "synthetic" ? "No place (synthetic)" : r?.center ? guessPlace(r.center) : "Location not recorded");

function Fact({ k, v, title, children }) {
  return <div className="sd-fact" title={title}><span>{k}</span><b className="mono">{children ?? (v == null || v === "" ? "—" : v)}</b></div>;
}

/* ------------------------------------------------------------------ card -- */

function SceneCard({ r, on, onPick }) {
  return (
    <button className={`sd-card ${on ? "on" : ""} ${r.available ? "" : "off"}`} onClick={() => onPick(r)} data-testid="sar-card" data-scene={r.scene_id}>
      <div className="sd-card-img">
        {r.thumb_url ? <img src={`${r.thumb_url}?size=256`} alt="" loading="lazy" /> : <span className="sd-noimg">raster not on this host</span>}
        <Badge tone={TONE[r.label] || "neutral"} className="sd-card-badge">{r.label}</Badge>
      </div>
      <div className="sd-card-body">
        <div className="sd-card-title">{r.label && r.geo_basis !== "synthetic" ? place(r).replace(/^the /, "") : place(r)}</div>
        <div className="sd-card-id mono" title={r.scene_id}>{r.scene_id}</div>
        <div className="sd-card-row mono"><MapPin size={11} /> {r.center ? `${lat(r.center[1])} · ${lon(r.center[0])}` : "—"}</div>
        <div className="sd-card-row mono">{day(r.acquired_utc)} · {clock(r.acquired_utc)} · {r.polarisation || "pol —"}</div>
        <div className="sd-card-foot">
          <span className={`sd-basis sd-basis-${BASIS_TONE[r.geo_basis] || "neutral"}`} title={r.geo_basis_note}>position {BASIS_WORD[r.geo_basis] || r.geo_basis}</span>
          <span className="dim">{r.runs} run{r.runs === 1 ? "" : "s"}</span>
        </div>
      </div>
    </button>
  );
}

/* ---------------------------------------------------------------- upload -- */

function UploadPanel({ canUpload, onReady }) {
  const [file, setFile] = useState(null);
  const [f, setF] = useState({ scene_id: "", acquired_utc: "", bbox: "", polarisation: "", product_type: "", orbit_direction: "", notes: "" });
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));

  async function send() {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const form = new FormData();
      form.append("raster", file);
      for (const [k, v] of Object.entries(f)) if (v) form.append(k, v);
      const s = await api.sarUpload(form);
      setState(s); if (s.status === "ready") onReady(s);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function complete() {
    setBusy(true); setError(null);
    try {
      const body = Object.fromEntries(Object.entries(f).filter(([k, v]) => v && ["acquired_utc", "bbox", "polarisation", "product_type", "orbit_direction", "notes"].includes(k)));
      const s = await api.sarCompleteUpload(state.upload_id, body);
      setState(s); if (s.status === "ready") onReady(s);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const missing = state?.missing || [];
  const need = (k) => missing.includes(k);
  return (
    <div className="sd-upload" data-testid="sar-upload">
      <div className="sd-h">Upload SAR data</div>
      <p className="sd-p">A calibrated sigma0 GeoTIFF (dB). A georeferenced raster supplies its own footprint; anything the file does not carry must be entered. Nothing is assumed.</p>
      {!canUpload && <Notice tone="info">Uploading needs the investigator or analyst role.</Notice>}
      <label className="sd-file">
        <input type="file" accept=".tif,.tiff,.png,.jpg,.jpeg" disabled={!canUpload} data-testid="sar-upload-file"
          onChange={(e) => { setFile(e.target.files?.[0] || null); setState(null); setError(null); }} />
        <Upload size={14} /> {file ? file.name : "Choose a raster…"}
      </label>

      {state?.status === "unsupported" && (
        <Notice tone="warn" testid="sar-upload-unsupported"><b>NOT ANALYSABLE.</b> {state.caveats?.[0]}</Notice>
      )}
      {state?.status === "metadata_required" && (
        <Notice tone="warn" testid="sar-metadata-required"><b>METADATA REQUIRED:</b> {missing.join(", ")}. The file does not carry {missing.length === 1 ? "it" : "them"} and OceanTrace will not guess.</Notice>
      )}
      {state?.raster_facts?.width && (
        <div className="sd-facts">
          <Fact k="Raster" v={`${state.raster_facts.width} × ${state.raster_facts.height} px · ${state.raster_facts.bands} band · ${state.raster_facts.dtype}`} />
          <Fact k="Georeferenced" v={state.raster_facts.georeferenced_raster ? `yes (${state.raster_facts.raster_crs})` : "no"} />
          <Fact k="Values p1–p99" v={state.raster_facts.value_p01_p99?.join(" … ")} />
        </div>
      )}
      {state?.caveats?.filter((_, i) => state.status !== "unsupported" || i > 0).map((c) => <Notice key={c} tone="warn">{c}</Notice>)}

      <div className="sd-form">
        <label>Scene ID<input value={f.scene_id} onChange={set("scene_id")} placeholder="defaults to the file name" disabled={Boolean(state)} /></label>
        <label className={need("acquired_utc") ? "need" : ""}>Acquisition time (UTC){need("acquired_utc") && <em>required</em>}
          <input value={f.acquired_utc} onChange={set("acquired_utc")} placeholder="2023-01-08T00:10:08Z" data-testid="sar-up-time" /></label>
        <label className={need("bbox") ? "need" : ""}>Footprint bbox{need("bbox") && <em>required</em>}
          <input value={f.bbox} onChange={set("bbox")} placeholder="min_lon, min_lat, max_lon, max_lat" data-testid="sar-up-bbox"
            disabled={state?.metadata_basis?.bbox === "raster"} title={state?.metadata_basis?.bbox === "raster" ? "Read from the raster; not overridable" : ""} /></label>
        <label className={need("polarisation") ? "need" : ""}>Polarisation{need("polarisation") && <em>required</em>}
          <select value={f.polarisation} onChange={set("polarisation")} data-testid="sar-up-pol"><option value="">—</option>{["VV", "VH", "HH", "HV"].map((p) => <option key={p}>{p}</option>)}</select></label>
        <label>Product type<input value={f.product_type} onChange={set("product_type")} placeholder="GRD" /></label>
        <label>Orbit direction<select value={f.orbit_direction} onChange={set("orbit_direction")}><option value="">—</option><option>ASCENDING</option><option>DESCENDING</option></select></label>
      </div>
      {error && <Notice tone="danger" testid="sar-upload-error">{error}</Notice>}
      {state?.status === "ready" ? (
        <Notice tone="ok" testid="sar-upload-ready"><CheckCircle2 size={13} /> Scene registered. Footprint {state.metadata_basis?.bbox === "raster" ? "read from the raster" : "as entered"}; time {state.metadata_basis?.acquired_utc === "product_identifier" ? "read from the product identifier" : "as entered"}.</Notice>
      ) : (
        <button className="sd-btn primary" disabled={!canUpload || !file || busy || state?.status === "unsupported"} onClick={state?.status === "metadata_required" ? complete : send} data-testid="sar-upload-send">
          {busy ? <Loader2 size={14} className="ws-spin" /> : <Upload size={14} />} {state?.status === "metadata_required" ? "Validate metadata" : "Upload and validate"}
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ analysis ---- */

const STEPS = [["upload", "Scene"], ["validate", "Metadata"], ["preprocess", "Pre-process"], ["detect", "Detection"], ["segment", "Segmentation"], ["characterise", "Characterisation"], ["result", "Result"]];

function Analysis({ scene, invId, runId, onFindVessels }) {
  const [status, setStatus] = useState(null);
  const [detect, setDetect] = useState(null);
  const [slick, setSlick] = useState(null);
  const [mask, setMask] = useState(true);
  const [imgFail, setImgFail] = useState(false);
  const got = useRef({});
  useEffect(() => {
    if (!invId || !runId) return undefined;
    let alive = true; got.current = {}; setStatus(null); setDetect(null); setSlick(null); setImgFail(false);
    const tick = async () => {
      try {
        const st = await api.invStatus(invId, runId);
        if (!alive) return;
        setStatus(st);
        const row = (n) => (st.stages || []).find((s) => s.stage === n);
        if (DONE.includes(row("detect")?.status) && !got.current.d) { got.current.d = 1; api.layer(runId, "detect").then((d) => alive && setDetect(d)).catch(() => {}); }
        if (DONE.includes(row("characterise")?.status) && !got.current.s) { got.current.s = 1; api.layer(runId, "slick").then((d) => alive && setSlick(d)).catch(() => {}); }
      } catch { /* keep polling */ }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(id); };
  }, [invId, runId]);

  const row = (n) => (status?.stages || []).find((s) => s.stage === n);
  const dRow = row("detect"), cRow = row("characterise");
  const dDone = DONE.includes(dRow?.status), cDone = DONE.includes(cRow?.status);
  const stepState = (id) => {
    if (id === "upload" || id === "validate") return "done";
    if (id === "preprocess" || id === "detect" || id === "segment") return dDone ? "done" : dRow?.status === "failed" ? "failed" : "running";
    if (id === "characterise") return cDone ? "done" : cRow?.status === "failed" ? "failed" : dDone ? "running" : "pending";
    return cDone ? "done" : "pending";
  };
  const p = slick?.features?.[0]?.properties;
  const total = (slick?.features || []).reduce((a, f) => a + (f.properties?.area_km2 || 0), 0);
  const oil = (detect?.candidates || []).filter((c) => c.class === "oil").length;
  const look = (detect?.candidates || []).filter((c) => c.class === "lookalike").length;
  return (
    <div className="sd-analysis" data-testid="sar-analysis">
      <div className="sd-steps" data-testid="sar-steps">
        {STEPS.map(([id, label]) => <span key={id} className={`sd-step sd-step-${stepState(id)}`} data-testid={`sar-step-${id}`} data-state={stepState(id)}>{stepState(id) === "running" ? <Loader2 size={11} className="ws-spin" /> : stepState(id) === "done" ? <CheckCircle2 size={11} /> : stepState(id) === "failed" ? <AlertTriangle size={11} /> : <i />}{label}</span>)}
      </div>
      <div className="sd-result">
        <figure className="sd-big">
          <div className="sd-big-box">
            {imgFail ? <div className="sd-noimg">The run records no scene raster to render.</div>
              : <img src={`/api/runs/${runId}/scene_png?size=1024`} alt="SAR scene, sigma0 dB" onError={() => setImgFail(true)} />}
            {dDone && mask && <img className="sd-big-mask" src={`/api/runs/${runId}/mask_png`} alt="" onError={(e) => { e.currentTarget.style.display = "none"; }} data-testid="sar-mask" />}
            {!dDone && dRow?.status !== "failed" && <div className="sd-scan"><span /></div>}
          </div>
          <figcaption>
            <span>SAR · σ⁰ dB — the model's input{dDone ? " · segmentation mask (raw_mask.tif) overlaid" : ""}</span>
            {dDone && <label className="sd-toggle"><input type="checkbox" checked={mask} onChange={(e) => setMask(e.target.checked)} /> mask</label>}
          </figcaption>
        </figure>
        <div className="sd-out">
          <div className="sd-h">Analysis results</div>
          {dRow?.status === "failed" && <Notice tone="danger" testid="sar-detect-failed">Detection failed: {dRow.detail || "no detail recorded"}</Notice>}
          <div className="sd-facts">
            <Fact k="Processing status" v={status ? (status.state === "running" ? `running · ${(status.stages || []).filter((s) => DONE.includes(s.status)).length}/5 stages` : status.state) : "starting…"} />
            <Fact k="Model" v={detect?.model_version} />
            <Fact k="Engine">{detect ? <Badge tone={detect.engine === "ml" ? "ok" : "warn"}>{detect.engine}</Badge> : "—"}</Fact>
            <Fact k="Processing time" v={detect?.runtime_ms ? `${(detect.runtime_ms / 1000).toFixed(1)} s` : null} />
            <Fact k="Detection confidence" v={detect?.confidence != null ? `${(detect.confidence * 100).toFixed(1)} %` : null} />
            <Fact k="Screen candidates" v={detect ? `${oil} oil · ${look} look-alike` : null} />
            <Fact k="Slick area" v={p ? `${Number(p.area_km2).toFixed(2)} km²${(slick.features.length > 1) ? ` (largest of ${slick.features.length}; ${total.toFixed(2)} total)` : ""}` : cDone ? "no region segmented" : null} />
            <Fact k="Segment confidence" v={p?.confidence != null ? `${(p.confidence * 100).toFixed(1)} %` : null} />
            <Fact k="Geometry" v={p?.major_axis_m ? `${(p.major_axis_m / 1000).toFixed(2)} × ${((p.minor_axis_m || 0) / 1000).toFixed(2)} km · ${Number(p.orientation_deg).toFixed(0)}°` : null} />
            <Fact k="Slick location" v={p?.centroid ? `${lat(p.centroid[1])} · ${lon(p.centroid[0])}` : null} />
            <Fact k="Scene time" v={scene?.acquired_utc ? fmt.utc(scene.acquired_utc) : null} />
            <Fact k="Position basis">{scene ? <span className={`sd-basis sd-basis-${BASIS_TONE[scene.geo_basis]}`}>{BASIS_WORD[scene.geo_basis]}</span> : "—"}</Fact>
          </div>
          {scene?.geo_basis === "assigned" && <Notice tone="warn">{scene.geo_basis_note}</Notice>}
          {scene?.trained_on && <Notice tone="warn">The deployed segmenter trained on this scene: confidence here is memorisation, not accuracy.</Notice>}
          <button className="sd-btn primary big" onClick={onFindVessels} disabled={!dDone} data-testid="find-vessels"
            title={dDone ? "Continue this run in the Investigation workspace" : "Available once detection has produced a result"}>
            <Ship size={16} /> Find vessels
          </button>
          <p className="sd-p dim">Opens the Investigation workspace on this same run: wind, current, hindcast, forecast, AIS, filtering, ranking and attribution play from this scene and this result.</p>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ page -- */

export default function SarDatabase() {
  const { user } = useSession();
  const canRun = hasRole(user, "investigator", "analyst");
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState({ q: "", lat: "", lon: "", radius_km: "500", start: "", end: "", polarisation: "", provider: "", provenance: "" });
  const [applied, setApplied] = useState({});
  const { data, error, loading, reload } = useApi(() => api.sarScenes(applied), [JSON.stringify(applied)]);
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState(null);
  const [remote, setRemote] = useState(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const invId = params.get("inv"), runId = params.get("run");
  const scenes = data?.scenes || [];
  const scene = useMemo(() => scenes.find((s) => s.key === (picked?.key || params.get("scene"))) || picked, [scenes, picked, params]);
  const set = (k) => (e) => setFilters((x) => ({ ...x, [k]: e.target.value }));

  function apply(e) {
    e?.preventDefault();
    const a = { ...filters };
    if (!a.lat || !a.lon) { delete a.lat; delete a.lon; delete a.radius_km; }
    if (a.start) a.start = `${a.start}T00:00:00Z`;
    if (a.end) a.end = `${a.end}T23:59:59Z`;
    setApplied(a); setRemote(null);
  }
  async function searchProviders() {
    const la = Number(filters.lat), lo = Number(filters.lon);
    if (!filters.lat || !filters.lon || !filters.start || !filters.end) { setRemote({ error: "Catalogue search needs a latitude, a longitude and a date range." }); return; }
    const d = Math.min(5, Number(filters.radius_km || 100) / 111);
    setRemoteBusy(true); setRemote(null);
    try {
      setRemote(await api.searchScenes({ bbox: [lo - d, la - d, lo + d, la + d].map((v) => v.toFixed(4)).join(","), start: `${filters.start}T00:00:00Z`, end: `${filters.end}T23:59:59Z`, source: "S1", product_type: "GRD", top: 20 }));
    } catch (e) { setRemote({ error: e.message }); } finally { setRemoteBusy(false); }
  }
  async function analyse(s) {
    setBusy(true); setRunError(null);
    try {
      const inv = await api.createInvestigation({ name: `SAR database · ${s.scene_id.slice(0, 48)}`, scene_meta_path: s.scene_meta_path });
      const started = await api.startRun(inv.id, { engine: "auto" });
      setParams({ scene: s.key, inv: inv.id, run: started.run_id });
    } catch (e) { setRunError(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="sd" data-testid="sar-database">
      <PageHeader icon={<Database size={18} />} kicker="Intelligence" title="SAR Image Database"
        sub="Every SAR scene held on this host, with its metadata and the basis of its time and place. Analyse one with the deployed models, then hand the result to the investigation workspace.">
        {data && <div className="sd-counts" data-testid="sar-counts">{Object.entries(data.by_label).map(([k, n]) => <Badge key={k} tone={TONE[k] || "neutral"}>{n} {k}</Badge>)}</div>}
      </PageHeader>

      <div className="sd-grid">
        <form className="sd-filters" onSubmit={apply} data-testid="sar-filters">
          <div className="sd-h">Search</div>
          <label className="sd-search"><Search size={14} /><input value={filters.q} onChange={set("q")} placeholder="Scene ID, region, provider…" data-testid="sar-q" /></label>
          <div className="sd-2"><label>Latitude<input value={filters.lat} onChange={set("lat")} placeholder="28.2" inputMode="decimal" data-testid="sar-lat" /></label><label>Longitude<input value={filters.lon} onChange={set("lon")} placeholder="-91.5" inputMode="decimal" data-testid="sar-lon" /></label></div>
          <label>Within (km)<input value={filters.radius_km} onChange={set("radius_km")} inputMode="numeric" /></label>
          <div className="sd-2"><label>From<input type="date" value={filters.start} onChange={set("start")} /></label><label>To<input type="date" value={filters.end} onChange={set("end")} /></label></div>
          <label>Polarisation<select value={filters.polarisation} onChange={set("polarisation")} data-testid="sar-pol"><option value="">Any</option>{["VV", "VH", "HH", "HV"].map((p) => <option key={p}>{p}</option>)}</select></label>
          <label>Provider<input value={filters.provider} onChange={set("provider")} placeholder="LocalCache, CDSE, UserUpload…" /></label>
          <label>Provenance<select value={filters.provenance} onChange={set("provenance")} data-testid="sar-prov"><option value="">Any</option>{["REAL", "REFERENCE", "UPLOADED", "SYNTHETIC"].map((p) => <option key={p}>{p}</option>)}</select></label>
          <div className="sd-2"><button className="sd-btn primary" type="submit" data-testid="sar-apply">Search this host</button><button className="sd-btn" type="button" onClick={() => { setFilters({ q: "", lat: "", lon: "", radius_km: "500", start: "", end: "", polarisation: "", provider: "", provenance: "" }); setApplied({}); setRemote(null); }}>Clear</button></div>
          <button className="sd-btn" type="button" onClick={searchProviders} disabled={remoteBusy} data-testid="sar-remote">{remoteBusy ? <Loader2 size={13} className="ws-spin" /> : <Radar size={13} />} Search Sentinel-1 catalogues</button>
          <p className="sd-p dim">Catalogue search asks the CDSE → ASF provider chain. A hit is a product that exists, not one this host has downloaded.</p>
          <UploadPanel canUpload={canRun} onReady={(s) => { reload(); setParams({ scene: s.key }); }} />
        </form>

        <div className="sd-main">
          {invId && runId ? (
            <>
              <div className="sd-bar"><button className="sd-btn" onClick={() => setParams(scene ? { scene: scene.key } : {})}><X size={13} /> Back to the database</button><span className="mono dim">{scene?.scene_id} · run {runId}</span></div>
              <Analysis scene={scene} invId={invId} runId={runId} onFindVessels={() => nav(`/investigation?inv=${invId}&run=${runId}&present=1`)} />
            </>
          ) : (
            <>
              {remote && (
                <div className="sd-remote" data-testid="sar-remote-results">
                  {remote.error ? <Notice tone="warn">{remote.error}</Notice> : (
                    <>
                      <div className="sd-h">Catalogue · {remote.total} product{remote.total === 1 ? "" : "s"} <span className="dim mono">{remote.provider || "no provider answered"} · {remote.elapsed_s}s</span></div>
                      {(remote.attempts || []).filter((a) => !a.ok && !a.inferred).map((a) => <Notice key={a.provider} tone="warn">{a.provider}: {a.error_class || a.result}{a.detail ? ` — ${a.detail}` : ""}</Notice>)}
                      {(remote.scenes || []).map((h) => <div key={h.product_id} className="sd-hit"><span className="mono">{h.product_id}</span><span className="mono dim">{fmt.utc(h.acquired_utc)} · {h.orbit_direction || "orbit —"} · {h.polarisation || "pol —"}</span><Badge tone={h.cached_path ? "ok" : "neutral"}>{h.cached_path ? "cached" : "not downloaded"}</Badge></div>)}
                    </>
                  )}
                </div>
              )}
              {loading && !data ? <DataState kind="loading" title="Reading the scene store…" />
                : error ? <DataState kind="error" error={error} title="The scene database could not be read" />
                  : !scenes.length ? <DataState title="No scene matches these filters" hint={`${data?.total ?? 0} scenes are held in total.`} testid="sar-empty" />
                    : <div className="sd-cards" data-testid="sar-cards">{scenes.map((r) => <SceneCard key={r.key} r={r} on={scene?.key === r.key} onPick={(x) => { setPicked(x); setParams({ scene: x.key }); }} />)}</div>}
            </>
          )}
        </div>

        {!(invId && runId) && (
          <aside className="sd-detail" data-testid="sar-detail">
            {!scene ? <DataState compact title="Select a scene" hint="Its metadata, provenance and analysis options appear here." /> : (
              <>
                <div className="sd-detail-img">{scene.thumb_url ? <img src={`${scene.thumb_url}?size=640`} alt="SAR scene" /> : <span className="sd-noimg">{scene.unavailable_reason}</span>}</div>
                <div className="sd-detail-head"><b>{place(scene)}</b><Badge tone={TONE[scene.label] || "neutral"}>{scene.label}</Badge></div>
                <div className="sd-facts" data-testid="sar-metadata">
                  <Fact k="Scene ID" v={scene.scene_id} />
                  <Fact k="Latitude" v={scene.center ? lat(scene.center[1]) : null} />
                  <Fact k="Longitude" v={scene.center ? lon(scene.center[0]) : null} />
                  <Fact k="Position basis" title={scene.geo_basis_note}><span className={`sd-basis sd-basis-${BASIS_TONE[scene.geo_basis]}`}>{BASIS_WORD[scene.geo_basis]}</span></Fact>
                  <Fact k="Acquisition date" v={day(scene.acquired_utc)} />
                  <Fact k="Acquisition time" v={clock(scene.acquired_utc)} />
                  <Fact k="Time basis" title={scene.time_basis_note}><span className={`sd-basis sd-basis-${BASIS_TONE[scene.time_basis]}`}>{BASIS_WORD[scene.time_basis]}</span></Fact>
                  <Fact k="Satellite / mission" v={scene.platform} title={scene.platform ? "" : "Not stated by this scene's identifier"} />
                  <Fact k="Orbit" v={scene.absolute_orbit ? `#${scene.absolute_orbit}${scene.orbit_direction ? ` · ${scene.orbit_direction}` : ""}` : scene.orbit_direction} />
                  <Fact k="Polarisation" v={scene.polarisation} />
                  <Fact k="Product type" v={scene.product_type} />
                  <Fact k="Resolution" v={scene.pixel_spacing_m ? `${scene.pixel_spacing_m} m` : null} />
                  <Fact k="Provider" v={scene.provider_used} />
                  <Fact k="Availability" v={scene.available ? `on this host · ${(scene.raster_bytes / 1e6).toFixed(1)} MB` : scene.unavailable_reason} />
                  <Fact k="Analysed by" v={`${scene.runs} run${scene.runs === 1 ? "" : "s"}`} />
                </div>
                {scene.geo_basis !== "measured" && scene.geo_basis !== "raster" && <Notice tone="warn" testid="sar-basis-note"><Info size={12} /> {scene.geo_basis_note}</Notice>}
                {scene.notes && <p className="sd-p">{scene.notes}</p>}
                {(scene.caveats || []).map((c) => <Notice key={c} tone="warn">{c}</Notice>)}
                {runError && <Notice tone="danger">{runError}</Notice>}
                <button className="sd-btn primary big" disabled={!canRun || !scene.available || busy} onClick={() => analyse(scene)} data-testid="sar-analyse"
                  title={!canRun ? "Analysis needs the investigator or analyst role" : !scene.available ? "The raster is not on this host" : "Run the deployed detection and segmentation models on this scene"}>
                  {busy ? <Loader2 size={15} className="ws-spin" /> : <Radar size={15} />} Analyse with OceanTrace AI
                </button>
                {scene.latest_run_id && <button className="sd-btn" onClick={() => nav(url.workspace({ run: scene.latest_run_id }))} data-testid="sar-open-latest">Open the latest investigation of this scene</button>}
              </>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
