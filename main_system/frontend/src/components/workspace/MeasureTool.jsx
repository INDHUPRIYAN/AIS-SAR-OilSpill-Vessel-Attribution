/* MAP-mode measure readout.
 *
 * The panel is deliberately separate from the map layers that draw the line:
 * the numbers an analyst reads and later quotes have to be checkable without
 * a GPU, and a measurement whose method is not printed beside it is not
 * reproducible. Every leg is listed, the total is the sum of exactly those
 * legs (see `pathLengthKm`), and the geodesy line at the bottom travels with
 * any screenshot of this panel.
 *
 * Distances are great-circle, never screen distance — see lib/geodesy.js for
 * why that difference is not cosmetic at Gulf and Baltic latitudes.
 */

import { Ruler, Trash2, Undo2 } from "lucide-react";

import { formatMeasurement, segments } from "../../lib/geodesy";

const fmtLon = (v) => `${Math.abs(v).toFixed(4)}°${v < 0 ? "W" : "E"}`;
const fmtLat = (v) => `${Math.abs(v).toFixed(4)}°${v < 0 ? "S" : "N"}`;

export default function MeasureTool({ points = [], onUndo, onClear }) {
  const legs = segments(points);
  const total = formatMeasurement(points);

  return (
    <div className="panel ms-panel" data-testid="measure-panel">
      <div className="ws-panel-title">
        <Ruler size={13} /> Measure
        <span className="ms-actions">
          <button className="btn btn-icon btn-sm" onClick={onUndo}
            disabled={!points.length} title="Remove the last point"
            data-testid="measure-undo"><Undo2 size={12} /></button>
          <button className="btn btn-icon btn-sm" onClick={onClear}
            disabled={!points.length} title="Clear the measurement"
            data-testid="measure-clear"><Trash2 size={12} /></button>
        </span>
      </div>

      {points.length === 0 && (
        <div className="tiny muted" data-testid="measure-hint">
          Click the map to drop points. Two points give a distance and a
          bearing; more extend the path.
        </div>
      )}

      {points.length === 1 && (
        <div className="tiny muted" data-testid="measure-hint">
          One point set at {fmtLat(points[0][1])} {fmtLon(points[0][0])}.
          Click again to measure.
        </div>
      )}

      {legs.length > 0 && (
        <>
          <table className="ms-table" data-testid="measure-legs">
            <thead>
              <tr><th>leg</th><th>km</th><th>nm</th><th>brg</th></tr>
            </thead>
            <tbody>
              {legs.map((leg) => (
                <tr key={leg.index} data-testid={`measure-leg-${leg.index}`}>
                  <td className="mono">{leg.index + 1}</td>
                  <td className="mono num">{leg.km.toFixed(2)}</td>
                  <td className="mono num">{leg.nm.toFixed(2)}</td>
                  <td className="mono num">{leg.bearingDeg.toFixed(0)}°</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="ms-total" data-testid="measure-total">
            <span className="ms-total-k">total</span>
            <span className="mono" data-testid="measure-total-value">{total.text}</span>
          </div>
          {legs.length > 1 && (
            <div className="tiny muted" data-testid="measure-bearing-note">
              The bearing on the total is start-to-end, not the sum of the legs.
            </div>
          )}
          <div className="ms-method tiny mono" data-testid="measure-method">
            {total.method}
          </div>
        </>
      )}
    </div>
  );
}
