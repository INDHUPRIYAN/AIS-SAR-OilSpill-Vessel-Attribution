/* Ranked suspects. Clicking a vessel expands its factor breakdown (verbatim
 * reason from suspects.json), highlights its track on the map and flies to
 * it. The header disclaimer is part of the design, not fine print. */

import { Ship } from "lucide-react";
import { FactorBar, Empty } from "../ui";

export default function SuspectsPanel({ suspects, error, tracks,
                                        selectedMmsi, onSelect }) {
  const list = suspects?.suspects ?? [];
  const trackOf = (mmsi) =>
    (tracks?.features ?? []).find((f) => f.properties.mmsi === mmsi)?.properties;

  if (error?.status === 404 || (!list.length && suspects)) {
    return (
      <div data-testid="suspects-empty">
        <Disclaimer />
        <Empty icon={<Ship size={24} color="var(--ink-3)" />}
          title="NO_VESSELS_IN_WINDOW"
          hint="No vessel passed the spatial, temporal and trajectory gates for this origin window. All other layers remain on the map." />
      </div>
    );
  }
  if (!suspects) {
    return <div className="tiny muted">Suspects not yet produced.</div>;
  }

  return (
    <div data-testid="suspects-panel">
      <Disclaimer />
      {list.map((s) => {
        const open = s.mmsi === selectedMmsi;
        const t = trackOf(s.mmsi);
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
                  <FactorBar key={k} name={k} value={v} />
                ))}
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
