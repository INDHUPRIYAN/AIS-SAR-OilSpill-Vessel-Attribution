/* Layer toggles with legend swatches. A layer whose contract file has not
 * been produced gets a DISABLED toggle with a "not yet produced" tooltip —
 * absence is a state to show, not to hide. */

import { WS, css, sourceBadge } from "./palette";


const ROWS = [
  { key: "sar", label: "SAR scene", swatch: "linear-gradient(90deg,#233,#889)", layer: "scene_meta", stage: "detect" },
  { key: "slick", label: "Detected slick", swatch: css(WS.slick), layer: "slick", stage: "characterise" },
  { key: "geometry", label: "Geometry (ellipse · centroid)", swatch: css(WS.geometry), layer: "slick", stage: "characterise" },
  { key: "forecast", label: "Forecast +6/+12/+24 h", swatch: css(WS.forecast), layer: "forecast", stage: "drift_forecast" },
  { key: "hindcast", label: "Hindcast cloud", swatch: css(WS.hindcast), layer: "origin_cloud", stage: "drift_hindcast" },
  { key: "origin", label: "Probable origin zone", swatch: css(WS.origin), layer: "origin_cloud", stage: "drift_hindcast" },
  { key: "vessels", label: "AIS tracks", swatch: css(WS.vessel), layer: "vessels", stage: "attribution" },
];

export default function LayerPanel({ show, onToggle, present, stages }) {
  const stageOf = (name) => (stages || []).find((s) => s.stage === name);
  return (
    <div data-testid="layer-panel">
      {ROWS.map((r) => {
        const available = present?.[r.layer] !== false;
        const st = stageOf(r.stage);
        return (
          <div key={r.key} className="ws-layer-row"
            data-testid={`layer-${r.key}`}
            data-disabled={String(!available)}
            title={available ? undefined : "not yet produced"}>
            <label className="switch" style={{ flex: 1, opacity: available ? 1 : 0.42 }}
              onClick={() => available && onToggle(r.key, !show[r.key])}>
              <span className={`switch-track ${show[r.key] && available ? "on" : ""}`}>
                <span className="switch-knob" />
              </span>
              <span className="legend-swatch" style={{ background: r.swatch }} />
              <span className="switch-label">{r.label}</span>
            </label>
            {st?.source && (
              <span className={`badge badge-${sourceBadge(st.source).tone}`}
                data-testid={`source-${r.key}`}>
                {sourceBadge(st.source).label}
              </span>
            )}
          </div>
        );
      })}

      <div className="ws-legend" data-testid="map-legend">
        <div className="tiny muted" style={{ margin: "8px 0 4px", letterSpacing: 1 }}>LEGEND</div>
        {[["Slick (observed)", css(WS.slick)],
          ["Forecast (amber)", css(WS.forecast)],
          ["Hindcast (magenta)", css(WS.hindcast)],
          ["Origin (gold ring)", css(WS.origin)],
          ["AIS track", css(WS.vessel)],
          ["Candidate", css(WS.candidate)],
          ["Top suspect", css(WS.suspect)],
          ["Excluded vessel", css(WS.filtered, 0.6)]].map(([label, c]) => (
          <div key={label} className="legend-row">
            <span className="legend-swatch" style={{ background: c }} />{label}
          </div>
        ))}
      </div>
    </div>
  );
}
