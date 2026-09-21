/* The headline of each analysis stage, on the map, at a size that can be read
 * from across a room.
 *
 * Taken from Incident Replay, which did this well: a big callout per step and
 * a candidate ranking whose bars grow in one by one. What is NOT taken from it:
 * the attribution total shown as a percentage. It is a weighted sum of factor
 * scores, not a probability that the vessel did it, so it is printed as a
 * score ("0.68") everywhere in the workspace. Model confidence IS a
 * probability and keeps its percent sign.
 *
 * Every number is the run's own artefact: slick.geojson, detect_response,
 * the origin estimate, the attribution funnel, suspects.json. A value the run
 * does not carry is not shown.
 */
import { AnimatePresence, motion } from "framer-motion";
import { Crosshair, Filter, MapPin, Waves } from "lucide-react";

const num = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? null : Number(v).toFixed(d));
const enter = { initial: { opacity: 0, y: -8, scale: 0.97 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, y: -6 }, transition: { duration: 0.35, ease: "easeOut" } };

function Hero({ icon, kicker, tone = "accent", big, unit, sub, children, testid }) {
  return (
    <motion.div className={`hud hud-${tone}`} data-testid={testid} {...enter}>
      <div className="hud-kicker">{icon}{kicker}</div>
      {big != null && <div className="hud-big mono">{big}{unit && <span className="hud-unit">{unit}</span>}</div>}
      {sub && <div className="hud-sub">{sub}</div>}
      {children}
    </motion.div>
  );
}

/** Candidate ranking: rows arrive one after another and each bar grows to its score. */
function Ranking({ suspects, selectedMmsi, onSelect, max = 5 }) {
  const rows = suspects.slice(0, max);
  return (
    <div className="hud-rank" data-testid="hud-ranking">
      {rows.map((s, i) => (
        <motion.button key={s.mmsi} type="button"
          className={`hud-rankrow ${s.rank === 1 ? "top" : ""} ${String(s.mmsi) === String(selectedMmsi) ? "sel" : ""}`}
          initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.25 + i * 0.18, duration: 0.3 }}
          onClick={() => onSelect?.(s.mmsi)} data-testid={`hud-rank-${s.rank}`}>
          <span className="hud-rank-n mono">#{s.rank}</span>
          <span className="hud-rank-name">{s.vessel_name || `MMSI ${s.mmsi}`}</span>
          <span className="hud-rank-bar">
            <motion.span className="hud-rank-fill" initial={{ width: 0 }}
              animate={{ width: `${Math.max(0, Math.min(1, Number(s.total_score) || 0)) * 100}%` }}
              transition={{ delay: 0.4 + i * 0.18, duration: 0.7, ease: "easeOut" }} />
          </span>
          <span className="hud-rank-v mono">{num(s.total_score) ?? "—"}</span>
        </motion.button>
      ))}
      {suspects.length > max && <div className="hud-more">{suspects.length - max} more in the panel</div>}
    </div>
  );
}

export default function MapHud({ stageId, sub, slick, detect, est, originMeta, forecast, counts, suspects, selectedMmsi, onSelect, hidden }) {
  if (hidden) return null;
  const p = slick?.features?.[0]?.properties;
  const total = (slick?.features || []).reduce((a, f) => a + (Number(f.properties?.area_km2) || 0), 0);
  const list = suspects?.suspects || [];
  const top = list[0];
  let body = null;

  if ((stageId === "detection" || stageId === "validation" || stageId === "geometry") && p) {
    const conf = detect?.confidence ?? p.confidence;
    body = (
      <Hero key="slick" testid="hud-slick" tone="oil" icon={<Waves size={13} />} kicker={stageId === "geometry" ? "Slick characterised" : "Oil slick detected"}
        big={conf != null ? (Number(conf) * 100).toFixed(1) : num(total, 2)} unit={conf != null ? "% confidence" : " km²"}
        sub={<>{num(total, 2)} km²{p.major_axis_m ? ` · ${num(p.major_axis_m / 1000, 2)} × ${num((p.minor_axis_m || 0) / 1000, 2)} km` : ""}{slick.features.length > 1 ? ` · ${slick.features.length} regions` : ""}</>} />
    );
  } else if (stageId === "drift" && est) {
    const horizons = [...new Set((forecast?.features || []).map((f) => f.properties?.horizon_h).filter((h) => h != null))].sort((a, b) => a - b);
    const ws = originMeta?.origin_window_start_utc, we = originMeta?.origin_window_end_utc;
    body = (
      <Hero key="origin" testid="hud-origin" tone="origin" icon={<MapPin size={13} />} kicker="Estimated origin"
        big={`± ${num(est.radiusKm, est.radiusKm < 1 ? 2 : 1)}`} unit=" km"
        sub={<>{Math.abs(est.center[1]).toFixed(4)}° {est.center[1] >= 0 ? "N" : "S"} · {Math.abs(est.center[0]).toFixed(4)}° {est.center[0] >= 0 ? "E" : "W"}
          {ws && we ? <><br />window {ws.slice(11, 16)}–{we.slice(11, 16)} UTC, {ws.slice(0, 10)}</> : null}
          {horizons.length ? <><br />forecast to +{horizons[horizons.length - 1]} h</> : null}</>} />
    );
  } else if (stageId === "ais" && sub !== "ranking" && counts?.total != null) {
    const steps = [["considered", counts.remaining?.[0] ?? counts.total], ["in the origin region", counts.remaining?.[1]], ["in the time window", counts.remaining?.[2]], ["on a compatible course", counts.remaining?.[3]]].filter(([, v]) => v != null);
    /* Older runs carry no per-gate counts and the fallback repeats the total
     * at every gate; rows that do not narrow towards the result say nothing
     * true, so they are left out and only the outcome is shown. */
    const narrows = steps.length > 1 && steps[steps.length - 1][1] <= steps[0][1] && steps.some(([, v]) => v !== steps[0][1]);
    if (!narrows) steps.length = 0;
    body = (
      <Hero key="funnel" testid="hud-funnel" tone="accent" icon={<Filter size={13} />} kicker="Vessel filtering"
        big={counts.ranked ?? steps[steps.length - 1]?.[1]} unit={` of ${counts.total} remain`}>
        {steps.length > 0 && <div className="hud-funnel">
          {steps.map(([label, v], i) => (
            <motion.div key={label} className="hud-funnel-row" initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.2 + i * 0.22 }}>
              <span className="hud-funnel-v mono">{v}</span><span>{label}</span>
            </motion.div>
          ))}
        </div>}
        {steps.length === 0 && <div className="hud-sub hud-dim">This run recorded the outcome of the filtering, not a count per gate.</div>}
      </Hero>
    );
  } else if ((stageId === "attribution" || (stageId === "ais" && sub === "ranking")) && top) {
    const shown = list.find((s) => String(s.mmsi) === String(selectedMmsi)) || top;
    body = (
      <Hero key={`rank-${shown.mmsi}`} testid="hud-attribution" tone="accent" icon={<Crosshair size={13} />}
        kicker={shown.rank === 1 ? "Highest-ranked candidate" : `Candidate #${shown.rank}`}
        big={num(shown.total_score) ?? "—"} unit=" score"
        sub={<><b>{shown.vessel_name || `MMSI ${shown.mmsi}`}</b>{shown.vessel_type ? ` · ${shown.vessel_type}` : ""}<br /><span className="hud-dim">weighted evidence score, not a probability</span></>}>
        <Ranking suspects={list} selectedMmsi={selectedMmsi ?? top.mmsi} onSelect={onSelect} />
      </Hero>
    );
  }
  return <div className="hud-wrap"><AnimatePresence mode="wait">{body}</AnimatePresence></div>;
}
