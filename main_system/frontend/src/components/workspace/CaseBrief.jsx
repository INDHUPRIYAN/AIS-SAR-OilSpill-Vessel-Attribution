/* Case brief: the investigator's first read of a run.
 *
 * It answers, in order, the questions an investigator asks -- what was seen,
 * where, when, how big, where it came from, where it is going, which vessels
 * were near, why they rank, what is uncertain -- and it answers each one ONLY
 * from a contract artefact that landed. A question whose artefact is missing
 * says "not produced"; nothing is paraphrased into a stronger claim than the
 * artefact makes. The "uncertain" list is built from facts the artefacts state
 * about themselves (a LOW age label, a wide window, a missing vessel name, a
 * synthetic AIS source), not from boilerplate.
 */

import {
  AlertTriangle, ArrowLeft, ArrowRight, Clock, Crosshair, Database, Droplets,
  MapPin, Maximize2, Ship,
} from "lucide-react";

import { sourceBadge } from "./palette";
import { originEstimate, screenVerdict } from "../../lib/drift";
import { fmtUtc, guessPlace } from "../../lib/replay";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v))
  ? "—" : Number(v).toFixed(d));
const utc = (s) => (s ? fmtUtc(Date.parse(s)) : null);
const latlon = (c) => (Array.isArray(c)
  ? `${num(Math.abs(c[1]), 4)}° ${c[1] >= 0 ? "N" : "S"}, ${num(Math.abs(c[0]), 4)}° ${c[0] >= 0 ? "E" : "W"}`
  : "—");

/* `pending` is true when the artefact behind a question is not on screen.
 * Why it is not matters: a layer the server answered 404 for was NOT
 * PRODUCED by this run; one that has simply not arrived yet is loading.
 * Printing "not produced" over a 430 KB origin cloud still in flight told
 * the reader the hindcast had never run. */
function Q({ icon: Icon, q, children, testid, pending, absent }) {
  return (
    <div className={`cb-q ${pending ? "cb-pending" : ""}`} data-testid={testid}>
      <div className="cb-q-head"><Icon size={12} /> {q}</div>
      <div className="cb-q-body">
        {pending
          ? <span className="dim">{absent ? "not produced by this run" : "loading…"}</span>
          : children}
      </div>
    </div>
  );
}

const SYNTH = new Set(["synthetic", "mock"]);
function SynthTag({ source }) {
  return SYNTH.has(String(source || "").toLowerCase())
    ? <span className="badge badge-mock" style={{ marginLeft: 6 }}>SYNTHETIC</span> : null;
}

function SourceRow({ label, value, source, detail, testid }) {
  const b = sourceBadge(source);
  return (
    <div className="cb-src" data-testid={testid}>
      <div className="cb-src-head">
        <span className="cb-src-k">{label}</span>
        {source
          ? <span className={`badge badge-${b.tone}`}>{b.label === "—" ? String(source).toUpperCase() : b.label}</span>
          : <span className="badge badge-neutral">UNRECORDED</span>}
      </div>
      <div className="cb-src-v">{value || <span className="dim">not recorded by this run</span>}</div>
      {detail && <div className="tiny dim">{detail}</div>}
    </div>
  );
}

export default function CaseBrief({ sceneMeta, slick, detect, origin, forecast, suspects,
                                    runRow, onShowTab, errors = {}, loaded = true }) {
  // A layer is "absent" once the server said so (or, outside the workspace,
  // when the caller says everything that will arrive has arrived).
  const absent = (name) => Boolean(errors[name]) || loaded === true;
  const feats = slick?.features ?? [];
  const p = feats[0]?.properties;
  const smd = slick?.metadata;
  const est = originEstimate(origin);
  const omd = origin?.metadata;
  const fmd = forecast?.metadata;
  const ranked = suspects?.suspects ?? [];
  const aisBlock = runRow?.manifest?.ais;
  const aisSource = aisBlock?.data_source || suspects?.source;
  const stages = runRow?.manifest?.stages ?? [];
  const lookalikes = (detect?.candidates ?? []).filter((c) => c.class === "lookalike").length;
  /* Forcing provenance is DATA provenance: the hindcast stage's data_source
   * (sensor / cached / synthetic), not the artefact's execution flag, which
   * says only that the engine really ran. */
  const hindStage = stages.find((s) => s.stage === "drift_hindcast");
  const forcingSource = (f) => (!f ? null
    : f.fallback ? "fallback"
      : hindStage?.data_source || omd?.source);

  /* Furthest forecast horizon's widest envelope: where the slick is expected
   * to be at the end of the forecast. */
  const envelopes = (forecast?.features ?? []).map((f) => f.properties || {});
  const maxH = envelopes.reduce((m, e) => Math.max(m, e.horizon_h ?? 0), 0);
  const far = envelopes.filter((e) => e.horizon_h === maxH)
    .sort((a, b) => (b.confidence_level ?? 0) - (a.confidence_level ?? 0))[0];

  /* ------------------------------------------------ what is uncertain -- */
  const caveats = [];
  if (p) {
    caveats.push(`Slick regions are segmentation-model output (confidence ${num((p.confidence ?? 0) * 100, 0)}%), not confirmed spills.`);
  }
  if (screenVerdict(p, detect) === "lookalike") {
    caveats.push(`The analysed slick ${p.slick_id || ""} lies inside the box of a region the screening model REJECTED as a look-alike. This run characterised the full segmentation mask, so its hindcast and vessel ranking trace that region.`);
  }
  if (p?.age_hours_estimate != null) {
    caveats.push(`Slick age (${num(p.age_hours_estimate, 1)} h) is a ${p.age_method || "heuristic"} estimate — LOW confidence.`);
  }
  if (est?.weak) {
    caveats.push(`The hindcast peaked at the image time, so it did not localise a release earlier than the image; treat the ${num(est.windowHours, 0)} h origin window as a window, not a discharge time.`);
  } else if (est && est.windowHours > 6) {
    caveats.push(`The origin window is ${num(est.windowHours, 1)} h wide.`);
  }
  const basis = sceneMeta?.basis;
  if (basis && basis.geo_basis !== "measured" && basis.geo_basis !== "raster" && basis.geo_basis_note) {
    caveats.push(`Scene position: ${basis.geo_basis_note}`);
  }
  if (String(aisSource).toLowerCase() === "synthetic") {
    caveats.push("AIS for this run is SYNTHETIC. The ranking demonstrates the method; it identifies no real vessel.");
  }
  const unnamed = ranked.filter((s) => !s.vessel_name).length;
  if (unnamed) {
    caveats.push(`${unnamed} of ${ranked.length} ranked vessel(s) have no name or IMO in the AIS archive; identity is the MMSI only.`);
  }
  const degraded = stages.filter((s) => s.engine_used === "fallback" || s.status === "mock" || s.status === "failed");
  for (const s of degraded) {
    caveats.push(`Stage ${s.stage} ${s.status === "failed" ? "failed" : s.status === "mock" ? "ran on mock data" : "used its fallback engine"}${s.detail ? ` — ${s.detail}` : ""}.`);
  }
  if (fmd?.weathering) caveats.push("Weathering is an order-of-magnitude estimate with an assumed oil type (LOW confidence).");
  if (ranked.length) caveats.push("A ranked vessel is a position in a weighted ordering of evidence, not a finding of responsibility.");

  // guessPlace falls back to formatted coordinates outside its named regions;
  // only a real place name adds anything next to the coordinates.
  const guessed = p?.centroid ? guessPlace(p.centroid) : null;
  const place = guessed && /[a-z]{3,}/i.test(guessed) ? guessed : null;

  return (
    <div data-testid="case-brief">
      <Q icon={Droplets} q="What was detected" testid="cb-what" pending={!p} absent={absent("slick")}>
        <b>{feats.length}</b> slick region{feats.length === 1 ? "" : "s"} segmented
        {smd?.model_version ? <> by <span className="mono">{smd.model_version}</span></> : null}
        {lookalikes ? <>; the screening model classed {lookalikes} candidate{lookalikes === 1 ? "" : "s"} as look-alike{lookalikes === 1 ? "" : "s"}</> : null}.
        {" "}Analysed slick <span className="mono">{p?.slick_id}</span> (largest — drift and attribution trace from it).
      </Q>

      <Q icon={MapPin} q="Where" testid="cb-where" pending={!p?.centroid} absent={absent("slick")}>
        <span className="mono">{latlon(p?.centroid)}</span>
        {place ? <span className="dim"> · {place}</span> : null}
        {basis?.geo_basis && basis.geo_basis !== "measured" && (
          <span className={`badge badge-${basis.geo_basis === "synthetic" ? "mock" : "warn"}`} style={{ marginLeft: 6 }}
            title={basis.geo_basis_note} data-testid="cb-basis">
            {basis.geo_basis === "assigned" ? "POSITION ASSIGNED" : basis.geo_basis === "synthetic" ? "SYNTHETIC"
              : basis.geo_basis === "raster" ? "FROM UPLOADED RASTER" : "USER-SUPPLIED"}
          </span>
        )}
      </Q>

      <Q icon={Clock} q="When" testid="cb-when" pending={!sceneMeta?.acquired_utc && !smd?.acquired_utc}
        absent={absent("scene_meta")}>
        Imaged <span className="mono">{utc(sceneMeta?.acquired_utc || smd?.acquired_utc)}</span>
        {p?.age_hours_estimate != null && (
          <> · age ≈ <span className="mono">{num(p.age_hours_estimate, 1)} h</span> <span className="badge badge-warn">LOW confidence</span></>
        )}
      </Q>

      <Q icon={Maximize2} q="How large" testid="cb-size" pending={!p} absent={absent("slick")}>
        <span className="mono">{num(p?.area_km2)} km²</span> · perimeter <span className="mono">{num(p?.perimeter_km)} km</span>
        {" "}· length <span className="mono">{num((p?.major_axis_m ?? 0) / 1000)} km</span>
        {feats.length > 1 && (
          <div className="tiny dim">All {feats.length} regions: {num(feats.reduce((a, f) => a + (f.properties?.area_km2 ?? 0), 0), 1)} km² combined</div>
        )}
      </Q>

      <Q icon={ArrowLeft} q="Where it likely came from" testid="cb-origin"
        pending={!est && !omd?.origin_window_start_utc} absent={absent("origin_cloud")}>
        {est
          ? <>Near <span className="mono">{latlon(est.center)}</span>, ± <span className="mono">{num(est.radiusKm)} km</span>, </>
          : <span className="dim">No origin position published; </span>}
        between <span className="mono">{utc(est?.windowStartUtc || omd?.origin_window_start_utc)}</span> and{" "}
        <span className="mono">{utc(est?.windowEndUtc || omd?.origin_window_end_utc)}</span>
        {omd?.origin_window_method ? <span className="dim"> ({omd.origin_window_method})</span> : null}
        <SynthTag source={omd?.source} />
      </Q>

      <Q icon={ArrowRight} q="Where it is going" testid="cb-forecast" pending={!far} absent={absent("forecast")}>
        By <span className="mono">{utc(far?.valid_utc)}</span> (+{far?.horizon_h} h) within a{" "}
        <span className="mono">{num(far?.area_km2, 1)} km²</span> envelope at {num((far?.confidence_level ?? 0) * 100, 0)}% confidence.
        <SynthTag source={far?.source} />
      </Q>

      <Q icon={Ship} q="Which vessels were near" testid="cb-vessels" pending={!suspects} absent={absent("suspects")}>
        <b>{suspects?.total_vessels_considered ?? "—"}</b> vessels reconstructed around the origin window;{" "}
        <b>{(suspects?.filtered_out ?? []).length}</b> excluded by the gates; <b>{ranked.length}</b> ranked.
        <SynthTag source={suspects?.source} />
        {ranked[0] && (
          <div className="cb-top">
            <button type="button" className="linkish" onClick={() => onShowTab?.("suspects", ranked[0].mmsi)}
              data-testid="cb-top-suspect">
              Rank #1 · {ranked[0].vessel_name || `MMSI ${ranked[0].mmsi}`} · score {num(ranked[0].total_score)}
            </button>
            {ranked[0].reason && <div className="tiny dim">{ranked[0].reason}</div>}
          </div>
        )}
      </Q>

      <div className="cb-block" data-testid="cb-uncertain">
        <div className="cb-q-head"><AlertTriangle size={12} /> What is uncertain</div>
        {caveats.length
          ? <ul className="cb-list">{caveats.map((c) => <li key={c}>{c}</li>)}</ul>
          : <div className="tiny dim">No artefacts yet.</div>}
      </div>

      <div className="cb-block" data-testid="cb-sources">
        <div className="cb-q-head"><Database size={12} /> Data used</div>
        <SourceRow label="Satellite" testid="src-satellite"
          source={sceneMeta?.source}
          value={sceneMeta ? [
            String(sceneMeta.scene_id || "").startsWith("S1") ? "Sentinel-1 SAR" : "SAR scene",
            sceneMeta.polarisation, sceneMeta.pixel_spacing_m ? `${sceneMeta.pixel_spacing_m} m` : null,
            utc(sceneMeta.acquired_utc),
          ].filter(Boolean).join(" · ") : null}
          detail={[sceneMeta?.scene_id, sceneMeta?.provider_used && `via ${sceneMeta.provider_used}`]
            .filter(Boolean).join(" · ") || null} />
        <SourceRow label="AIS" testid="src-ais" source={aisSource}
          value={aisBlock?.detail || (suspects ? `${suspects.total_vessels_considered ?? "—"} vessels in window` : null)} />
        <SourceRow label="Currents" testid="src-currents"
          source={forcingSource(omd?.forcing?.currents)}
          value={omd?.forcing?.currents?.provider} />
        <SourceRow label="Wind" testid="src-wind"
          source={forcingSource(omd?.forcing?.wind)}
          value={omd?.forcing?.wind?.provider} />
      </div>

      {onShowTab && (
        <div className="cb-jump">
          <button className="btn btn-sm" onClick={() => onShowTab("spill")}><Droplets size={11} /> Spill detail</button>
          <button className="btn btn-sm" onClick={() => onShowTab("drift")}><Crosshair size={11} /> Drift detail</button>
          <button className="btn btn-sm" onClick={() => onShowTab("suspects")}><Ship size={11} /> Suspects</button>
        </div>
      )}
    </div>
  );
}
