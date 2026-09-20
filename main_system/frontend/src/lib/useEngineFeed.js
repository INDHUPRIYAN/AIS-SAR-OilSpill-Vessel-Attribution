/* Live hindcast-engine state for one job.
 *
 * Primary transport is the WebSocket (/ws/engines/{job}): a snapshot on
 * connect, then one message per engine update. If the socket cannot open or
 * drops, the hook falls back to polling /api/engines/status every 2 s and
 * keeps trying to get the socket back, so a proxy that strips Upgrade headers
 * degrades the page instead of freezing it. `transport` says which of the two
 * is feeding the tiles right now, and the page shows it.
 *
 * The socket authenticates with the same session cookie as every /api call;
 * a refused handshake (no session) simply lands in the polling path, where the
 * 401 is handled like any other. */

import { useEffect, useRef, useState } from "react";
import { api } from "./api";

const POLL_MS = 2000;
const RECONNECT_MS = 8000;
const TERMINAL = new Set(["succeeded", "failed"]);

const wsUrl = (jobId) =>
  `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws/engines/${jobId}`;

export function useEngineFeed(jobId) {
  const [job, setJob] = useState(null);
  const [runs, setRuns] = useState({});
  const [transport, setTransport] = useState("idle");      // idle | live | polling
  const jobRef = useRef(null);

  useEffect(() => {
    setJob(null); setRuns({}); jobRef.current = null;
    if (!jobId) { setTransport("idle"); return undefined; }

    let closed = false;
    let socket = null;
    let pollTimer = null;
    let reconnectTimer = null;

    const applySnapshot = (snap) => {
      if (closed || !snap) return;
      jobRef.current = snap.job;
      setJob(snap.job);
      setRuns(Object.fromEntries((snap.engines || []).map((e) => [e.engine_id, e])));
    };
    const stopPolling = () => { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } };
    const startPolling = () => {
      if (pollTimer || closed) return;
      setTransport("polling");
      const tick = () => api.engineStatus(jobId).then(applySnapshot).catch(() => {});
      tick();
      pollTimer = setInterval(tick, POLL_MS);
    };
    const connect = () => {
      if (closed) return;
      try { socket = new WebSocket(wsUrl(jobId)); } catch { startPolling(); return; }
      socket.onopen = () => { stopPolling(); setTransport("live"); };
      socket.onmessage = (event) => {
        let msg;
        try { msg = JSON.parse(event.data); } catch { return; }
        if (msg.type === "snapshot") applySnapshot(msg);
        else if (msg.type === "engine") setRuns((prev) => ({ ...prev, [msg.engine_id]: msg }));
        else if (msg.type === "job") {
          jobRef.current = { ...(jobRef.current || {}), status: msg.status, error: msg.error };
          setJob((prev) => (prev ? { ...prev, status: msg.status, error: msg.error } : prev));
        }
      };
      socket.onclose = () => {
        if (closed) return;
        startPolling();
        // A finished job has nothing more to stream.
        if (!TERMINAL.has(jobRef.current?.status)) reconnectTimer = setTimeout(connect, RECONNECT_MS);
      };
      socket.onerror = () => { try { socket.close(); } catch { /* already closing */ } };
    };

    connect();
    return () => {
      closed = true;
      stopPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) { socket.onclose = null; try { socket.close(); } catch { /* ignore */ } }
    };
  }, [jobId]);

  return { job, runs, transport };
}
