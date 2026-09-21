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

/* Read the server's own `detail` off a response body, or null.
 *
 * Worth the extra await on the 401 path: the two 401s this API returns mean
 * completely different things -- "your session is gone" and "that password is
 * wrong" -- and only the body distinguishes them. */
async function detailOf(res) {
  try {
    const body = await res.json();
    return body?.detail || null;
  } catch {
    return null;   // not JSON; the status is all we have
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

  if (res.status === 401) {
    /* Keep the server's message. Throwing a bare Unauthenticated here told a
     * user with a mistyped password that authentication was "required" -- a
     * sentence about an expired session -- while the server had plainly said
     * "invalid email or password". The sign-in form shows this verbatim, so
     * the two cases have to stay distinguishable. */
    throw new Unauthenticated(await detailOf(res) || "authentication required");
  }

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

/* multipart/form-data: the browser sets the boundary, so no Content-Type here.
 * Same error contract as request(). */
async function upload(path, form) {
  const res = await fetch(path, { method: "POST", credentials: "include", body: form });
  if (res.status === 401) throw new Unauthenticated(await detailOf(res) || "authentication required");
  if (!res.ok) {
    const err = new Error(await detailOf(res) || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  health: () => request("/health"),

  // SAR Image Database: scenes held on this host, and uploads.
  sarScenes: (params = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v != null));
    return request(`/api/sar/scenes${q.toString() ? `?${q}` : ""}`);
  },
  sarUpload: (form) => upload("/api/sar/upload", form),
  sarCompleteUpload: (id, body) => request(`/api/sar/upload/${id}/metadata`, { method: "POST", body }),

  // The session is the cookie; these just drive it.
  login: (email, password) =>
    request("/api/auth/login", { method: "POST", body: { email, password } }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  me: () => request("/api/auth/me"),
  // Public: whether this deployment serves the password-less evaluator view.
  authMode: () => request("/api/auth/mode"),

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
    /* One shape for every caller. The endpoint answers with a bare array
     * today and most callers already guard for `{items}`; one did not, and
     * would have rendered "compose a report" forever the day it paginates. */
    return request(`/api/reports${q ? `?${q}` : ""}`)
      .then((r) => (Array.isArray(r) ? r : r?.items ?? []));
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

  // Provider truth, model registry and measured host health.
  // Alerts: the queue that turns the watcher into a notification.
  listAlerts: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== ""),
    ).toString();
    return request(`/api/alerts${q ? `?${q}` : ""}`);
  },
  alertsSummary: () => request("/api/alerts/summary"),
  ackAlert: (id) => request(`/api/alerts/${id}/ack`, { method: "POST" }),
  assignAlert: (id, userId) =>
    request(`/api/alerts/${id}/assign`, { method: "POST", body: { user_id: userId } }),
  dismissAlert: (id, reason) =>
    request(`/api/alerts/${id}/dismiss`, { method: "POST", body: { reason } }),

  // One box across runs, incidents, investigations, vessels and scenes.
  // `kinds` narrows it; the server states its own matching rule in the reply.
  search: (q, params = {}) => {
    const qs = new URLSearchParams({ q, ...params }).toString();
    return request(`/api/search?${qs}`);
  },

  catalog: () => request("/api/catalog"),
  models: () => request("/api/models"),
  systemHealth: () => request("/api/system/health"),

  apiStatus: () => request("/api/apis/status"),
  testProvider: (p) => request(`/api/apis/${p}/test`, { method: "POST" }),
  testAll: () => request("/api/apis/test-all", { method: "POST" }),
  providerCalls: (p) => request(`/api/apis/${p}/calls`),

  listKeys: () => request("/api/keys"),
  setKey: (body) => request("/api/keys", { method: "PUT", body }),
  testKey: (p) => request(`/api/keys/${p}/test`, { method: "POST" }),
  keyAudit: () => request("/api/keys/audit"),

  /* --- operational zones ------------------------------------------------
   * `listZones` omits polygons by default and `zonesGeojson` returns them,
   * mirroring the two server routes. The split is not premature: the Zone
   * Management table renders five text columns, and shipping forty polygons
   * to do that is what makes a dashboard feel slow for no reason. */
  listZones: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/zones${q.toString() ? `?${q}` : ""}`);
  },
  zonesGeojson: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/zones/geojson${q.toString() ? `?${q}` : ""}`);
  },
  myZones: () => request("/api/zones/mine"),
  getZone: (id) => request(`/api/zones/${id}`),
  zoneRevisions: (id, limit = 50) =>
    request(`/api/zones/${id}/revisions?limit=${limit}`),
  /* Which zone owns a coordinate, and whose desk it lands on. The boundary
   * editor calls this on click so an operator sees the routing consequence
   * of a point BEFORE saving a polygon. */
  lookupZone: (lon, lat) => request(`/api/zones/lookup?lon=${lon}&lat=${lat}`),
  createZone: (body) => request("/api/zones", { method: "POST", body }),
  /* `revision` is mandatory in `body` whenever `geometry` is present -- the
   * server rejects a geometry edit without it rather than letting two
   * officers silently overwrite each other. */
  updateZone: (id, body) => request(`/api/zones/${id}`, { method: "PATCH", body }),
  deleteZone: (id) => request(`/api/zones/${id}`, { method: "DELETE" }),
  assignZoneOfficer: (id, userId, isPrimary = true) =>
    request(`/api/zones/${id}/assignments`,
            { method: "POST", body: { user_id: userId, is_primary: isPrimary } }),
  unassignZoneOfficer: (id, userId) =>
    request(`/api/zones/${id}/assignments/${userId}`, { method: "DELETE" }),

  /* --- live AIS ---------------------------------------------------------
   * `liveVessels` is a LIVE layer and the caller is expected to poll it. The
   * response carries `total_in_view` and `truncated` alongside the rows,
   * because a client that received 2000 of 4200 vessels has to be able to
   * say so rather than draw a partial picture as if it were complete. */
  liveVessels: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/ais/live${q.toString() ? `?${q}` : ""}`);
  },
  liveVesselsGeojson: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/ais/live/geojson${q.toString() ? `?${q}` : ""}`);
  },
  liveVessel: (mmsi) => request(`/api/ais/live/${mmsi}`),
  /* Carries `stream.functionally_working` SEPARATELY from `stream.connected`,
   * and a `note` naming the cause when they disagree. The UI must render the
   * note: "connected but no data" over water the provider has no receivers
   * for is a coverage fact, not an empty sea. */
  aisStatus: () => request("/api/ais/status"),
  aisZoneSummary: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/ais/zones/summary${q.toString() ? `?${q}` : ""}`);
  },
  startAisStream: () => request("/api/ais/stream/start", { method: "POST" }),
  stopAisStream: () => request("/api/ais/stream/stop", { method: "POST" }),
  flushAisArchive: () => request("/api/ais/stream/flush", { method: "POST" }),

  /* --- automatic incidents ---------------------------------------------- */
  previewAutoIncident: (runId) =>
    request(`/api/incidents/auto/preview/${runId}`),
  createAutoIncident: (runId, force = false) =>
    request(`/api/incidents/auto/${runId}${force ? "?force=true" : ""}`,
            { method: "POST" }),
  backfillIncidentZones: () =>
    request("/api/incidents/backfill-zones", { method: "POST" }),

  /* --- accounts ---------------------------------------------------------
   * `listUsers` is administrator-and-above and 403s for everyone else, which
   * the Officer Assignment page renders as an honest "not available" rather
   * than an empty table. `roles` is readable by any role, so a user can find
   * out why a control is disabled. */
  listUsers: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/users${q.toString() ? `?${q}` : ""}`);
  },
  getUser: (id) => request(`/api/users/${id}`),
  createUser: (body) => request("/api/users", { method: "POST", body }),
  /* A role change is refused for anyone but a super_admin, and a
   * deactivation returns a `warning` naming the zones it left unrouted. Both
   * come back in the response and must be shown, not swallowed. */
  updateUser: (id, body) => request(`/api/users/${id}`, { method: "PATCH", body }),
  userZones: (id) => request(`/api/users/${id}/zones`),
  roles: () => request("/api/roles"),

  /* --- ops surfaces -----------------------------------------------------
   * `/logs` is a bounded live buffer and says so in its own response; the
   * durable, hash-chained record is `/audit`. The two are never presented as
   * the same thing. `/workers` reports the threads this process really runs
   * and reports GPU as NOT measured rather than as zero. */
  logs: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/logs${q.toString() ? `?${q}` : ""}`);
  },
  clearLogs: () => request("/api/logs/clear", { method: "POST" }),
  workers: () => request("/api/workers"),
  audit: (params = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== null));
    return request(`/api/audit${q.toString() ? `?${q}` : ""}`);
  },
  auditVerify: () => request("/api/audit/verify"),
  hindcastModels: () => request("/api/models/hindcast"),
  attributionWeights: () => request("/api/attribution/weights"),
  verifyRun: (runId) => request(`/api/runs/${runId}/verify`),
  runDecisions: (runId) => request(`/api/runs/${runId}/decisions`),
  recordDecision: (runId, body) =>
    request(`/api/runs/${runId}/decisions`, { method: "POST", body }),
  decisions: (limit = 100) => request(`/api/decisions?limit=${limit}`),
  localScene: (id) => request(`/api/scenes/local/${encodeURIComponent(id)}`),
  tilesInfo: (runId) => request(`/api/tiles/${runId}/info`),

  /* --- BAYES-TRACK hindcast ----------------------------------------------
   * Live engine state comes over the WebSocket (lib/useEngineFeed); these are
   * the registry, the job records, and the three ways to start a job. */
  hindcastEngines: () => request("/api/engines"),
  engineStatus: (jobId) => request(`/api/engines/status${jobId ? `?job_id=${jobId}` : ""}`),
  engineRuns: (engineId) => request(`/api/engines/${engineId}/runs?limit=15`),
  hindcastJobs: () => request("/api/hindcast/jobs?limit=25"),
  hindcastJob: (id) => request(`/api/hindcast/jobs/${id}`),
  hindcastDemo: (body = {}) => request("/api/hindcast/demo", { method: "POST", body }),
  hindcastFromRun: (runId, body = {}) => request(`/api/hindcast/from_run/${runId}`, { method: "POST", body }),
  authRoles: () => request("/api/auth/roles"),
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
