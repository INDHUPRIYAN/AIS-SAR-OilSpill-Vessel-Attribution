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

/* Every screen in the shell.
 *
 * `section` groups the left navigation; `icon` names a lucide glyph the nav
 * resolves; `roles`, when present, is the set of roles the nav SHOWS the entry
 * to. That is presentation only -- the server is what enforces, and every
 * route stays reachable by deep link and from the palette so a user told
 * "not available to your role" sees the server's own 403 rather than a
 * missing screen. `nav: false` entries are reachable but earn no nav slot. */
export const ROUTES = [
  // -- operations ---------------------------------------------------------
  { to: "/", label: "Overview", section: "Operations", icon: "LayoutDashboard", nav: true,
    hint: "global maritime picture" },
  { to: "/alerts", label: "Alerts", section: "Operations", icon: "Siren", nav: true },
  { to: "/incidents", label: "Incidents", section: "Operations", icon: "ClipboardList", nav: true },
  { to: "/incident", label: "Incident Replay", section: "Operations", icon: "Film", nav: true },
  { to: "/my-desk", label: "My Desk", section: "Operations", icon: "Inbox", nav: true },
  // -- intelligence -------------------------------------------------------
  { to: "/globe", label: "Global View", section: "Intelligence", icon: "Globe2", nav: true,
    hint: "3D globe · zone splitting" },
  { to: "/vessels", label: "Vessels", section: "Intelligence", icon: "Ship", nav: true },
  { to: "/satellite", label: "Satellite", section: "Intelligence", icon: "Satellite", nav: true },
  { to: "/environment", label: "Environment", section: "Intelligence", icon: "Wind", nav: true },
  { to: "/zones", label: "Zones", section: "Intelligence", icon: "Map", nav: true },
  // -- analysis -----------------------------------------------------------
  { to: "/investigation", label: "Workspace", section: "Analysis", icon: "Radar", nav: true },
  { to: "/dashboard", label: "Investigations", section: "Analysis", icon: "FolderOpen", nav: true,
    hint: "runs and cases" },
  { to: "/reports", label: "Reports", section: "Analysis", icon: "FileText", nav: true },
  { to: "/analytics", label: "Analytics", section: "Analysis", icon: "BarChart3", nav: true },
  // -- system -------------------------------------------------------------
  { to: "/monitoring", label: "API Monitor", section: "System", icon: "Activity", nav: true },
  { to: "/catalog", label: "Data Sources", section: "System", icon: "Database", nav: true },
  { to: "/models", label: "ML Models", section: "System", icon: "BrainCircuit", nav: true },
  { to: "/system", label: "System Ops", section: "System", icon: "Server", nav: true,
    hint: "jobs · workers · logs" },
  { to: "/audit", label: "Audit Trail", section: "System", icon: "ScrollText", nav: true,
    roles: ["reviewer", "auditor", "admin", "super_admin"] },
  { to: "/officers", label: "Users & Roles", section: "System", icon: "Users", nav: true,
    roles: ["admin", "super_admin"] },
  { to: "/keys", label: "Credentials", section: "System", icon: "KeyRound", nav: true,
    roles: ["admin", "super_admin"] },
  { to: "/about", label: "About", section: "System", icon: "BookOpen", nav: true },
  // -- deep-link only -----------------------------------------------------
  { to: "/report", label: "Report", nav: false },
];

export const NAV_SECTIONS = ["Operations", "Intelligence", "Analysis", "System"];

/** The route entry for a pathname, longest prefix first, so `/incidents`
 *  does not resolve to `/incident` and `/` only matches itself. */
export function routeFor(pathname) {
  if (!pathname) return null;
  const exact = ROUTES.find((r) => r.to === pathname);
  if (exact) return exact;
  return ROUTES
    .filter((r) => r.to !== "/" && pathname.startsWith(`${r.to}/`))
    .sort((a, b) => b.to.length - a.to.length)[0] || null;
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
