/* Thin client over the OceanTrace REST API.
 *
 * Everything goes through Vite's dev proxy, so requests are same-origin.
 *
 * Authentication is an HttpOnly session cookie set by /api/auth/login. This
 * file deliberately cannot read it: the shared admin token used to live in
 * localStorage, where any scripting bug on the page could lift it and where
 * it identified nobody (audit AD-06). `credentials: "include"` is what sends
 * the cookie; there is no token for JavaScript to hold.
 */

import { useCallback, useEffect, useRef, useState } from "react";

// Raised when the session is missing or expired, so the shell can show the
// sign-in screen instead of rendering a page full of failed panels.
export class Unauthenticated extends Error {
  constructor(message = "authentication required") {
    super(message);
    this.name = "Unauthenticated";
    this.status = 401;
  }
}

async function request(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 401) throw new Unauthenticated();

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* body was not JSON; the status is all we have */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  health: () => request("/health"),

  // The session is the cookie; these just drive it.
  login: (email, password) =>
    request("/api/auth/login", { method: "POST", body: { email, password } }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  me: () => request("/api/auth/me"),

  localScenes: () => request("/api/scenes/local"),

  listInvestigations: () => request("/api/investigations"),
  createInvestigation: (body) =>
    request("/api/investigations", { method: "POST", body }),
  startRun: (id, body) =>
    request(`/api/investigations/${id}/run`, { method: "POST", body }),

  getInvestigation: (id) => request(`/api/investigations/${id}`),
  invStatus: (id, run) => request(`/api/investigations/${id}/status${run ? `?run=${run}` : ""}`),
  invLayer: (id, name) => request(`/api/investigations/${id}/layers/${name}`),
  invReplay: (id) => request(`/api/investigations/${id}/replay`, { method: "POST" }),

  listRuns: () => request("/api/runs"),
  getRun: (id) => request(`/api/runs/${id}`),
  layer: (runId, name, opts = {}) =>
    request(`/api/layers/${runId}/${name}${opts.lite ? "?lite=true" : ""}`),

  metrics: () => request("/api/metrics"),
  replayRuns: () => request("/api/replay/runs"),
  vesselsGeojson: (runId) => request(`/api/runs/${runId}/vessels_geojson`),
  forcingField: (runId) => request(`/api/runs/${runId}/forcing_field`),

  apiStatus: () => request("/api/apis/status"),
  testProvider: (p) => request(`/api/apis/${p}/test`, { method: "POST" }),
  testAll: () => request("/api/apis/test-all", { method: "POST" }),
  providerCalls: (p) => request(`/api/apis/${p}/calls`),

  listKeys: () => request("/api/keys"),
  setKey: (body) => request("/api/keys", { method: "PUT", body }),
  testKey: (p) => request(`/api/keys/${p}/test`, { method: "POST" }),
  keyAudit: () => request("/api/keys/audit"),
};

/* ------------------------------------------------------------------ hooks */

/** Fetch once, plus optional polling. Guards against setting state after
 *  unmount, which is otherwise a constant source of console noise here
 *  because the monitoring page mounts and unmounts as you navigate. */
export function useApi(fn, deps = [], { interval = 0 } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const result = await fn();
      if (alive.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e);
    } finally {
      if (alive.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    alive.current = true;
    load();
    if (!interval) return () => { alive.current = false; };
    const id = setInterval(load, interval);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [load, interval]);

  return { data, error, loading, reload: load };
}

/* ----------------------------------------------------------------- format */

export const fmt = {
  pct: (v, d = 1) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`),
  num: (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d)),
  int: (v) => (v == null ? "—" : Math.round(v).toLocaleString()),
  ms: (v) => (v == null ? "—" : v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(1)} s`),
  utc: (v) => {
    if (!v) return "—";
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? String(v) : d.toISOString().replace("T", " ").slice(0, 19) + "Z";
  },
  ago: (v) => {
    if (!v) return "never";
    const s = (Date.now() - new Date(v).getTime()) / 1000;
    if (Number.isNaN(s)) return "—";
    if (s < 60) return `${Math.round(s)}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    return `${Math.round(s / 86400)}d ago`;
  },
};

/** Map a stage/provider status onto a badge class. One place, so the colour
 *  vocabulary stays consistent everywhere it is used. */
export function statusTone(status) {
  switch ((status || "").toUpperCase()) {
    case "OK":
    case "WORKING":
    case "COMPLETE":
      return "ok";
    case "FALLBACK":
    case "DEGRADED":
    case "RUNNING":
    case "PENDING":
      return "warn";
    case "MOCK":
      return "mock";
    case "FAILED":
    case "UNCONFIGURED":
      return "danger";
    default:
      return "neutral";
  }
}
