/* Ranked suspects. Clicking a vessel expands its factor breakdown (verbatim
 * reason from suspects.json), highlights its track on the map and flies to
 * it. The header disclaimer is part of the design, not fine print.
 *
 * Everything shown is auditable against suspects.json: the factor weights
 * actually used, the raw evidence numbers behind each score, and the vessels
 * that were filtered out with the reason each one was excluded. */

import { useState } from "react";
import { ChevronDown, ChevronRight, Ship } from "lucide-react";
import { FactorBar, Empty } from "../ui";

/* Contract field -> human label + formatter. Any field may be null (and the
 * whole evidence block may be absent on old runs); nulls are hidden, never
 * rendered as "0" or "—" — an absent measurement is not a measurement. */
const num = (v, d = 1) => Number(v).toFixed(d).replace(/\.0$/, "");
const EVIDENCE_ROWS = [
  ["closest_approach_km", "Closest approach", (v) => `${num(v, 1)} km`],
  ["time_in_origin_window_min", "Time in origin window", (v) => `${num(v, 0)} min`],
  ["ais_gap_minutes", "AIS gap", (v) => `${num(v, 0)} min`],
  ["course_delta_deg", "Course change", (v) => `${num(v, 0)}°`],
  ["min_sog_kn", "Slowest speed", (v) => `${num(v, 1)} kn`],
  ["track_points_in_cloud", "Track points in cloud", (v) => `${Math.round(v)}`],
];

export default function SuspectsPanel({ suspects, error, tracks,
                                        selectedMmsi, onSelect }) {
  const list = suspects?.suspects ?? [];
  const weights = suspects?.weights ?? null;
  const trackOf = (mmsi) =>
    (tracks?.features ?? []).find((f) => f.properties.mmsi === mmsi)?.properties;

  if (error?.status === 404 || (!list.length && suspects)) {
    return (
      <div data-testid="suspects-empty">
        <Disclaimer />
        <Empty icon={<Ship size={24} color="var(--ink-3)" />}
          title="NO_VESSELS_IN_WINDOW"
          hint="No vessel passed the spatial, temporal and trajectory gates for this origin window. All other layers remain on the map." />
        <FilteredOut suspects={suspects} onSelect={onSelect} />
      </div>
    );
  }
  if (!suspects) {
    return <div className="tiny muted">Suspects not yet produced.</div>;
  }

  return (
    <div data-testid="suspects-panel">
      <Disclaimer />
      <WeightsLegend weights={weights} />
      {list.map((s) => {
        const open = s.mmsi === selectedMmsi;
        const t = trackOf(s.mmsi);
        const evidence = EVIDENCE_ROWS
          .map(([key, label, fmt]) => {
            const v = s.evidence?.[key];
            return v == null ? null : [key, label, fmt(v)];
          })
          .filter(Boolean);
        return (
          <div key={s.mmsi}
            className={`ws-suspect ${open ? "open" : ""} ${s.rank === 1 ? "top" : ""}`}
            data-testid={`suspect-${s.rank}`}
            onClick={() => onSelect(open ? null : s.mmsi)}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="mono ws-rank">#{s.rank}</span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="ws-suspect-name">
                  {s.vessel_name || `MMSI ${s.mmsi}`}
                </div>
                <div className="tiny muted mono">{s.mmsi} · {s.vessel_type}</div>
              </div>
              <div style={{ width: 74 }}>
                <div className="factor-track">
                  <span className="factor-fill" style={{
                    width: `${Math.min(100, s.total_score * 100)}%`,
                    background: s.rank === 1 ? "var(--danger)" : "var(--warn)",
                    display: "block", height: "100%",
                  }} />
                </div>
              </div>
              <span className="mono ws-score" data-testid={`score-${s.rank}`}>
                {Number(s.total_score).toFixed(2)}
              </span>
            </div>

            {open && (
              <div className="ws-suspect-detail" data-testid="suspect-detail">
                {Object.entries(s.sub_scores || {}).map(([k, v]) => (
                  <FactorBar key={k} name={k} value={v} weight={weights?.[k]} />
                ))}
                {evidence.length > 0 && (
                  <div className="ws-evidence" data-testid="suspect-evidence">
                    <div className="tiny muted ws-evidence-title">EVIDENCE</div>
                    {evidence.map(([key, label, value]) => (
                      <div key={key} className="ip-row"
                        data-testid={`evidence-${key}`}>
                        <span className="ip-k">{label}</span>
                        <span className="ip-v mono">{value}</span>
                      </div>
                    ))}
                  </div>
                )}
                {t?.distance_km != null && (
                  <div className="ip-row">
                    <span className="ip-k">Distance in window</span>
                    <span className="ip-v mono">{t.distance_km} km
                      {t.duration_h ? ` / ${t.duration_h} h` : ""}</span>
                  </div>
                )}
                {s.reason && (
                  <div className="tiny ws-reason" data-testid="suspect-reason">
                    {s.reason}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      <FilteredOut suspects={suspects} onSelect={onSelect} />
    </div>
  );
}

function Disclaimer() {
  return (
    <div className="tiny muted ws-disclaimer" data-testid="suspects-disclaimer">
      Attribution likelihood — investigative support, not proof of guilt.
    </div>
  );
}

/** The factor weights the run ACTUALLY used, straight from suspects.json —
 *  never hardcoded here. Shown before the list so the scoring is auditable
 *  without opening a suspect; the per-factor ×w repeats inside each bar. */
function WeightsLegend({ weights }) {
  if (!weights) return null;
  return (
    <div className="ws-weights" data-testid="weights-legend">
      <span className="tiny muted">score = Σ weight × factor</span>
      <div className="ws-weights-chips">
        {Object.entries(weights).map(([k, w]) => (
          <span key={k} className="ws-weight-chip mono">
            {k.replace(/_/g, " ")} <b>{Number(w).toFixed(2)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

/** Vessels excluded before scoring, each with the gate that excluded it.
 *  Collapsed by default — it proves the filtering was principled, it is not
 *  part of the ranking. Clicking a row highlights the dimmed track. */
function FilteredOut({ suspects, onSelect }) {
  const [expanded, setExpanded] = useState(false);
  const filtered = suspects?.filtered_out ?? [];
  if (!filtered.length) return null;
  const total = suspects?.total_vessels_considered;
  return (
    <div className="ws-filtered" data-testid="filtered-section">
      <button className="ws-filtered-head" data-testid="filtered-toggle"
        onClick={() => setExpanded((v) => !v)}>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        Filtered out ({filtered.length}{total ? ` of ${total} considered` : ""})
      </button>
      {expanded && filtered.map((f) => (
        <div key={f.mmsi} className="ws-filtered-row"
          data-testid={`filtered-${f.mmsi}`}
          onClick={() => onSelect?.(f.mmsi)}>
          <span className="mono ws-filtered-mmsi">{f.mmsi}</span>
          <span className="tiny muted">{f.reason}</span>
        </div>
      ))}
    </div>
  );
}
