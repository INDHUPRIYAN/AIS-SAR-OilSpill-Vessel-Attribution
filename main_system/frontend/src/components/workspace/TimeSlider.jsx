/* Global time slider: origin window start − 6 h → scene time + 24 h, UTC.
 * Scrubbing moves vessels along interpolated tracks, steps the hindcast
 * cloud, and shifts which forecast horizon is emphasised. Play at 1/4/16×. */

import { useEffect, useRef } from "react";
import { Pause, Play } from "lucide-react";
import { fmtUtc, fmtRel } from "../../lib/replay";

const SPEEDS = [1, 4, 16];
const BASE_H_PER_S = 0.5;      // 1× = half a simulated hour per real second

export default function TimeSlider({ domain, value, onChange, playing,
                                     onPlaying, speed, onSpeed, sceneT0 }) {
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

  if (!domain) return null;
  const [d0, d1] = domain;
  const v = value ?? sceneT0 ?? d0;
  const frac = (v - d0) / (d1 - d0);
  const nowFrac = sceneT0 ? (sceneT0 - d0) / (d1 - d0) : null;

  const ticks = [];
  for (let t = Math.ceil(d0 / 21.6e6) * 21.6e6; t <= d1; t += 21.6e6) ticks.push(t);

  return (
    <div className="ws-timeslider panel" data-testid="time-slider">
      <button className="tbtn" data-testid="time-play"
        onClick={() => onPlaying(!playing)}>
        {playing ? <Pause size={13} /> : <Play size={13} />}
      </button>
      <div className="tspeed">
        {SPEEDS.map((s) => (
          <button key={s} className={`tspeed-b mono ${speed === s ? "on" : ""}`}
            data-testid={`speed-${s}`} onClick={() => onSpeed(s)}>{s}×</button>
        ))}
      </div>

      <div className="ws-rail" data-testid="time-rail"
        onPointerDown={(e) => {
          const el = e.currentTarget;
          const set = (ev) => {
            const r = el.getBoundingClientRect();
            const f = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
            onPlaying(false);
            onChange(d0 + f * (d1 - d0));
          };
          set(e);
          const mv = (ev) => set(ev);
          const up = () => { window.removeEventListener("pointermove", mv);
                             window.removeEventListener("pointerup", up); };
          window.addEventListener("pointermove", mv);
          window.addEventListener("pointerup", up);
        }}>
        {ticks.map((t) => (
          <span key={t} className="ws-tick" style={{ left: `${((t - d0) / (d1 - d0)) * 100}%` }}>
            <i /><em className="mono">{new Date(t).toISOString().slice(11, 16)}</em>
          </span>
        ))}
        {nowFrac != null && (
          <span className="ws-now" style={{ left: `${nowFrac * 100}%` }}>SCENE</span>
        )}
        <span className="ws-head" style={{ left: `${frac * 100}%` }} />
      </div>

      <div className="mono tiny ws-clock" data-testid="time-value">
        {fmtUtc(v)}
        {sceneT0 && <span className="ws-rel">{fmtRel(v, sceneT0)}</span>}
      </div>
    </div>
  );
}
