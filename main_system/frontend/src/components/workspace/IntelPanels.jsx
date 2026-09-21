/* The right-hand intelligence panel: one component per investigation stage.
 *
 * Every panel reads from the same context the page holds (the run's layers,
 * its status rows, the registry row, the funnel, the forcing field) and
 * writes nothing. Every number on screen is quoted from an artefact or an
 * API response; a value the pipeline did not produce says so ("not
 * recorded", "not modelled", "—") rather than being estimated here.
 *
 * Wording rule (spec §20, §31): a ranked vessel is a "Potential Source
 * Vessel" / "Highest-Ranked Candidate", never guilty or responsible.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, Check, CheckCircle2, Download, ExternalLink, FileText, Info, Loader2,
  Map as MapIcon, Ship, Wind, Waves, Radar, Shapes, Printer,
} from "lucide-react";

import { Check_, Meter, Primary, Row, Section, Tabs, latlon, num, utc } from "./intel";
import VesselIdentity, { VesselHistory } from "../vessels/VesselIdentity";
import { url } from "../../lib/urls";
import SpillPanel from "./SpillPanel";
import DriftPanel from "./DriftPanel";
import SuspectsPanel from "./SuspectsPanel";
import { sourceBadge } from "./palette";
import { fmtTile } from "../../lib/stages";
import { originEstimate, screenVerdict } from "../../lib/drift";
import { haversineKm, sampleField, trackStateAt, guessPlace } from "../../lib/replay";
import { FactorBar, provenanceOf } from "../ui";
import IncidentReport from "../report/IncidentReport";
import { sceneFact } from "../../lib/sceneName";

/* --------------------------------------------------------------- shared -- */

/** The scene's own greyscale render. A run reconciled from a manifest that
 *  recorded no raster path answers 404 here; that is said, not hidden. */
function QuickLook({ runId, caption }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <div className="ip-quicklook ip-quicklook-none" data-testid="quicklook-missing">
      Quick look not available: this run's manifest records no scene raster path.
    </div>;
  }
  return (
    <div className="ip-quicklook" data-testid="quicklook">
      <img src={`/api/runs/${runId}/scene_png?size=512`} alt="Scene quick look" onError={() => setFailed(true)} />
      <span className="ip-quicklook-cap mono">{caption}</span>
    </div>
  );
}

function PanelHead({ title, testid }) {
  return (
    <div className="ip-head" data-testid={testid}>
      <span className="ip-head-title">{title}</span>
      <span className="ip-head-menu">≡</span>
    </div>
  );
}

function Foot({ children }) {
  return <div className="ip-foot">{children}</div>;
}

function ProvBadge({ source, testid }) {
  const b = sourceBadge(source);
  if (!source) return <span className="badge badge-ghost" data-testid={testid}>UNRECORDED</span>;
  return <span className={`badge badge-${b.tone}`} data-testid={testid}>{b.label === "—" ? String(source).toUpperCase() : b.label}</span>;
}

/** AIS DATA provenance for the correlation/attribution panels. The manifest's
 *  `ais.data_source` is the provenance of the AIS BYTES: "real" means a
 *  historical archive (there is no live feed in a sealed run). */
export function aisProvenance(runRow, suspects) {
  const src = runRow?.manifest?.ais?.data_source || suspects?.source;
  if (!src) return { label: "UNRECORDED", tone: "ghost", word: "not recorded" };
  const s = String(src).toLowerCase();
  if (s === "real" || s === "historical" || s === "archive" || s === "sensor") return { label: "HISTORICAL", tone: "ok", word: "historical archive" };
  return provenanceOf(s);
}

/** Presentation reveal of a checklist: while the presentation's beat `id`
 *  plays, row i is shown as pending until the beat reaches it and as
 *  running until the beat passes it -- ONLY when its real state is done.
 *  A row the pipeline reports as running, failed, pending or not modelled
 *  is shown exactly as reported; the reveal never upgrades a state. */
function revealState(real, i, n, cine, beatId) {
  if (!cine?.active || cine.beat?.id !== beatId || real !== "done") return real;
  const at = cine.t * n;
  return at < i ? "pending" : at < i + 1 ? "running" : "done";
}

const bboxCentre = (b) => (Array.isArray(b) ? [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] : null);
const bboxSizeKm = (b) => {
  if (!Array.isArray(b)) return null;
  const w = haversineKm(b[1], b[0], b[1], b[2]);
  const h = haversineKm(b[1], b[0], b[3], b[0]);
  return `${Math.round(w)} km × ${Math.round(h)} km`;
};

/* ---------------------------------------------------------------- scene -- */

export function ScenePanel({ ctx }) {
  const { selectedScene: sel, layers, runId, inv, judged, actions, busy, canRun, stage } = ctx;
  const sm = layers.scene_meta;
  const [tab, setTab] = useState("scene");
  /* What is described: the loaded run's scene_meta wins; before a run, the
   * selected catalogue product / local scene. */
  const s = sm ? {
    id: sm.scene_id, acquired: sm.acquired_utc, bbox: sm.bbox, pol: sm.polarisation,
    res: sm.pixel_spacing_m, provider: sm.provider_used, source: sm.source, product: sceneFact(sm, "product"),
    orbit: null, size: null, status: "loaded", crs: sm.crs || "not recorded", platform: sceneFact(sm, "mission"),
  } : sel ? {
    id: sel.product_id || sel.scene_id, acquired: sel.acquired_utc, bbox: sel.bbox,
    pol: sel.polarisation, res: sel.pixel_spacing_m, provider: sel.provider_used || sel.provider,
    source: sel.kind === "local" ? sel.source : null, product: sel.product_type || "not recorded",
    orbit: sel.orbit_direction, size: sel.size_bytes, platform: sel.platform || sceneFact(sel, "mission"),
    status: sel.kind === "local" ? (sel.available ? "available" : sel.unavailable_reason)
      : sel.cached_path ? "cached" : "not downloaded", crs: "EPSG:4326",
    caveats: sel.caveats, provenance: sel.provenance,
  } : null;

  if (!s) {
    return (
      <div className="ip" data-testid="intel-scene">
        <PanelHead title="Scene" />
        <div className="ip-empty">
          <MapIcon size={22} />
          <div>Select a scene from the catalogue or a cached scene on this host.</div>
          <div className="dim">The footprint appears on the map as soon as one is selected.</div>
        </div>
      </div>
    );
  }
  const centre = bboxCentre(s.bbox);
  const pols = String(s.pol || "").toUpperCase();
  const loaded = Boolean(inv);
  const runDone = judged.detection.state === "done";
  return (
    <div className="ip" data-testid="intel-scene">
      <PanelHead title={loaded || sm ? "Scene Loaded" : "Scene Selected"} />
      <Tabs value={tab} onChange={setTab} items={[
        { id: "scene", label: "Scene" }, { id: "meta", label: "Metadata" }, { id: "footprint", label: "Footprint" },
      ]} />
      {tab === "scene" && (
        <>
          <Section title={[s.platform, s.product].filter((v) => v && v !== "not recorded").join(" ") || "Scene"} testid="scene-grd">
            {runId && <QuickLook runId={runId} caption={s.id?.slice(0, 34)} />}
            <Row k="Acquisition" v={utc(s.acquired)} testid="scene-acquired" />
            <Row k="Orbit direction" v={s.orbit ? s.orbit[0] + s.orbit.slice(1).toLowerCase() : null}
              title={s.orbit ? "" : "Not carried by this catalogue row"} />
            <Row k="Relative orbit" v={null} title="Not exposed by the catalogue response" />
            <Row k="Product" v={s.product} />
            <Row k="Resolution" v={s.res ? `${s.res} m` : null} />
            <Row k="Product size" v={s.size ? `~ ${(s.size / 1e9).toFixed(1)} GB` : null} />
            <Row k="Scene ID" v={s.id} />
            <Row k="Status">
              <span className={`ip-status ${["loaded", "available", "cached"].includes(s.status) ? "ok" : "warn"}`}>
                <i /> {String(s.status || "—").replace(/^\w/, (c) => c.toUpperCase())}
              </span>
            </Row>
          </Section>
          <Section title="Polarization">
            <div className="ip-pols">
              {["VV", "VH", "HH", "HV"].map((p) => (
                <label key={p} className={`ip-pol ${pols.includes(p) ? "on" : ""}`}>
                  <span className="ip-pol-box">{pols.includes(p) && <Check size={11} />}</span>{p}
                </label>
              ))}
            </div>
          </Section>
          <Section title="Geolocation">
            <Row k="Center latitude" v={centre ? `${Math.abs(centre[1]).toFixed(4)}° ${centre[1] >= 0 ? "N" : "S"}` : null} />
            <Row k="Center longitude" v={centre ? `${Math.abs(centre[0]).toFixed(4)}° ${centre[0] >= 0 ? "E" : "W"}` : null} />
            <Row k="Coverage" v={bboxSizeKm(s.bbox)} />
            <Row k="CRS" v={`${s.crs} (WGS84)`} />
          </Section>
          <Section title="Data availability">
            <Row k="Status">
              <span className={`ip-status ${["loaded", "available", "cached"].includes(s.status) ? "ok" : "warn"}`}><i /> {s.status === "loaded" ? "Available" : s.status}</span>
            </Row>
            <Row k="Source" v={s.provider || s.source || null} mono={false} />
            {s.source && <Row k="Provenance"><ProvBadge source={s.source} testid="scene-provenance" /></Row>}
            {s.provenance && <Row k="Catalogue class" v={String(s.provenance)} />}
          </Section>
          {s.caveats?.map((c) => <div key={c} className="ip-note warn"><AlertTriangle size={12} /> {c}</div>)}
        </>
      )}
      {tab === "meta" && (
        <Section title="Catalogue record">
          {Object.entries(sm || sel || {}).filter(([k, v]) => !["key", "kind", "caveats", "notes"].includes(k) && (typeof v !== "object" || v === null))
            .map(([k, v]) => <Row key={k} k={k} v={v == null ? null : String(v)} />)}
        </Section>
      )}
      {tab === "footprint" && (
        <Section title="Footprint (WGS84)">
          {s.bbox ? (
            <>
              <Row k="West" v={`${s.bbox[0].toFixed(4)}°`} /><Row k="South" v={`${s.bbox[1].toFixed(4)}°`} />
              <Row k="East" v={`${s.bbox[2].toFixed(4)}°`} /><Row k="North" v={`${s.bbox[3].toFixed(4)}°`} />
              <Row k="Centre" v={latlon(centre)} />
            </>
          ) : <div className="dim">No footprint in this record.</div>}
        </Section>
      )}
      <Foot>
        {!loaded ? (
          <>
            <Primary onClick={actions.loadScene} busy={busy === "load"} testid="load-scene"
              disabled={!canRun || (sel?.kind === "hit" && !sel.cached_path) || (sel?.kind === "local" && !sel.available)}
              title={!canRun ? "Opening an investigation needs investigator or analyst"
                : sel?.kind === "hit" && !sel.cached_path ? "This product is a catalogue hit, not a downloaded scene" : ""}
              icon={false}>
              <Download size={14} /> Load scene
            </Primary>
            <button className="ip-secondary" onClick={() => actions.flyTo("scene")} data-testid="view-footprint"><MapIcon size={14} /> View footprint</button>
          </>
        ) : (
          <>
            <button className="ip-secondary" onClick={() => actions.flyTo("scene")} data-testid="view-full-scene"><ExternalLink size={14} /> View full scene</button>
            {runDone || stage === "scene" ? (
              <Primary onClick={() => (runDone ? actions.play("preprocess") : actions.run())} busy={busy === "run"}
                disabled={!runDone && !canRun} testid="proceed-detection"
                title={!runDone && !canRun ? "Starting a run needs investigator or analyst" : runDone ? "Play the analysis from pre-processing onward" : ""}>
                Proceed to detection
              </Primary>
            ) : null}
          </>
        )}
      </Foot>
    </div>
  );
}

/* ---------------------------------------------------- pre-processing ----- */

/** The pre-processing checklist, derived from the input product and the
 *  detection stage. The pipeline has no speckle filter: that row is stated
 *  as not modelled rather than faked green. */
function preprocessRows(ctx) {
  const sm = ctx.layers.scene_meta;
  const t = ctx.tilesInfo;
  const st = ctx.judged.preprocess.state;
  const dbr = sm?.db_range || t?.default_stretch_db;
  const tileNote = tileCountNote(ctx);
  return [
    { label: "Radiometric calibration", state: sm ? "done" : st === "running" ? "running" : "pending",
      note: sm ? `σ⁰ in dB${dbr ? ` · clip ${dbr[0]}…${dbr[1]} dB` : ""}` : undefined },
    { label: "Speckle filtering", state: "na", note: "Not modelled — the segmenter is trained on unfiltered σ⁰" },
    { label: "Georeferencing", state: sm?.crs || t?.native_crs ? "done" : st === "running" ? "running" : "pending",
      note: sm?.crs || t?.native_crs ? `${sm?.crs || t?.native_crs}` : undefined },
    { label: "Image tiling", state: st, note: st === "done" ? tileNote : undefined },
  ];
}

function tileCountNote(ctx) {
  const w = (ctx.judged.detection.row?.warnings || []).find((x) => /over \d+ tile/.test(x));
  const m = w && w.match(/over (\d+) tile/);
  if (m) return `${Number(m[1]).toLocaleString()} tiles screened`;
  if (ctx.tileGrid) return `${ctx.tileGrid.n.toLocaleString()} tiles (${ctx.tileGrid.cols} × ${ctx.tileGrid.rows})`;
  return "Completed";
}

export function ProcessingPanel({ ctx }) {
  const { layers, judged, actions, tilesInfo, cine } = ctx;
  const sm = layers.scene_meta || {};
  const [mode, setMode] = useState("quick");
  const [tab, setTab] = useState("scene");
  const rows = preprocessRows(ctx).map((r, i, all) => ({ ...r, state: revealState(r.state, i, all.length, cine, "preprocess"),
    note: revealState(r.state, i, all.length, cine, "preprocess") === r.state ? r.note : undefined }));
  const st = judged.preprocess.state;
  const detectRow = judged.detection.row;
  return (
    <div className="ip" data-testid="intel-processing">
      <PanelHead title="Scene Analysis" />
      <Tabs value={tab} onChange={setTab} items={[{ id: "scene", label: "Scene" }, { id: "lineage", label: "Lineage" }]} />
      {tab === "scene" ? (
        <>
          <Section title={sceneTitle(ctx.layers?.scene_meta)}>
            <Row k="Polarization" v={sm.polarisation} />
            <Row k="Orbit direction" v={null} title="Not recorded in scene_meta" />
            <Row k="Acquisition time" v={utc(sm.acquired_utc)} />
            <Row k="Resolution (GRD)" v={sm.pixel_spacing_m ? `${sm.pixel_spacing_m} m` : null} />
            <Row k="Raster size" v={tilesInfo?.shape ? `${tilesInfo.shape[1].toLocaleString()} × ${tilesInfo.shape[0].toLocaleString()} px` : null} />
            <Row k="Status"><span className={`ip-status ${sm.scene_id ? "ok" : "warn"}`}><i /> {sm.scene_id ? "Available" : "Awaiting scene"}</span></Row>
          </Section>
          <Section title="SAR pre-processing" testid="preprocess-section">
            <div className="ip-seg">
              <button className={mode === "quick" ? "on" : ""} onClick={() => setMode("quick")}>Quick</button>
              <button className={mode === "detailed" ? "on" : ""} onClick={() => setMode("detailed")}>Detailed</button>
            </div>
            {rows.map((r) => <Check_ key={r.label} {...r} testid={`pre-${r.label.split(" ")[0].toLowerCase()}`} />)}
            {mode === "detailed" && (
              <div className="ip-note">
                <Info size={12} /> Calibration and georeferencing are properties of the GRD product read from scene_meta.json; tiling happens inside the detection stage.
                {detectRow?.warnings?.length ? ` Detection reported: ${detectRow.warnings.join(" · ")}` : ""}
              </div>
            )}
          </Section>
        </>
      ) : (
        <Section title="Stage lineage">
          {(ctx.status?.stages || []).map((s) => (
            <Row key={s.stage} k={s.stage} mono={false}>
              <span className={`badge badge-${s.status === "ok" ? "ok" : s.status === "failed" ? "danger" : "warn"}`}>{s.status}</span>{" "}
              <ProvBadge source={s.data_source || s.source} />
            </Row>
          ))}
          {!ctx.status?.stages?.length && <div className="dim">No stage has reported yet.</div>}
        </Section>
      )}
      <Foot>
        <button className="ip-secondary" onClick={() => actions.go("tiling")}>View all steps →</button>
        {st === "running" || (cine?.active && cine.beat?.id === "preprocess") ? (
          <Primary busy testid="processing-btn">Processing…</Primary>
        ) : st === "done" ? (
          <Primary onClick={() => actions.go("tiling")} testid="processing-btn">Georeferencing &amp; tiling</Primary>
        ) : (
          <Primary onClick={actions.run} disabled={!ctx.canRun} testid="processing-btn"
            title={!ctx.canRun ? "Starting a run needs investigator or analyst" : ""}>Start processing</Primary>
        )}
      </Foot>
    </div>
  );
}

export function TilingPanel({ ctx }) {
  const { layers, judged, actions, tilesInfo, tileGrid, models, cine } = ctx;
  const sm = layers.scene_meta || {};
  const [tab, setTab] = useState("scene");
  const rows = preprocessRows(ctx);
  /* the tiling beat: georeferencing and tiling rows complete in front of the analyst */
  const tilingT = cine?.active && cine.beat?.id === "tiling" ? cine.t : null;
  const st = tilingT != null && judged.tiling.state === "done" && tilingT < 0.55 ? "running" : judged.tiling.state;
  const geoDone = tilesInfo?.bounds_wgs84 && (tilingT == null || tilingT > 0.3);
  const seg = (models?.models || []).find((m) => m.kind === "segment");
  const tileSize = seg?.metadata?.tile_size ? `${seg.metadata.tile_size} × ${seg.metadata.tile_size} px` : tileGrid ? `${tileGrid.tileSizePx} × ${tileGrid.tileSizePx} px` : null;
  return (
    <div className="ip" data-testid="intel-tiling">
      <PanelHead title="Scene Analysis" />
      <Tabs value={tab} onChange={setTab} items={[{ id: "scene", label: "Scene" }, { id: "details", label: "Details" }]} />
      <Section title={sceneTitle(ctx.layers?.scene_meta)}>
        <Row k="Data source" v={sm.provider_used ? `${sm.provider_used}` : null} mono={false} />
        <Row k="Resolution" v={sm.pixel_spacing_m ? `${sm.pixel_spacing_m} m` : null} />
        <Row k="Acquisition time" v={utc(sm.acquired_utc)} />
        <Row k="Orbit" v={null} title="Not recorded in scene_meta" />
        <Row k="Polarization" v={sm.polarisation} />
      </Section>
      <Section title="Georeferencing">
        <Row k="CRS" v={tilesInfo?.native_crs || sm.crs ? `${tilesInfo?.native_crs || sm.crs} (WGS84)` : null} />
        <Row k="Geolocation status" v={geoDone ? "Completed" : st === "running" || tilingT != null ? "Processing…" : "Pending"} mono={false}
          tone={geoDone ? "ok" : undefined} />
        <Row k="Ground control points" v={null} title="Not exposed by the tile service; the GRD product is delivered georeferenced" />
        <Row k="RMS error" v={null} title="Not measured by this pipeline" />
      </Section>
      <Section title="Image tiling">
        <Row k="Tile size" v={tileSize} />
        <Row k="Overlap" v={null} title="Not recorded in the detection response" />
        <Row k="Tiles generated" v={tileGrid ? `${tileGrid.n.toLocaleString()}` : null} testid="tiles-generated" />
        <Row k="Selected tile" v={ctx.selectedTile ? fmtTile(ctx.selectedTile.index) : null} />
        <Row k="Status" v={st === "done" ? "Completed" : st === "running" ? "Processing…" : st === "failed" ? "Failed" : "Pending"} mono={false} tone={st === "done" ? "ok" : undefined} />
      </Section>
      <Section title="Processing status">
        {rows.map((r) => <Check_ key={r.label} {...r} />)}
      </Section>
      {tab === "details" && tilesInfo && (
        <Section title="Tile service">
          <Row k="Raster" v={`${tilesInfo.shape[1]} × ${tilesInfo.shape[0]} px · ${tilesInfo.bands} band`} />
          <Row k="Stretch" v={`${tilesInfo.default_stretch_db[0]}…${tilesInfo.default_stretch_db[1]} dB`} />
          <div className="ip-note">{tilesInfo.stretch_note}</div>
        </Section>
      )}
      <Foot>
        {st === "done" ? (
          <Primary onClick={() => actions.go("detection")} testid="start-detection">Start detection</Primary>
        ) : st === "running" ? (
          <Primary busy testid="start-detection">Processing…</Primary>
        ) : (
          <Primary onClick={actions.run} disabled={!ctx.canRun} testid="start-detection">Start detection</Primary>
        )}
      </Foot>
    </div>
  );
}

/* ------------------------------------------------------------ detection -- */

export function DetectionPanel({ ctx }) {
  const { layers, judged, actions, runRow, models, autoPreview, show, onShow, selectedTile, cine } = ctx;
  const sm = layers.scene_meta || {};
  const det = layers.detect;
  const slick = layers.slick;
  const [tab, setTab] = useState("scene");
  const [sub, setSub] = useState("overview");
  /* scanning / detection beats: the metrics are still being found */
  const scanning = cine?.active && (cine.beat?.id === "scan" || (cine.beat?.id === "detect" && cine.t < 0.5));
  const st = scanning && judged.detection.state === "done" ? "running" : judged.detection.state;
  const rows = preprocessRows(ctx);
  const seg = (models?.models || []).find((m) => m.kind === "segment");
  const p = slick?.features?.[0]?.properties;
  const total = (slick?.features || []).reduce((a, f) => a + (f.properties?.area_km2 || 0), 0);
  const threshold = autoPreview?.confidence_threshold ?? (seg?.metadata?.output?.match(/default ([\d.]+)/)?.[1]);
  const oil = (det?.candidates || []).filter((c) => c.class === "oil").length;
  const look = (det?.candidates || []).filter((c) => c.class === "lookalike").length;
  return (
    <div className="ip" data-testid="intel-detection">
      <PanelHead title="Scene Analysis" />
      <Tabs value={tab} onChange={setTab} items={[{ id: "scene", label: "Scene" }, { id: "evidence", label: "Evidence" }]} />
      {tab === "scene" ? (
        <>
          <Section title={sceneTitle(ctx.layers?.scene_meta)}>
            <div className="ip-seg">
              <button className={sub === "overview" ? "on" : ""} onClick={() => setSub("overview")}>Overview</button>
              <button className={sub === "details" ? "on" : ""} onClick={() => setSub("details")}>Details</button>
            </div>
            <Row k="Model" v={det?.model_version || seg?.name || null} testid="det-model" />
            <Row k="Task" v="Semantic segmentation + screening" mono={false} />
            <Row k="Threshold" testid="det-threshold">
              <span className="ip-threshold"><Meter value={threshold ?? 0} /> <span className="mono">{threshold ?? "—"}</span></span>
            </Row>
            {sub === "details" && (
              <>
                <Row k="Engine"><span className={`badge ${det?.engine === "ml" ? "badge-ok" : "badge-warn"}`}>{det?.engine || runRow?.detect_engine || "—"}</span></Row>
                <Row k="Runtime" v={det?.runtime_ms ? `${(det.runtime_ms / 1000).toFixed(1)} s` : null} />
                <Row k="Input range" v={seg?.metadata?.input_range} />
                <Row k="Weights" v={seg?.sha256 ? seg.sha256.slice(0, 12) : null} />
              </>
            )}
          </Section>
          <Section title="Detection metrics" testid="det-metrics">
            <Row k="Slick area (km²)" v={scanning ? "scanning…" : p ? `${num(p.area_km2)} largest · ${num(total, 2)} total` : st === "done" ? "no region" : null} testid="det-area" />
            <Row k="Detection confidence" v={scanning ? "scanning…" : det?.confidence != null ? `${(det.confidence * 100).toFixed(1)}%` : null} testid="det-confidence" />
            <Row k="Segmentation status" mono={false} testid="det-status"
              v={st === "done" ? `Completed · ${oil} oil + ${look} look-alike region${oil + look === 1 ? "" : "s"}` : st === "running" ? (scanning ? "Scanning…" : "Segmenting…") : st === "failed" ? "Failed" : "Ready"}
              tone={st === "done" ? "ok" : st === "failed" ? "danger" : undefined} />
            <Row k="Last run" v={utc(runRow?.finished_utc)} />
          </Section>
          <Section title="Sentinel-1 preprocessing" open={false}>
            {rows.map((r) => <Check_ key={r.label} {...r} />)}
          </Section>
          <Section title="AIS trajectories" open={false}>
            <div className="ip-r"><span className="ip-rk">Vessel tracks (run window)</span>
              <span className={`switch-track ${show.vessels ? "on" : ""}`} onClick={() => onShow("vessels", !show.vessels)}><span className="switch-knob" /></span></div>
          </Section>
          <Section title="Spatio-temporal controls">
            <Row k="Scene date" v={utc(sm.acquired_utc)} />
            <Row k="Scene location" v={selectedTile ? `${fmtTile(selectedTile.index)}` : p?.centroid ? latlon(p.centroid) : null} />
          </Section>
        </>
      ) : (
        <Section title="Detection response">
          <Row k="Candidates" v={det ? `${det.candidates?.length ?? 0} (${oil} oil · ${look} look-alike)` : null} />
          {(judged.detection.row?.warnings || []).map((w) => <div key={w} className="ip-note">{w}</div>)}
          {!det && <div className="dim">No detection response yet.</div>}
        </Section>
      )}
      <Foot>
        {st === "done" ? (
          <Primary onClick={() => actions.go("validation")} testid="validate-detection">Validate detection</Primary>
        ) : st === "running" ? (
          <Primary busy testid="validate-detection">Segmenting…</Primary>
        ) : (
          <Primary onClick={actions.run} disabled={!ctx.canRun} testid="validate-detection">Run detection</Primary>
        )}
      </Foot>
    </div>
  );
}

/* ----------------------------------------------------------- validation -- */

export function ValidationPanel({ ctx }) {
  const { layers, judged, actions, forcing, sceneT0, autoPreview, cine } = ctx;
  const det = layers.detect;
  const p = layers.slick?.features?.[0]?.properties;
  const st = judged.validation.state;
  const busy = st === "running" || st === "loading";
  /* validation beat: the three analyses resolve one after another */
  const valT = cine?.active && cine.beat?.id === "validate" ? cine.t : null;
  const analysing = (i) => busy || (valT != null && valT < (i + 1) / 3);
  const [tab, setTab] = useState("analysis");

  const wind = useMemo(() => {
    const f = forcing?.wind;
    if (!f || !p?.centroid) return null;
    const [u, v] = sampleField(f, p.centroid[0], p.centroid[1], sceneT0);
    const speed = Math.hypot(u, v);
    const toDeg = ((90 - (Math.atan2(v, u) * 180) / Math.PI) + 360) % 360;
    const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    return { speed, toDeg, from: dirs[Math.round(((toDeg + 180) % 360) / 45) % 8], provider: f.provider, mean: f.mean_speed, matches: f.matches_run };
  }, [forcing, p, sceneT0]);

  const verdict = screenVerdict(p, det);
  const elong = p?.major_axis_m && p?.minor_axis_m ? p.major_axis_m / p.minor_axis_m : null;
  const shape = elong == null ? null : elong >= 3 ? "Elongated" : elong >= 1.5 ? "Oblong" : "Compact";
  const threshold = autoPreview?.confidence_threshold;
  const conf = det?.confidence;
  const oil = (det?.candidates || []).filter((c) => c.class === "oil").length;
  const look = (det?.candidates || []).filter((c) => c.class === "lookalike").length;

  const outcome = st !== "done" ? null
    : verdict === "oil" ? { tone: "ok", title: "Validation complete", sub: "The screening model confirmed the analysed region as oil." }
      : verdict === "lookalike" ? { tone: "warn", title: "Look-alike / rejected", sub: "The analysed region lies inside a box the screening model rejected as a look-alike. Downstream stages traced it anyway; read them with that in mind." }
        : det?.engine !== "ml" ? { tone: "warn", title: "No screening verdict", sub: "The threshold fallback engine has no screening model; nothing confirmed or rejected this region." }
          : { tone: "neutral", title: "No screening verdict for this region", sub: "No screening box contains the analysed slick's centroid." };

  const Block = ({ icon: Icon, title, children, testid, i = 0 }) => (
    <div className="ip-vblock" data-testid={testid} data-analysing={analysing(i) ? "true" : "false"}>
      <div className="ip-vblock-head"><span className="ip-vblock-ico"><Icon size={18} /></span><span>{title}</span></div>
      <div className="ip-vblock-body">
        {analysing(i) ? <><div className="ip-analysing">Analysing…</div><div className="ip-progress"><span /></div></> : children}
      </div>
    </div>
  );
  const pending = busy || (valT != null && valT < 0.98);

  return (
    <div className="ip" data-testid="intel-validation">
      <PanelHead title="Look-alike Validation" />
      <Tabs value={tab} onChange={setTab} items={[{ id: "analysis", label: "Analysis" }, { id: "compare", label: "Compare" }]} />
      {tab === "analysis" ? (
        <>
          <Block icon={Wind} title="Wind conditions" testid="val-wind" i={0}>
            {wind ? (
              <>
                <Row k="At slick, scene time" v={`${wind.speed.toFixed(1)} m/s (${wind.from})`} testid="val-wind-speed" />
                <Row k="Field mean" v={wind.mean != null ? `${wind.mean.toFixed(1)} m/s` : null} />
                <Row k="Source" v={wind.provider} mono={false} />
                {wind.matches === false && <div className="ip-note warn">Not the grid this run's drift used.</div>}
                <div className="ip-note">{wind.speed < 2 ? "Very low wind: slick-like dark patches can be calm water (look-alike risk stated by the screen, not here)." : wind.speed > 10 ? "High wind disperses thin oil; a persistent dark patch is more likely real." : "Within the 2–10 m/s window in which oil damping is detectable in SAR."}</div>
              </>
            ) : <div className="dim">No wind grid is recorded for this run (turn on wind vectors to fetch it).</div>}
          </Block>
          <Block icon={Radar} title="Backscatter" testid="val-backscatter" i={1}>
            <Row k="Damping ratio" v={p?.damping_ratio != null ? `${num(p.damping_ratio, 1)} dB` : null} testid="val-damping" />
            <Row k="Segmenter confidence" v={conf != null ? `${(conf * 100).toFixed(1)}%${threshold != null ? ` (threshold ${threshold})` : ""}` : null} />
            <Row k="Screen verdict" mono={false} v={verdict === "oil" ? "Consistent — oil" : verdict === "lookalike" ? "Rejected — look-alike" : "None"}
              tone={verdict === "oil" ? "ok" : verdict === "lookalike" ? "warn" : undefined} testid="val-verdict" />
          </Block>
          <Block icon={Shapes} title="Shape / texture" testid="val-shape" i={2}>
            <Row k="Shape" v={shape ? `${shape} (${num(elong, 1)} : 1)` : null} mono={false} />
            <Row k="Orientation" v={p?.orientation_deg != null ? `${num(p.orientation_deg, 1)}°` : null} />
            <Row k="Area" v={p?.area_km2 != null ? `${num(p.area_km2)} km²` : null} />
            <Row k="Screen" v={det ? `${oil} oil · ${look} look-alike` : null} />
          </Block>
          <div className={`ip-outcome ${outcome && !pending ? `ip-outcome-${outcome.tone}` : ""}`} data-testid="val-outcome">
            {pending || !outcome ? (
              <>
                <span className="ip-outcome-ico"><Loader2 size={26} className="ws-spin" /></span>
                <div className="ip-outcome-title">Validation in progress…</div>
                <div className="ip-outcome-sub">Cross-checking with the screening model and environmental conditions.</div>
              </>
            ) : (
              <>
                <span className="ip-outcome-ico">{outcome.tone === "ok" ? <CheckCircle2 size={28} /> : <AlertTriangle size={28} />}</span>
                <div className="ip-outcome-title">{outcome.title}</div>
                <div className="ip-outcome-sub">{outcome.sub}</div>
                {p?.age_hours_estimate != null && (
                  <div className="ip-outcome-sub">Estimated age {num(p.age_hours_estimate, 1)} h · <b>Confidence: LOW</b></div>
                )}
              </>
            )}
          </div>
        </>
      ) : (
        <Section title="Screening candidates">
          <Row k="Oil" v={oil} /><Row k="Look-alike" v={look} />
          <div className="ip-note">Look-alikes are reported and drawn hatched on the map; they are never counted as oil.</div>
        </Section>
      )}
      <Foot>
        <Primary onClick={() => actions.go("geometry")} disabled={st !== "done"} testid="characterise-btn">Characterise slick</Primary>
      </Foot>
    </div>
  );
}

/* ------------------------------------------------------------- geometry -- */

export function GeometryPanel({ ctx }) {
  const { layers, judged, actions } = ctx;
  const st = judged.geometry.state;
  return (
    <div className="ip" data-testid="intel-geometry">
      <PanelHead title="Slick Characterisation" />
      <div className="ip-body">
        {st === "running" && <div className="ip-note"><Loader2 size={12} className="ws-spin" /> Characterising…</div>}
        <SpillPanel slick={layers.slick} detect={layers.detect} sceneMeta={layers.scene_meta} />
      </div>
      <Foot>
        <Primary onClick={() => actions.go("drift")} disabled={st !== "done"} testid="hindcast-btn">Run drift hindcast</Primary>
      </Foot>
    </div>
  );
}

/* ---------------------------------------------------------------- drift -- */

export function DriftIntelPanel({ ctx }) {
  const { layers, judged, actions, errors, loaded, show, onShow } = ctx;
  const md = layers.origin_cloud?.metadata;
  const fmd = layers.forecast?.metadata;
  const est = originEstimate(layers.origin_cloud);
  const st = judged.drift.state;
  return (
    <div className="ip" data-testid="intel-drift">
      <PanelHead title="Drift Analysis" />
      <Section title="Estimated origin" testid="drift-origin">
        <Row k="Estimated origin" v={est ? latlon(est.center) : null} testid="drift-origin-pos" />
        <Row k="Origin uncertainty" v={est ? `± ${num(est.radiusKm, est.radiusKm < 1 ? 2 : 1)} km` : null} testid="drift-origin-unc" />
        <Row k="Origin window" v={md ? `${utc(md.origin_window_start_utc)} → ${utc(md.origin_window_end_utc)}` : null} />
        {est?.weak && <div className="ip-note warn">The convergence peak sits at the image time: this is a window, not a discharge time.</div>}
      </Section>
      <Section title="Hindcast">
        <Row k="Past" v={md?.backtrack_hours != null ? `${md.backtrack_hours} h` : null} />
        <Row k="Particles" v={md?.n_particles?.toLocaleString()} />
        <Row k="Engine" v={md?.forcing?.engine} />
        <Row k="Source"><ProvBadge source={md?.source} /></Row>
      </Section>
      <Section title="Forecast">
        <Row k="Next" v={fmd?.horizons_h?.length ? fmd.horizons_h.map((h) => `+${h} h`).join(" · ") : null} />
        <Row k="Issued" v={utc(fmd?.issued_utc)} />
      </Section>
      <Section title="Environmental data">
        <Row k="Currents" v={md?.forcing?.currents?.provider || fmd?.forcing?.currents?.provider || null} mono={false} />
        <Row k="Wind" v={md?.forcing?.wind?.provider || fmd?.forcing?.wind?.provider || null} mono={false} />
        <div className="ip-toggles">
          <label className="ctl-check"><input type="checkbox" checked={Boolean(show.currents)} onChange={(e) => onShow("currents", e.target.checked)} /><span>Show currents</span></label>
          <label className="ctl-check"><input type="checkbox" checked={Boolean(show.wind)} onChange={(e) => onShow("wind", e.target.checked)} /><span>Show wind</span></label>
          <label className="ctl-check"><input type="checkbox" checked={Boolean(show.hindcast)} onChange={(e) => onShow("hindcast", e.target.checked)} /><span>Hindcast cloud</span></label>
          <label className="ctl-check"><input type="checkbox" checked={Boolean(show.forecast)} onChange={(e) => onShow("forecast", e.target.checked)} /><span>Forecast envelopes</span></label>
        </div>
      </Section>
      <Section title="Details" open={false}>
        <DriftPanel origin={layers.origin_cloud} forecast={layers.forecast} errors={errors} loaded={loaded} />
      </Section>
      <Foot>
        <Primary onClick={() => actions.go("ais")} disabled={st !== "done" && judged.ais.state !== "done"} testid="correlate-btn">Correlate AIS</Primary>
      </Foot>
    </div>
  );
}

/* ------------------------------------------------------------ correlation */

export function CorrelationPanel({ ctx }) {
  const { layers, judged, actions, runRow, funnel, selectedMmsi, onSelectMmsi, errors, aisStatus, cine } = ctx;
  /* ranking beat: candidates enter the list one by one, in rank order; the
   * AIS and filtering beats show the list empty until the ranking runs */
  const beat = cine?.active ? cine.beat?.id : null;
  const revealN = beat === "ranking" ? cine.reveal?.ranked ?? 0 : beat === "ais" || beat === "filter" ? 0 : null;
  const sus = useMemo(() => {
    const s = layers.suspects;
    if (!s || revealN == null) return s;
    return { ...s, suspects: (s.suspects || []).filter((x) => x.rank <= revealN) };
  }, [layers.suspects, revealN]);
  const p = layers.slick?.features?.[0]?.properties;
  const est = originEstimate(layers.origin_cloud);
  const sm = layers.scene_meta;
  const vmeta = layers.vessels?.metadata;
  const [tab, setTab] = useState("analysis");
  const st = judged.ais.state;
  const prov = aisProvenance(runRow, sus);
  const window = useMemo(() => {
    const feats = layers.vessels?.features || [];
    let a = null, b = null;
    for (const f of feats) {
      const s = Date.parse(f.properties?.start_utc), e = Date.parse(f.properties?.end_utc);
      if (Number.isFinite(s)) a = a == null ? s : Math.min(a, s);
      if (Number.isFinite(e)) b = b == null ? e : Math.max(b, e);
    }
    return a != null && b != null ? [a, b] : null;
  }, [layers.vessels]);
  const w = sus?.weights;
  const live = aisStatus?.stream;
  return (
    <div className="ip" data-testid="intel-ais">
      <PanelHead title="AIS Trajectory Correlation" />
      <Tabs value={tab} onChange={setTab} items={[{ id: "analysis", label: "Analysis" }, { id: "evidence", label: "Evidence" }]} />
      {tab === "analysis" ? (
        <>
          <Section title="Spill / origin">
            <Row k="Spill location" v={latlon(p?.centroid)} />
            <Row k="Estimated origin (uncertainty)" v={est ? `± ${num(est.radiusKm, est.radiusKm < 1 ? 2 : 1)} km` : null} />
            <Row k="Acquisition time" v={utc(sm?.acquired_utc)} />
            <Row k="Spill area" v={p ? `${num(p.area_km2)} km²` : null} />
          </Section>
          <Section title="AIS data" testid="ais-data">
            <Row k="Source" mono={false}>
              <span className={`badge badge-${prov.tone}`} data-testid="ais-source">{prov.label}</span>
              {runRow?.manifest?.ais?.detail && <span className="dim ip-small"> {runRow.manifest.ais.detail}</span>}
            </Row>
            <Row k="Time window" v={window ? `${utc(new Date(window[0]).toISOString())} → ${utc(new Date(window[1]).toISOString())}` : null} />
            <Row k="Total vessels (in area)" v={vmeta?.vessels ?? funnel?.found ?? null} />
            <Row k="Vessels analysed" v={sus?.total_vessels_considered ?? null} />
            <Row k="Candidates ranked" v={sus ? `${sus.suspects?.length ?? 0} · ${(sus.filtered_out || []).length} filtered` : null} />
            <Row k="Live AIS" mono={false}>
              {live?.functionally_working ? <span className="badge badge-ok">LIVE</span>
                : <span className="badge badge-warn" title={live?.note || ""}>{live?.state === "not_configured" ? "NOT CONFIGURED" : "NO COVERAGE IN CURRENT AREA"}</span>}
            </Row>
          </Section>
          <Section title="Spatio-temporal filters" testid="ais-filters">
            {funnel ? (
              <>
                <Row k="Distance from origin" v={`${funnel.exclusive_by_gate?.after_spatial ?? funnel.reasons_histogram?.["outside origin region"] ?? 0} excluded`} mono={false} title="Gate: outside origin region" />
                <Row k="Time window" v={`${funnel.exclusive_by_gate?.after_temporal ?? funnel.reasons_histogram?.["outside time window"] ?? 0} excluded`} mono={false} title="Gate: outside time window" />
                <Row k="Track vs slick axis" v={`${funnel.exclusive_by_gate?.after_trajectory ?? funnel.reasons_histogram?.["course incompatible with slick axis"] ?? 0} excluded`} mono={false} />
                <Row k="Vessel type filter" v={w?.vessel_prior != null ? `All (weighted ×${w.vessel_prior})` : "All"} mono={false} />
                <div className="ip-note">{funnel.note}</div>
              </>
            ) : <div className="dim">Funnel not available for this run.</div>}
          </Section>
          <Section title="Candidate ranking" testid="ais-ranking">
            {revealN != null && !sus?.suspects?.length ? (
              <div className="ip-analysing" data-testid="ranking-pending"><Loader2 size={12} className="ws-spin" /> {beat === "ranking" ? "Scoring candidates…" : beat === "filter" ? "Applying the spatial, temporal and trajectory gates…" : "Reconstructing AIS traffic…"}</div>
            ) : (
              <SuspectsPanel suspects={sus} error={errors.suspects} tracks={layers.vessels}
                selectedMmsi={selectedMmsi} onSelect={onSelectMmsi} />
            )}
          </Section>
          {w && (
            <div className="ip-note">Ranking based on: proximity ({Math.round(w.proximity * 100)}%), temporal ({Math.round(w.temporal * 100)}%), trajectory ({Math.round(w.trajectory * 100)}%), AIS continuity ({Math.round(w.ais_gap * 100)}%), behaviour ({Math.round(w.behaviour * 100)}%), vessel-type prior ({Math.round(w.vessel_prior * 100)}%).</div>
          )}
          <div className="ip-note"><Info size={12} /> This analysis suggests potential source vessel(s). It does not confirm responsibility.</div>
        </>
      ) : (
        <Section title="Excluded vessels">
          {(sus?.filtered_out || []).map((f) => (
            <Row key={f.mmsi} k={String(f.mmsi)} v={f.filter_reason || f.reason} mono={false} />
          ))}
          {!sus?.filtered_out?.length && <div className="dim">Nothing was filtered out.</div>}
        </Section>
      )}
      <Foot>
        <Primary onClick={() => actions.go("attribution")} disabled={st !== "done"} testid="view-candidates">View candidates</Primary>
      </Foot>
    </div>
  );
}

/* ------------------------------------------------------------ attribution */

const FACTORS = [
  ["proximity", "Spatial proximity"], ["temporal", "Temporal proximity"],
  ["trajectory", "Trajectory match"], ["behaviour", "Vessel motion consistency"],
  ["ais_gap", "AIS continuity"], ["vessel_prior", "Vessel-type prior"],
];

export function AttributionPanel({ ctx }) {
  const { layers, judged, actions, runRow, selectedMmsi, onSelectMmsi, dossier, sceneT0 } = ctx;
  const sus = layers.suspects;
  const list = sus?.suspects || [];
  const s = list.find((x) => x.mmsi === selectedMmsi) || list[0] || null;
  const [tab, setTab] = useState("vessel");
  const p = layers.slick?.features?.[0]?.properties;
  const est = originEstimate(layers.origin_cloud);
  const prov = aisProvenance(runRow, sus);
  const track = useMemo(() => (layers.vessels?.features || []).find((f) => f.properties?.mmsi === s?.mmsi), [layers.vessels, s]);
  const stateAt = useMemo(() => {
    if (!track) return null;
    const pr = track.properties;
    const t = { path: track.geometry.coordinates, times: (pr.times_epoch || []).map((v) => (v == null ? null : v * 1000)), headings: pr.headings_deg, sog: pr.sog_kn };
    const at = est?.tUtc ? Date.parse(est.tUtc) : sceneT0;
    return trackStateAt(t, at);
  }, [track, est, sceneT0]);
  const st = judged.attribution.state;
  const d = dossier && dossier.mmsi === s?.mmsi ? dossier : null;
  return (
    <div className="ip" data-testid="intel-attribution">
      <PanelHead title="Vessel Attribution" />
      <Tabs value={tab} onChange={setTab} items={[{ id: "vessel", label: "Vessel" }, { id: "evidence", label: "Evidence" }]} />
      {!s ? (
        <div className="ip-empty">
          <Ship size={22} />
          <div>{st === "done" ? "No vessel passed the spatial, temporal and trajectory gates for this origin window." : "Attribution has not run."}</div>
        </div>
      ) : tab === "vessel" ? (
        <>
          <Section title="Potential source vessel" testid="attr-vessel">
            <div className="ip-vessel-head">
              <span className="ip-vessel-name">{s.vessel_name || `MMSI ${s.mmsi}`}</span>
              <span className={`badge ${s.rank === 1 ? "badge-ok" : "badge-neutral"}`} data-testid="attr-rank-badge">
                {s.rank === 1 ? "Highest-Ranked Candidate" : `Candidate #${s.rank}`}
              </span>
            </div>
            {/* Identity is the same block the vessel's own page shows. It used
                to be a weaker copy here, with a DWT row no provider fills and a
                slot for a photograph that does not exist. */}
            <VesselIdentity dossier={d} mmsi={s.mmsi} name={s.vessel_name} type={s.vessel_type} linkOut />
            <div className="ip-grid2">
              <div><span className="ip-gk">Speed (at origin time)</span><span className="ip-gv mono">{stateAt?.sog != null ? `${stateAt.sog.toFixed(1)} kn` : "—"}</span></div>
              <div><span className="ip-gk">Distance from slick</span><span className="ip-gv mono">{s.evidence?.closest_approach_km != null ? `${num(s.evidence.closest_approach_km, 1)} km` : "—"}</span></div>
              <div><span className="ip-gk">Course (at origin time)</span><span className="ip-gv mono">{stateAt?.heading != null ? `${Math.round(stateAt.heading)}°` : "—"}</span></div>
              <div><span className="ip-gk">Time in origin window</span><span className="ip-gv mono">{s.evidence?.time_in_origin_window_min != null ? `${Math.round(s.evidence.time_in_origin_window_min)} min` : "—"}</span></div>
            </div>
            <div className="ip-r"><span className="ip-rk">AIS source</span><span className={`badge badge-${prov.tone}`} data-testid="attr-ais-source">{prov.label}</span></div>
            {prov.label === "SYNTHETIC" && <div className="ip-note">Synthetic AIS carries no identity: the MMSI is the whole record.</div>}
          </Section>
          <Section title="Attribution analysis" testid="attr-analysis">
            {FACTORS.map(([k, label]) => s.sub_scores?.[k] != null && (
              <FactorBar key={k} name={label} value={s.sub_scores[k]} weight={sus?.weights?.[k]} tone="accent" />
            ))}
            <div className="ip-combined">
              <span className="ip-combined-k">Combined attribution score</span>
              <Meter value={s.total_score} tone="gradient" />
              <span className="ip-combined-v mono" data-testid="attr-score">{num(s.total_score, 2)}</span>
            </div>
            <div className="ip-note"><Info size={12} /> Score indicates relative likelihood based on available evidence. Not a definitive determination of responsibility.</div>
          </Section>
          <Section title="Seen before" testid="attr-history">
            {/* The cross-run index: every other run that considered this MMSI,
                which is the question a second sighting of a ship raises. */}
            <VesselHistory dossier={d} exceptRun={runRow?.id} />
          </Section>
          {list.length > 1 && (
            <Section title="Other candidates" open={false}>
              {list.filter((x) => x.mmsi !== s.mmsi).map((x) => (
                <button key={x.mmsi} className="ip-cand" onClick={() => onSelectMmsi(x.mmsi)}>
                  <span className="mono">#{x.rank}</span><span>{x.vessel_name || `MMSI ${x.mmsi}`}</span><span className="mono">{num(x.total_score, 2)}</span>
                </button>
              ))}
            </Section>
          )}
        </>
      ) : (
        <Section title="Evidence behind the score">
          {Object.entries(s.evidence || {}).map(([k, v]) => <Row key={k} k={k.replace(/_/g, " ")} v={v} />)}
          {s.reason && <div className="ip-note">{s.reason}</div>}
        </Section>
      )}
      <Foot>
        {s && (
          <Link className="ip-secondary" to={url.vessel(s.mmsi)} data-testid="view-vessel-details">
            View vessel details <ExternalLink size={13} />
          </Link>
        )}
        <Primary onClick={() => actions.go("evidence")} testid="review-evidence">Review evidence</Primary>
      </Foot>
    </div>
  );
}

/* ---------------------------------------------------------------- evidence */

const VERDICTS = ["accepted", "rejected", "inconclusive", "annotated"];

export function EvidencePanel({ ctx }) {
  const { layers, judged, actions, runRow, inv, incident, autoPreview, decisions, verify, canRun, onDecision, onCreateIncident, busy, zones, users, incidentError } = ctx;
  const m = runRow?.manifest;
  const [verdict, setVerdict] = useState("annotated");
  const [note, setNote] = useState("");
  const zoneName = incident?.zone_id ? (zones || []).find((z) => z.id === incident.zone_id)?.name || incident.zone_id : null;
  const officer = incident?.assignee_id != null ? (users || []).find((u) => u.id === incident.assignee_id) : null;
  const gate = autoPreview;
  return (
    <div className="ip" data-testid="intel-evidence">
      <PanelHead title="Investigation Record" />
      <Section title="Record" testid="record-section">
        <Row k="Investigation ID" v={inv?.id || runRow?.investigation_id || "unfiled run"} />
        <Row k="Run ID" v={runRow?.run_id} />
        <Row k="Incident ID" v={incident?.id || runRow?.incident_id || null} />
        <Row k="Created at" v={utc(inv?.created_utc || runRow?.started_utc)} />
        <Row k="Created by" v={null} title="The run registry does not record an actor; the audit trail does" />
        <Row k="Assigned zone" v={zoneName} mono={false} />
        <Row k="Assigned officer" v={officer ? (officer.display_name || officer.email) : incident?.assignee_id != null ? `user #${incident.assignee_id}` : null} mono={false} />
        <Row k="Status" mono={false}><span className={`badge badge-${runRow?.status === "complete" ? "ok" : "warn"}`}>{runRow?.status || "—"}</span>{incident && <> <span className="badge badge-accent">incident {incident.status}</span></>}</Row>
        <Row k="Registry source" v={runRow?.registry_source} />
      </Section>
      <Section title="Sealed evidence" testid="sealed-section">
        <Row k="Artefact digest" v={m?.artefact_digest ? `⌗ ${String(m.artefact_digest).slice(0, 16)}` : null} title={m?.artefact_digest} />
        <Row k="Verification" mono={false}>
          {verify ? verify.unverifiable ? <span className="badge badge-neutral">UNVERIFIABLE</span>
            : verify.ok ? <span className="badge badge-ok">OK · {verify.checked} files</span>
              : <span className="badge badge-danger">{verify.problems?.length} PROBLEMS</span> : "—"}
        </Row>
        <Row k="Stages real" v={runRow ? `${runRow.stages_real}/${runRow.stages_total}` : null} />
        <Row k="Code" v={m?.code_git_sha ? String(m.code_git_sha).slice(0, 10) : null} />
        <table className="ws-table">
          <thead><tr><th>Stage</th><th>Status</th><th>Data</th><th>Engine</th></tr></thead>
          <tbody>
            {(m?.stages || []).map((s) => (
              <tr key={s.stage}><td>{s.stage}</td><td><span className={`badge badge-${s.status === "ok" ? "ok" : s.status === "failed" ? "danger" : "warn"}`}>{s.status}</span></td>
                <td><ProvBadge source={s.data_source || s.source} /></td><td className="mono">{s.engine_used || "—"}</td></tr>
            ))}
          </tbody>
        </table>
        {(m?.artefacts || []).map((a) => <Row key={a.file} k={a.file} v={`${String(a.sha256).slice(0, 10)} · ${(a.bytes / 1024).toFixed(0)} kB`} />)}
      </Section>
      <Section title="Incident" testid="incident-section">
        {incident ? (
          <div className="ip-incident ok">
            <div className="ip-incident-title"><CheckCircle2 size={14} /> Incident created</div>
            <Row k="Incident ID" v={incident.id} />
            <Row k="Zone" v={zoneName || (incident.zone_id ? incident.zone_id : "unzoned")} mono={false} />
            <Row k="Assigned officer" v={officer ? (officer.display_name || officer.email) : incident.assignee_id != null ? `user #${incident.assignee_id}` : "unassigned"} mono={false} />
            <Row k="Status" v={incident.status} mono={false} />
            <Row k="Severity" v={incident.severity} mono={false} />
            <Row k="Origin" v={incident.origin === "auto" ? "automatic gate" : incident.origin || "—"} mono={false} />
            <Link className="ip-secondary" to={`/operations/incidents?focus=${incident.id}`}>Open incident <ExternalLink size={12} /></Link>
          </div>
        ) : gate ? (
          <>
            <Row k="Automatic gate" mono={false}>
              <span className={`badge badge-${gate.validated ? "ok" : "warn"}`} data-testid="gate-verdict">{gate.validated ? "PASSED" : "NOT PASSED"}</span>
            </Row>
            <Row k="Severity" v={gate.severity} mono={false} />
            <Row k="Age confidence" v={gate.age_confidence_label ? String(gate.age_confidence_label).toUpperCase() : null} mono={false} />
            <ul className="cb-list">{(gate.reasons || []).map((r) => <li key={r}>{r}</li>)}</ul>
            {incidentError && <div className="ip-note danger">{incidentError}</div>}
            <button className="ip-secondary" onClick={() => onCreateIncident(false)} disabled={!canRun || busy === "incident" || !gate.validated}
              title={!canRun ? "Needs investigator or analyst" : !gate.validated ? "The gate refused; an override records the refusal on the incident" : ""}
              data-testid="create-incident">
              {busy === "incident" ? <Loader2 size={12} className="ws-spin" /> : null} Create incident from this run
            </button>
            {!gate.validated && canRun && (
              <button className="ip-secondary" onClick={() => onCreateIncident(true)} disabled={busy === "incident"} data-testid="create-incident-force">
                Override the gate (recorded)
              </button>
            )}
          </>
        ) : <div className="dim">Gate preview not available.</div>}
      </Section>
      <Section title="Decisions" testid="decisions-section">
        {(decisions || []).map((d) => (
          <div key={d.id} className="ip-decision">
            <span className={`badge badge-${d.verdict === "accepted" ? "ok" : d.verdict === "rejected" ? "danger" : "neutral"}`}>{d.verdict}</span>
            <span className="mono dim">{d.mmsi ? `MMSI ${d.mmsi}` : "run"} · {d.actor} · {utc(d.decided_utc)}</span>
            {d.note && <div className="ip-small">{d.note}</div>}
          </div>
        ))}
        {!decisions?.length && <div className="dim">No analyst decision recorded for this run.</div>}
        <div className="ip-decide">
          <select value={verdict} onChange={(e) => setVerdict(e.target.value)} data-testid="decision-verdict">
            {VERDICTS.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" data-testid="decision-note" />
          <button className="ip-secondary" disabled={!canRun || busy === "decision"} onClick={() => { onDecision(verdict, note); setNote(""); }}
            title={!canRun ? "Needs investigator or analyst" : "A verdict on the ranking, never on a person"} data-testid="record-decision">Record</button>
        </div>
        <div className="ip-note">A verdict judges whether the ranking was sound and worth pursuing; it never names anyone as responsible.</div>
      </Section>
      <Foot>
        {runRow && <a className="ip-secondary" href={`/api/runs/${runRow.run_id}/export`} download><Download size={13} /> Export bundle</a>}
        <Primary onClick={() => actions.go("report")} disabled={judged.report.state !== "done"} testid="generate-report">Generate incident report</Primary>
      </Foot>
    </div>
  );
}

/* ------------------------------------------------------------------ report */

/** "Sentinel-1A GRD", read from the record or the product name. */
const sceneTitle = (sm) => `${sceneFact(sm, "mission")} ${sceneFact(sm, "product")}`.replace(/not recorded not recorded/, "Scene");

export function ReportPanel({ ctx }) {
  const { layers, runRow, runId, funnel, incident, reports, onComposeReport, onSubmitReport, onPublishReport, canRun, canPublish, busy, reportError, actions } = ctx;
  const report = (reports || []).reduce((best, r) => (!best || r.version > best.version ? r : best), null);
  return (
    <div className="ip ip-report" data-testid="intel-report">
      <PanelHead title="Incident Report" />
      <Section title="Report status" testid="report-status">
        <Row k="Composed" mono={false}>
          {report ? <span className={`badge badge-${report.status === "published" ? "ok" : report.status === "in_review" ? "accent" : "warn"}`} data-testid="report-state">{report.status.replace("_", " ")} · v{report.version}</span>
            : <span className="badge badge-ghost">not composed</span>}
        </Row>
        {report && <Row k="Report ID" v={report.id} />}
        {report && <Row k="Digest" v={report.artefact_digest ? String(report.artefact_digest).slice(0, 12) : null} title={report.digest_matches_run === false ? "The run's artefacts were re-sealed after this report was composed" : ""} />}
        {reportError && <div className="ip-note danger">{reportError}</div>}
        <div className="ip-report-actions">
          {!report && (
            <button className="ip-secondary" onClick={onComposeReport} disabled={!canRun || busy === "report"} data-testid="compose-report"
              title={!canRun ? "Composing needs investigator or analyst" : "Snapshots the document from the sealed artefacts"}>
              {busy === "report" ? <Loader2 size={12} className="ws-spin" /> : <FileText size={12} />} Compose report
            </button>
          )}
          {report?.status === "draft" && <button className="ip-secondary" onClick={() => onSubmitReport(report.id)} disabled={!canRun || busy === "report"} data-testid="submit-report">Submit for review</button>}
          {report?.status === "in_review" && <button className="ip-secondary" onClick={() => onPublishReport(report.id)} disabled={!canPublish || busy === "report"} title={!canPublish ? "Publishing needs reviewer, investigator or admin" : ""} data-testid="publish-report">Publish</button>}
          {/* There is no server-side PDF (BACKEND_GAPS G5). This opens the
              printable document, which the browser prints to PDF -- so that is
              what the control says. It used to say "Download PDF". */}
          {runId && (
            <a className="ip-secondary" href={url.reportPrint(runId)} target="_blank" rel="noreferrer" data-testid="open-printable"
              title="Opens the printable report; use your browser's Print to save it as a PDF">
              <Printer size={12} /> Printable report
            </a>
          )}
          {/* The machine-readable exports the server does produce. */}
          {report && (
            <>
              <a className="ip-secondary" href={`/api/reports/${report.id}/export.csv`} data-testid="report-csv">
                <Download size={12} /> CSV
              </a>
              <a className="ip-secondary" href={`/api/reports/${report.id}/export.json`} data-testid="report-json">
                <Download size={12} /> JSON
              </a>
            </>
          )}
        </div>
        <div className="ip-note">Reports are zone-scoped: a zone officer can read reports only for the zones they hold; the server enforces this.</div>
      </Section>
      <div className="ip-report-preview" data-testid="report-preview">
        <IncidentReport runId={runId} run={runRow} sceneMeta={layers.scene_meta} slick={layers.slick} detect={layers.detect}
          origin={layers.origin_cloud} forecast={layers.forecast} suspects={layers.suspects} funnel={funnel}
          incident={incident} report={report} generatedUtc={report?.created_utc || new Date().toISOString()} compact />
      </div>
      <Foot>
        <button className="ip-secondary" onClick={() => actions.go("attribution")} data-testid="back-to-analysis">← Back to analysis</button>
        {runId && <a className="ip-primary ip-primary-accent" href={url.reportPrint(runId)} target="_blank" rel="noreferrer"><span>Open report</span><ExternalLink size={14} /></a>}
      </Foot>
    </div>
  );
}

/* --------------------------------------------------------------- switch -- */

export default function IntelPanel({ ctx }) {
  switch (ctx.panel) {
    case "scene": return <ScenePanel ctx={ctx} />;
    case "processing": return <ProcessingPanel ctx={ctx} />;
    case "tiling": return <TilingPanel ctx={ctx} />;
    case "detection": return <DetectionPanel ctx={ctx} />;
    case "validation": return <ValidationPanel ctx={ctx} />;
    case "geometry": return <GeometryPanel ctx={ctx} />;
    case "drift": return <DriftIntelPanel ctx={ctx} />;
    case "ais": return <CorrelationPanel ctx={ctx} />;
    case "attribution": return <AttributionPanel ctx={ctx} />;
    case "evidence": return <EvidencePanel ctx={ctx} />;
    case "report": return <ReportPanel ctx={ctx} />;
    default: return <ScenePanel ctx={ctx} />;
  }
}
