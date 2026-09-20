/* The URL contract: one canonical, deep-linkable address per thing.
 *
 * Three claims live here so they cannot drift apart:
 *
 * 1. **Builders** (`url.*`) -- the only way new code should spell an address.
 * 2. **`canonical()`** -- every address the app has ever answered to, mapped
 *    to its canonical form. The router's legacy redirects are this function,
 *    and so are the two shapes the backend's search still emits
 *    (`/incidents?incident=`, `/investigation?scene=`): the server is not
 *    ours to change, so the frontend accepts what it says and moves on.
 * 3. **Path-backed params** (`usePathParams`) -- pages that were written
 *    against `useSearchParams` keep that interface while their identity
 *    (investigation, run, stage, MMSI) moves into the path.
 *
 * Redirects are permanent: an address that worked once keeps working. */

import { useCallback, useMemo, useRef } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

function qs(params) {
  const s = params.toString();
  return s ? `?${s}` : "";
}

function seg(v) {
  return encodeURIComponent(v);
}

/* ------------------------------------------------------------ workspace -- */

/** The workspace address for a parameter set. `inv`, `run`, `stage` and `new`
 *  are identity and live in the path; anything else stays a query param. */
export function workspaceUrl(input) {
  const p = input instanceof URLSearchParams ? new URLSearchParams(input) : new URLSearchParams(
    Object.entries(input || {}).filter(([, v]) => v != null && v !== ""));
  const isNew = p.get("new") === "1";
  const inv = p.get("inv");
  const run = p.get("run");
  const stage = p.get("stage");
  p.delete("new"); p.delete("inv"); p.delete("stage");
  if (isNew) { p.delete("run"); return `/investigations/new${qs(p)}`; }
  const tail = stage ? `/${seg(stage)}` : "";
  // An investigation may pin one of its runs; a run with no investigation
  // (a CLI run the registry reconciled) is addressed by the run alone.
  if (inv) return `/investigations/${seg(inv)}${tail}${qs(p)}`;
  if (run) { p.delete("run"); return `/investigations/run/${seg(run)}${tail}${qs(p)}`; }
  return `/investigations/latest${tail}${qs(p)}`;
}

export const url = {
  dashboard: () => "/",
  investigations: () => "/investigations",
  workspace: (o) => workspaceUrl(o),
  newInvestigation: () => "/investigations/new",
  runRegistry: () => "/investigations/registry",
  map: (o = {}) => `/map${qs(new URLSearchParams(Object.entries(o).filter(([, v]) => v != null)))}`,
  detections: (scene) => (scene ? `/detections?scene=${seg(scene)}` : "/detections"),
  sceneViewer: (run) => (run ? `/detections/viewer?run=${seg(run)}` : "/detections/viewer"),
  vessel: (mmsi) => (mmsi ? `/vessels/${seg(mmsi)}` : "/vessels"),
  reports: () => "/reports",
  reportPrint: (run) => `/reports/print/${seg(run)}`,
  incidents: (focus) => (focus ? `/operations/incidents?focus=${seg(focus)}` : "/operations/incidents"),
  alerts: () => "/operations/alerts",
  desk: () => "/operations/desk",
  replay: (run) => (run ? `/operations/replay?run=${seg(run)}` : "/operations/replay"),
  zones: (zone) => (zone ? `/system/zones?zone=${seg(zone)}` : "/system/zones"),
  engines: (job) => (job ? `/system/engines?job=${seg(job)}` : "/system/engines"),
  systemHealth: (tab) => (tab ? `/system/health?tab=${seg(tab)}` : "/system/health"),
};

/* ------------------------------------------------------ legacy addresses -- */

const MOVED = {
  "/globe": "/map",
  "/sar-database": "/detections",
  "/satellite": "/detections/viewer",
  "/dashboard": "/investigations/registry",
  "/alerts": "/operations/alerts",
  "/my-desk": "/operations/desk",
  "/incident": "/operations/replay",
  "/hindcast": "/system/engines",
  "/monitoring": "/system/api-monitor",
  "/catalog": "/system/data-sources",
  "/models": "/system/models",
  "/analytics": "/system/analytics",
  "/audit": "/system/audit",
  "/officers": "/system/users",
  "/keys": "/system/credentials",
  "/zones": "/system/zones",
  "/environment": "/system/environment",
  "/about": "/help",
};

/** Every legacy pathname this module answers for. The router mounts a
 *  redirect for each, and a test asserts none of them is also a live route. */
export const LEGACY_PATHS = [
  ...Object.keys(MOVED), "/investigation", "/report", "/incidents", "/system",
];

/** The canonical address for a legacy one, or null when `pathname` is not a
 *  legacy address. `search` is the raw query string, with or without `?`. */
export function canonical(pathname, search = "") {
  const p = new URLSearchParams(search);
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;

  if (path === "/investigation") {
    // The backend's search results address a scene through the workspace,
    // which never read `scene`; the detections registry does.
    if (p.get("scene") && !p.get("inv") && !p.get("run") && p.get("new") !== "1") {
      return `/detections${qs(p)}`;
    }
    return workspaceUrl(p);
  }
  if (path === "/report") {
    const run = p.get("run");
    if (!run) return "/reports";
    p.delete("run");
    return `${url.reportPrint(run)}${qs(p)}`;
  }
  if (path === "/incidents") {
    // `?incident=` is what the backend's search emits; the register reads `focus`.
    if (p.get("incident") && !p.get("focus")) p.set("focus", p.get("incident"));
    p.delete("incident");
    return `/operations/incidents${qs(p)}`;
  }
  if (path === "/vessels" && p.get("mmsi")) {
    const mmsi = p.get("mmsi");
    p.delete("mmsi");
    return `/vessels/${seg(mmsi)}${qs(p)}`;
  }
  if (path === "/system") {
    // The status bar used to ask for a `workers` tab that never existed;
    // worker state is on the runtime tab.
    if (p.get("tab") === "workers") p.set("tab", "runtime");
    return `/system/health${qs(p)}`;
  }
  if (MOVED[path]) return `${MOVED[path]}${qs(p)}`;
  return null;
}

/* ---------------------------------------------------- path-backed params -- */

/** `useSearchParams` for a page whose identity lives in the path.
 *
 *  `read(routeParams, pathname)` returns the path-held values as an object;
 *  `build(URLSearchParams)` returns the address for a full parameter set. The
 *  page sees one merged `URLSearchParams` and one setter with the
 *  react-router signature, and never learns where each value is kept. */
export function usePathParams(read, build) {
  const routeParams = useParams();
  const location = useLocation();
  const navigate = useNavigate();

  const merged = useMemo(() => {
    const m = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(read(routeParams, location.pathname) || {})) {
      if (v != null && v !== "") m.set(k, v);
    }
    return m;
    // `read` is a module-level function at every call site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, location.search]);

  const latest = useRef(merged);
  latest.current = merged;

  const setParams = useCallback((next, opts) => {
    const value = typeof next === "function" ? next(new URLSearchParams(latest.current)) : next;
    const n = value instanceof URLSearchParams ? value : new URLSearchParams(
      Object.entries(value || {}).filter(([, v]) => v != null));
    latest.current = n;
    navigate(build(n), opts);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navigate]);

  return [merged, setParams];
}

const readWorkspace = (rp, pathname) => ({
  new: pathname.replace(/\/+$/, "") === "/investigations/new" ? "1" : null,
  inv: rp.inv, run: rp.run, stage: rp.stage,
});

/** The workspace's params: `/investigations/:inv/:stage?`,
 *  `/investigations/run/:run/:stage?`, `/investigations/new`, `…/latest`. */
export function useWorkspaceParams() {
  return usePathParams(readWorkspace, workspaceUrl);
}

const readVessel = (rp) => ({ mmsi: rp.mmsi });
const buildVessel = (p) => {
  const n = new URLSearchParams(p);
  const mmsi = n.get("mmsi");
  n.delete("mmsi");
  return `${url.vessel(mmsi)}${qs(n)}`;
};

/** The vessel page's params: `/vessels/:mmsi?`. */
export function useVesselParams() {
  return usePathParams(readVessel, buildVessel);
}

/* ---------------------------------------------------------------- tabs -- */

/** A page tab held in `?tab=`, so a refresh reopens it and a link can name
 *  it. An id the page does not have falls back rather than rendering nothing. */
export function useUrlTab(ids, fallback = ids[0], key = "tab") {
  const location = useLocation();
  const navigate = useNavigate();
  const raw = new URLSearchParams(location.search).get(key);
  const tab = ids.includes(raw) ? raw : fallback;
  const setTab = useCallback((id) => {
    const n = new URLSearchParams(location.search);
    if (id === fallback) n.delete(key); else n.set(key, id);
    navigate(`${location.pathname}${qs(n)}`, { replace: true });
  }, [location.pathname, location.search, navigate, fallback, key]);
  return [tab, setTab];
}
