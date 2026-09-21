// @ts-check
/* One clock.
 *
 * Every time-aware layer -- AIS replay, wind, currents, hindcast particles,
 * forecast -- reads this context, so there is one slider and nothing can
 * desync. The clock is physical time in epoch milliseconds (UTC).
 *
 * Playing advances a ref on every animation frame and commits to React state
 * at ~20 fps: a layer that needs per-frame smoothness reads `peek()`, and the
 * page does not re-render sixty times a second to move a slider. */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

/** Simulated hours per real second at 1x. */
export const BASE_HOURS_PER_SECOND = 0.5;
export const SPEEDS = [1, 4, 16];
const COMMIT_MS = 50;
const HOUR = 3_600_000;

/**
 * @typedef {object} TimeApi
 * @property {number|null} t            the committed clock, epoch ms
 * @property {[number, number]|null} range
 * @property {boolean} playing
 * @property {number} speed
 * @property {number|null} now          the reference instant the range is built around (e.g. SAR acquisition)
 * @property {(ms: number) => void} setT
 * @property {(range: [number, number]|null, opts?: {now?: number|null, t?: number}) => void} setRange
 * @property {() => void} play
 * @property {() => void} pause
 * @property {() => void} toggle
 * @property {(s: number) => void} setSpeed
 * @property {(hours: number) => void} step
 * @property {() => number|null} peek   the uncommitted, per-frame clock
 */

const Ctx = createContext(/** @type {TimeApi|null} */ (null));

/** @param {number} v @param {[number, number]|null} r */
export function clampToRange(v, r) {
  if (!r) return v;
  return Math.max(r[0], Math.min(r[1], v));
}

/** @param {{children: any, initialRange?: [number, number]|null, initialT?: number|null}} props */
export function TimeProvider({ children, initialRange = null, initialT = null }) {
  const [range, setRangeState] = useState(initialRange);
  const [t, setTState] = useState(initialT ?? (initialRange ? initialRange[1] : null));
  const [now, setNow] = useState(/** @type {number|null} */ (null));
  const [playing, setPlaying] = useState(false);
  const loopFrom = useRef(/** @type {number | null} */ (null));
  const [speed, setSpeed] = useState(SPEEDS[1]);

  const live = useRef(t);
  const rangeRef = useRef(range);
  rangeRef.current = range;

  const setT = useCallback((/** @type {number} */ ms) => {
    if (!Number.isFinite(ms)) return;                 // never let a bad value become the time
    const v = clampToRange(ms, rangeRef.current);
    live.current = v;
    setTState(v);
  }, []);

  const setRange = useCallback((/** @type {[number, number]|null} */ r, opts = {}) => {
    rangeRef.current = r;
    setRangeState(r);
    if (opts.now !== undefined) setNow(opts.now);
    const next = opts.t ?? live.current ?? opts.now ?? (r ? r[1] : null);
    const v = next == null ? null : clampToRange(next, r);
    live.current = v;
    setTState(v);
  }, []);

  useEffect(() => {
    if (!playing) return undefined;
    let raf = 0;
    let last = performance.now();
    let committed = last;
    const tick = (/** @type {number} */ ts) => {
      const r = rangeRef.current;
      if (!r || live.current == null) { setPlaying(false); return; }
      const dt = (ts - last) / 1000;
      last = ts;
      const next = live.current + dt * speed * BASE_HOURS_PER_SECOND * HOUR;
      if (next >= r[1]) {
        /* a loop replays [from, end] until someone pauses it */
        const from = loopFrom.current;
        if (from != null && from < r[1]) { live.current = Math.max(r[0], from); setTState(live.current); committed = ts; raf = requestAnimationFrame(tick); return; }
        live.current = r[1]; setTState(r[1]); setPlaying(false); return;
      }
      live.current = next;
      if (ts - committed >= COMMIT_MS) { committed = ts; setTState(next); }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed]);

  const api = useMemo(() => /** @type {TimeApi} */ ({
    t, range, playing, speed, now, setT, setRange, setSpeed,
    play: () => {
      const r = rangeRef.current;
      // Play from the start when the clock is parked at the end.
      if (r && live.current != null && live.current >= r[1]) { live.current = r[0]; setTState(r[0]); }
      setPlaying(true);
    },
    /* Replay [fromMs, end of range] continuously: the attribution map uses it
     * so the ships are seen moving without anyone pressing play. Any pause,
     * and any manual play, ends the loop. */
    playLoop: (fromMs) => {
      const r = rangeRef.current;
      if (!r) return;
      loopFrom.current = Math.max(r[0], Math.min(fromMs, r[1]));
      live.current = loopFrom.current; setTState(loopFrom.current);
      setPlaying(true);
    },
    pause: () => { loopFrom.current = null; setPlaying(false); },
    toggle: () => { loopFrom.current = null; setPlaying((p) => !p); },
    step: (hours) => setT((live.current ?? 0) + hours * HOUR),
    peek: () => live.current,
  }), [t, range, playing, speed, now, setT, setRange]);

  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}

/** The shared clock. Throws outside a provider: a layer silently running on
 *  its own clock is the bug this context exists to prevent. */
export function useTime() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTime must be used inside <TimeProvider>");
  return v;
}

/** The clock if there is one -- for components that also render without time. */
export function useOptionalTime() {
  return useContext(Ctx);
}
