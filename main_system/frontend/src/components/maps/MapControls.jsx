// @ts-check
/* The map console: one compact set of controls, disclosed progressively.
 *
 *   BasemapSwitch   a radio group; a basemap that is not configured says so
 *   LayerControl    a checklist with the legend colour beside each layer, and
 *                   the reason when a layer has nothing to draw
 *   TimeController  the one time bar, bound to TimeContext
 *   MapLegend       what the colours on screen mean
 *   SourceChip      where a layer's data came from, and as of when
 *
 * None of these own data. They render what the page tells them is real. */

import { useId } from "react";
import { Layers, Pause, Play, SkipBack, SkipForward } from "lucide-react";

import { basemapOptions } from "./basemaps";
import { SPEEDS, useTime } from "./TimeContext";

/** A CSS custom property is not in React's style type. @param {string} color */
const swatchStyle = (color) => /** @type {import("react").CSSProperties} */ ({ "--sw": color });

/** @param {{value: string, onChange: (id: string) => void, className?: string}} props */
export function BasemapSwitch({ value, onChange, className = "" }) {
  const name = useId();
  return (
    <fieldset className={`mc-basemap ${className}`} data-testid="basemap-switch">
      <legend className="mc-legend">Basemap</legend>
      {basemapOptions().map((o) => (
        <label key={o.id} className={`mc-radio ${value === o.id ? "on" : ""} ${o.available ? "" : "off"}`}
          title={o.available ? `${o.label} — ${o.source}` : o.unavailable}>
          <input type="radio" name={name} value={o.id} checked={value === o.id} disabled={!o.available}
            onChange={() => onChange(o.id)} data-testid={`basemap-${o.id}`} />
          <span>{o.label}</span>
          {!o.available && <em className="mc-na">not configured</em>}
        </label>
      ))}
    </fieldset>
  );
}

/**
 * @typedef {object} LayerRow
 * @property {string} id
 * @property {string} label
 * @property {string} [color]      legend swatch (css colour)
 * @property {"fill"|"line"|"dash"|"dot"} [shape]
 * @property {boolean} [disabled]
 * @property {string} [note]       why it is empty or disabled, in the user's terms
 * @property {string|number} [count]
 */

/** @param {{rows: LayerRow[], value: Record<string, boolean>, onToggle: (id: string, on: boolean) => void, title?: string}} props */
export function LayerControl({ rows, value, onToggle, title = "Layers" }) {
  return (
    <div className="mc-layers" data-testid="layer-control">
      <div className="mc-legend"><Layers size={11} aria-hidden="true" /> {title}</div>
      {rows.map((r) => (
        <label key={r.id} className={`mc-check ${r.disabled ? "off" : ""}`} title={r.note}>
          <input type="checkbox" checked={Boolean(value[r.id]) && !r.disabled} disabled={r.disabled}
            onChange={(e) => onToggle(r.id, e.target.checked)} data-testid={`layer-${r.id}`} />
          <span className={`mc-swatch mc-swatch-${r.shape || "fill"}`} style={swatchStyle(r.color || "var(--ink-3)")}
            aria-hidden="true" />
          <span className="mc-check-label">{r.label}</span>
          {r.count != null && <span className="mc-count mono">{r.count}</span>}
          {r.note && <span className="mc-note">{r.note}</span>}
        </label>
      ))}
    </div>
  );
}

const pad = (/** @type {number} */ n) => String(n).padStart(2, "0");

/** `2023-01-08 00:10 UTC` @param {number|null|undefined} ms */
export function utcLabel(ms) {
  if (ms == null || !Number.isFinite(ms)) return "—";
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

/** Signed offset from the reference instant: `T−13 h`, `T+6 h`. @param {number} ms @param {number} ref */
export function offsetLabel(ms, ref) {
  const h = (ms - ref) / 3_600_000;
  if (Math.abs(h) < 0.05) return "T0";
  return `T${h < 0 ? "−" : "+"}${Math.abs(h) < 10 ? Math.abs(h).toFixed(1) : Math.round(Math.abs(h))} h`;
}

/** The global time bar. Arrow keys step an hour (Shift: six); Space plays.
 *  @param {{label?: string, nowLabel?: string, marks?: {t: number, label: string}[]}} props */
export function TimeController({ label = "Time", nowLabel = "acquisition", marks = [] }) {
  const time = useTime();
  const { t, range, playing, speed, now } = time;
  if (!range || t == null) return null;
  const span = Math.max(1, range[1] - range[0]);
  const pct = (/** @type {number} */ v) => `${((v - range[0]) / span) * 100}%`;

  /** @param {import("react").KeyboardEvent} e */
  const onKey = (e) => {
    const h = e.shiftKey ? 6 : 1;
    if (e.key === "ArrowLeft") { e.preventDefault(); time.pause(); time.step(-h); }
    else if (e.key === "ArrowRight") { e.preventDefault(); time.pause(); time.step(h); }
    else if (e.key === " ") { e.preventDefault(); time.toggle(); }
    else if (e.key === "Home") { e.preventDefault(); time.setT(range[0]); }
    else if (e.key === "End") { e.preventDefault(); time.setT(range[1]); }
  };

  return (
    <div className="mc-time" data-testid="time-controller" role="group" aria-label={label}>
      <div className="mc-time-btns">
        <button type="button" className="mc-btn" onClick={() => { time.pause(); time.step(-1); }} title="Back one hour (←)"
          aria-label="Back one hour"><SkipBack size={13} /></button>
        <button type="button" className="mc-btn mc-btn-play" onClick={time.toggle} data-testid="time-play"
          title={playing ? "Pause (Space)" : "Play (Space)"} aria-label={playing ? "Pause" : "Play"}>
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button type="button" className="mc-btn" onClick={() => { time.pause(); time.step(1); }} title="Forward one hour (→)"
          aria-label="Forward one hour"><SkipForward size={13} /></button>
        <div className="mc-speed" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button key={s} type="button" className={`mc-speed-b ${speed === s ? "on" : ""}`} aria-pressed={speed === s}
              onClick={() => time.setSpeed(s)} data-testid={`time-speed-${s}`}>{s}×</button>
          ))}
        </div>
      </div>

      <div className="mc-rail">
        <input type="range" className="mc-slider" min={range[0]} max={range[1]} step={60_000} value={t}
          onChange={(e) => { time.pause(); time.setT(Number(e.target.value)); }} onKeyDown={onKey}
          aria-label={`${label}, UTC`} aria-valuetext={utcLabel(t)} data-testid="time-slider" />
        {now != null && now >= range[0] && now <= range[1] && (
          <span className="mc-mark mc-mark-now" style={{ left: pct(now) }} title={`${nowLabel} · ${utcLabel(now)}`}>
            <i />{nowLabel}
          </span>
        )}
        {marks.filter((m) => m.t >= range[0] && m.t <= range[1]).map((m) => (
          <span key={`${m.t}-${m.label}`} className="mc-mark" style={{ left: pct(m.t) }} title={utcLabel(m.t)}><i />{m.label}</span>
        ))}
      </div>

      <div className="mc-clock" aria-live="off">
        <span className="mc-clock-utc mono" data-testid="time-utc">{utcLabel(t)}</span>
        {now != null && <span className="mc-clock-rel mono">{offsetLabel(t, now)}</span>}
      </div>
    </div>
  );
}

/** @param {{items: {label: string, color: string, shape?: string, note?: string}[], title?: string}} props */
export function MapLegend({ items, title = "Legend" }) {
  if (!items.length) return null;
  return (
    <div className="mc-legendbox" data-testid="map-legend">
      <div className="mc-legend">{title}</div>
      {items.map((it) => (
        <div key={it.label} className="mc-legend-row" title={it.note}>
          <span className={`mc-swatch mc-swatch-${it.shape || "fill"}`} style={swatchStyle(it.color)} aria-hidden="true" />
          <span>{it.label}</span>
        </div>
      ))}
    </div>
  );
}

/** "Wind — ERA5 — 12:00 UTC". Renders nothing without a source: an unlabelled
 *  layer is better than an invented credit.
 *  @param {{layer: string, source?: string|null, at?: number|null, note?: string}} props */
export function SourceChip({ layer, source, at, note }) {
  if (!source) return null;
  return (
    <span className="mc-source" title={note} data-testid={`source-${layer.toLowerCase()}`}>
      <b>{layer}</b> — {source}{at != null ? ` — ${utcLabel(at).slice(11)}` : ""}
    </span>
  );
}
