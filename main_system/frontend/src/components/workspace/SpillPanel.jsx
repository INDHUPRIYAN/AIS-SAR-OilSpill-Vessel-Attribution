/* Spill panel: every number straight from slick.geojson, formatted with
 * units. The engine badge says whether the ML path or the threshold
 * fallback produced the mask — never hidden. */

import { sourceBadge } from "./palette";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v))
  ? "—" : Number(v).toFixed(d));

export default function SpillPanel({ slick, detect }) {
  const f = slick?.features?.[0];
  if (!f) {
    return <div className="tiny muted" data-testid="spill-empty">
      No slick detected yet — run the investigation.
    </div>;
  }
  const p = f.properties || {};
  const engine = detect?.engine || p.engine;
  const rows = [
    ["Area", `${num(p.area_km2)} km²`, "area"],
    ["Perimeter", `${num(p.perimeter_km)} km`, "perimeter"],
    ["Centroid", p.centroid ? `${num(p.centroid[1], 4)}, ${num(p.centroid[0], 4)}` : "—", "centroid"],
    ["Major axis", `${num((p.major_axis_m ?? 0) / 1000)} km`, "major"],
    ["Minor axis", `${num((p.minor_axis_m ?? 0) / 1000)} km`, "minor"],
    ["Orientation", `${num(p.orientation_deg, 1)}°`, "orientation"],
    ["Damping ratio", p.damping_ratio == null ? "—" : `${num(p.damping_ratio, 1)} dB`, "damping"],
    ["Age estimate", p.age_hours_estimate == null ? "—" : `${num(p.age_hours_estimate, 1)} h`, "age"],
    ["Detection confidence", `${num((p.confidence ?? 0) * 100, 1)}%`, "confidence"],
  ];
  return (
    <div data-testid="spill-panel">
      {rows.map(([k, v, id]) => (
        <div key={k} className="ip-row">
          <span className="ip-k">{k}</span>
          <span className="ip-v mono" data-testid={`spill-${id}`}>{v}</span>
        </div>
      ))}
      <div className="ip-row">
        <span className="ip-k">Engine</span>
        <span data-testid="spill-engine">
          <span className={`badge ${engine === "ml" ? "badge-ok" : "badge-warn"}`}>
            {engine || "—"}
          </span>
        </span>
      </div>
      {p.source && (
        <div className="ip-row">
          <span className="ip-k">Source</span>
          <span className={`badge badge-${sourceBadge(p.source).tone}`}>
            {sourceBadge(p.source).label}
          </span>
        </div>
      )}
    </div>
  );
}
