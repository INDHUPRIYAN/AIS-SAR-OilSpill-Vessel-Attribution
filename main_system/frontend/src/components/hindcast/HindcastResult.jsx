/* The posterior, once the last engine has succeeded: MAP origin and release
 * time, the credible window, the 90 % region, P(tau), and every mode.
 *
 * Wording follows the rest of OceanTrace: this is an ESTIMATE with a credible
 * region, never "the source". A demo job also carries the truth it was built
 * from, and says how close the estimate came. */

import { AlertTriangle, CheckCircle2, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge } from "../ui";
import { fmt } from "../../lib/api";

function Stat({ label, children, sub }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="he-stat-k">{label}</div>
      <div className="he-stat-v">{children}</div>
      {sub ? <div className="he-stat-s">{sub}</div> : null}
    </div>
  );
}

const hemi = (v, pos, neg) => `${Math.abs(v).toFixed(4)}° ${v >= 0 ? pos : neg}`;

export default function HindcastResult({ result, source }) {
  if (!result) return null;
  const { map, release_window: win, truth_check: truth, p_tau: pTau } = result;
  const max = Math.max(...pTau.probability, 1e-12);
  const runId = source?.startsWith("run:") ? source.slice(4) : null;

  return (
    <section className="he-result" data-testid="hindcast-result">
      <div className="he-result-head">
        <MapPin size={16} /> Estimated origin
        {result.forcing_source === "synthetic" && <Badge tone="mock" title="Analytic forcing field, not a provider product">SYNTHETIC FORCING</Badge>}
        {result.forcing_source?.includes("WIND ONLY") && <Badge tone="warn" title="No currents grid covered this scene">WIND ONLY</Badge>}
        {result.multi_modal && (
          <Badge tone="warn" title="Every mode is reported below; they are never averaged">
            MULTI-MODAL · {result.modes.length} origin / {result.age_modes.length} age
          </Badge>
        )}
        {runId && <Link className="btn btn-sm" style={{ marginLeft: "auto" }} to={`/investigation?run=${runId}`}>Open run {runId}</Link>}
      </div>

      <div className="he-result-body">
        <div className="he-stats">
          <Stat label="MAP origin" sub="most probable age, then place">{hemi(map.lat, "N", "S")}, {hemi(map.lon, "E", "W")}</Stat>
          <Stat label="Release time" sub={`${map.tau_h} h before the pass`}>{fmt.utc(map.release_time)}</Stat>
          <Stat label={`${Math.round(win.credible_mass * 100)} % release window`} sub={`τ ∈ [${win.tau_lo_h}, ${win.tau_hi_h}] h`}>
            {fmt.utc(win.earliest).slice(5, 16)} → {fmt.utc(win.latest).slice(5, 16)}Z
          </Stat>
          <Stat label="90 % origin region" sub={result.slick_type ? `slick type: ${result.slick_type.replace(/_/g, " ")}` : null}>
            {result.hdr90_area_km2} km²
          </Stat>
        </div>

        <div data-testid="hindcast-age-histogram">
          <div className="he-hist">
            {pTau.tau_hours.map((tau, i) => (
              <div className="he-hist-col" key={tau} title={`τ = ${tau} h · P = ${(pTau.probability[i] * 100).toFixed(1)} %`}
                data-in={tau >= win.tau_lo_h && tau <= win.tau_hi_h} data-map={tau === map.tau_h} data-truth={tau === truth?.tau_h}>
                <i style={{ height: `${Math.max(2, (pTau.probability[i] / max) * 100)}%` }} />
              </div>
            ))}
          </div>
          <div className="he-hist-axis">
            <span>{pTau.tau_hours[0]} h</span><span>P(τ) · age before the satellite pass</span>
            <span>{pTau.tau_hours[pTau.tau_hours.length - 1]} h</span>
          </div>
        </div>
      </div>

      {result.multi_modal && result.modes.length > 1 && (
        <ul className="he-modes">
          {result.modes.map((m, i) => (
            <li key={i}>mode {i + 1}: {hemi(m.lat, "N", "S")}, {hemi(m.lon, "E", "W")} · τ {m.tau_h} h · {(m.mass * 100).toFixed(0)} % of the posterior</li>
          ))}
        </ul>
      )}

      {truth && (
        <div className="he-truth" data-ok={truth.inside_hdr90} data-testid="hindcast-truth">
          <b>{truth.inside_hdr90 ? <CheckCircle2 size={14} style={{ color: "var(--ok)" }} /> : <AlertTriangle size={14} />} Demo truth check</b>
          <span>true origin {hemi(truth.lat, "N", "S")}, {hemi(truth.lon, "E", "W")} · released {truth.tau_h} h before the pass</span>
          <span>MAP error {truth.map_error_km} km</span>
          <span>true origin {truth.inside_hdr90 ? "inside" : "OUTSIDE"} the 90 % region</span>
          <span>true age {truth.tau_inside_window ? "inside" : "OUTSIDE"} the window</span>
        </div>
      )}
    </section>
  );
}
