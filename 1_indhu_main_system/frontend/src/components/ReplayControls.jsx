/* Bottom transport bar: step navigation, playback transport, speed, and the
 * global investigation timeline. The timeline is a real clock — dragging it
 * moves every time-aware layer (vessels, hindcast cloud, forcing field) to
 * that instant; it never invents intermediate states.
 */

import { useCallback, useRef } from "react";
import {
  Play, Pause, RotateCcw, SkipBack, SkipForward, ChevronLeft, ChevronRight,
  Film, ListOrdered,
} from "lucide-react";

import { STEPS, fmtRel, clamp01 } from "../lib/replay";

const SPEEDS = [0.5, 1, 2, 4, 8, 16];

export default function ReplayControls({
  bundle: b, frame, playing, speed, mode,
  onPlay, onPause, onRestart, onStep, onJump, onSpeed, onMode, onScrub,
}) {
  const railRef = useRef(null);
  const step = STEPS[frame.stepIdx];

  const scrub = useCallback((e) => {
    const el = railRef.current;
    if (!el || !b) return;
    const r = el.getBoundingClientRect();
    const f = clamp01((e.clientX - r.left) / r.width);
    onScrub(b.domain[0] + f * (b.domain[1] - b.domain[0]));
  }, [b, onScrub]);

  const drag = useCallback((e) => {
    scrub(e);
    const move = (ev) => scrub(ev);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [scrub]);

  if (!b) return null;
  const [d0, d1] = b.domain;
  const playFrac = clamp01((frame.simT - d0) / (d1 - d0));
  const nowFrac = clamp01((b.t0 - d0) / (d1 - d0));

  /* tick marks every 6 h across the domain */
  const ticks = [];
  for (let t = Math.ceil(d0 / 21.6e6) * 21.6e6; t <= d1; t += 21.6e6) {
    ticks.push(t);
  }

  return (
    <div className="transport panel">
      {/* ------------------------------------------------- step chips ----- */}
      <div className="transport-steps">
        {STEPS.map((s, i) => (
          <button key={s.id}
            className={`stepchip ${i === frame.stepIdx ? "on" : ""} ${i < frame.stepIdx ? "done" : ""}`}
            title={s.title}
            onClick={() => onJump(i)}>
            <span className="stepchip-n mono">{String(s.n).padStart(2, "0")}</span>
            <span className="stepchip-t">{shortTitle(s)}</span>
            {i === frame.stepIdx && (
              <span className="stepchip-bar"
                style={{ width: `${frame.stepT * 100}%` }} />
            )}
          </button>
        ))}
      </div>

      <div className="transport-row">
        {/* ------------------------------------------------ transport ----- */}
        <div className="transport-buttons">
          <button className="tbtn" title="Restart" onClick={onRestart}>
            <RotateCcw size={14} /></button>
          <button className="tbtn" title="Previous step"
            onClick={() => onStep(-1)}><SkipBack size={14} /></button>
          <button className="tbtn tbtn-play"
            title={playing ? "Pause" : "Play"}
            onClick={playing ? onPause : onPlay}>
            {playing ? <Pause size={16} /> : <Play size={16} />}
          </button>
          <button className="tbtn" title="Next step"
            onClick={() => onStep(1)}><SkipForward size={14} /></button>

          <div className="tsep" />
          <div className="tmode">
            <button className={`tmode-b ${mode === "replay" ? "on" : ""}`}
              onClick={() => onMode("replay")} title="Full replay">
              <Film size={12} /> REPLAY</button>
            <button className={`tmode-b ${mode === "step" ? "on" : ""}`}
              onClick={() => onMode("step")} title="Step-by-step">
              <ListOrdered size={12} /> STEP</button>
          </div>

          {mode === "step" && (
            <div className="tstep-nav mono">
              <button className="tbtn" onClick={() => onStep(-1)}>
                <ChevronLeft size={13} /></button>
              {String(step.n).padStart(2, "0")} / {STEPS.length}
              <button className="tbtn" onClick={() => onStep(1)}>
                <ChevronRight size={13} /></button>
            </div>
          )}

          <div className="tsep" />
          <div className="tspeed">
            {SPEEDS.map((s) => (
              <button key={s}
                className={`tspeed-b mono ${speed === s ? "on" : ""}`}
                onClick={() => onSpeed(s)}>{s}x</button>
            ))}
          </div>
        </div>

        {/* -------------------------------------------------- timeline ---- */}
        <div className="timeline" ref={railRef} onPointerDown={drag}>
          <div className="timeline-rail">
            <div className="timeline-past"
              style={{ width: `${nowFrac * 100}%` }} />
            <div className="timeline-now" style={{ left: `${nowFrac * 100}%` }}>
              <span>NOW</span>
            </div>
            {ticks.map((t) => (
              <div key={t} className="timeline-tick"
                style={{ left: `${((t - d0) / (d1 - d0)) * 100}%` }}>
                <i /><span className="mono">{fmtRel(t, b.t0)}</span>
              </div>
            ))}
            <div className="timeline-head" style={{ left: `${playFrac * 100}%` }} />
          </div>
          <div className="timeline-clock mono">
            {new Date(frame.simT).toISOString().replace("T", " ").slice(0, 16)} UTC
            <span className="timeline-rel">{fmtRel(frame.simT, b.t0)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function shortTitle(s) {
  return {
    location: "Location", radar: "Radar", ais: "Vessels", detect: "Detection",
    env: "Wind·Current", drift: "Drift", hindcast: "Hindcast",
    origin: "Origin", filter: "Filtering", attribution: "Attribution",
    evidence: "Evidence",
  }[s.id];
}
