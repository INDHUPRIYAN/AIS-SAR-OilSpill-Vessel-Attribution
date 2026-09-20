/* The analytical contexts inside an investigation stage.
 *
 * A stage is a pipeline fact (detect, drift, attribution); an analyst thinks
 * in finer steps -- the mask, the wind, the current, the hindcast's physics,
 * the forecast horizons, the traffic, the gates, the ranking. Each of those
 * gets its own right-hand panel here, so the panel changes with the question
 * being asked instead of staying one long drift or AIS card.
 *
 * The rule is the workspace's: every number is quoted from an artefact or an
 * API response, or computed on screen from two such numbers and labelled as
 * that (windage × wind speed, centroid-to-centroid distance). A value the
 * pipeline did not produce renders "—" with the reason in its tooltip.
 * Nothing here knows a coordinate, a score or a vessel in advance.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, ArrowDown, Info, Loader2, Navigation, Ship } from "lucide-react";

import { Meter, Primary, Row, Section, latlon, num, utc } from "./intel";
import { aisProvenance } from "./IntelPanels";
import { sourceBadge } from "./palette";
import { fmtTile } from "../../lib/stages";
import { originEstimate } from "../../lib/drift";
import { bearingDeg, haversineKm, sampleField } from "../../lib/replay";

/* --------------------------------------------------------------- shared -- */

function Head({ title, badge }) {
  return (
    <div className="ip-head">
      <span className="ip-head-title">{title}</span>
      {badge}
    </div>
  );
}
const Foot = ({ children }) => <div className="ip-foot">{children}</div>;
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg) => (deg == null ? null : COMPASS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]);

/** A headline number with its unit and caption: the panel's "answer". */
export function Metric({ label, value, unit, sub, tone, testid }) {
  return (
    <div className={`ap-metric ${tone ? `tone-${tone}` : ""}`}>
      <span className="ap-metric-k">{label}</span>
      <span className="ap-metric-v mono" data-testid={testid}>{value ?? "—"}{value != null && unit ? <em>{unit}</em> : null}</span>
      {sub && <span className="ap-metric-sub">{sub}</span>}
    </div>
  );
}

function ProvBadge({ source }) {
  if (!source) return <span className="badge badge-ghost">UNRECORDED</span>;
  const b = sourceBadge(source);
  return <span className={`badge badge-${b.tone}`}>{b.label === "—" ? String(source).toUpperCase() : b.label}</span>;
}

/** A run image that says so when the run holds no such raster. */
function RunImage({ src, alt, overlay, caption, testid }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="ap-img" data-testid={testid}>
      {failed ? <div className="ap-img-none">Not available for this run</div> : (
        <div className="ap-img-box">
          <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />
          {overlay && <img className="ap-img-over" src={overlay} alt="" loading="lazy" onError={(e) => { e.currentTarget.style.display = "none"; }} />}
        </div>
      )}
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

/* --------------------------------------------------------- segmentation -- */

export function SegmentationPanel({ ctx }) {
  const { layers, judged, runId, selectedTile, actions, cine } = ctx;
  const det = layers.detect;
  const feats = layers.slick?.features || [];
  const p = feats[0]?.properties;
  const total = feats.reduce((a, f) => a + (f.properties?.area_km2 || 0), 0);
  const st = judged.detection.state;
  const tracing = cine?.active && cine.beat?.id === "segment" && cine.t < 0.95;
  return (
    <div className="ip" data-testid="intel-segmentation">
      <Head title="Oil-Slick Segmentation" badge={<ProvBadge source={p?.source} />} />
      {runId && (
        <div className="ap-imgrow">
          <RunImage src={`/api/runs/${runId}/scene_png?size=512`} alt="SAR scene" caption="SAR · σ⁰ dB (model input)" testid="seg-sar" />
          <RunImage src={`/api/runs/${runId}/scene_png?size=512`} overlay={`/api/runs/${runId}/mask_png`} alt="Segmentation mask"
            caption="Segmentation mask (raw_mask.tif)" testid="seg-mask" />
        </div>
      )}
      <div className="ap-metrics">
        <Metric label="Detected area" value={tracing ? null : p ? num(p.area_km2) : null} unit=" km²" sub={feats.length > 1 ? `${num(total)} km² over ${feats.length} regions` : "largest region"} testid="seg-area" />
        <Metric label="Confidence" value={tracing ? null : p?.confidence != null ? (p.confidence * 100).toFixed(1) : null} unit="%" sub="segmenter, analysed region" testid="seg-confidence" />
      </div>
      <Section title="Geometry">
        <Row k="Status" mono={false} v={tracing ? "Tracing the boundary…" : st === "done" ? (p ? "Polygon extracted" : "No region segmented") : st === "running" ? "Segmenting…" : st === "failed" ? "Failed" : "Pending"}
          tone={!tracing && st === "done" && p ? "ok" : undefined} testid="seg-status" />
        <Row k="IoU" v={null} title="An operational scene has no ground-truth mask; the model's held-out IoU is on the Models page" />
        <Row k="Perimeter" v={p?.perimeter_km != null ? `${num(p.perimeter_km)} km` : null} />
        <Row k="Axes" v={p?.major_axis_m ? `${num(p.major_axis_m / 1000, 2)} × ${num((p.minor_axis_m || 0) / 1000, 2)} km` : null} />
        <Row k="Tile" v={selectedTile ? fmtTile(selectedTile.index) : null} />
        <Row k="Latitude" v={p?.centroid ? `${Math.abs(p.centroid[1]).toFixed(4)}° ${p.centroid[1] >= 0 ? "N" : "S"}` : null} testid="seg-lat" />
        <Row k="Longitude" v={p?.centroid ? `${Math.abs(p.centroid[0]).toFixed(4)}° ${p.centroid[0] >= 0 ? "E" : "W"}` : null} testid="seg-lon" />
      </Section>
      <Section title="Model">
        <Row k="Model" v={det?.model_version} />
        <Row k="Engine" mono={false}><span className={`badge ${det?.engine === "ml" ? "badge-ok" : "badge-warn"}`}>{det?.engine || "—"}</span></Row>
        <Row k="Runtime" v={det?.runtime_ms ? `${(det.runtime_ms / 1000).toFixed(1)} s` : null} />
      </Section>
      <Foot><Primary onClick={() => actions.go("validation")} disabled={st !== "done"} testid="seg-next">Validate detection</Primary></Foot>
    </div>
  );
}

/* ------------------------------------------------------- wind / currents -- */

/** A small picture of the real grid: up to 9×9 arrows sampled from the field
 *  at the timestep nearest the scene, the slick marked where it lies. */
function FieldGlyph({ field, at, timeMs, tone }) {
  const data = useMemo(() => {
    if (!field?.u?.length || !field.lats?.length || !field.lons?.length) return null;
    const ts = (field.times_utc || []).map((t) => Date.parse(t));
    let ti = 0, best = Infinity;
    ts.forEach((t, i) => { const d = Math.abs(t - (timeMs ?? t)); if (d < best) { best = d; ti = i; } });
    const U = field.u[Math.min(ti, field.u.length - 1)], V = field.v[Math.min(ti, field.v.length - 1)];
    const ny = field.lats.length, nx = field.lons.length;
    const sy = Math.max(1, Math.ceil(ny / 9)), sx = Math.max(1, Math.ceil(nx / 9));
    let max = 0; const pts = [];
    for (let i = 0; i < ny; i += sy) for (let j = 0; j < nx; j += sx) {
      const u = U[i]?.[j], v = V[i]?.[j]; const s = Math.hypot(u, v);
      if (Number.isFinite(s)) { max = Math.max(max, s); pts.push({ lon: field.lons[j], lat: field.lats[i], u, v, s }); }
    }
    const w = Math.min(...field.lons), e = Math.max(...field.lons), so = Math.min(...field.lats), n = Math.max(...field.lats);
    return { pts, max, w, e, so, n, time: field.times_utc?.[ti] };
  }, [field, timeMs]);
  if (!data || !data.max) return null;
  const W = 300, H = 150, pad = 14;
  const X = (lon) => pad + ((lon - data.w) / ((data.e - data.w) || 1)) * (W - 2 * pad);
  const Y = (lat) => H - pad - ((lat - data.so) / ((data.n - data.so) || 1)) * (H - 2 * pad);
  const len = 15;
  return (
    <svg className={`ap-field ap-field-${tone}`} viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Forcing field sampled from the run's grid" data-testid={`field-${tone}`}>
      {data.pts.map((q, k) => {
        const l = len * (q.s / data.max), x = X(q.lon), y = Y(q.lat);
        const dx = (q.u / (q.s || 1)) * l, dy = -(q.v / (q.s || 1)) * l;
        const a = Math.atan2(dy, dx);
        return (
          <g key={k}>
            <line x1={x} y1={y} x2={x + dx} y2={y + dy} />
            <path d={`M${x + dx},${y + dy} l${-4 * Math.cos(a - 0.5)},${-4 * Math.sin(a - 0.5)} M${x + dx},${y + dy} l${-4 * Math.cos(a + 0.5)},${-4 * Math.sin(a + 0.5)}`} />
          </g>
        );
      })}
      {at && at[0] >= data.w && at[0] <= data.e && at[1] >= data.so && at[1] <= data.n && <circle className="ap-field-slick" cx={X(at[0])} cy={Y(at[1])} r={4.5} />}
    </svg>
  );
}

export function ForcingPanel({ ctx, kind }) {
  const { layers, forcing, forcingState, sceneT0, actions, judged } = ctx;
  const wind = kind === "wind";
  const f = forcing?.[kind];
  const c = layers.slick?.features?.[0]?.properties?.centroid;
  const recorded = layers.origin_cloud?.metadata?.forcing?.[kind] || layers.forecast?.metadata?.forcing?.[kind];
  const at = useMemo(() => {
    if (!f || !c) return null;
    const [u, v] = sampleField(f, c[0], c[1], sceneT0);
    const speed = Math.hypot(u, v);
    const toDeg = ((90 - (Math.atan2(v, u) * 180) / Math.PI) + 360) % 360;
    return { speed, toDeg, fromDeg: (toDeg + 180) % 360 };
  }, [f, c, sceneT0]);
  const cell = f?.lats?.length > 1 ? Math.abs(f.lats[1] - f.lats[0]) : null;
  const tNear = useMemo(() => {
    const ts = f?.times_utc || []; if (!ts.length || !sceneT0) return ts[0] || null;
    return ts.reduce((b, t) => (Math.abs(Date.parse(t) - sceneT0) < Math.abs(Date.parse(b) - sceneT0) ? t : b), ts[0]);
  }, [f, sceneT0]);
  return (
    <div className="ip" data-testid={`intel-${kind}`}>
      <Head title={wind ? "Wind Analysis" : "Ocean Current Analysis"} />
      {!f ? (
        <div className="ip-empty" data-testid={`${kind}-empty`}>
          {forcingState === "loading" || forcingState === "idle" ? <><Loader2 size={20} className="ws-spin" /><div>Reading the forcing grid this run used…</div></>
            : <><AlertTriangle size={20} /><div>No {wind ? "wind" : "current"} grid is recorded for this run.</div><div className="dim">The drift engine degrades to {wind ? "current-only" : "wind-only"} mode and the manifest records it.</div></>}
        </div>
      ) : (
        <>
          <div className="ap-metrics">
            <Metric label={wind ? "Wind speed at slick" : "Current speed at slick"} value={at ? at.speed.toFixed(wind ? 1 : 2) : null} unit=" m/s" sub="sampled at the slick centroid, scene time" testid={`${kind}-speed`} />
            <Metric label="Direction" value={at ? `${Math.round(wind ? at.fromDeg : at.toDeg)}°` : null}
              sub={at ? (wind ? `from ${compass(at.fromDeg)} · blowing toward ${compass(at.toDeg)}` : `setting toward ${compass(at.toDeg)}`) : null} testid={`${kind}-dir`} />
          </div>
          <div className="ap-fieldwrap">
            <FieldGlyph field={f} at={c} timeMs={sceneT0} tone={kind} />
            <div className="ap-field-cap"><Navigation size={11} /> arrows are the grid's own vectors (relative length) · ● slick</div>
          </div>
          <Section title="Source">
            <Row k="Provider" v={f.provider || recorded?.provider || null} mono={false} testid={`${kind}-provider`} />
            <Row k="Dataset" v={recorded?.dataset || null} mono={false} />
            <Row k="Variables" v={recorded?.variables?.join(", ") || null} />
            <Row k="File" v={f.file || recorded?.file || null} />
            <Row k="Valid (nearest step)" v={utc(tNear)} />
            {f.matches_run === false && <div className="ip-note warn">Not the grid this run's drift integrated: the cache changed after the run.</div>}
            {f.matches_run === true && <div className="ip-note">This is the exact grid the drift integrated (normalisation stamp matches).</div>}
          </Section>
          <Section title="Grid">
            <Row k="Size" v={`${f.lats.length} × ${f.lons.length} cells · ${f.times_utc?.length || 1} step${(f.times_utc?.length || 1) === 1 ? "" : "s"}`} />
            <Row k="Cell" v={cell ? `${cell.toFixed(3)}°` : null} />
            <Row k="Field mean / max" v={f.mean_speed != null ? `${num(f.mean_speed, 2)} / ${num(f.max_speed, 2)} m/s` : null} />
          </Section>
        </>
      )}
      <Foot>
        <Primary onClick={() => actions.sub(wind ? "drift" : "drift", wind ? "currents" : "hindcast")} disabled={judged.drift.state !== "done"} testid={`${kind}-next`}>
          {wind ? "Ocean current analysis" : "Run hindcast"}
        </Primary>
      </Foot>
    </div>
  );
}

/* ------------------------------------------------------------- hindcast -- */

export function HindcastPanel({ ctx }) {
  const { layers, forcing, sceneT0, actions, judged, cine } = ctx;
  const oc = layers.origin_cloud;
  const md = oc?.metadata;
  const p = layers.slick?.features?.[0]?.properties;
  const est = useMemo(() => originEstimate(oc), [oc]);
  const fo = md?.forcing || {};
  const at = (kind) => {
    const f = forcing?.[kind]; if (!f || !p?.centroid) return null;
    const [u, v] = sampleField(f, p.centroid[0], p.centroid[1], sceneT0); return Math.hypot(u, v);
  };
  const wS = at("wind"), cS = at("currents");
  /* cloud size per hour back: the run's own ellipses, widest confidence level */
  const spread = useMemo(() => {
    const by = new Map();
    for (const f of oc?.features || []) {
      const q = f.properties || {};
      if ((q.feature_type || q.kind) !== "ellipse" || !q.semi_major_m) continue;
      const s = q.step_index ?? 0; by.set(s, Math.max(by.get(s) || 0, q.semi_major_m));
    }
    return [...by.entries()].sort((a, b) => a[0] - b[0]);
  }, [oc]);
  const maxSpread = Math.max(1, ...spread.map(([, v]) => v));
  const step = cine?.active && sceneT0 && cine.timeMs != null ? Math.round((sceneT0 - cine.timeMs) / 3.6e6) : null;
  /* How far back the published origin window reaches. The engine integrates
   * the full backtrack, but only claims an origin inside this window; the
   * chart and the map both dim what lies beyond it. */
  const winBackH = sceneT0 && md?.origin_window_start_utc
    ? (sceneT0 - Date.parse(md.origin_window_start_utc)) / 3.6e6 : null;
  const inWin = (h) => winBackH == null || h <= winBackH + 0.01;
  const revealOrigin = !cine?.active || cine.beat?.id !== "hindcast";
  const st = judged.drift.state;
  if (!oc) {
    return (
      <div className="ip" data-testid="intel-hindcast"><Head title="Hindcast Analysis" />
        <div className="ip-empty">{st === "running" ? <><Loader2 size={20} className="ws-spin" /><div>Integrating the drift backward…</div></> : <div>{judged.drift.note || "The hindcast has not run."}</div>}</div>
      </div>
    );
  }
  return (
    <div className="ip" data-testid="intel-hindcast">
      <Head title="Hindcast Analysis" badge={<ProvBadge source={md?.source} />} />
      <Section title="Inputs" testid="hind-inputs">
        <Row k="Current field" v={fo.currents?.provider || null} mono={false} title={fo.currents?.dataset} />
        <Row k="Wind field" v={fo.wind?.provider || null} mono={false} title={fo.wind?.dataset} />
        <Row k="Slick geometry" v={p ? `${num(p.area_km2)} km² at ${latlon(p.centroid)}` : null} />
        <Row k="Detection timestamp" v={utc(layers.scene_meta?.acquired_utc)} />
      </Section>
      <Section title="Calculation" testid="hind-calc">
        <Row k="Integrator" v={fo.engine || null} />
        <Row k="Current contribution" v={cS != null ? `${cS.toFixed(2)} m/s at the slick` : null} title="Surface current sampled from the grid at the slick, scene time" />
        <Row k="Wind contribution" v={fo.windage != null && wS != null ? `${(fo.windage * wS).toFixed(3)} m/s` : null}
          title={fo.windage != null && wS != null ? `windage ${fo.windage} × wind ${wS.toFixed(1)} m/s, computed here from the run's two values` : "Needs the run's windage and its wind grid"} />
        <Row k="Windage" v={fo.windage != null ? `${(fo.windage * 100).toFixed(1)} % of 10 m wind` : null} />
        <Row k="Particles" v={md.n_particles?.toLocaleString()} />
        <Row k="Time step" v={md.timestep_minutes != null ? `${md.timestep_minutes} min` : null} />
        <Row k="Backward duration" v={md.backtrack_hours != null ? `${md.backtrack_hours} h` : null} testid="hind-duration" />
        <Row k="ML residual" mono={false} v={fo.ml_residual ? (fo.ml_residual.applied ? `applied (${fo.ml_residual.model})` : "not applied") : null} title={fo.ml_residual?.corrects} />
      </Section>
      {spread.length > 1 && (
        <Section title="Cloud spread vs hours back">
          <div className="ap-spark" data-testid="hind-spread">
            {spread.map(([s, v]) => (
              <i key={s} className={`${step === s ? "on" : ""} ${inWin(s) ? "" : "out"}`}
                style={{ height: `${Math.max(6, (v / maxSpread) * 100)}%` }}
                title={`T−${s} h · semi-major ${(v / 1000).toFixed(2)} km${inWin(s) ? "" : " · beyond the origin window"}`} />
            ))}
          </div>
          <div className="ap-spark-cap">
            <span>image time</span>
            {winBackH != null && <span className="ap-spark-win">origin window ends T−{Math.round(winBackH)} h</span>}
            <span>T−{spread[spread.length - 1][0]} h</span>
          </div>
          {winBackH != null && spread.some(([s]) => !inWin(s)) && (
            <div className="ip-note"><Info size={12} /> The engine integrated {md.backtrack_hours} h back; the origin is claimed only inside the {Math.round(winBackH)} h window. Hatched bars, and the faint dashed tail on the map, are backtrack it explored but did not support.</div>
          )}
        </Section>
      )}
      <Section title="Estimated origin" testid="hind-origin">
        {revealOrigin ? (
          <>
            <div className="ap-metrics">
              <Metric label="Latitude" value={est ? `${Math.abs(est.center[1]).toFixed(4)}° ${est.center[1] >= 0 ? "N" : "S"}` : null} testid="hind-lat" />
              <Metric label="Longitude" value={est ? `${Math.abs(est.center[0]).toFixed(4)}° ${est.center[0] >= 0 ? "E" : "W"}` : null} testid="hind-lon" />
            </div>
            <Row k="Uncertainty" v={est ? `± ${num(est.radiusKm, est.radiusKm < 1 ? 2 : 1)} km` : null} testid="hind-unc" />
            <Row k="Coverage" v={md.origin_uncertainty_coverage != null ? `${(md.origin_uncertainty_coverage * 100).toFixed(0)} %` : null} title="Share of closed-loop scenarios whose true origin fell inside this radius" />
            <Row k="Origin window" v={`${utc(md.origin_window_start_utc)} → ${utc(md.origin_window_end_utc)}`} />
            {md.origin_uncertainty_method && <div className="ip-note"><Info size={12} /> {md.origin_uncertainty_method}</div>}
            {est?.weak && <div className="ip-note warn">The convergence peak sits at the image time: this is a window, not a discharge time.</div>}
          </>
        ) : <div className="ip-analysing"><Loader2 size={12} className="ws-spin" /> Tracing backward{step != null ? ` · T−${step} h` : ""}…</div>}
      </Section>
      <Foot><Primary onClick={() => actions.sub("drift", "forecast")} testid="hind-next">Forecast</Primary></Foot>
    </div>
  );
}

/* ------------------------------------------------------------- forecast -- */

function ringCentroid(g) {
  const ring = g?.type === "Polygon" ? g.coordinates?.[0] : g?.type === "MultiPolygon" ? g.coordinates?.[0]?.[0] : null;
  if (!ring?.length) return null;
  let x = 0, y = 0; for (const c of ring) { x += c[0]; y += c[1]; }
  return [x / ring.length, y / ring.length];
}

export function ForecastPanel({ ctx }) {
  const { layers, actions, judged, cine, sceneT0 } = ctx;
  const fc = layers.forecast;
  const c0 = layers.slick?.features?.[0]?.properties?.centroid;
  /* One row per horizon. A forecast carries several confidence envelopes for
   * the same hour (50 %, 90 %...): distance and bearing are read from the
   * widest, and every envelope's own area is listed beside its level. */
  const rows = useMemo(() => {
    const by = new Map();
    for (const f of fc?.features || []) {
      const q = f.properties || {}; const h = q.horizon_h ?? 0;
      if (!by.has(h)) by.set(h, []);
      by.get(h).push({ f, conf: q.confidence_level ?? null, area: q.area_km2 ?? null });
    }
    return [...by.entries()].sort((a, b) => a[0] - b[0]).map(([h, env]) => {
      env.sort((a, b) => (b.conf ?? 0) - (a.conf ?? 0));
      const c = ringCentroid(env[0].f.geometry);
      const q = env[0].f.properties;
      return { h, valid: q.valid_utc, source: q.source, env,
        km: c && c0 ? haversineKm(c0[1], c0[0], c[1], c[0]) : null, brg: c && c0 ? bearingDeg(c0[1], c0[0], c[1], c[0]) : null };
    });
  }, [fc, c0]);
  const upTo = cine?.active && cine.beat?.id === "forecast" && sceneT0 && cine.timeMs != null ? (cine.timeMs - sceneT0) / 3.6e6 : Infinity;
  const maxKm = Math.max(0.001, ...rows.map((r) => r.km || 0));
  const fo = fc?.metadata?.forcing || {};
  return (
    <div className="ip" data-testid="intel-forecast">
      <Head title="Forecast Analysis" badge={<ProvBadge source={rows[0]?.source} />} />
      {!rows.length ? (
        <div className="ip-empty">{judged.drift.forecastRow?.status === "failed" ? <><AlertTriangle size={20} /><div>The forecast stage failed: {judged.drift.forecastRow.detail || "no detail recorded"}.</div></> : <div>No forecast footprint in this run.</div>}</div>
      ) : (
        <>
          <Section title="Horizons" testid="fc-horizons">
            {rows.map((r) => (
              <div key={r.h} className={`ap-horizon ${r.h <= upTo + 0.01 ? "on" : ""}`} data-testid={`fc-h${r.h}`}>
                <span className="ap-horizon-h mono">+{r.h} h</span>
                <div className="ap-horizon-body">
                  <div className="ap-horizon-line"><span>{r.h <= upTo + 0.01 ? (r.km != null ? `${r.km.toFixed(1)} km toward ${compass(r.brg)} (${Math.round(r.brg)}°)` : "—") : "…"}</span></div>
                  <Meter value={r.h <= upTo + 0.01 ? (r.km || 0) : 0} max={maxKm} />
                  <div className="ap-horizon-sub mono">{utc(r.valid)}</div>
                  {r.h <= upTo + 0.01 && (
                    <div className="ap-envs">{r.env.map((e, k) => <span key={k} className="mono">{e.conf != null ? `${(e.conf * 100).toFixed(0)} % envelope` : "footprint"} <b>{e.area != null ? `${num(e.area)} km²` : "—"}</b></span>)}</div>
                  )}
                </div>
              </div>
            ))}
            <div className="ip-note"><Info size={12} /> Distance and bearing are measured here, from the slick centroid to the centroid of each horizon's widest envelope.</div>
          </Section>
          <Section title="Environmental forcing">
            <Row k="Currents" v={fo.currents?.provider || null} mono={false} />
            <Row k="Wind" v={fo.wind?.provider || null} mono={false} />
            <Row k="Issued" v={utc(fc.metadata?.issued_utc)} />
            <Row k="Weathering" mono={false} v={fc.metadata?.weathering ? (typeof fc.metadata.weathering === "object" ? Object.entries(fc.metadata.weathering).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ") || "recorded" : String(fc.metadata.weathering)) : null} />
          </Section>
        </>
      )}
      <Foot><Primary onClick={() => actions.go("ais")} disabled={judged.ais.state !== "done"} testid="fc-next">Reconstruct AIS traffic</Primary></Foot>
    </div>
  );
}

/* ----------------------------------------------------------- AIS traffic -- */

export function AisTrafficPanel({ ctx }) {
  const { layers, runRow, funnel, aisStatus, actions, judged, cine } = ctx;
  const feats = layers.vessels?.features || [];
  const prov = aisProvenance(runRow, layers.suspects);
  const stats = useMemo(() => {
    let a = null, b = null, fixes = 0; const types = {};
    for (const f of feats) {
      const q = f.properties || {};
      const s = Date.parse(q.start_utc), e = Date.parse(q.end_utc);
      if (Number.isFinite(s)) a = a == null ? s : Math.min(a, s);
      if (Number.isFinite(e)) b = b == null ? e : Math.max(b, e);
      fixes += f.geometry?.coordinates?.length || 0;
      const t = q.vessel_type || "unknown"; types[t] = (types[t] || 0) + 1;
    }
    return { a, b, fixes, types: Object.entries(types).sort((x, y) => y[1] - x[1]) };
  }, [feats]);
  const loading = cine?.active && cine.beat?.id === "ais" && cine.t < 0.2;
  const live = aisStatus?.stream;
  return (
    <div className="ip" data-testid="intel-aistraffic">
      <Head title="AIS Traffic Reconstruction" badge={<span className={`badge badge-${prov.tone}`} data-testid="traffic-source">{prov.label}</span>} />
      <div className="ap-metrics">
        <Metric label="Vessels reconstructed" value={loading ? null : feats.length || null} sub="tracks in the run's vessel layer" testid="traffic-vessels" />
        <Metric label="AIS fixes" value={loading ? null : stats.fixes ? stats.fixes.toLocaleString() : null} sub="positions along those tracks" />
      </div>
      <Section title="Source">
        <Row k="Provenance" v={prov.word} mono={false} />
        {runRow?.manifest?.ais?.detail && <div className="ip-note">{runRow.manifest.ais.detail}</div>}
        <Row k="Time window" v={stats.a != null ? `${utc(new Date(stats.a).toISOString())} → ${utc(new Date(stats.b).toISOString())}` : null} />
        <Row k="Found by the AIS index" v={funnel?.found ?? null} title={funnel?.found_note || ""} />
        <Row k="Considered by attribution" v={layers.suspects?.total_vessels_considered ?? funnel?.indexed ?? null} />
        <Row k="Live AIS" mono={false}>
          {live?.functionally_working ? <span className="badge badge-ok">LIVE</span>
            : <span className="badge badge-warn" title={live?.note || ""}>{live?.state === "not_configured" ? "NOT CONFIGURED" : "NO COVERAGE IN CURRENT AREA"}</span>}
        </Row>
      </Section>
      {stats.types.length > 0 && (
        <Section title="Traffic by vessel type">
          {stats.types.slice(0, 6).map(([t, n]) => (
            <div key={t} className="ap-bar"><span className="ap-bar-k">{t}</span><Meter value={n} max={stats.types[0][1]} /><span className="ap-bar-v mono">{n}</span></div>
          ))}
        </Section>
      )}
      <Foot><Primary onClick={() => actions.sub("ais", "filter")} disabled={judged.ais.state !== "done"} testid="traffic-next">Apply the gates</Primary></Foot>
    </div>
  );
}

/* ---------------------------------------------------------- AIS filtering -- */

export function AisFilterPanel({ ctx }) {
  const { funnel, layers, actions, cine } = ctx;
  const gate = cine?.active && cine.beat?.id === "filter" ? cine.reveal?.gate ?? 0 : 3;
  const steps = funnel ? [
    { k: "Vessels considered", v: funnel.indexed, show: true },
    { k: "Spatial filter", sub: "inside the origin region", v: funnel.after_spatial, cut: funnel.exclusive_by_gate?.after_spatial, show: gate >= 1 },
    { k: "Temporal filter", sub: "inside the origin time window", v: funnel.after_temporal, cut: funnel.exclusive_by_gate?.after_temporal, show: gate >= 2 },
    { k: "Trajectory filter", sub: "course compatible with the slick axis", v: funnel.after_trajectory, cut: funnel.exclusive_by_gate?.after_trajectory, show: gate >= 3 },
    { k: "Candidates", v: funnel.candidates, show: gate >= 3, final: true },
  ] : [];
  const top = Math.max(1, funnel?.indexed || 1);
  return (
    <div className="ip" data-testid="intel-aisfilter">
      <Head title="AIS Filtering" badge={<ProvBadge source={funnel?.source} />} />
      {!funnel ? <div className="ip-empty"><div>The filtering funnel is not available for this run.</div></div> : (
        <>
          <div className="ap-funnel" data-testid="funnel">
            {steps.map((s, i) => (
              <div key={s.k} className={`ap-funnel-step ${s.show ? "on" : ""} ${s.final ? "final" : ""}`} data-testid={`funnel-${i}`}>
                {i > 0 && <ArrowDown size={14} className="ap-funnel-arrow" />}
                <div className="ap-funnel-bar" style={{ width: `${s.show ? Math.max(14, ((s.v ?? 0) / top) * 100) : 14}%` }}>
                  <span className="ap-funnel-v mono">{s.show ? s.v ?? "—" : "…"}</span>
                </div>
                <div className="ap-funnel-k">{s.k}{s.sub && <em>{s.sub}</em>}{s.show && s.cut != null && <b className="mono">−{s.cut} failed this gate</b>}</div>
              </div>
            ))}
          </div>
          <div className="ip-note"><Info size={12} /> {funnel.note}</div>
          <Section title="Filter reasons" testid="funnel-reasons">
            {Object.entries(funnel.reasons_histogram || {}).map(([r, n]) => (
              <div key={r} className="ap-bar"><span className="ap-bar-k wide">{r}</span><Meter value={n} max={funnel.filtered || 1} /><span className="ap-bar-v mono">{n}</span></div>
            ))}
            {!Object.keys(funnel.reasons_histogram || {}).length && <div className="dim">No vessel was filtered out.</div>}
            {funnel.unclassified > 0 && <Row k="Unclassified" v={funnel.unclassified} />}
          </Section>
          <Section title="Excluded vessels" open={false}>
            {(layers.suspects?.filtered_out || []).slice(0, 40).map((f) => <Row key={f.mmsi} k={String(f.mmsi)} v={(f.failed_gates || [f.filter_reason]).filter(Boolean).join(" · ")} mono={false} />)}
          </Section>
        </>
      )}
      <Foot><Primary onClick={() => actions.sub("ais", "ranking")} testid="filter-next">Rank candidates</Primary></Foot>
    </div>
  );
}

/* --------------------------------------------------------------- ranking -- */

const SUBS = [["proximity", "Proximity", "#22d3ee"], ["temporal", "Temporal", "#60a5fa"], ["trajectory", "Trajectory", "#a78bfa"],
  ["ais_gap", "AIS continuity", "#34d399"], ["behaviour", "Behaviour", "#fbbf24"], ["vessel_prior", "Type prior", "#f472b6"]];

export function RankingPanel({ ctx }) {
  const { layers, selectedMmsi, onSelectMmsi, actions, judged, cine, runRow } = ctx;
  const sus = layers.suspects;
  const all = sus?.suspects || [];
  const n = cine?.active && cine.beat?.id === "ranking" ? cine.reveal?.ranked ?? 0 : all.length;
  const list = all.filter((s) => s.rank <= n);
  const prov = aisProvenance(runRow, sus);
  return (
    <div className="ip" data-testid="intel-ranking">
      <Head title="Candidate Ranking" badge={<span className={`badge badge-${prov.tone}`}>{prov.label}</span>} />
      {!all.length ? (
        <div className="ip-empty"><Ship size={22} /><div>{judged.attribution.state === "done" ? "No vessel passed the spatial, temporal and trajectory gates for this origin window." : "Attribution has not run."}</div></div>
      ) : (
        <>
        {/* every candidate on one scale: the gap between them is the finding */}
        <div className="ap-board" data-testid="rank-board">
          {all.map((s) => (
            <div key={s.mmsi} className={`ap-board-row ${s.rank <= n ? "on" : ""} ${s.rank === 1 ? "top" : ""}`}>
              <span className="ap-board-n mono">#{s.rank}</span>
              <div className="ap-board-bar"><i style={{ width: `${s.rank <= n ? Math.round((s.total_score || 0) * 100) : 0}%` }} /></div>
              <b className="mono">{s.rank <= n ? num(s.total_score, 2) : "…"}</b>
            </div>
          ))}
          <div className="ap-board-axis mono"><span>0</span><span>0.5</span><span>1.0</span></div>
        </div>
        <div className="ap-legend">{SUBS.map(([k, label, col]) => <span key={k}><i style={{ background: col }} />{label}{sus.weights?.[k] != null ? ` ×${sus.weights[k]}` : ""}</span>)}</div>
        <div className="ap-rank" data-testid="rank-list">
          {!list.length && <div className="ip-analysing"><Loader2 size={12} className="ws-spin" /> Scoring candidates…</div>}
          {list.map((s) => (
            <button key={s.mmsi} className={`ap-cand ${s.rank === 1 ? "top" : ""} ${selectedMmsi === s.mmsi ? "sel" : ""}`} onClick={() => onSelectMmsi(s.mmsi)} data-testid={`rank-${s.rank}`}>
              <span className="ap-cand-n mono">#{s.rank}</span>
              <div className="ap-cand-body">
                <div className="ap-cand-head">
                  <span className="ap-cand-name">{s.vessel_name || `MMSI ${s.mmsi}`}</span>
                  {s.rank === 1 && <span className="badge badge-ok">Highest-Ranked</span>}
                </div>
                <div className="ap-cand-meta mono">{s.vessel_name ? `MMSI ${s.mmsi} · ` : ""}{s.vessel_type || "type —"}{s.evidence?.closest_approach_km != null ? ` · ${num(s.evidence.closest_approach_km, 1)} km closest` : ""}</div>
                <div className="ap-cand-score"><Meter value={s.total_score} tone={s.rank === 1 ? "gradient" : "accent"} /><b className="mono" data-testid={`rank-score-${s.rank}`}>{num(s.total_score, 2)}</b></div>
                {/* what the score is made of: each factor's sub-score times the run's own weight */}
                {sus.weights && (
                  <div className="ap-stack" title="Weighted contribution of each factor (sub-score × weight)" data-testid={`rank-stack-${s.rank}`}>
                    {SUBS.map(([k, label, col]) => s.sub_scores?.[k] != null && sus.weights?.[k] != null && (
                      <i key={k} style={{ width: `${(s.sub_scores[k] * sus.weights[k] * 100).toFixed(2)}%`, background: col }}
                        title={`${label}: ${num(s.sub_scores[k], 2)} × ${sus.weights[k]} = ${num(s.sub_scores[k] * sus.weights[k], 3)}`} />
                    ))}
                  </div>
                )}
                <div className="ap-cand-subs">
                  {SUBS.map(([k, label, col]) => s.sub_scores?.[k] != null && (
                    <span key={k} title={`${label} ${num(s.sub_scores[k], 2)} · weight ×${sus.weights?.[k] ?? "—"}`}><em>{label} <b className="mono">{num(s.sub_scores[k], 2)}</b></em><i><u style={{ width: `${Math.round(s.sub_scores[k] * 100)}%`, background: col }} /></i></span>
                  ))}
                </div>
              </div>
            </button>
          ))}
        </div>
        </>
      )}
      <div className="ip-note"><Info size={12} /> A rank orders evidence; it suggests potential source vessel(s) and does not confirm responsibility.</div>
      <Foot><Primary onClick={() => actions.go("attribution")} disabled={!all.length} testid="rank-next">Vessel attribution</Primary></Foot>
    </div>
  );
}
