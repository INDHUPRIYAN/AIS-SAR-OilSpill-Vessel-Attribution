/* Shell state that outlives a route: the run in context, the commands the
 * current view contributes to the palette, and the keymap.
 *
 * Three things live here rather than in a component because all three are
 * claims the UI makes about itself, and a claim that lives in two places
 * drifts:
 *
 * 1. **ROUTES** is the single list of every screen. The nav renders from it
 *    and the command palette builds a command per entry, so "⌘K reaches every
 *    route" is a property of the data, not a promise somebody kept in sync.
 * 2. **KEYMAP** is the single list of every shortcut. The global key handler
 *    dispatches by walking it and the `?` overlay renders from the same array,
 *    so the help can't describe a binding that no longer exists — the UX spec
 *    asks for exactly this ("generated from the live keymap so it can't
 *    drift", §10.7).
 * 3. **The run in context** is what the top-bar provenance chips describe.
 *    Chips must reflect the run being looked at, not global configuration
 *    (spec §1.1.4), so the run id has to survive navigation — a page that
 *    unmounts must not take the honesty strip with it.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { matchPath } from "react-router-dom";

/* Every screen in the shell.
 *
 * `to` is the address the nav and the palette open; `paths`, when present, is
 * every router pattern the same page answers to (App.jsx mounts its routes
 * from this list, so a screen cannot exist that the shell does not know).
 *
 * `group` decides where the one sidebar shows the entry:
 *   main        the core product, always visible, in MAIN_ORDER
 *   operations  incident routing -- a main-sidebar group for a zone officer,
 *               whose daily work it is; under System for everyone else
 *   system      platform operations, behind the System group
 *   help        below the rule
 *   (none)      reachable by link, breadcrumb and palette; no sidebar slot
 *
 * `roles`, when present, is the set of roles the nav SHOWS the entry to. That
 * is presentation only -- the server is what enforces, and every route stays
 * reachable by deep link and from the palette so a user told "not available
 * to your role" sees the server's own 403 rather than a missing screen. */
export const ROUTES = [
  // -- core -----------------------------------------------------------------
  { id: "dashboard", to: "/", label: "Dashboard", group: "main", icon: "LayoutDashboard",
    hint: "what needs attention now" },
  { id: "investigations", to: "/investigations", label: "Investigations", group: "main",
    icon: "FolderOpen", hint: "investigation records" },
  { id: "workspace", to: "/investigations/latest", label: "Workspace", icon: "Radar",
    parent: "investigations", hint: "the investigation workspace",
    paths: ["/investigations/new", "/investigations/latest/:stage?",
      "/investigations/run/:run/:stage?", "/investigations/:inv/:stage?"] },
  { id: "registry", to: "/investigations/registry", label: "Run registry", icon: "Layers",
    parent: "investigations", hint: "every pipeline run, offline replay" },
  // -- situational awareness ------------------------------------------------
  { id: "map", to: "/map", label: "Live Map", group: "main", icon: "Globe2",
    hint: "detections, investigations, zones, live AIS" },
  { id: "detections", to: "/detections", label: "Detections", group: "main", icon: "ScanSearch",
    hint: "SAR scenes, metadata, upload and AI analysis" },
  { id: "scene-viewer", to: "/detections/viewer", label: "Scene viewer", icon: "Satellite",
    parent: "detections" },
  { id: "vessels", to: "/vessels", label: "Vessels", group: "main", icon: "Ship",
    paths: ["/vessels/:mmsi?"] },
  { id: "reports", to: "/reports", label: "Reports", group: "main", icon: "FileText" },
  { id: "report-print", to: "/reports", label: "Printable report", parent: "reports",
    paths: ["/reports/print/:run"], palette: false },
  // -- incident routing -----------------------------------------------------
  { id: "desk", to: "/operations/desk", label: "My Desk", group: "operations", icon: "Inbox" },
  { id: "incidents", to: "/operations/incidents", label: "Incidents", group: "operations",
    icon: "ClipboardList" },
  { id: "alerts", to: "/operations/alerts", label: "Alerts", group: "operations", icon: "Siren" },
  { id: "replay", to: "/operations/replay", label: "Incident Replay", group: "operations",
    icon: "Film" },
  // -- system ---------------------------------------------------------------
  { id: "engines", to: "/system/engines", label: "Engine Monitoring", group: "system", icon: "Cpu",
    hint: "BAYES-TRACK · seven hindcast engines, live" },
  { id: "data-sources", to: "/system/data-sources", label: "Data Sources", group: "system",
    icon: "Database" },
  { id: "api-monitor", to: "/system/api-monitor", label: "API Monitor", group: "system",
    icon: "Activity" },
  { id: "zones", to: "/system/zones", label: "Zones", group: "system", icon: "Map" },
  { id: "health", to: "/system/health", label: "System Health", group: "system", icon: "Server",
    hint: "jobs · workers · logs" },
  { id: "models", to: "/system/models", label: "ML Models", group: "system", icon: "BrainCircuit" },
  { id: "analytics", to: "/system/analytics", label: "Analytics", group: "system", icon: "BarChart3" },
  { id: "environment", to: "/system/environment", label: "Forcing Data", group: "system", icon: "Wind" },
  { id: "audit", to: "/system/audit", label: "Audit Trail", group: "system", icon: "ScrollText",
    roles: ["reviewer", "auditor", "admin", "super_admin"] },
  { id: "users", to: "/system/users", label: "Users & Roles", group: "system", icon: "Users",
    roles: ["admin", "super_admin"] },
  { id: "credentials", to: "/system/credentials", label: "Credentials", group: "system",
    icon: "KeyRound", roles: ["admin", "super_admin"] },
  // -- help -----------------------------------------------------------------
  { id: "help", to: "/help", label: "Help", group: "help", icon: "BookOpen" },
];

/* The sidebar's main section, in order. */
export const MAIN_ORDER = ["dashboard", "investigations", "map", "detections", "vessels", "reports"];

export const GROUP_LABEL = { operations: "Operations", system: "System" };

/* A zone officer's working day is incidents and alerts, so that group sits in
 * the main sidebar for them; for every other role it is part of System. */
export function operationsInMain(role) {
  return role === "zone_officer";
}

/** Router patterns for a route entry. */
export function pathsOf(route) {
  return route.paths || [route.to];
}

function trimSlash(pathname) {
  return pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
}

/** The route entry for a pathname. Static patterns win over parameterised
 *  ones, so `/investigations/registry` is not read as an investigation id. */
export function routeFor(pathname) {
  if (!pathname) return null;
  const path = trimSlash(pathname);
  let best = null;
  for (const r of ROUTES) {
    for (const pattern of pathsOf(r)) {
      if (!matchPath({ path: pattern, end: true }, path)) continue;
      const parts = pattern.split("/").filter(Boolean);
      const score = parts.filter((s) => !s.startsWith(":")).length * 10
        - parts.filter((s) => s.startsWith(":")).length;
      if (!best || score > best.score) best = { route: r, score };
    }
  }
  return best ? best.route : null;
}

/** Breadcrumbs for a location: [{label, to?, mono?}], the last being the page.
 *  `stageLabel` lets the workspace name its stage rather than show the id. */
export function crumbsFor(pathname, stageLabel) {
  const route = routeFor(pathname);
  if (!route) return [{ label: "Not found" }];
  const path = trimSlash(pathname);
  const out = [];
  if (GROUP_LABEL[route.group]) out.push({ label: GROUP_LABEL[route.group] });
  const parent = route.parent && ROUTES.find((r) => r.id === route.parent);
  if (parent) out.push({ label: parent.label, to: parent.to });

  if (route.id === "workspace") {
    if (path === "/investigations/new") return [...out, { label: "New investigation" }];
    const byRun = matchPath("/investigations/run/:run/:stage?", path);
    const m = byRun || matchPath("/investigations/:inv/:stage?", path);
    const id = m?.params.run || m?.params.inv;
    const stage = m?.params.stage;
    const base = byRun ? `/investigations/run/${id}` : `/investigations/${id}`;
    out.push({ label: id === "latest" ? "Latest" : id, to: stage ? base : undefined,
      mono: id !== "latest" });
    if (stage) out.push({ label: stageLabel || stage });
    return out;
  }
  if (route.id === "vessels") {
    const m = matchPath("/vessels/:mmsi", path);
    if (m) return [...out, { label: route.label, to: route.to }, { label: `MMSI ${m.params.mmsi}`, mono: true }];
  }
  if (route.id === "report-print") {
    const m = matchPath("/reports/print/:run", path);
    return [...out, { label: m?.params.run || route.label, mono: true }];
  }
  out.push({ label: route.label });
  return out;
}

/* Shortcuts. `scope: "global"` entries carry a `test` and are dispatched by
 * the shell's key handler; the rest are documented here because the overlay
 * has to list them, and are handled where they apply (the palette owns its own
 * arrow keys, since they only mean anything while it is open). */
export const KEYMAP = [
  {
    id: "palette", keys: ["⌘K", "Ctrl+K"], scope: "global",
    label: "Open the command palette",
    // `e.key` is not guaranteed present (IME composition, some synthetic
    // events); a throw here would take out every binding after it.
    test: (e) => (e.metaKey || e.ctrlKey) && String(e.key || "").toLowerCase() === "k",
  },
  {
    id: "help", keys: ["?"], scope: "global",
    label: "Show this shortcut list",
    test: (e) => e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey,
  },
  {
    id: "close", keys: ["Esc"], scope: "global",
    label: "Close the palette, the shortcut list, or the open overlay",
    test: (e) => e.key === "Escape",
  },
  { id: "move", keys: ["↑", "↓"], scope: "palette", label: "Move through results" },
  { id: "open", keys: ["Enter"], scope: "palette", label: "Open the highlighted result" },
];

const ShellContext = createContext(null);

/** Typing into a field must not fire a shortcut. `?` and `Escape` are both
 *  ordinary keystrokes inside a search box. */
function isTypingTarget(target) {
  if (!target) return false;
  const tag = (target.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select"
    || target.isContentEditable === true;
}

export function ShellProvider({ children }) {
  const [runId, setRunId] = useState(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  // Commands contributed by whichever view is mounted, keyed by the id the
  // view registered under so a remount replaces rather than duplicates.
  const [contributed, setContributed] = useState({});
  const counter = useRef(0);

  const registerCommands = useCallback((commands) => {
    counter.current += 1;
    const key = `c${counter.current}`;
    setContributed((c) => ({ ...c, [key]: commands }));
    return () => setContributed((c) => {
      const next = { ...c };
      delete next[key];
      return next;
    });
  }, []);

  const commands = useMemo(
    () => Object.values(contributed).flat(), [contributed]);

  // The overlay lists the base bindings plus any a mounted view contributed.
  const keymap = useMemo(() => [
    ...KEYMAP,
    ...commands.filter((c) => c.keys).map((c) => ({
      id: c.id, keys: c.keys, scope: c.scope || "view", label: c.label,
    })),
  ], [commands]);

  const value = useMemo(() => ({
    runId,
    setRunId,
    clearRun: () => setRunId(null),
    paletteOpen,
    openPalette: () => setPaletteOpen(true),
    closePalette: () => setPaletteOpen(false),
    helpOpen,
    closeHelp: () => setHelpOpen(false),
    commands,
    registerCommands,
    keymap,
  }), [runId, paletteOpen, helpOpen, commands, registerCommands, keymap]);

  /* One global handler, dispatching by walking the keymap. Adding a shortcut
   * anywhere else would make the `?` overlay wrong, which is the failure this
   * arrangement exists to prevent. */
  useEffect(() => {
    const onKey = (e) => {
      const typing = isTypingTarget(e.target);
      for (const binding of keymap) {
        if (binding.scope !== "global" || !binding.test) continue;
        // Escape still has to reach an open overlay from inside its own input.
        if (typing && binding.id !== "close") continue;
        if (!binding.test(e)) continue;
        e.preventDefault();
        if (binding.id === "palette") { setHelpOpen(false); setPaletteOpen(true); }
        if (binding.id === "help") { setPaletteOpen(false); setHelpOpen(true); }
        if (binding.id === "close") { setPaletteOpen(false); setHelpOpen(false); }
        return;
      }
      // View-contributed bindings, after the global ones and never while a
      // field has focus.
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      const pressed = String(e.key || "").toLowerCase();
      const hit = pressed && commands.find(
        (c) => c.keys && c.keys.some((k) => k.toLowerCase() === pressed));
      if (hit) { e.preventDefault(); hit.run?.(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keymap, commands]);

  return <ShellContext.Provider value={value}>{children}</ShellContext.Provider>;
}

export function useShell() {
  const ctx = useContext(ShellContext);
  if (!ctx) throw new Error("useShell must be used inside <ShellProvider>");
  return ctx;
}

/** Contribute commands for as long as the calling view is mounted.
 *
 *  `deps` is the caller's own dependency list, exactly as with useMemo: the
 *  commands close over view state (which layers are on, where `t` is), so they
 *  have to be rebuilt when that state changes or the palette would run an
 *  action against a stale snapshot. */
export function useRegisterCommands(factory, deps = []) {
  const { registerCommands } = useShell();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const commands = useMemo(factory, deps);
  useEffect(() => registerCommands(commands), [commands, registerCommands]);
}

/** Set the run whose provenance the top bar describes, for as long as the
 *  calling view is mounted with one. Deliberately not cleared on unmount:
 *  §10.10 says the open run rides across every view until explicitly closed. */
export function useRunInContext(runId) {
  const { setRunId } = useShell();
  useEffect(() => {
    if (runId) setRunId(runId);
  }, [runId, setRunId]);
}
