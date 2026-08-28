/* Shared primitives. Small on purpose -- the map and the data are the
 * interesting parts, and chrome that competes with them is a bug. */

import { useEffect, useState } from "react";

import { statusTone } from "../lib/api";

export function Card({ title, right, children, style, bodyStyle }) {
  return (
    <div className="card" style={style}>
      {(title || right) && (
        <div className="card-head">
          <span className="card-title">{title}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
            {right}
          </span>
        </div>
      )}
      <div className="card-body" style={bodyStyle}>{children}</div>
    </div>
  );
}

export function Badge({ status, children }) {
  return <span className={`badge badge-${statusTone(status)}`}>{children ?? status}</span>;
}

export function Dot({ status, pulsing }) {
  const tone = statusTone(status);
  const cls = tone === "ok" ? "dot-ok" : tone === "warn" ? "dot-warn"
    : tone === "danger" ? "dot-danger" : "dot-idle";
  return <span className={`dot ${cls} ${pulsing ? "pulsing" : ""}`} />;
}

export function Stat({ label, value, sub, tone }) {
  const colour = tone === "ok" ? "var(--ok)" : tone === "danger" ? "var(--danger)"
    : tone === "warn" ? "var(--warn)" : "var(--ink-0)";
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value" style={{ color: colour }}>{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

export function Switch({ checked, onChange, label, swatch }) {
  return (
    <label className="switch" onClick={() => onChange(!checked)}>
      <span className={`switch-track ${checked ? "on" : ""}`}>
        <span className="switch-knob" />
      </span>
      {swatch && <span className="legend-swatch" style={{ background: swatch }} />}
      <span className="switch-label">{label}</span>
    </label>
  );
}

export function Spinner({ label }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <span className="spinner" />
      {label && <span className="tiny muted">{label}</span>}
    </span>
  );
}

export function Empty({ icon, title, hint }) {
  return (
    <div className="empty">
      {icon}
      <div style={{ fontSize: 15, color: "var(--ink-1)" }}>{title}</div>
      {hint && <div className="tiny" style={{ maxWidth: 380 }}>{hint}</div>}
    </div>
  );
}

/** Horizontal bar for one attribution factor. Colour tracks magnitude so a
 *  strong signal reads at a glance without consulting the number. */
export function FactorBar({ name, value }) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  const colour = v > 0.7 ? "var(--danger)" : v > 0.4 ? "var(--oil)" : "var(--accent-dim)";
  return (
    <div className="factor">
      <span className="factor-name">{name.replace(/_/g, " ")}</span>
      <span className="factor-track">
        <span className="factor-fill" style={{ width: `${v * 100}%`, background: colour }} />
      </span>
      <span className="factor-val">{v.toFixed(2)}</span>
    </div>
  );
}

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


/** Chart libraries need concrete colour values, not var() strings. This reads
 *  the design tokens off the document and re-reads them when the theme flips,
 *  so recharts follows the light/dark toggle instead of staying dark. */
export function useThemeColors() {
  const read = () => {
    const s = getComputedStyle(document.documentElement);
    const v = (name, fb) => (s.getPropertyValue(name) || fb).trim();
    return {
      ink0: v("--ink-0", "#f0f4fa"),
      ink2: v("--ink-2", "#6b7c99"),
      line: v("--line", "#1e2a41"),
      bg1: v("--bg-1", "#0c1220"),
      accent: v("--accent", "#38bdf8"),
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
