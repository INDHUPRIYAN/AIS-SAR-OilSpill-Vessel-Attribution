/* Satellite -- SAR imagery analysis.
 *
 * A scene, what the detector saw in it, and what the characteriser measured.
 * The three are kept visually distinct because they are different kinds of
 * claim: the SAR raster is an OBSERVATION, the mask is a MODEL OUTPUT, and the
 * slick geometry is a MEASUREMENT DERIVED FROM that output. An interface that
 * blends them invites an operator to read a segmentation as a photograph.
 *
 * Every value is read from the run's contract files. Where the characteriser
 * produced nothing, the row says so rather than showing a zero -- and the age
 * estimate always carries its LOW confidence, because it is a damping-ratio
 * heuristic with no ground truth behind it and a bare "6.9 h" reads as a
 * measurement.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, Crosshair, Download, Eye, EyeOff, Film, Layers, Radar, RefreshCw,
  Satellite as SatIcon, ScanLine, Search,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, ProvenanceBadge, Segmented, Tile,
} from "../components/ui";
import { fmtLat, fmtLon } from "../components/Globe";
import { api, fmt, useApi } from "../lib/api";
import { url } from "../lib/urls";
import { sceneFact } from "../lib/sceneName";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));

export default function Satellite() {
  const [params, setParams] = useSearchParams();
  const [showMask, setShowMask] = useState(true);
  const [stretch, setStretch] = useState("frozen");

  /* Scenes that have actually been through the pipeline -- a run is the only
   * way this system holds a scene AND what was found in it. */
  const runsQ = useApi(() => api.listRunsPaged({ status: "complete", limit: 60 }), [],
                       { interval: 60000 });
  const localQ = useApi(() => api.localScenes(), []);

  const runs = runsQ.data?.items || [];
  const runId = params.get("run") || runs[0]?.run_id || null;
  const run = runs.find((r) => r.run_id === runId) || null;

  const [layers, setLayers] = useState(null);
  useEffect(() => {
    if (!runId) { setLayers(null); return undefined; }
    let alive = true;
    setLayers(null);
    Promise.all([
      api.layer(runId, "scene_meta").catch(() => null),
      api.layer(runId, "detect").catch(() => null),
      api.layer(runId, "slick").catch(() => null),
      api.getRun(runId).catch(() => null),
    ]).then(([sceneMeta, detect, slick, meta]) => {
      if (alive) setLayers({ sceneMeta, detect, slick, meta });
    });
    return () => { alive = false; };
  }, [runId]);

  const sceneMeta = layers?.sceneMeta;
  const detect = layers?.detect;
  const slickProps = layers?.slick?.features?.[0]?.properties || null;
  const candidates = detect?.candidates || [];
  const lookalikes = candidates.filter((c) => c.class === "lookalike");
  const oil = candidates.filter((c) => c.class === "oil");

  const centre = useMemo(() => {
    const bb = sceneMeta?.bbox;
    return bb?.length === 4 ? { lon: (bb[0] + bb[2]) / 2, lat: (bb[1] + bb[3]) / 2 } : null;
  }, [sceneMeta]);

  const cached = (localQ.data?.scenes || []).filter((s) => s.available);

  return (
    <div className="page" data-testid="satellite-page">
      <PageHeader icon={<SatIcon size={17} />} kicker="Intelligence" title="Satellite"
        sub="Sentinel-1 SAR scenes, what the detector found in them, and what was measured from it."
        actions={<>
          <button className="btn btn-sm" onClick={runsQ.reload}><RefreshCw size={12} /> Refresh</button>
          {runId && (
            <Link className="btn btn-sm" to={url.workspace({ run: runId })}>
              <Radar size={12} /> Workspace
            </Link>
          )}
        </>} />

      <div className="grid grid-4 mb-3">
        <Tile label="Scenes analysed" value={runsQ.data ? fmt.int(runsQ.data.total) : "—"}
          sub="completed runs" tone="accent" />
        <Tile label="Scenes cached locally" value={cached.length || "—"}
          sub={localQ.data ? `${(localQ.data.scenes || []).length} known` : "reading"} />
        <Tile label="Oil candidates" value={detect ? oil.length : "—"}
          tone={oil.length ? "danger" : undefined} sub="in the selected scene" />
        <Tile label="Look-alikes" value={detect ? lookalikes.length : "—"} tone="warn"
          sub="reported, not counted as oil" />
      </div>

      <div className="split-wide">
        {/* ----------------------------------------------------- imagery --- */}
        <div className="stack" style={{ gap: 12 }}>
          <Panel title="Scene imagery" icon={<ScanLine size={12} />}
            right={<>
              <Segmented value={showMask ? "mask" : "raw"} testidPrefix="sat-view"
                onChange={(v) => setShowMask(v === "mask")} items={[
                  { id: "raw", label: "SAR", icon: <Eye size={11} /> },
                  { id: "mask", label: "Detection mask", icon: <Layers size={11} /> },
                ]} />
              {runId && (
                <a className="btn btn-sm" href={`/api/runs/${runId}/export`} download
                  title="Download the run's contract artefacts">
                  <Download size={12} /> Bundle
                </a>
              )}
            </>}>
            {!runId ? (
              <DataState kind="empty" title="No satellite scene available"
                hint="No completed run holds a scene. Run an investigation from the Workspace to produce one." />
            ) : (
              <>
                <div className="sat-frame">
                  <img className="sat-img" src={`/api/runs/${runId}/scene_png`}
                    alt={`Sentinel-1 SAR scene ${sceneMeta?.scene_id || runId}`}
                    onError={(e) => { e.currentTarget.dataset.failed = "1"; e.currentTarget.style.opacity = 0.1; }} />
                  {showMask && (
                    /* The mask is a MODEL OUTPUT laid over an observation. It
                       is tinted and labelled so the two can never be read as
                       the same kind of thing. */
                    <img className="sat-mask" src={`/api/runs/${runId}/mask_png`}
                      alt="Segmentation mask"
                      onError={(e) => { e.currentTarget.style.display = "none"; }} />
                  )}
                  <div className="sat-tags">
                    <Badge tone="neutral">{sceneFact(sceneMeta, "mission").toUpperCase()} · {sceneFact(sceneMeta, "polarisation")}</Badge>
                    {showMask && <Badge tone="danger">U-NET MASK OVERLAY</Badge>}
                    {layers?.meta?.detect_engine && (
                      <Badge tone={layers.meta.detect_engine === "ml" ? "ok" : "warn"}>
                        engine {layers.meta.detect_engine}
                      </Badge>
                    )}
                  </div>
                  <div className="sat-cap mono">
                    <span>{sceneMeta?.scene_id || runId}</span>
                    <span>{sceneMeta?.acquired_utc ? fmt.utc(sceneMeta.acquired_utc) : "—"}</span>
                  </div>
                </div>
                <div className="tiny dim mt-2" style={{ lineHeight: 1.55 }}>
                  The raster is the observation; the overlay is what the segmenter produced from
                  it. They are different kinds of claim and are never blended into one image.
                </div>
              </>
            )}
          </Panel>

          {detect && candidates.length > 0 && (
            <Panel title={`Detector candidates · ${candidates.length}`} icon={<Crosshair size={12} />} flush>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Class</th><th>Phenomenon</th><th className="num">Score</th><th>Bounding box</th></tr>
                  </thead>
                  <tbody>
                    {candidates.map((c, i) => (
                      <tr key={i} className={c.class === "lookalike" ? "row-dim" : ""}>
                        <td>
                          <Badge tone={c.class === "oil" ? "danger" : "teal"}>{c.class}</Badge>
                        </td>
                        <td className="tiny">{c.phenomenon?.replace(/_/g, " ") || "unclassified"}</td>
                        <td className="num mono tiny">{c.score != null ? num(c.score) : "—"}</td>
                        <td className="mono tiny dim">
                          {Array.isArray(c.bbox)
                            ? c.bbox.map((v) => Number(v).toFixed(2)).join(", ") : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {lookalikes.length > 0 && (
                <div className="panel-foot">
                  <Notice tone="warn">
                    Look-alikes are REPORTED and never deleted, and never counted as oil. A biogenic
                    film or a low-wind patch that looks like a slick is evidence about the scene,
                    not a detection.
                  </Notice>
                </div>
              )}
            </Panel>
          )}
        </div>

        {/* -------------------------------------------------- scene meta --- */}
        <div className="stack" style={{ gap: 12 }}>
          <Panel title="Scene" icon={<SatIcon size={12} />} flush
            right={runs.length > 1 ? (
              <select className="sm" value={runId || ""} style={{ maxWidth: 190 }}
                onChange={(e) => setParams({ run: e.target.value }, { replace: true })}
                data-testid="scene-select">
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>{r.scene_id || r.run_id}</option>
                ))}
              </select>
            ) : null}>
            <div className="panel-body">
              {!sceneMeta ? (
                <DataState kind={runId ? "loading" : "empty"} compact
                  title={runId ? "Reading scene metadata" : "No scene selected"} />
              ) : (
                <div className="kv-dense">
                  <KV k="Scene id" v={sceneMeta.scene_id || "—"} wrap />
                  <KV k="Acquired" v={fmt.utc(sceneMeta.acquired_utc)} />
                  <KV k="Sensor" v={`${sceneFact(sceneMeta, "mission")} · ${sceneFact(sceneMeta, "polarisation")}`} />
                  <KV k="Mode" v={sceneFact(sceneMeta, "mode")} />
                  <KV k="Provider" v={sceneMeta.provider_used || "—"} />
                  <KV k="Centre" v={centre ? `${fmtLat(centre.lat)}  ${fmtLon(centre.lon)}` : "—"} />
                  <KV k="Coverage (bbox)" v={sceneMeta.bbox
                    ? sceneMeta.bbox.map((v) => Number(v).toFixed(3)).join(", ") : "—"} wrap />
                  <KV k="Provenance" v={<ProvenanceBadge source={sceneMeta.source} />} />
                </div>
              )}
            </div>
          </Panel>

          <Panel title="Slick characterisation" icon={<Crosshair size={12} />}>
            {!slickProps ? (
              <DataState kind="empty" compact title="No slick in this scene"
                hint={runId ? "The characteriser produced no geometry for this run." : "Select a scene."} />
            ) : (
              <>
                <div className="kv-dense">
                  <KV k="Area" v={`${num(slickProps.area_km2)} km²`} testid="sat-area" />
                  <KV k="Perimeter" v={`${num(slickProps.perimeter_km)} km`} />
                  <KV k="Centroid" v={slickProps.centroid
                    ? `${num(slickProps.centroid[1], 4)}, ${num(slickProps.centroid[0], 4)}` : "—"} />
                  <KV k="Major axis" v={`${num((slickProps.major_axis_m ?? 0) / 1000)} km`} />
                  <KV k="Minor axis" v={`${num((slickProps.minor_axis_m ?? 0) / 1000)} km`} />
                  <KV k="Orientation" v={`${num(slickProps.orientation_deg, 1)}°`} />
                  <KV k="Damping ratio" v={slickProps.damping_ratio == null
                    ? "—" : `${num(slickProps.damping_ratio, 1)} dB`} />
                  <KV k="Detection confidence"
                    v={`${num((slickProps.confidence ?? 0) * 100, 1)}%`} />
                </div>
                {/* Standing rule: the age estimate always carries its LOW
                    confidence. It is a damping-ratio heuristic with no ground
                    truth, and a bare "6.9 h" reads as a measurement. */}
                <div className="kv">
                  <span className="kv-k">Age estimate</span>
                  <span className="kv-v">
                    {slickProps.age_hours_estimate == null
                      ? "—"
                      : <>{num(slickProps.age_hours_estimate, 1)} h <Badge tone="warn">LOW CONFIDENCE</Badge></>}
                  </span>
                </div>
                {slickProps.source && (
                  <div className="kv">
                    <span className="kv-k">Source</span>
                    <span className="kv-v"><ProvenanceBadge source={slickProps.source} /></span>
                  </div>
                )}
              </>
            )}
          </Panel>

          {runId && (
            <Panel title="Next" icon={<Film size={12} />}>
              <div className="globe-actions" style={{ marginTop: 0 }}>
                <Link className="btn btn-primary btn-sm" to={url.workspace({ run: runId })}>
                  <Radar size={12} /> Open in workspace
                </Link>
                <Link className="btn btn-sm" to={url.replay(runId)}><Film size={12} /> Replay</Link>
                <Link className="btn btn-sm" to={`/system/environment?run=${runId}`}>Drift &amp; forcing</Link>
              </div>
            </Panel>
          )}
        </div>
      </div>

      <style>{`
        .sat-frame { position: relative; background: #05080f; border: 1px solid var(--line);
          border-radius: var(--r-sm); overflow: hidden; min-height: 260px; display: grid; }
        .sat-img, .sat-mask { grid-area: 1 / 1; width: 100%; display: block; object-fit: contain; max-height: 62vh; }
        .sat-mask { mix-blend-mode: screen; opacity: 0.75; }
        .sat-tags { position: absolute; top: 8px; left: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
        .sat-cap { position: absolute; left: 0; right: 0; bottom: 0; display: flex;
          justify-content: space-between; gap: 10px; padding: 6px 10px;
          background: linear-gradient(180deg, transparent, rgba(4,8,16,0.9));
          color: #dbe6f3; font-size: var(--fs-xs); }
      `}</style>
    </div>
  );
}
