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

/* Every screen in the shell. `nav: false` entries are reachable by deep link
 * and from the palette, but do not earn a top-bar slot. */
export const ROUTES = [
  { to: "/incident", label: "Incident Replay", nav: true },
  { to: "/dashboard", label: "Investigations", nav: true },
  { to: "/investigation", label: "Workspace", nav: true },
  { to: "/analytics", label: "Analytics", nav: true },
  { to: "/monitoring", label: "Monitoring", nav: true },
  { to: "/catalog", label: "Data & Models", nav: true },
  { to: "/alerts", label: "Alerts", nav: true },
  { to: "/keys", label: "Keys", nav: true },
  { to: "/about", label: "About", nav: true },
  { to: "/incidents", label: "Incident register", nav: false },
  { to: "/vessels", label: "Vessel index", nav: false },
  { to: "/report", label: "Report", nav: false },
];

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
