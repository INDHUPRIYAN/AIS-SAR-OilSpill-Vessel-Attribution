/* Live stage progress for a run, over SSE, with polling as the fallback.
 *
 * The workspace used to poll `/investigations/{id}/status` on a timer. That is
 * a fine fallback and a poor default: at a 2 s interval a 4 s stage looks
 * instantaneous and a 150 s stage costs 75 pointless requests.
 *
 * Two things this deliberately does NOT do:
 *
 *  - it does not hold pipeline state of its own. Every stage row it reports
 *    came from the server, so a browser that connects halfway through a run
 *    gets the current status immediately rather than only future transitions;
 *  - it does not silently give up. If EventSource fails -- a proxy that buffers
 *    text/event-stream, an old browser, a corporate MITM -- it falls back to
 *    polling and says so through `transport`, because a UI that quietly stops
 *    updating is worse than one that admits it is polling.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";

const TERMINAL = new Set(["complete", "failed", "cancelled", "idle_timeout"]);
const POLL_MS = 2000;

export function useRunEvents(runId, { onEnd } = {}) {
  const [stages, setStages] = useState([]);
  const [runStatus, setRunStatus] = useState(null);
  // "sse" | "poll" | null. Surfaced so the UI can be honest about how it knows.
  const [transport, setTransport] = useState(null);
  const [error, setError] = useState(null);
  const endedRef = useRef(false);
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;

  const finish = useCallback((status) => {
    if (endedRef.current) return;
    endedRef.current = true;
    setRunStatus(status);
    if (onEndRef.current) onEndRef.current(status);
  }, []);

  useEffect(() => {
    if (!runId) {
      setStages([]); setRunStatus(null); setTransport(null);
      return undefined;
    }

    endedRef.current = false;
    setStages([]); setRunStatus(null); setError(null);

    let source = null;
    let pollTimer = null;
    let cancelled = false;

    /* ---------------------------------------------------------- polling -- */
    function startPolling(reason) {
      if (cancelled || pollTimer) return;
      setTransport("poll");
      if (reason) setError(reason);
      const tick = async () => {
        if (cancelled) return;
        try {
          const status = await api.getRun(runId);
          const rows = status?.stages || [];
          if (rows.length) setStages(rows);
          if (status?.status && TERMINAL.has(status.status)) {
            finish(status.status);
            clearInterval(pollTimer);
            pollTimer = null;
          }
        } catch {
          /* a transient failure is not a reason to stop watching */
        }
      };
      tick();
      pollTimer = setInterval(tick, POLL_MS);
    }

    /* -------------------------------------------------------------- SSE -- */
    try {
      source = new EventSource(`/api/events/runs/${runId}`, {
        withCredentials: true,
      });

      source.addEventListener("open", () => setTransport("sse"));

      source.addEventListener("stage", (event) => {
        const row = JSON.parse(event.data);
        setStages((prev) => {
          const next = prev.filter((s) => s.stage !== row.stage);
          next.push(row);
          return next;
        });
      });

      source.addEventListener("status", (event) => {
        const payload = JSON.parse(event.data);
        if (payload.run_status) setRunStatus(payload.run_status);
      });

      source.addEventListener("end", (event) => {
        const payload = JSON.parse(event.data);
        finish(payload.run_status);
        if (source) source.close();
      });

      source.addEventListener("error", () => {
        // EventSource retries on its own, so one error is not fatal. Only a
        // closed connection means SSE is genuinely unavailable here.
        if (source && source.readyState === EventSource.CLOSED && !endedRef.current) {
          startPolling("live updates unavailable — polling instead");
        }
      });
    } catch (e) {
      startPolling(e?.message || "EventSource unsupported — polling instead");
    }

    return () => {
      cancelled = true;
      if (source) source.close();
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [runId, finish]);

  return { stages, runStatus, transport, error };
}
