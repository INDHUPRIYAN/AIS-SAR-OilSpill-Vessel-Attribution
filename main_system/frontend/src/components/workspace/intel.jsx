/* Primitives the contextual panels are built from: a collapsible section
 * with a tracked uppercase title, a key/value row, a tab strip, a two-state
 * checklist row and the panel's primary action. One vocabulary, so every
 * stage panel reads as the same instrument.
 *
 * Density is the frames': 12.5 px rows on a 24 px pitch, 11 px section
 * titles, thin rules. Values that are missing render "—"; a caller that has
 * a better word ("not modelled", "not recorded") passes it instead. */

import { useState } from "react";
import { ArrowRight, Check, ChevronDown, ChevronUp, Loader2, X } from "lucide-react";

export function Section({ title, right, children, open: initial = true, testid, tone, id }) {
  const [open, setOpen] = useState(initial);
  return (
    <section className={`ip-sec ${open ? "" : "closed"} ${tone ? `ip-sec-${tone}` : ""}`} data-testid={testid} id={id}>
      <button className="ip-sec-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        <span className="ip-sec-title">{title}</span>
        {right && <span className="ip-sec-right" onClick={(e) => e.stopPropagation()}>{right}</span>}
      </button>
      {open && <div className="ip-sec-body">{children}</div>}
    </section>
  );
}

export function Row({ k, v, testid, title, tone, mono = true, children }) {
  return (
    <div className="ip-r" title={title}>
      <span className="ip-rk">{k}</span>
      <span className={`ip-rv ${mono ? "mono" : ""} ${tone ? `tone-${tone}` : ""}`} data-testid={testid}>
        {children ?? (v == null || v === "" ? "—" : v)}
      </span>
    </div>
  );
}

export function Tabs({ items, value, onChange, testidPrefix = "ip-tab" }) {
  return (
    <div className="ip-tabs" role="tablist">
      {items.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id}
          className={`ip-tab ${value === t.id ? "on" : ""}`} onClick={() => onChange(t.id)}
          data-testid={`${testidPrefix}-${t.id}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** One line of a processing checklist. `state`: done | running | pending |
 *  failed | na. `note` explains a "na" (not modelled) or a value. */
export function Check_({ label, state, note, testid }) {
  return (
    <div className={`ip-check ip-check-${state}`} data-testid={testid} data-state={state}>
      <span className="ip-check-box">
        {state === "done" && <Check size={11} />}
        {state === "running" && <Loader2 size={11} className="ws-spin" />}
        {state === "failed" && <X size={11} />}
      </span>
      <span className="ip-check-label">{label}</span>
      <span className="ip-check-note">
        {note ?? (state === "done" ? "Completed" : state === "running" ? "Processing…"
          : state === "failed" ? "Failed" : state === "na" ? "Not modelled" : "Pending")}
      </span>
      {state === "done" && <Check size={13} className="ip-check-tick" />}
    </div>
  );
}

export function Primary({ children, onClick, disabled, title, busy, testid, tone = "accent", icon = true }) {
  return (
    <button className={`ip-primary ip-primary-${tone}`} onClick={onClick} disabled={disabled || busy}
      title={title} data-testid={testid}>
      {busy && <Loader2 size={14} className="ws-spin" />}
      <span>{children}</span>
      {icon && !busy && <ArrowRight size={15} />}
    </button>
  );
}

export function Meter({ value, max = 1, tone = "accent", testid }) {
  const pct = Math.max(0, Math.min(100, (Number(value) / (max || 1)) * 100));
  return (
    <span className="ip-meter" data-testid={testid}>
      <span className={`ip-meter-fill ip-meter-${tone}`} style={{ width: `${pct}%` }} />
    </span>
  );
}

export const num = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? null : Number(v).toFixed(d));
export const utc = (s) => {
  if (!s) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : `${d.toISOString().replace("T", " ").slice(0, 19)} UTC`;
};
export const latlon = (c) => (Array.isArray(c)
  ? `${Math.abs(c[1]).toFixed(4)}° ${c[1] >= 0 ? "N" : "S"} | ${Math.abs(c[0]).toFixed(4)}° ${c[0] >= 0 ? "E" : "W"}`
  : null);
