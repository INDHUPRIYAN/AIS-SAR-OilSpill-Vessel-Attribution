/* Environment -- the forcing fields the drift model actually consumed.
 *
 * Not a weather page. These are the specific grids a given run resolved and
 * fed to the hindcast and the forecast, which is why the page is scoped to a
 * run rather than to "now": a current field for today says nothing about a
 * spill detected last Tuesday.
 *
 * The provider rows come from `/api/apis/status` and keep the server's own
 * vocabulary, where WORKING and REACHABLE are deliberately different claims.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Activity, AlertTriangle, Compass, Droplets, Gauge, RefreshCw, Waves, Wind,
} from "lucide-react";

import {
  Badge, DataState, KV, Notice, PageHeader, Panel, ProvenanceBadge, Tile,
} from "../components/ui";
import { api, fmt, statusTone, useApi } from "../lib/api";

const num = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));

/* Providers that actually feed the drift model. Others are shown on the API
 * monitor; this page is about the forcing chain. */
const FORCING_KINDS = new Set(["currents", "wind"]);

/** A compact vector field preview drawn from the grid the run used.
 *  Arrows are sampled from the real u/v arrays -- nothing is interpolated for
 *  looks, and a grid that did not arrive draws nothing. */
function FieldPreview({ field, color, label }) {
  const arrows = useMemo(() => {
    if (!field?.u?.length || !field?.lats?.length || !field?.lons?.length) return [];
    const U = field.u[0];
    const V = field.v?.[0];
    if (!U || !V) return [];
    const rows = U.length;
    const cols = U[0]?.length || 0;
    const stepR = Math.max(1, Math.floor(rows / 9));
    const stepC = Math.max(1, Math.floor(cols / 14));
    const out = [];
    let max = 0;
    for (let r = 0; r < rows; r += stepR) {
      for (let c = 0; c < cols; c += stepC) {
        const u = U[r]?.[c] ?? 0;
        const v = V[r]?.[c] ?? 0;
        const m = Math.hypot(u, v);
        if (Number.isFinite(m)) max = Math.max(max, m);
        out.push({ r, c, u, v, m, rows, cols });
      }
    }
    return out.map((a) => ({ ...a, norm: max > 0 ? a.m / max : 0 }));
  }, [field]);

  if (!arrows.length) {
    return <DataState kind="empty" compact title={`No ${label} grid`}
      hint="This run holds no forcing grid for that variable." />;
  }
  const W = 100;
  const H = 62;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="env-field" role="img"
      aria-label={`${label} vector field sampled from the run's own grid`}>
      {arrows.map((a, i) => {
        // Grid row 0 is the southern edge; SVG y grows downward.
        const x = (a.c / Math.max(1, a.cols - 1)) * (W - 8) + 4;
        const y = H - 4 - (a.r / Math.max(1, a.rows - 1)) * (H - 8);
        const len = 3 + a.norm * 5;
        const ang = Math.atan2(a.v, a.u);
        const x2 = x + Math.cos(ang) * len;
        const y2 = y - Math.sin(ang) * len;
        return (
          <g key={i} opacity={0.35 + a.norm * 0.65}>
            <line x1={x} y1={y} x2={x2} y2={y2} stroke={color} strokeWidth={0.6} />
            <circle cx={x2} cy={y2} r={0.8} fill={color} />
          </g>
        );
      })}
    </svg>
  );
}

export default function Environment() {
  const [params, setParams] = useSearchParams();

  const runsQ = useApi(() => api.listRunsPaged({ status: "complete", limit: 60 }), []);
  const providersQ = useApi(() => api.apiStatus(), [], { interval: 30000 });

  const runs = runsQ.data?.items || [];
  const runId = params.get("run") || runs[0]?.run_id || null;

  const [forcing, setForcing] = useState(null);
  const [origin, setOrigin] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    if (!runId) { setForcing(null); return undefined; }
    let alive = true;
    setForcing(null); setErr(null);
    Promise.all([
      api.forcingField(runId).catch((e) => { if (alive) setErr(e); return null; }),
      api.layer(runId, "origin_cloud", { lite: true }).catch(() => null),
    ]).then(([f, o]) => { if (alive) { setForcing(f); setOrigin(o); } });
    return () => { alive = false; };
  }, [runId]);

  const wind = forcing?.wind;
  const currents = forcing?.currents;
  const md = origin?.metadata || {};
  const forcingProviders = (providersQ.data?.providers || [])
    .filter((p) => FORCING_KINDS.has(p.kind));

  return (
    <div className="page" data-testid="environment-page">
      <PageHeader icon={<Wind size={17} />} kicker="Intelligence" title="Environment"
        sub="The ocean-current and wind grids a run actually consumed — not a forecast for today."
        actions={<>
          <button className="btn btn-sm" onClick={() => { runsQ.reload(); providersQ.reload(); }}>
            <RefreshCw size={12} /> Refresh
          </button>
          {runs.length > 1 && (
            <select className="sm" value={runId || ""} style={{ maxWidth: 230 }}
              onChange={(e) => setParams({ run: e.target.value }, { replace: true })}
              data-testid="env-run-select">
              {runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>{r.scene_id || r.run_id}</option>
              ))}
            </select>
          )}
        </>} />

      <div className="grid grid-4 mb-3">
        <Tile label="Mean wind" value={wind?.mean_speed != null ? `${num(wind.mean_speed, 1)} m/s` : "—"}
          tone="accent" sub={wind?.file ? "from the run's grid" : "no grid"} />
        <Tile label="Mean current" value={currents?.mean_speed != null ? `${num(currents.mean_speed, 2)} m/s` : "—"}
          tone="teal" sub={currents?.file ? "from the run's grid" : "no grid"} />
        <Tile label="Backtrack" value={md.backtrack_hours != null ? `${md.backtrack_hours} h` : "—"}
          sub="hindcast window" />
        <Tile label="Particles" value={md.n_particles != null ? fmt.int(md.n_particles) : "—"}
          sub={md.forcing?.engine || "drift engine"} />
      </div>

      {!runId ? (
        <Panel>
          <DataState kind="empty" title="No run selected"
            hint="Forcing fields belong to a run. Complete an investigation to produce them." />
        </Panel>
      ) : (
        <div className="split">
          <div className="stack" style={{ gap: 12 }}>
            <div className="grid grid-2">
              <Panel title="Ocean current" icon={<Waves size={12} />}
                right={currents ? <Badge tone="teal">CMEMS chain</Badge> : <Badge tone="ghost">NO GRID</Badge>}>
                <FieldPreview field={currents} color="var(--teal)" label="current" />
                <div className="kv-dense mt-2">
                  <KV k="Mean speed" v={currents?.mean_speed != null ? `${num(currents.mean_speed, 3)} m/s` : "—"} />
                  <KV k="Max speed" v={currents?.max_speed != null ? `${num(currents.max_speed, 3)} m/s` : "—"} />
                  <KV k="Grid file" v={currents?.file || "—"} wrap />
                  <KV k="Timesteps" v={currents?.times_utc?.length ?? "—"} />
                </div>
              </Panel>

              <Panel title="Wind (10 m)" icon={<Wind size={12} />}
                right={wind ? <Badge tone="accent">ERA5 chain</Badge> : <Badge tone="ghost">NO GRID</Badge>}>
                <FieldPreview field={wind} color="var(--accent)" label="wind" />
                <div className="kv-dense mt-2">
                  <KV k="Mean speed" v={wind?.mean_speed != null ? `${num(wind.mean_speed, 2)} m/s` : "—"} />
                  <KV k="Max speed" v={wind?.max_speed != null ? `${num(wind.max_speed, 2)} m/s` : "—"} />
                  <KV k="Grid file" v={wind?.file || "—"} wrap />
                  <KV k="Timesteps" v={wind?.times_utc?.length ?? "—"} />
                </div>
              </Panel>
            </div>

            {err && (
              <DataState kind="unavailable" title="Forcing field unavailable" error={err}
                hint="This run produced no forcing_field artefact, or it could not be read." />
            )}

            <Panel title="Drift model" icon={<Compass size={12} />}>
              <div className="kv-dense">
                <KV k="Engine" v={md.forcing?.engine || "—"} />
                <KV k="Windage coefficient" v={md.forcing?.windage ?? "0.03 (default)"} />
                <KV k="Timestep" v={md.timestep_minutes != null ? `${md.timestep_minutes} min` : "—"} />
                <KV k="Backtrack" v={md.backtrack_hours != null ? `${md.backtrack_hours} h` : "—"} />
                <KV k="Origin window" v={md.origin_window_start_utc
                  ? `${fmt.utc(md.origin_window_start_utc)} → ${fmt.utc(md.origin_window_end_utc)}` : "—"} wrap />
              </div>
              <Notice style={{ marginTop: 10 }}>
                The origin estimate is physics — a Lagrangian backward integration under these
                grids. No machine learning contributes to it; see{" "}
                <Link to="/models">ML Models</Link> for the benchmark that says why.
              </Notice>
            </Panel>
          </div>

          <div className="stack" style={{ gap: 12 }}>
            <Panel title="Forcing providers" icon={<Activity size={12} />} flush>
              {providersQ.loading && !providersQ.data
                ? <DataState kind="loading" compact title="Probing providers" />
                : forcingProviders.length === 0
                  ? <DataState kind="empty" compact title="No forcing providers registered" />
                  : (
                    <div className="table-wrap">
                      <table>
                        <thead><tr><th>Provider</th><th>Status</th><th>Serving</th></tr></thead>
                        <tbody>
                          {forcingProviders.map((p) => (
                            <tr key={p.provider}>
                              <td>
                                <div>{p.provider}</div>
                                <div className="sub">{p.kind}</div>
                              </td>
                              <td>
                                <Badge status={p.status}>{p.status}</Badge>
                                {p.status === "REACHABLE" && (
                                  <div className="tiny dim mt-1">host answered; not proven to serve data</div>
                                )}
                              </td>
                              <td className="mono tiny">
                                {p.active_provider || "—"}
                                {p.last_success_utc && (
                                  <div className="sub">{fmt.ago(p.last_success_utc)}</div>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
              <div className="panel-foot">
                <Link className="btn btn-sm" to="/monitoring"><Gauge size={12} /> Full API monitor</Link>
              </div>
            </Panel>

            <Panel title="Provenance" icon={<Droplets size={12} />}>
              <div className="kv-dense">
                <KV k="Current source" v={<ProvenanceBadge source={currents?.source || (currents ? "real" : "")} />} />
                <KV k="Wind source" v={<ProvenanceBadge source={wind?.source || (wind ? "real" : "")} />} />
                <KV k="Run" v={runId} wrap />
              </div>
              <div className="tiny dim mt-2" style={{ lineHeight: 1.55 }}>
                These are the grids the drift engine consumed, resolved through the same fallback
                chain the pipeline used — not decorative animation and not a live weather feed.
              </div>
            </Panel>
          </div>
        </div>
      )}

      <style>{`
        .env-field { width: 100%; height: auto; background: var(--bg-0);
          border: 1px solid var(--line); border-radius: var(--r-sm); display: block; }
      `}</style>
    </div>
  );
}
