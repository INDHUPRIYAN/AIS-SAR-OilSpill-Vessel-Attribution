/* Shared primitives -- the OceanTrace component vocabulary.
 *
 * Small on purpose: the map and the data are the interesting parts, and chrome
 * that competes with them is a bug. Every primitive here reads its colour from
 * the tokens in styles/tokens.css, so the light theme is a palette swap and
 * never a second implementation.
 *
 * The exports that existed before (Card, Badge, Dot, Stat, Switch, Spinner,
 * Empty, FactorBar, ProvenanceChip, useThemeColors) keep their signatures.
 */

import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, Ban, ChevronDown, ChevronUp, CloudOff, Inbox, Radio, ServerOff,
  TimerReset, WifiOff, X,
} from "lucide-react";

import { statusTone } from "../lib/api";

/* ------------------------------------------------------------- panels ---- */

/** A flat panel. `title` renders a head; `right` goes in the tools slot. */
export function Panel({ title, icon, right, children, className = "", style, bodyStyle,
                        flush = false, collapsible = false, defaultOpen = true, testid }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`panel ${collapsible && !open ? "panel-collapsed" : ""} ${className}`}
      style={style} data-testid={testid}>
      {(title || right || icon) && (
        <header className="panel-head">
          <span className="panel-title">{icon}{title}</span>
          <span className="panel-tools">
            {right}
            {collapsible && (
              <button className="btn btn-ghost btn-sm btn-icon" onClick={() => setOpen((o) => !o)}
                aria-label={open ? "Collapse" : "Expand"} aria-expanded={open}>
                {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>
            )}
          </span>
        </header>
      )}
      <div className={`panel-body ${flush ? "flush" : ""}`} style={bodyStyle}>{children}</div>
    </section>
  );
}

/** Backwards-compatible alias: the old pages call this `Card`. */
export function Card({ title, right, children, style, bodyStyle, icon, className, flush }) {
  return (
    <Panel title={title} icon={icon} right={right} style={style} bodyStyle={bodyStyle}
      className={className} flush={flush}>
      {children}
    </Panel>
  );
}

/** Page header: kicker, title, sub-line, and an actions slot. */
export function PageHeader({ icon, kicker, title, sub, actions, children }) {
  return (
    <div className="page-head">
      <div className="page-head-main">
        {icon && <div className="page-icon">{icon}</div>}
        <div style={{ minWidth: 0 }}>
          {kicker && <div className="page-kicker">{kicker}</div>}
          <div className="page-title">{title}</div>
          {sub && <div className="page-sub">{sub}</div>}
        </div>
        {children}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

/* ------------------------------------------------------------- badges ---- */

/** A badge. Accepts `tone` (ok|warn|danger|mock|accent|teal|neutral|ghost)
 *  directly, or a `status` word which is mapped through `statusTone`, so the
 *  colour vocabulary is the same everywhere. */
export function Badge({ status, tone, children, title, solid, lg, className = "", testid }) {
  const t = tone || statusTone(status);
  const cls = solid ? `badge-solid-${t}` : `badge-${t}`;
  return (
    <span className={`badge ${cls} ${lg ? "badge-lg" : ""} ${className}`} title={title}
      data-testid={testid}>
      {children ?? status}
    </span>
  );
}

export function Chip({ k, v, className = "", title }) {
  return (
    <span className={`chip ${className}`} title={title}>
      {k && <span className="chip-k">{k}</span>}
      <span>{v}</span>
    </span>
  );
}

export function Dot({ status, tone, pulsing }) {
  const t = tone || statusTone(status);
  const cls = t === "ok" ? "dot-ok" : t === "warn" ? "dot-warn"
    : t === "danger" ? "dot-danger" : t === "accent" ? "dot-accent"
      : t === "mock" ? "dot-mock" : "dot-idle";
  return <span className={`dot ${cls} ${pulsing ? "pulsing" : ""}`} />;
}

/** LIVE / STALE / OFFLINE indicator. `tone` decides whether the ring animates
 *  -- only `ok` does, so nothing pulses that is not measured as live. */
export function LiveIndicator({ tone = "idle", label, title }) {
  return (
    <span className={`live live-${tone}`} title={title}>
      <span className="live-dot" />
      {label}
    </span>
  );
}

export function Kbd({ children }) {
  return <kbd className="kbd">{children}</kbd>;
}

/* -------------------------------------------------------------- stats ---- */

export function Stat({ label, value, sub, tone, small, testid }) {
  const colour = tone === "ok" ? "var(--ok)" : tone === "danger" ? "var(--danger)"
    : tone === "warn" ? "var(--warn)" : tone === "accent" ? "var(--accent)" : "var(--ink-0)";
  return (
    <div className="stat" data-testid={testid}>
      <span className="stat-label">{label}</span>
      <LiveValue className={`stat-value ${small ? "sm" : ""}`} style={{ color: colour }}
        value={value} />
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

/** A metric tile: a Stat in a panel, optionally a link, with a tone rail. */
export function Tile({ label, value, sub, tone, to, testid, onClick }) {
  const Tag = to ? "a" : "div";
  const props = to ? { href: to } : {};
  return (
    <Tag className={`panel tile tile-${tone || "line"}`} {...props} onClick={onClick}
      data-testid={testid} style={onClick ? { cursor: "pointer" } : undefined}>
      <Stat label={label} value={value} sub={sub} tone={tone} small />
    </Tag>
  );
}

/** Renders a value and flashes briefly whenever it changes, so an operator
 *  can see that a live number moved without staring at it. */
export function LiveValue({ value, className = "", style, as: Tag = "span" }) {
  const [flash, setFlash] = useState(false);
  const prev = useRef(value);
  useEffect(() => {
    if (prev.current !== value && prev.current !== undefined && prev.current !== null) {
      setFlash(true);
      const id = setTimeout(() => setFlash(false), 900);
      prev.current = value;
      return () => clearTimeout(id);
    }
    prev.current = value;
    return undefined;
  }, [value]);
  return <Tag className={`${className} ${flash ? "live-flash" : ""}`} style={style}>{value}</Tag>;
}

/* --------------------------------------------------------------- rows ---- */

/** Key / value row. */
export function KV({ k, v, tone, title, testid, wrap }) {
  return (
    <div className="kv" title={title}>
      <span className="kv-k">{k}</span>
      <span className={`kv-v ${tone || ""} ${wrap ? "wrap" : ""}`} data-testid={testid}>{v}</span>
    </div>
  );
}

/* ----------------------------------------------------------- controls ---- */

export function Switch({ checked, onChange, label, swatch, disabled, title, testid }) {
  return (
    <label className="switch" title={title} data-testid={testid}
      style={disabled ? { opacity: 0.45, cursor: "not-allowed" } : undefined}
      onClick={() => !disabled && onChange(!checked)}>
      <span className={`switch-track ${checked ? "on" : ""}`}>
        <span className="switch-knob" />
      </span>
      {swatch && <span className="legend-swatch" style={{ background: swatch }} />}
      <span className="switch-label">{label}</span>
    </label>
  );
}

/** Tab strip. `items` = [{id, label, icon, count}]. */
export function Tabs({ items, value, onChange, fill = false, testidPrefix = "tab" }) {
  return (
    <div className={`tabs ${fill ? "tabs-fill" : ""}`} role="tablist">
      {items.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id}
          className={`tab ${value === t.id ? "on" : ""}`}
          onClick={() => onChange(t.id)} data-testid={`${testidPrefix}-${t.id}`}>
          {t.icon}{t.label}
          {t.count != null && t.count !== 0 && <span className="tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** Segmented control. `items` = [{id, label, icon, disabled, title}]. */
export function Segmented({ items, value, onChange, testidPrefix }) {
  return (
    <span className="seg" role="group">
      {items.map((it) => (
        <button key={it.id} className={`seg-b ${value === it.id ? "on" : ""}`}
          onClick={() => onChange(it.id)} disabled={it.disabled} title={it.title}
          aria-pressed={value === it.id}
          data-testid={testidPrefix ? `${testidPrefix}-${it.id}` : undefined}>
          {it.icon}{it.label}
        </button>
      ))}
    </span>
  );
}

/* ------------------------------------------------------------- states ---- */

export function Spinner({ label }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span className="spinner" />
      {label && <span className="tiny muted">{label}</span>}
    </span>
  );
}

/** Legacy empty state (kept for the pages that call it). */
export function Empty({ icon, title, hint }) {
  return (
    <div className="state state-compact">
      {icon && <span className="state-icon">{icon}</span>}
      <div className="state-title">{title}</div>
      {hint && <div className="state-hint">{hint}</div>}
    </div>
  );
}

const STATE_PRESETS = {
  loading: { icon: null, title: "Loading", tone: "" },
  empty: { icon: <Inbox size={15} />, title: "Nothing here", tone: "" },
  error: { icon: <AlertTriangle size={15} />, title: "Request failed", tone: "state-error" },
  offline: { icon: <WifiOff size={15} />, title: "Offline", tone: "state-error" },
  "no-coverage": { icon: <Radio size={15} />, title: "No coverage", tone: "state-warn" },
  "not-deployed": { icon: <ServerOff size={15} />, title: "Not deployed", tone: "" },
  stale: { icon: <TimerReset size={15} />, title: "Stale data", tone: "state-warn" },
  forbidden: { icon: <Ban size={15} />, title: "Not available to your role", tone: "state-warn" },
  unavailable: { icon: <CloudOff size={15} />, title: "Unavailable", tone: "state-warn" },
};

/** One component for every honest non-success state. `kind` picks the
 *  preset; `title`/`hint` override it. `error` renders the server's own
 *  message, never a paraphrase. */
export function DataState({ kind = "empty", title, hint, error, icon, compact, children, testid }) {
  const preset = STATE_PRESETS[kind] || STATE_PRESETS.empty;
  return (
    <div className={`state ${preset.tone} ${compact ? "state-compact" : ""}`} data-testid={testid}>
      {kind === "loading"
        ? <span className="spinner" />
        : <span className="state-icon">{icon ?? preset.icon}</span>}
      <div className="state-title">{title ?? preset.title}</div>
      {(hint || error) && (
        <div className="state-hint">{hint}{hint && error ? " — " : ""}{error?.message || (typeof error === "string" ? error : "")}</div>
      )}
      {children}
    </div>
  );
}

/** Picks the state for a useApi result: loading → error → empty → children. */
export function Loadable({ loading, error, data, empty, emptyKind = "empty", emptyTitle,
                           emptyHint, children, compact = true, loadingLabel }) {
  if (loading && !data) return <DataState kind="loading" title={loadingLabel || "Loading"} compact={compact} />;
  if (error) {
    const kind = error?.status === 403 ? "forbidden" : error?.status === 404 ? "unavailable" : "error";
    return <DataState kind={kind} error={error} compact={compact} />;
  }
  if (empty) return <DataState kind={emptyKind} title={emptyTitle} hint={emptyHint} compact={compact} />;
  return children;
}

/** An inline notice with a tone. Distinct from a data state: it sits next
 *  to data that DID load, to qualify it. */
export function Notice({ tone = "info", icon, children, testid, style }) {
  return (
    <div className={`notice ${tone === "info" ? "" : `notice-${tone}`}`} data-testid={testid} style={style}>
      {icon ?? (tone === "warn" || tone === "danger" ? <AlertTriangle size={12} /> : null)}
      <span>{children}</span>
    </div>
  );
}

/* ------------------------------------------------------------ overlays --- */

export function Drawer({ title, icon, onClose, children, foot, width, testid }) {
  return (
    <aside className="drawer" style={width ? { width } : undefined} data-testid={testid}
      role="dialog" aria-label={typeof title === "string" ? title : "Details"}>
      <header className="drawer-head">
        {icon}
        <span className="drawer-title">{title}</span>
        <button className="btn btn-ghost btn-sm btn-icon ml-auto" onClick={onClose} aria-label="Close">
          <X size={14} />
        </button>
      </header>
      <div className="drawer-body">{children}</div>
      {foot && <footer className="drawer-foot">{foot}</footer>}
    </aside>
  );
}

export function Modal({ title, onClose, children, foot, width, testid }) {
  return (
    <div className="scrim" onMouseDown={onClose} data-testid={testid ? `${testid}-scrim` : undefined}>
      <div className="modal" style={width ? { width } : undefined} role="dialog" aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()} data-testid={testid}>
        <header className="modal-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-sm btn-icon" onClick={onClose} aria-label="Close">
            <X size={14} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {foot && <footer className="modal-foot">{foot}</footer>}
      </div>
    </div>
  );
}

/** A hover readout for maps: title + rows. Positioned by the caller. */
export function MapTip({ x, y, title, icon, rows, style, testid }) {
  return (
    <div className="tip" style={{ left: x, top: y, ...style }} data-testid={testid}>
      <div className="tip-title">{icon}{title}</div>
      {(rows || []).map(([k, v]) => (
        <div key={k} className="tip-row">
          <span className="tip-k">{k}</span>
          <span className="tip-v">{v}</span>
        </div>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------- bars ----- */

/** Horizontal bar for one attribution factor. Colour tracks magnitude so a
 *  strong signal reads at a glance without consulting the number. When the
 *  factor's weight is known it is printed under the name (×0.25), so the
 *  weighted scoring is auditable right where the factor is drawn. */
export function FactorBar({ name, value, weight, tone }) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  // `tone` pins the fill colour (the workspace draws every factor cyan, as
  // the frames do); without it the colour tracks magnitude as before.
  const colour = tone ? `var(--${tone})`
    : v > 0.7 ? "var(--danger)" : v > 0.4 ? "var(--oil)" : "var(--accent-dim)";
  return (
    <div className="factor">
      <span className="factor-name">
        {name.replace(/_/g, " ")}
        {weight != null && (
          <em className="factor-w mono" data-testid={`weight-${name}`}>
            ×{Number(weight).toFixed(2)}
          </em>
        )}
      </span>
      <span className="factor-track">
        <span className="factor-fill" style={{ width: `${v * 100}%`, background: colour }} />
      </span>
      <span className="factor-val">{v.toFixed(2)}</span>
    </div>
  );
}

export function Bar({ value, max = 1, tone = "accent", height }) {
  const pct = Math.max(0, Math.min(100, (Number(value) / (max || 1)) * 100));
  const colour = { ok: "var(--ok)", warn: "var(--warn)", danger: "var(--danger)",
                   mock: "var(--mock)", accent: "var(--accent)", teal: "var(--teal)" }[tone] || tone;
  return (
    <span className="bar" style={height ? { height } : undefined}>
      <span className="bar-fill" style={{ width: `${pct}%`, background: colour }} />
    </span>
  );
}

/* --------------------------------------------------------- provenance ---- */

/** Provenance chip. The whole point of the project's honesty rule: a layer
 *  that came from a mock must never look like one that came from a sensor. */
export function ProvenanceChip({ status, source }) {
  const text = status === "ok" ? "REAL"
    : status === "fallback" ? "FALLBACK"
    : status === "mock" ? "MOCK"
    : status === "failed" ? "FAILED" : String(status || "").toUpperCase();
  const title = {
    ok: "Produced and contract-validated by the real component",
    fallback: "A degraded path produced this — see the stage note",
    mock: "Component unavailable; mock file served instead",
    failed: "Stage failed; this layer is unavailable",
  }[status];
  return <span className={`badge badge-${statusTone(status)}`} title={title}>{text}</span>;
}

/** Data-source word -> label + tone. One vocabulary for the whole product:
 *  REAL / SENSOR / HISTORICAL / CACHED / FALLBACK / SYNTHETIC / NOT DEPLOYED. */
export function provenanceOf(source) {
  switch (String(source || "").toLowerCase()) {
    case "sensor": return { label: "SENSOR", tone: "ok" };
    case "real": return { label: "REAL", tone: "ok" };
    case "archive":
    case "historical": return { label: "HISTORICAL", tone: "ok" };
    case "live": return { label: "LIVE", tone: "ok" };
    case "cached":
    case "cache": return { label: "CACHED", tone: "warn" };
    case "fallback": return { label: "FALLBACK", tone: "warn" };
    case "synthetic":
    case "mock": return { label: "SYNTHETIC", tone: "mock" };
    case "not_deployed": return { label: "NOT DEPLOYED", tone: "neutral" };
    case "no_coverage": return { label: "NO COVERAGE", tone: "warn" };
    case "": case "null": case "undefined": return { label: "UNRECORDED", tone: "ghost" };
    default: return { label: String(source).toUpperCase(), tone: "neutral" };
  }
}

export function ProvenanceBadge({ source, prefix, testid }) {
  const p = provenanceOf(source);
  return <Badge tone={p.tone} testid={testid}>{prefix ? `${prefix} ` : ""}{p.label}</Badge>;
}

/* -------------------------------------------------------- theme colours --- */

/** Chart libraries and deck.gl need concrete colour values, not var()
 *  strings. This reads the design tokens off the document and re-reads them
 *  when the theme flips, so recharts and the map follow the light/dark toggle
 *  instead of staying dark. */
export function useThemeColors() {
  const read = () => {
    const s = getComputedStyle(document.documentElement);
    const v = (name, fb) => (s.getPropertyValue(name) || fb).trim();
    return {
      ink0: v("--ink-0", "#e8eef8"),
      ink1: v("--ink-1", "#aab9cf"),
      ink2: v("--ink-2", "#728499"),
      ink3: v("--ink-3", "#4b5b71"),
      line: v("--line", "#182335"),
      lineBright: v("--line-bright", "#243650"),
      bg0: v("--bg-0", "#060a12"),
      bg1: v("--bg-1", "#0a1019"),
      bg2: v("--bg-2", "#0e1621"),
      accent: v("--accent", "#22c3ee"),
      teal: v("--teal", "#2dd4bf"),
      ok: v("--ok", "#2fbf71"),
      warn: v("--warn", "#f5a524"),
      danger: v("--danger", "#ef4444"),
      mock: v("--mock", "#a78bfa"),
      oil: v("--oil", "#f59e0b"),
    };
  };
  const [colors, setColors] = useState(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setColors(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return colors;
}

/** Recharts boilerplate that follows the theme. */
export function chartTheme(tc) {
  return {
    axis: { fill: tc.ink2, fontSize: 10, fontFamily: "JetBrains Mono, monospace" },
    grid: { stroke: tc.line, vertical: false },
    tooltip: {
      contentStyle: {
        background: tc.bg1, border: `1px solid ${tc.lineBright}`, borderRadius: 4,
        fontSize: 11, color: tc.ink0, fontFamily: "JetBrains Mono, monospace",
      },
      cursor: { fill: "rgba(34,195,238,0.06)" },
    },
  };
}
