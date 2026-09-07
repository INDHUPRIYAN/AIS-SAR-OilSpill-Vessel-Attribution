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

  listIncidents: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
    ).toString();
    return request(`/api/incidents${q ? `?${q}` : ""}`);
  },
  getIncident: (id) => request(`/api/incidents/${id}`),
  createIncident: (body) => request("/api/incidents", { method: "POST", body }),
  patchIncident: (id, body) =>
    request(`/api/incidents/${id}`, { method: "PATCH", body }),
  promoteRun: (runId) =>
    request(`/api/incidents/from_run/${runId}`, { method: "POST" }),

  listInvestigations: () => request("/api/investigations"),
  createInvestigation: (body) =>
    request("/api/investigations", { method: "POST", body }),
  startRun: (id, body) =>
    request(`/api/investigations/${id}/run`, { method: "POST", body }),

  // Jobs: a run you can watch and stop.
  getJob: (jobId) => request(`/api/jobs/${jobId}`),
  cancelJob: (jobId) => request(`/api/jobs/${jobId}/cancel`, { method: "POST" }),
  rerun: (runId) => request(`/api/runs/${runId}/rerun`, { method: "POST" }),

  // AOIs. The registry is a table now, so these actually change something.
  listAois: () => request("/api/aois"),
  getAoi: (id) => request(`/api/aois/${id}`),
  createAoi: (body) => request("/api/aois", { method: "POST", body }),
  updateAoi: (id, body) => request(`/api/aois/${id}`, { method: "PATCH", body }),
  deleteAoi: (id) => request(`/api/aois/${id}`, { method: "DELETE" }),
  searchScenes: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
    ).toString();
    return request(`/api/scenes/search${q ? `?${q}` : ""}`);
  },

  getInvestigation: (id) => request(`/api/investigations/${id}`),
  invStatus: (id, run) => request(`/api/investigations/${id}/status${run ? `?run=${run}` : ""}`),
  invLayer: (id, name) => request(`/api/investigations/${id}/layers/${name}`),
  invReplay: (id) => request(`/api/investigations/${id}/replay`, { method: "POST" }),

  // `/api/runs` now returns {total, items}. Unwrapped here so the two existing
  // callers keep receiving an array; paging callers use listRunsPaged.
  listRuns: async (params = {}) => (await api.listRunsPaged(params)).items,
  listRunsPaged: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
    ).toString();
    return request(`/api/runs${q ? `?${q}` : ""}`);
  },
  runFunnel: (runId) => request(`/api/runs/${runId}/funnel`),

  listVessels: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
    ).toString();
    return request(`/api/vessels${q ? `?${q}` : ""}`);
  },
  getVessel: (mmsi) => request(`/api/vessels/${mmsi}`),
  vesselTracks: (mmsi) => request(`/api/vessels/${mmsi}/tracks`),
  archiveRun: (runId, archived = true) =>
    request(`/api/runs/${runId}/archive?archived=${archived}`, { method: "POST" }),
  getRun: (id) => request(`/api/runs/${id}`),
  layer: (runId, name, opts = {}) =>
    request(`/api/layers/${runId}/${name}${opts.lite ? "?lite=true" : ""}`),

  // Reports: composed server-side, versioned, reviewable.
  listReports: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
    ).toString();
    return request(`/api/reports${q ? `?${q}` : ""}`);
  },
  getReport: (id) => request(`/api/reports/${id}`),
  composeReport: (body) => request("/api/reports", { method: "POST", body }),
  submitReport: (id, body = {}) =>
    request(`/api/reports/${id}/submit`, { method: "POST", body }),
  publishReport: (id, body = {}) =>
    request(`/api/reports/${id}/publish`, { method: "POST", body }),
  reviseReport: (id) => request(`/api/reports/${id}/revise`, { method: "POST" }),

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
