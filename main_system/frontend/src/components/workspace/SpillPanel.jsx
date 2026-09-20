/* Spill panel: every number straight from slick.geojson, formatted with
 * units. The engine badge says whether the ML path or the threshold
 * fallback produced the mask — never hidden.
 *
 * slick.geojson is sorted largest-area first and the drift stage seeds from
 * the largest slick, so feature 0 is the one the hindcast and the attribution
 * actually traced. The panel says so, and lists the other regions rather than
 * letting a 62-region scene read as a single slick. */

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { sourceBadge } from "./palette";
import { screenVerdict } from "../../lib/drift";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v))
  ? "—" : Number(v).toFixed(d));

export default function SpillPanel({ slick, detect, sceneMeta }) {
  const [othersOpen, setOthersOpen] = useState(false);
  const feats = slick?.features ?? [];
  const f = feats[0];
  if (!f) {
    return <div className="tiny muted" data-testid="spill-empty">
      No slick detected yet — run the investigation.
    </div>;
  }
  const p = f.properties || {};
  const engine = detect?.engine || p.engine;
  const lookalikes = (detect?.candidates ?? []).filter((c) => c.class === "lookalike").length;
  const verdict = screenVerdict(p, detect);
  const rows = [
    ["Area", `${num(p.area_km2)} km²`, "area"],
    ["Perimeter", `${num(p.perimeter_km)} km`, "perimeter"],
    ["Centroid", p.centroid ? `${num(p.centroid[1], 4)}, ${num(p.centroid[0], 4)}` : "—", "centroid"],
    ["Major axis", `${num((p.major_axis_m ?? 0) / 1000)} km`, "major"],
    ["Minor axis", `${num((p.minor_axis_m ?? 0) / 1000)} km`, "minor"],
    ["Elongation", p.major_axis_m && p.minor_axis_m ? `${num(p.major_axis_m / p.minor_axis_m, 1)} : 1` : "—", "elongation"],
    ["Orientation", `${num(p.orientation_deg, 1)}°`, "orientation"],
    ["Damping ratio", p.damping_ratio == null ? "—" : `${num(p.damping_ratio, 1)} dB`, "damping"],
    // Standing rule 7: slick age always shows LOW confidence. The estimate is
    // a damping-ratio heuristic with no ground truth behind it, and a bare
    // "6.9 h" reads as a measurement.
    ["Age estimate", p.age_hours_estimate == null ? "—" : `${num(p.age_hours_estimate, 1)} h · LOW confidence`, "age"],
    ["Detection confidence", `${num((p.confidence ?? 0) * 100, 1)}%`, "confidence"],
  ];
  return (
    <div data-testid="spill-panel">
      <div className="ws-section-title" data-testid="spill-analysed">
        Analysed slick <span className="mono">{p.slick_id || "—"}</span>
        {feats.length > 1 && <span className="dim"> · largest of {feats.length}</span>}
      </div>
      {feats.length > 1 && (
        <div className="tiny muted" style={{ marginBottom: 6 }}>
          Drift and attribution trace from this region.
        </div>
      )}
      {verdict === "lookalike" && (
        <div className="ws-note ws-note-warn" data-testid="spill-screen-lookalike" style={{ marginBottom: 8 }}>
          This slick lies inside the box of a region the screening model
          <b> rejected as a look-alike</b>. The run characterised the full
          segmentation mask, so its drift and ranking trace that region.
        </div>
      )}
      {rows.map(([k, v, id]) => (
        <div key={k} className="ip-row">
          <span className="ip-k">{k}</span>
          <span className="ip-v mono" data-testid={`spill-${id}`}>{v}</span>
        </div>
      ))}
      {p.age_method && (
        <div className="ip-row">
          <span className="ip-k">Age method</span>
          <span className="ip-v mono">{p.age_method}</span>
        </div>
      )}
      <div className="ip-row">
        <span className="ip-k">Engine</span>
        <span data-testid="spill-engine">
          <span className={`badge ${engine === "ml" ? "badge-ok" : "badge-warn"}`}>
            {engine || "—"}
          </span>
        </span>
      </div>
      {slick?.metadata?.model_version && (
        <div className="ip-row">
          <span className="ip-k">Model</span>
          <span className="ip-v mono" title={slick.metadata.model_version}>{slick.metadata.model_version}</span>
        </div>
      )}
      {p.source && (
        <div className="ip-row">
          <span className="ip-k">Source</span>
          <span className={`badge badge-${sourceBadge(p.source).tone}`}>
            {sourceBadge(p.source).label}
          </span>
        </div>
      )}
      {verdict && (
        <div className="ip-row">
          <span className="ip-k">Screen verdict</span>
          <span className={`badge ${verdict === "oil" ? "badge-ok" : "badge-warn"}`}
            data-testid="spill-screen-verdict">
            {verdict === "oil" ? "OIL (screen confirmed)" : "LOOK-ALIKE (screen rejected)"}
          </span>
        </div>
      )}
      {lookalikes > 0 && (
        <div className="ip-row">
          <span className="ip-k">Candidates classed look-alike</span>
          <span className="ip-v mono">{lookalikes}</span>
        </div>
      )}

      {sceneMeta && (
        <div className="ws-section" data-testid="spill-scene">
          <div className="ws-section-title">Scene</div>
          <div className="ip-row"><span className="ip-k">Acquired</span>
            <span className="ip-v mono">{String(sceneMeta.acquired_utc || "—").replace("T", " ").replace("Z", " UTC")}</span></div>
          {sceneMeta.polarisation && <div className="ip-row"><span className="ip-k">Polarisation</span>
            <span className="ip-v mono">{sceneMeta.polarisation}</span></div>}
          {sceneMeta.pixel_spacing_m && <div className="ip-row"><span className="ip-k">Pixel spacing</span>
            <span className="ip-v mono">{sceneMeta.pixel_spacing_m} m</span></div>}
          {sceneMeta.provider_used && <div className="ip-row"><span className="ip-k">Provider</span>
            <span className="ip-v mono">{sceneMeta.provider_used}</span></div>}
        </div>
      )}

      {feats.length > 1 && (
        <div className="ws-filtered" data-testid="spill-others">
          <button className="ws-filtered-head" onClick={() => setOthersOpen((v) => !v)}
            data-testid="spill-others-toggle">
            {othersOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Other regions ({feats.length - 1})
          </button>
          {othersOpen && (
            <table className="ws-table">
              <thead><tr><th>ID</th><th>Area</th><th>Length</th><th>Conf.</th></tr></thead>
              <tbody>
                {feats.slice(1).map((x) => {
                  const q = x.properties || {};
                  return (
                    <tr key={q.slick_id}>
                      <td className="mono">{String(q.slick_id || "").replace(/^.*_slick_/, "#")}</td>
                      <td className="mono">{num(q.area_km2)} km²</td>
                      <td className="mono">{num((q.major_axis_m ?? 0) / 1000, 1)} km</td>
                      <td className="mono">{num((q.confidence ?? 0) * 100, 0)}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
