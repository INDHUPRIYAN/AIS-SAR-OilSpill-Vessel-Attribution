/* ⌘K — one box that reaches every route, every mounted view's actions, and
 * every run, incident, investigation, vessel and scene in the system.
 *
 * The honesty problem specific to a palette is that it answers instantly and
 * always looks right. Three rules follow:
 *
 * - **Local commands are matched the same way the server matches entities**:
 *   case-insensitive substring, no fuzzy scoring. A palette that guesses what
 *   you meant on an identifier is a palette that opens the wrong vessel.
 * - **An empty result is stated, with the matching rule that produced it** —
 *   the sentence comes from the endpoint, so the UI cannot describe a matching
 *   behaviour the server does not implement.
 * - **A failed search says it failed.** Rendering "no results" for a request
 *   that never completed is the same lie as an empty EO list.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Command, CornerDownLeft, Search } from "lucide-react";

import { api } from "../lib/api";
import { ROUTES, useShell } from "../lib/shell";

const KIND_LABEL = {
  run: "RUN", incident: "INCIDENT", investigation: "INVESTIGATION",
  vessel: "VESSEL", scene: "SCENE",
};

/* Same rule as the server: case-insensitive substring, nothing cleverer. */
function matches(text, needle) {
  return String(text || "").toLowerCase().includes(needle);
}

export default function CommandPalette() {
  const { paletteOpen, closePalette, commands } = useShell();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const [remote, setRemote] = useState(
    { results: [], matching: null, error: null, loading: false });
  const inputRef = useRef(null);
  const listRef = useRef(null);

  /* Route commands are derived from ROUTES rather than listed again here, so
   * a screen cannot exist that the palette cannot reach. */
  const localCommands = useMemo(() => [
    ...ROUTES.filter((r) => r.palette !== false).map((r) => ({
      id: `route:${r.to}`, group: "Go to", label: r.label,
      hint: r.to, run: () => navigate(r.to),
    })),
    ...commands,
  ], [commands, navigate]);

  const needle = q.trim().toLowerCase();
  const filtered = useMemo(() => (
    needle
      ? localCommands.filter((c) => matches(c.label, needle)
        || matches(c.hint, needle) || matches(c.group, needle))
      : localCommands
  ), [localCommands, needle]);

  /* Entities come from the server. Debounced so a nine-character MMSI is one
   * request rather than nine. */
  useEffect(() => {
    if (!paletteOpen || needle.length < 2) {
      setRemote({ results: [], matching: null, error: null, loading: false });
      return undefined;
    }
    let alive = true;
    setRemote((r) => ({ ...r, loading: true }));
    const timer = setTimeout(async () => {
      try {
        const body = await api.search(needle);
        if (alive) {
          setRemote({ results: body.results || [], matching: body.matching,
                      error: null, loading: false });
        }
      } catch (e) {
        // Stated, not swallowed into an empty list.
        if (alive) {
          setRemote({ results: [], matching: null, error: e, loading: false });
        }
      }
    }, 160);
    return () => { alive = false; clearTimeout(timer); };
  }, [needle, paletteOpen]);

  const rows = useMemo(() => [
    ...filtered.map((c) => ({ type: "command", key: c.id, command: c })),
    ...remote.results.map((r) => ({ type: "entity", key: `${r.kind}:${r.id}`, entity: r })),
  ], [filtered, remote.results]);

  useEffect(() => { setCursor(0); }, [needle, paletteOpen]);

  useEffect(() => {
    if (paletteOpen) inputRef.current?.focus();
    else { setQ(""); setCursor(0); }
  }, [paletteOpen]);

  const activate = useCallback((row) => {
    if (!row) return;
    if (row.type === "command") row.command.run?.();
    else navigate(row.entity.route);
    closePalette();
  }, [closePalette, navigate]);

  /* Arrow keys and Enter are palette-scoped: they only mean anything while it
   * is open, so they live with the list rather than in the global handler. */
  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => (rows.length ? (c + 1) % rows.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => (rows.length ? (c - 1 + rows.length) % rows.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      activate(rows[cursor]);
    }
  };

  useEffect(() => {
    // Optional call: scrollIntoView is absent in jsdom and in some embedded
    // webviews, and keeping the highlighted row in view is a convenience --
    // it must not be able to take the palette down with it.
    const active = listRef.current?.querySelector('[data-active="true"]');
    active?.scrollIntoView?.({ block: "nearest" });
  }, [cursor, rows.length]);

  if (!paletteOpen) return null;

  return (
    <div className="cp-scrim" onMouseDown={closePalette} data-testid="palette-scrim">
      <div className="cp panel" role="dialog" aria-modal="true"
        aria-label="Command palette" data-testid="command-palette"
        onMouseDown={(e) => e.stopPropagation()}>
        <div className="cp-input-row">
          <Search size={15} color="var(--ink-2)" />
          <input ref={inputRef} className="cp-input" value={q} onKeyDown={onKeyDown}
            onChange={(e) => setQ(e.target.value)}
            aria-label="Search commands, runs, incidents, vessels and scenes"
            aria-controls="cp-list"
            aria-activedescendant={rows[cursor] ? `cp-row-${cursor}` : undefined}
            placeholder="Go to a screen, or search a run, incident, vessel or scene"
            data-testid="palette-input" />
          {remote.loading && <span className="tiny muted" data-testid="palette-loading">searching…</span>}
        </div>

        <div className="cp-list" id="cp-list" role="listbox" ref={listRef}
          aria-label="Results">
          {rows.map((row, i) => (
            <button key={row.key} id={`cp-row-${i}`} role="option"
              aria-selected={i === cursor} data-active={i === cursor}
              className={`cp-row ${i === cursor ? "on" : ""}`}
              onMouseEnter={() => setCursor(i)}
              onClick={() => activate(row)}
              data-testid={row.type === "command"
                ? `palette-command-${row.command.id}`
                : `palette-entity-${row.entity.kind}-${row.entity.id}`}>
              <span className="cp-kind mono">
                {row.type === "command"
                  ? (row.command.group || "ACTION").toUpperCase()
                  : KIND_LABEL[row.entity.kind] || row.entity.kind.toUpperCase()}
              </span>
              <span className="cp-label">
                {row.type === "command" ? row.command.label : row.entity.label}
              </span>
              <span className="cp-hint mono">
                {row.type === "command"
                  ? (row.command.keys?.join(" / ") || row.command.hint || "")
                  : row.entity.context}
              </span>
              {i === cursor && <CornerDownLeft size={12} color="var(--accent)" />}
            </button>
          ))}

          {remote.error && (
            <div className="cp-note cp-note-error" data-testid="palette-error">
              Search failed — {remote.error.message}. This is not an empty
              result: the query did not complete.
            </div>
          )}

          {!rows.length && !remote.loading && !remote.error && (
            <div className="cp-note" data-testid="palette-empty">
              {needle.length < 2
                ? "Type at least two characters to search runs, incidents, vessels and scenes."
                : `No match for "${q}".`}
              {remote.matching && <div className="cp-matching">{remote.matching}</div>}
            </div>
          )}
        </div>

        <div className="cp-foot tiny mono">
          <span><Command size={11} /> ↑ ↓ move · ⏎ open · esc close · ? shortcuts</span>
          <span className="muted">{rows.length} shown</span>
        </div>
      </div>
    </div>
  );
}

/** The `?` overlay, rendered from the live keymap so it cannot describe a
 *  binding the shell does not have. */
export function ShortcutOverlay() {
  const { helpOpen, closeHelp, keymap } = useShell();
  if (!helpOpen) return null;
  return (
    <div className="cp-scrim" onMouseDown={closeHelp} data-testid="shortcut-scrim">
      <div className="cp panel" role="dialog" aria-modal="true"
        aria-label="Keyboard shortcuts" data-testid="shortcut-overlay"
        onMouseDown={(e) => e.stopPropagation()}>
        <div className="cp-input-row"><strong>Keyboard shortcuts</strong></div>
        <div className="cp-list">
          {keymap.map((k) => (
            <div className="cp-row" key={k.id} data-testid={`shortcut-${k.id}`}>
              <span className="cp-kind mono">{k.scope}</span>
              <span className="cp-label">{k.label}</span>
              <span className="cp-hint mono">
                {k.keys.map((key) => <kbd key={key}>{key}</kbd>)}
              </span>
            </div>
          ))}
        </div>
        <div className="cp-foot tiny mono">
          <span className="muted">
            Generated from the live keymap — a binding that is removed
            disappears from this list.
          </span>
        </div>
      </div>
    </div>
  );
}
