/* Drift panel: the hindcast (backward, towards the origin) and the forecast
 * (forward, where the slick is going), side by side with the forcing that
 * drove both. Every value is read from origin_cloud.geojson / forecast.geojson
 * metadata; a field the run did not write is left out rather than shown as 0.
 *
 * The two directions are kept visually distinct -- magenta for hindcast,
 * amber for forecast, the same colours the map uses -- because confusing
 * "where it came from" with "where it is going" is the one mistake this panel
 * exists to prevent. */

import { ArrowLeft, ArrowRight, CloudSun, Droplet, Waves } from "lucide-react";

import { WS, css, sourceBadge } from "./palette";
import { fmtUtc } from "../../lib/replay";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v))
  ? null : Number(v).toFixed(d));

function Row({ k, v, testid, title }) {
  if (v == null || v === "") return null;
  return (
    <div className="ip-row" title={title}>
      <span className="ip-k">{k}</span>
      <span className="ip-v mono" data-testid={testid}>{v}</span>
    </div>
  );
}

function Section({ icon: Icon, color, title, children, testid }) {
  return (
    <div className="ws-section" data-testid={testid}>
      <div className="ws-section-title" style={{ color: color ? css(color) : undefined }}>
        <Icon size={12} /> {title}
      </div>
      {children}
    </div>
  );
}

const utc = (s) => (s ? fmtUtc(Date.parse(s)) : null);
const hoursBetween = (a, b) => {
  const d = (Date.parse(b) - Date.parse(a)) / 3.6e6;
  return Number.isFinite(d) ? d : null;
};

/** Forcing block, shared by hindcast and forecast. The provider strings are
 *  the ones the metocean service wrote; a non-null `fallback` is surfaced
 *  because a fallback field changes what the trajectory means. */
function Forcing({ forcing }) {
  if (!forcing) {
    return <div className="tiny muted">Forcing not recorded by this run.</div>;
  }
  const rows = [
    ["Currents", forcing.currents],
    ["Wind", forcing.wind],
  ];
  return (
    <>
      {rows.map(([label, f]) => (
        <div key={label} className="ip-row" data-testid={`forcing-${label.toLowerCase()}`}>
          <span className="ip-k">{label}</span>
          <span className="ip-v" style={{ whiteSpace: "normal" }}>
            {f?.provider || "not recorded"}
            {f?.variables?.length ? <span className="mono dim"> · {f.variables.join(", ")}</span> : null}
            {f?.fallback && (
              <span className="badge badge-warn" style={{ marginLeft: 6 }}
                title="The primary provider was unavailable; this field came from the fallback.">
                FALLBACK {String(f.fallback)}
              </span>
            )}
          </span>
        </div>
      ))}
      <Row k="Windage" v={forcing.windage != null ? `${num(forcing.windage * 100, 1)} % of 10 m wind` : null} />
      <Row k="Integrator" v={forcing.engine} />
      <Row k="ML correction"
        v={forcing.ml_residual
          ? (forcing.ml_residual.applied ? `applied (${forcing.ml_residual.model})` : "not applied — physics only")
          : null}
        title="The drift ML residual is experimental and disabled; trajectories are pure physics." />
    </>
  );
}

export default function DriftPanel({ origin, forecast, errors = {}, loaded = true }) {
  const md = origin?.metadata;
  const fmd = forecast?.metadata;
  // Absent (the server answered 404) is not the same as still arriving.
  const gone = (name) => Boolean(errors[name]) || loaded === true;
  const missing = (name, what) => (gone(name)
    ? `${what} not produced for this run.` : `Loading ${what.toLowerCase()}…`);

  if (!md && !fmd) {
    return (
      <div className="tiny muted" data-testid="drift-empty">
        {gone("origin_cloud") && gone("forecast")
          ? "No drift output for this run — the hindcast and forecast stages produced nothing."
          : "Loading the hindcast and forecast…"}
      </div>
    );
  }

  const windowH = md ? hoursBetween(md.origin_window_start_utc, md.origin_window_end_utc) : null;
  // The final hindcast ellipse: the furthest-back step at the highest
  // confidence level the run wrote. Its size is the spatial uncertainty of
  // the origin that a reader can see on the map.
  const ellipses = (origin?.features ?? [])
    .filter((f) => (f.properties?.feature_type || f.properties?.kind) === "ellipse");
  const lastStep = ellipses.reduce((m, f) => Math.max(m, f.properties.step_index ?? 0), 0);
  const lastEllipse = ellipses
    .filter((f) => (f.properties.step_index ?? 0) === lastStep)
    .sort((a, b) => (b.properties.confidence_level ?? 0) - (a.properties.confidence_level ?? 0))[0]?.properties;

  const envelopes = (forecast?.features ?? [])
    .map((f) => f.properties || {})
    .sort((a, b) => (a.horizon_h - b.horizon_h) || (a.confidence_level - b.confidence_level));
  const weathering = fmd?.weathering;

  return (
    <div data-testid="drift-panel">
      {/* ------------------------------------------------------- hindcast */}
      <Section icon={ArrowLeft} color={WS.hindcast} title="Hindcast — back to origin"
        testid="drift-hindcast">
        {md ? (
          <>
            <Row k="Origin window"
              v={md.origin_window_start_utc
                ? `${utc(md.origin_window_start_utc)} → ${utc(md.origin_window_end_utc)}`
                : null}
              testid="drift-window" />
            <Row k="Window width" v={windowH != null ? `${num(windowH, 1)} h` : null} />
            <Row k="Window method" v={md.origin_window_method} testid="drift-window-method"
              title="How the window was derived. 'cloud_convergence' = the span where the back-tracked particle cloud is most compact." />
            <Row k="Convergence peak" v={utc(md.origin_peak_utc)} />
            <Row k="Origin uncertainty"
              v={md.origin_uncertainty_km != null
                ? `${num(md.origin_uncertainty_km, 2)} km`
                  + (md.origin_uncertainty_coverage != null
                    ? ` · ${num(md.origin_uncertainty_coverage * 100, 0)}% coverage` : "")
                : null}
              testid="drift-uncertainty" />
            {lastEllipse && (
              <Row k={`Final ellipse (${num((lastEllipse.confidence_level ?? 0) * 100, 0)}%)`}
                v={`${num(lastEllipse.semi_major_m / 1000, 2)} × ${num(lastEllipse.semi_minor_m / 1000, 2)} km semi-axes`} />
            )}
            <Row k="Backtrack" v={md.backtrack_hours != null ? `${md.backtrack_hours} h` : null} />
            <Row k="Particles" v={md.n_particles != null ? Number(md.n_particles).toLocaleString() : null} />
            <Row k="Timestep" v={md.timestep_minutes != null ? `${md.timestep_minutes} min` : null} />
            {md.source && (
              <div className="ip-row">
                <span className="ip-k">Source</span>
                <span className={`badge badge-${sourceBadge(md.source).tone}`}>
                  {sourceBadge(md.source).label}
                </span>
              </div>
            )}
            {md.origin_window_method === "cloud_convergence" && md.origin_peak_utc
              && md.origin_peak_utc === md.origin_window_end_utc && (
              <div className="ws-note ws-note-warn" data-testid="drift-weak-window">
                The convergence peak sits at the image acquisition time, so the
                drift did not localise a release earlier than the image. Read this
                as a {windowH != null ? `${num(windowH, 0)}-hour` : ""} window, not a
                discharge time.
              </div>
            )}
            {md.origin_uncertainty_method && (
              <div className="tiny muted ws-method" title={md.origin_uncertainty_method}>
                Uncertainty: {md.origin_uncertainty_method}
              </div>
            )}
          </>
        ) : <div className="tiny muted" data-testid="drift-hindcast-missing">{missing("origin_cloud", "Hindcast")}</div>}
      </Section>

      {/* ------------------------------------------------------- forecast */}
      <Section icon={ArrowRight} color={WS.forecast} title="Forecast — where it is going"
        testid="drift-forecast">
        {fmd ? (
          <>
            <Row k="Issued at" v={utc(fmd.issued_utc)} />
            <Row k="Horizons" v={fmd.horizons_h?.length ? fmd.horizons_h.map((h) => `+${h} h`).join(" · ") : null} />
            {envelopes[0]?.source && (
              <div className="ip-row">
                <span className="ip-k">Source</span>
                <span className={`badge badge-${sourceBadge(envelopes[0].source).tone}`}
                  data-testid="drift-forecast-source">
                  {sourceBadge(envelopes[0].source).label}
                </span>
              </div>
            )}
            {envelopes.length > 0 && (
              <table className="ws-table" data-testid="forecast-table">
                <thead>
                  <tr><th>Horizon</th><th>Valid (UTC)</th><th>Envelope</th><th>Area</th></tr>
                </thead>
                <tbody>
                  {envelopes.map((e, i) => (
                    <tr key={i}>
                      <td className="mono">+{e.horizon_h} h</td>
                      <td className="mono">{e.valid_utc ? String(e.valid_utc).slice(5, 16).replace("T", " ") : "—"}</td>
                      <td className="mono">{e.confidence_level != null ? `${num(e.confidence_level * 100, 0)}%` : "—"}</td>
                      <td className="mono">{e.area_km2 != null ? `${num(e.area_km2, 1)} km²` : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="tiny muted" style={{ marginTop: 5 }}>
              Envelopes are particle-density contours: the slick is expected inside
              the 50% area with even odds, inside the 90% area with high odds.
            </div>
          </>
        ) : <div className="tiny muted" data-testid="drift-forecast-missing">{missing("forecast", "Forecast")}</div>}
      </Section>

      {/* -------------------------------------------------------- forcing */}
      <Section icon={Waves} title="Environmental inputs" testid="drift-forcing">
        <Forcing forcing={md?.forcing || fmd?.forcing} />
      </Section>

      {/* ----------------------------------------------------- weathering */}
      {weathering && (
        <Section icon={Droplet} title="Weathering estimate" testid="drift-weathering">
          <div className="ip-row">
            <span className="ip-k">Confidence</span>
            <span className="badge badge-warn">{String(weathering.confidence || "low").toUpperCase()}</span>
          </div>
          <Row k="Model" v={weathering.model} />
          <Row k="Oil type" v={weathering.oil_type_assumed ? `${weathering.oil_type_assumed} (assumed)` : null} />
          <Row k="Wind used" v={weathering.wind_speed_m_s_used != null ? `${num(weathering.wind_speed_m_s_used, 1)} m/s` : null} />
          {(weathering.states ?? []).map((s) => (
            <Row key={s.hours} k={`At +${num(s.hours, 0)} h`}
              v={`${num(s.evaporated_fraction * 100, 0)}% evaporated · water ${num(s.water_fraction * 100, 0)}%`} />
          ))}
          {weathering.honesty_note && (
            <div className="tiny muted ws-method">{weathering.honesty_note}</div>
          )}
        </Section>
      )}

      {/* Stated only when the artefact itself says the residual was off. */}
      {(md?.forcing || fmd?.forcing)?.ml_residual?.applied === false && (
        <div className="tiny dim" style={{ marginTop: 8, display: "flex", gap: 6, alignItems: "center" }}>
          <CloudSun size={11} /> Trajectories are Lagrangian particle physics; no ML contributed.
        </div>
      )}
    </div>
  );
}
