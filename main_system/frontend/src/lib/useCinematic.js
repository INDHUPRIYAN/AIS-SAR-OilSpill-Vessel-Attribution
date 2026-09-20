/* The presentation clock for lib/cinematic's beats.
 *
 * One requestAnimationFrame loop advances the active beat's progress. Before
 * each frame it asks the page (through a ref, so the loop always reads the
 * latest render) whether the beat's inputs are ready:
 *
 *   ready    progress advances
 *   wait     progress freezes and `hold` says what the run is doing
 *   missing  a soft beat is skipped (`skipped` remembers why); a hard beat
 *            stops the presentation on that input
 *   failed   the presentation stops on the failure, with its detail
 *
 * The hook holds no pipeline state and never fakes a completion: it only
 * decides how far along the storytelling is. `jump()` lets the analyst scrub
 * the beats afterward, `skip()` moves to the next one, `stop()` hands the
 * workspace back. */

import { useCallback, useEffect, useRef, useState } from "react";

import { BEATS, BEAT_INDEX, beatGate } from "./cinematic";

const IDLE = { active: false, index: 0, t: 0, hold: null, finished: false, stopped: null, skipped: {}, speed: 1 };
/* Progress is committed to React at ~24 fps, not every animation frame: each
 * commit re-renders the whole workspace (and rebuilds its deck layers), and
 * the page's own data loading must not be starved by the presentation. */
const COMMIT_MS = 42;
/* A soft beat waits this long (wall clock) for an optional input that is
 * still loading -- a forcing grid can take tens of seconds to read -- then
 * moves on and says so. The vectors still appear later if the grid arrives. */
const SOFT_WAIT_MS = 10_000;

export function useCinematic({ readiness }) {
  const [st, setSt] = useState(IDLE);
  const readyRef = useRef(readiness);
  readyRef.current = readiness;
  const raf = useRef(0);
  const last = useRef(0);
  const state = useRef(IDLE);

  const commit = useCallback((next) => { state.current = next; setSt(next); }, []);

  const start = useCallback((fromId = "globe", speed) => {
    const index = BEAT_INDEX[fromId] ?? 0;
    commit({ ...IDLE, active: true, index, speed: speed ?? state.current.speed ?? 1 });
  }, [commit]);
  const stop = useCallback((reason = "stopped") => {
    if (!state.current.active) return;
    commit({ ...state.current, active: false, stopped: reason });
  }, [commit]);
  const jump = useCallback((id) => {
    const index = BEAT_INDEX[id];
    if (index == null) return;
    commit({ ...state.current, active: true, index, t: 0, hold: null, finished: false, stopped: null });
  }, [commit]);
  const skip = useCallback(() => {
    const cur = state.current;
    if (!cur.active) return;
    if (cur.index + 1 >= BEATS.length) { commit({ ...cur, active: false, t: 1, finished: true }); return; }
    commit({ ...cur, index: cur.index + 1, t: 0, hold: null });
  }, [commit]);
  const setSpeed = useCallback((speed) => commit({ ...state.current, speed }), [commit]);

  useEffect(() => {
    if (!st.active) return undefined;
    last.current = performance.now();
    let lastCommit = 0;
    let holdSince = null;
    const exhausted = new Set();   // optional inputs whose wait budget is spent
    let pending = null;   // progress accumulated between commits
    const loop = (now) => {
      const dt = Math.min(0.1, (now - last.current) / 1000);
      last.current = now;
      const cur = pending || state.current;
      if (!cur.active) return;
      const beat = BEATS[cur.index];
      const gate = beatGate(beat, readyRef.current);
      let next = cur;
      let structural = false;   // a beat change, hold, skip or stop commits at once
      if (gate.s === "ready") {
        holdSince = null;
        const t = cur.t + (dt * cur.speed) / beat.dur;
        if (t >= 1) {
          structural = true;
          if (cur.index + 1 >= BEATS.length) next = { ...cur, t: 1, active: false, finished: true, hold: null };
          else next = { ...cur, index: cur.index + 1, t: 0, hold: null };
        } else next = { ...cur, t, hold: null };
        if (cur.hold) structural = true;
      } else if (gate.s === "wait" && beat.soft && (exhausted.has(gate.need) || (holdSince != null && now - holdSince > SOFT_WAIT_MS))) {
        /* The budget is per input, not per beat: once the forcing grid has
         * had its ten seconds, every later optional beat on it moves on too. */
        structural = true;
        exhausted.add(gate.need);
        const skipped = { ...cur.skipped, [beat.id]: `still ${gate.why}` };
        holdSince = null;
        if (cur.index + 1 >= BEATS.length) next = { ...cur, active: false, finished: true, skipped };
        else next = { ...cur, index: cur.index + 1, t: 0, hold: null, skipped };
      } else if (gate.s === "wait") {
        if (holdSince == null) holdSince = now;
        if (!cur.hold || cur.hold.need !== gate.need) { structural = true; next = { ...cur, hold: { need: gate.need, why: gate.why } }; }
      } else if (gate.s === "missing" && beat.soft) {
        structural = true;
        const skipped = { ...cur.skipped, [beat.id]: gate.why };
        if (cur.index + 1 >= BEATS.length) next = { ...cur, active: false, finished: true, skipped };
        else next = { ...cur, index: cur.index + 1, t: 0, hold: null, skipped };
      } else {
        structural = true;
        next = { ...cur, active: false, hold: null,
          stopped: `${beat.title}: ${gate.need} ${gate.s === "failed" ? "failed" : "unavailable"} — ${gate.why}` };
      }
      if (next !== cur) {
        if (structural || now - lastCommit >= COMMIT_MS) { commit(next); pending = null; lastCommit = now; }
        else pending = next;
      }
      if (next.active) raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf.current);
  }, [st.active, commit]);

  return {
    ...st,
    beat: BEATS[st.index],
    start, stop, jump, skip, setSpeed,
  };
}
