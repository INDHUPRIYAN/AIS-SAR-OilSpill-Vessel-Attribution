/* The bottom analysis bar: stage chips, transport, and the investigation
 * time rail.
 *
 * Two clocks live here and are never confused:
 *   - the STAGE chips are the investigation's workflow. Each executed stage
 *     carries the wall-clock instant it completed (run start + cumulative
 *     stage seconds) as its tooltip; a derived stage carries none.
 *   - the RAIL is physical time over the scene: origin window − 6 h → scene
 *     + 24 h. Scrubbing it moves vessels along interpolated tracks, steps the
 *     hindcast cloud and shifts which forecast horizon is emphasised. Play
 *     advances that clock; prev/next step the stages.
 * Everything drawn is read from artefacts; nothing animates on its own. */

import { useEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, Clapperboard, Loader2, Pause, Play, SkipBack, SkipForward, Square, StepForward } from "lucide-react";

import { CHIPS, STAGES, STAGE_INDEX } from "../../lib/stages";
import { BEATS, BEAT_CHIPS } from "../../lib/cinematic";
import { fmtUtc, fmtRel } from "../../lib/replay";

const CINE_SPEEDS = [1, 2, 4];

/* The investigation's presentation rail (spec §22): every analytical beat,
 * grouped under its heading, the active one glowing. Read-only while the
 * presentation plays; afterwards each beat is a scrub target. */
function BeatRail({ cine, onCine, canPlay }) {
  const { active, index, t, finished, hold, stopped, beat } = cine;
  const reached = (i) => (finished ? true : i < index || (i === index && active));
  return (
    <div className={`tl-cine ${active ? "live" : ""}`} data-testid="cine-strip">
      <div className="tl-cine-ctl">
        {active ? (
          <>
            <button className="tl-cine-btn stop" onClick={() => onCine.stop()} title="Stop the presentation and take over" data-testid="cine-stop"><Square size={11} /> Stop</button>
            <button className="tl-cine-btn" onClick={() => onCine.skip()} title="Next beat" data-testid="cine-skip"><StepForward size={12} /></button>
            <div className="tl-speed">
              {CINE_SPEEDS.map((s) => (
                <button key={s} className={`tl-speed-b mono ${cine.speed === s ? "on" : ""}`} data-testid={`cine-speed-${s}`}
                  onClick={() => onCine.setSpeed(s)}>{s}×</button>
              ))}
            </div>
          </>
        ) : (
          <button className="tl-cine-btn play" onClick={() => onCine.start("globe")} disabled={!canPlay}
            title={canPlay ? "Play the investigation as one continuous analysis over the map" : "Load a scene and run or replay the analysis first"}
            data-testid="cine-play"><Clapperboard size={12} /> Play replay</button>
        )}
      </div>
      <div className="tl-beats" data-testid="beat-rail">
        {BEAT_CHIPS.map((chip) => {
          const group = BEATS.map((b, i) => ({ ...b, i })).filter((b) => b.chip === chip);
          const on = group.some((b) => b.i === index && (active || finished || stopped));
          return (
            <div key={chip} className={`tl-beatgroup ${on ? "on" : ""}`}>
              <span className="tl-beatgroup-label">{chip}</span>
              <div className="tl-beatgroup-dots">
                {group.map((b) => (
                  <button key={b.id} className={`tl-beat ${b.i === index && active ? "live" : ""} ${reached(b.i) ? "done" : ""} ${cine.skipped?.[b.id] ? "skipped" : ""}`}
                    style={b.i === index && active ? { "--p": `${Math.round(t * 100)}%` } : undefined}
                    onClick={() => canPlay && onCine.jump(b.id)} disabled={!canPlay}
                    title={cine.skipped?.[b.id] ? `${b.title} — skipped: ${cine.skipped[b.id]}` : b.title}
                    data-testid={`beat-${b.id}`} data-live={b.i === index && active ? "true" : "false"} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div className="tl-cine-now" data-testid="cine-now">
        {active ? (
          <>
            {hold && hold.need !== "flight" ? <Loader2 size={12} className="ws-spin" /> : null}
            <b>{beat?.title}</b>
            {hold && hold.need !== "flight" && <span className="tl-cine-hold">waiting for {hold.need} — {hold.why}</span>}
          </>
        ) : stopped ? (
          <span className="tl-cine-hold" data-testid="cine-stopped">{stopped}</span>
        ) : finished ? (
          <span className="dim">Presentation complete — scrub any beat, or inspect the stages above.</span>
        ) : (
          <span className="dim">Scene → detection → segmentation → wind → current → hindcast → origin → forecast → AIS → filtering → ranking → attribution.</span>
        )}
      </div>
    </div>
  );
}

const SPEEDS = [1, 4, 16];
const BASE_H_PER_S = 0.5;      // 1× = half a simulated hour per real second

function tickLabel(t, spanMs) {
  const d = new Date(t);
  const day = d.toLocaleDateString("en-GB", { month: "short", day: "2-digit", timeZone: "UTC" });
  const hm = d.toISOString().slice(11, 16);
  return spanMs > 3 * 86400e3 ? day : `${day}\n${hm}`;
}

export default function StageTimeline({
  stageId, judged, onStage, clocks, domain, value, onChange, playing, onPlaying,
  speed, onSpeed, sceneT0, title = "Scene analysis", scenes, cine, onCine, canPlay,
}) {
  const raf = useRef(0);
  const last = useRef(0);

  useEffect(() => {
    if (!playing || !domain) return undefined;
    last.current = performance.now();
    const loop = (now) => {
      const dt = (now - last.current) / 1000;
      last.current = now;
      onChange((v) => {
        const nv = (v ?? domain[0]) + dt * speed * BASE_H_PER_S * 3.6e6;
        if (nv >= domain[1]) { onPlaying(false); return domain[1]; }
        return nv;
      });
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, speed, domain, onChange, onPlaying]);

  const idx = STAGE_INDEX[stageId] ?? 0;
  const activeChip = STAGES[idx]?.chip;
  const chipStage = (chip) => {
    /* A chip opens the first stage of its group; clicking the chip of the
     * group already open steps to the group's next stage (and wraps), so the
     * five chips walk every stage without a second control. The SCENE chip
     * opens the loaded scene rather than the acquisition form once a scene
     * exists. */
    const group = STAGES.filter((s) => s.chip === chip);
    const inGroup = group.findIndex((s) => s.id === stageId);
    if (inGroup >= 0) return group[(inGroup + 1) % group.length].id;
    if (chip === "SCENE" && judged?.scene?.state === "done") return "scene";
    return group[0].id;
  };
  const chipState = (chip) => {
    const group = STAGES.filter((s) => s.chip === chip);
    if (group.some((s) => judged?.[s.id]?.state === "running")) return "running";
    if (group.every((s) => judged?.[s.id]?.state === "done")) return "done";
    if (group.some((s) => judged?.[s.id]?.state === "failed")) return "failed";
    return "pending";
  };
  const chipClock = (chip) => {
    const group = STAGES.filter((s) => s.chip === chip && s.executed);
    const ts = group.map((s) => clocks?.[s.executed]).filter(Boolean);
    return ts.length ? Math.max(...ts) : null;
  };

  const prev = () => onStage(STAGES[Math.max(0, idx - 1)].id);
  const next = () => onStage(STAGES[Math.min(STAGES.length - 1, idx + 1)].id);

  const [d0, d1] = domain || [null, null];
  const v = value ?? sceneT0 ?? d0;
  const frac = domain ? (v - d0) / (d1 - d0) : 0;
  const nowFrac = domain && sceneT0 ? (sceneT0 - d0) / (d1 - d0) : null;
  const ticks = [];
  if (domain) {
    const span = d1 - d0;
    const step = span > 3 * 86400e3 ? 86400e3 : span > 36 * 3.6e6 ? 43.2e6 : 21.6e6;
    for (let t = Math.ceil(d0 / step) * step; t <= d1; t += step) ticks.push(t);
  }

  return (
    <div className={`tl ${cine?.active ? "tl-live" : ""}`} data-testid="stage-timeline">
      <div className="tl-top">
        <span className="tl-title">{cine?.active ? "Investigation timeline" : title}</span>
        <div className="tl-chips" data-testid="stage-chips">
          {CHIPS.map((chip) => {
            const st = chipState(chip);
            const t = chipClock(chip);
            return (
              <button key={chip} className={`tl-chip tl-chip-${st} ${activeChip === chip ? "on" : ""} ${activeChip === chip && cine?.active ? "live" : ""}`}
                onClick={() => onStage(chipStage(chip))} data-testid={`chip-${chip.toLowerCase()}`}
                title={t ? `completed ${fmtUtc(t)}` : st === "pending" ? "not reached" : st}>
                {chip}
              </button>
            );
          })}
        </div>
        <div className="tl-nav">
          <button className="tl-btn" onClick={prev} disabled={idx === 0} title="Previous stage" data-testid="stage-prev"><ChevronLeft size={15} /></button>
          <button className="tl-btn" onClick={next} disabled={idx === STAGES.length - 1} title="Next stage" data-testid="stage-next"><ChevronRight size={15} /></button>
        </div>
      </div>

      {cine && onCine && <BeatRail cine={cine} onCine={onCine} canPlay={canPlay} />}

      <div className="tl-row">
        <div className="tl-transport">
          <button className="tl-btn" onClick={() => onChange(d0)} disabled={!domain} title="Start"><SkipBack size={13} /></button>
          <button className="tl-play" onClick={() => domain && onPlaying(!playing)} disabled={!domain || cine?.active} data-testid="time-play"
            title={cine?.active ? "The presentation is driving the clock" : playing ? "Pause" : "Play the investigation clock"}>
            {playing ? <Pause size={15} /> : <Play size={15} />}
          </button>
          <button className="tl-btn" onClick={() => onChange(d1)} disabled={!domain} title="End"><SkipForward size={13} /></button>
          <div className="tl-speed">
            {SPEEDS.map((s) => (
              <button key={s} className={`tl-speed-b mono ${speed === s ? "on" : ""}`} data-testid={`speed-${s}`}
                onClick={() => onSpeed(s)}>{s}×</button>
            ))}
          </div>
        </div>

        {domain ? (
          <div className="tl-rail" data-testid="time-rail"
            onPointerDown={(e) => {
              if (cine?.active) onCine?.stop();
              const el = e.currentTarget;
              const set = (ev) => {
                const r = el.getBoundingClientRect();
                const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
                onPlaying(false);
                onChange(d0 + f * (d1 - d0));
              };
              set(e);
              const mv = (ev) => set(ev);
              const up = () => { window.removeEventListener("pointermove", mv); window.removeEventListener("pointerup", up); };
              window.addEventListener("pointermove", mv);
              window.addEventListener("pointerup", up);
            }}>
            <div className="tl-line" />
            <div className="tl-past" style={{ width: `${Math.max(0, Math.min(100, frac * 100))}%` }} />
            {ticks.map((t) => (
              <span key={t} className="tl-tick" style={{ left: `${((t - d0) / (d1 - d0)) * 100}%` }}>
                <i /><em className={`mono ${sceneT0 && Math.abs(t - sceneT0) < 1 ? "now" : ""}`}>{tickLabel(t, d1 - d0)}</em>
              </span>
            ))}
            {nowFrac != null && (
              <span className="tl-scene" style={{ left: `${nowFrac * 100}%` }} title={`scene acquired ${fmtUtc(sceneT0)}`}><i /><em>SCENE</em></span>
            )}
            {(scenes || []).map((s) => (
              <span key={s.id} className={`tl-mark ${s.selected ? "on" : ""}`} style={{ left: `${((s.t - d0) / (d1 - d0)) * 100}%` }} title={s.label} />
            ))}
            <span className="tl-head" style={{ left: `${Math.max(0, Math.min(100, frac * 100))}%` }} />
          </div>
        ) : (
          <div className="tl-rail tl-rail-empty"><span className="dim tiny">The timeline fills in when a scene is loaded.</span></div>
        )}

        <div className={`tl-clock mono ${cine?.active ? "live" : ""}`} data-testid="time-value">
          {domain ? fmtUtc(v) : "—"}
          {domain && sceneT0 && <span className="tl-rel">{fmtRel(v, sceneT0)}</span>}
        </div>
      </div>
    </div>
  );
}
