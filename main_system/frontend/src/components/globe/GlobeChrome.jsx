/* Instrument chrome that sits over a globe or a map: the compass, the scale
 * bar, the camera readout and the time transport.
 *
 * Each of these is a real instrument, not decoration:
 *   - the compass shows the camera's bearing, and clicking it puts north back
 *     at the top;
 *   - the scale bar is computed from the viewport's own metres-per-pixel at
 *     the cursor latitude, so it is correct at every zoom and every latitude
 *     rather than a picture of a scale bar;
 *   - the readout prints the camera altitude deck.gl is actually using;
 *   - the transport drives a real clock that the layers read.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Calendar, ChevronLeft, ChevronRight, Pause, Play, RotateCcw, SkipBack, SkipForward,
} from "lucide-react";

/* deck.gl's GlobeViewport: world units are GLOBE_RADIUS=256 at zoom 0, and
 * scale doubles per zoom level. Metres per screen pixel therefore follows the
 * same relation the viewport uses internally. */
const GLOBE_RADIUS = 256;
const EARTH_RADIUS_M = 6370972;

/** Metres per CSS pixel at `zoom` on the globe view. */
export function metresPerPixel(zoom, latitude = 0) {
  const scale = Math.pow(2, zoom);
  const unitsPerMeter = (GLOBE_RADIUS * scale) / EARTH_RADIUS_M;
  return Math.cos((latitude * Math.PI) / 180) / unitsPerMeter || 1;
}

/** Camera altitude above the surface, in metres, for a globe zoom. deck.gl
 *  places the eye at 1.5 world units; converting that through the same scale
 *  gives the distance an operator would read off a camera readout. */
export function cameraAltitudeM(zoom) {
  const scale = Math.pow(2, zoom);
  return (1.5 * EARTH_RADIUS_M) / (GLOBE_RADIUS * scale / 256) / 256;
}

const NICE = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];

/** A scale bar whose label is a round number and whose width is that number's
 *  true length on screen. */
export function ScaleBar({ zoom, latitude = 0, maxPx = 130, className = "" }) {
  const mpp = metresPerPixel(zoom, latitude);
  const maxKm = (mpp * maxPx) / 1000;
  const km = [...NICE].reverse().find((n) => n <= maxKm) ?? NICE[0];
  const px = Math.max(24, (km * 1000) / mpp);
  return (
    <div className={`gc-scale ${className}`} data-testid="scale-bar">
      <div className="gc-scale-bar" style={{ width: px }}>
        <i /><i />
      </div>
      <span className="mono">{km >= 1 ? `${km} km` : `${Math.round(km * 1000)} m`}</span>
    </div>
  );
}

/** Compass rose. Rotates with the camera bearing; click resets north up. */
export function Compass({ bearing = 0, pitch = 0, onReset, className = "" }) {
  return (
    <button className={`gc-compass ${className}`} onClick={onReset} data-testid="compass"
      title={`Bearing ${Math.round(((bearing % 360) + 360) % 360)}° — click to reset north up`}
      aria-label="Reset orientation to north up">
      <span className="gc-compass-dial" style={{ transform: `rotate(${-bearing}deg)` }}>
        <span className="gc-compass-n">N</span>
        <span className="gc-compass-e">E</span>
        <span className="gc-compass-s">S</span>
        <span className="gc-compass-w">W</span>
        <svg viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">
          <circle cx="20" cy="20" r="15" className="gc-ring" />
          <path d="M20 7 L23.2 19 L20 17 L16.8 19 Z" className="gc-needle-n" />
          <path d="M20 33 L16.8 21 L20 23 L23.2 21 Z" className="gc-needle-s" />
        </svg>
      </span>
      {pitch > 1 && <span className="gc-compass-pitch mono">{Math.round(pitch)}°</span>}
    </button>
  );
}

/** Camera readout: where the camera is looking and how far away it is. */
export function CameraReadout({ viewState, className = "" }) {
  const alt = cameraAltitudeM(viewState?.zoom ?? 3);
  return (
    <div className={`map-hud gc-camera ${className}`} data-testid="camera-readout">
      <span><span className="map-hud-k">LAT</span>{fmtDeg(viewState?.latitude, "N", "S")}</span>
      <span><span className="map-hud-k">LON</span>{fmtDeg(viewState?.longitude, "E", "W")}</span>
      <span><span className="map-hud-k">ALT</span>{alt > 1000 ? `${Math.round(alt / 1000).toLocaleString()} km` : `${Math.round(alt)} m`}</span>
    </div>
  );
}

function fmtDeg(v, pos, neg) {
  if (v === null || v === undefined || Number.isNaN(v)) return "--";
  return `${Math.abs(v).toFixed(6)}° ${v >= 0 ? pos : neg}`;
}

/* ------------------------------------------------------------- transport --- */

const SPEEDS = [0.5, 1, 2, 4, 8, 16];

/** The time transport: play / step / scrub across a real domain, with the
 *  clock printed in UTC and every tick labelled relative to NOW.
 *
 *  `domain` is [fromMs, toMs] and `value` is an epoch in it. Both come from
 *  the caller's data -- this component never invents a time range. `events`
 *  are real records placed on the rail so an operator can scrub to one.
 */
export function TimeTransport({
  domain, value, onChange, playing, onPlaying, speed = 1, onSpeed,
  now, events = [], onStep, label = "TIMELINE", className = "", secondsPerTick = 0.5,
  onReset, testid = "time-transport", children,
}) {
  const railRef = useRef(null);
  const raf = useRef(0);
  const last = useRef(0);

  /* Playback advances the clock in simulated hours per real second, so the
   * speed multiplier means the same thing here as on the replay page. */
  useEffect(() => {
    if (!playing || !domain) return undefined;
    last.current = performance.now();
    const loop = (t) => {
      const dt = (t - last.current) / 1000;
      last.current = t;
      onChange((v) => {
        const next = (v ?? domain[0]) + dt * speed * secondsPerTick * 3.6e6;
        if (next >= domain[1]) { onPlaying(false); return domain[1]; }
        return next;
      });
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, speed, domain, onChange, onPlaying, secondsPerTick]);

  const scrub = useCallback((e) => {
    const el = railRef.current;
    if (!el || !domain) return;
    const r = el.getBoundingClientRect();
    const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    onPlaying?.(false);
    onChange(domain[0] + f * (domain[1] - domain[0]));
  }, [domain, onChange, onPlaying]);

  const drag = useCallback((e) => {
    scrub(e);
    const move = (ev) => scrub(ev);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [scrub]);

  /* Ticks are spaced by the rail's MEASURED width, not by a fixed hour step:
   * a 48 h domain at 6 h steps put nine labels in 400 px and they collided
   * into unreadable mush. ~78 px per label is the point at which "−12h" stops
   * touching its neighbour. */
  const [railW, setRailW] = useState(640);
  useEffect(() => {
    const el = railRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(([e]) => setRailW(e.contentRect.width || 640));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const ticks = useMemo(() => {
    if (!domain || !now) return [];
    const spanH = (domain[1] - domain[0]) / 3.6e6;
    const maxLabels = Math.max(3, Math.floor(railW / 78));
    const STEPS_H = [1, 2, 3, 6, 12, 24, 48, 72, 168];
    const stepH = STEPS_H.find((h) => spanH / h <= maxLabels) ?? 168;
    const step = stepH * 3.6e6;
    const out = [];
    // Anchored on NOW so the NOW tick always lands exactly on the now marker.
    for (let t = now - Math.ceil((now - domain[0]) / step) * step; t <= domain[1]; t += step) {
      if (t < domain[0]) continue;
      const dh = Math.round((t - now) / 3.6e6);
      out.push({ t, label: dh === 0 ? "NOW" : `${dh > 0 ? "+" : "−"}${Math.abs(dh)}h` });
    }
    return out;
  }, [domain, now, railW]);

  if (!domain) return null;
  const [d0, d1] = domain;
  const v = value ?? now ?? d0;
  const frac = Math.max(0, Math.min(1, (v - d0) / (d1 - d0)));
  const nowFrac = now != null ? Math.max(0, Math.min(1, (now - d0) / (d1 - d0))) : null;

  return (
    <div className={`gc-transport map-panel ${className}`} data-testid={testid}>
      <div className="gc-transport-controls">
        <button className="gc-tbtn" onClick={() => onStep?.(-1)} title="Step back" data-testid="transport-back">
          <SkipBack size={13} />
        </button>
        <button className="gc-tbtn gc-tbtn-play" onClick={() => onPlaying(!playing)}
          title={playing ? "Pause" : "Play"} data-testid="transport-play"
          aria-label={playing ? "Pause" : "Play"}>
          {playing ? <Pause size={16} /> : <Play size={16} />}
        </button>
        <button className="gc-tbtn" onClick={() => onStep?.(1)} title="Step forward" data-testid="transport-forward">
          <SkipForward size={13} />
        </button>
        {onReset && (
          <button className="gc-tbtn" onClick={onReset} title="Back to now" data-testid="transport-reset">
            <RotateCcw size={12} />
          </button>
        )}
      </div>

      <div className="gc-transport-clock">
        <div className="gc-clock-date mono">{new Date(v).toISOString().slice(0, 10)}</div>
        <div className="gc-clock-time mono">{new Date(v).toISOString().slice(11, 16)} UTC</div>
      </div>

      <div className="gc-rail-wrap">
        <div className="gc-rail" ref={railRef} onPointerDown={drag} data-testid="transport-rail"
          role="slider" aria-label={label} aria-valuemin={d0} aria-valuemax={d1} aria-valuenow={v}
          tabIndex={0}
          onKeyDown={(e) => {
            const step = (d1 - d0) / 48;
            if (e.key === "ArrowLeft") { e.preventDefault(); onChange(Math.max(d0, v - step)); }
            if (e.key === "ArrowRight") { e.preventDefault(); onChange(Math.min(d1, v + step)); }
          }}>
          <div className="gc-rail-line" />
          <div className="gc-rail-past" style={{ width: `${frac * 100}%` }} />
          {nowFrac != null && <div className="gc-rail-now" style={{ left: `${nowFrac * 100}%` }} />}
          {events.map((ev, i) => {
            const f = (ev.t - d0) / (d1 - d0);
            if (f < 0 || f > 1) return null;
            return (
              <button key={`${ev.t}-${i}`} className={`gc-rail-event ${ev.tone || ""}`}
                style={{ left: `${f * 100}%` }} title={ev.label}
                onClick={(e) => { e.stopPropagation(); onPlaying?.(false); onChange(ev.t); }} />
            );
          })}
          {ticks.map((t) => (
            <span key={t.t} className={`gc-rail-tick ${t.label === "NOW" ? "now" : ""}`}
              style={{ left: `${((t.t - d0) / (d1 - d0)) * 100}%` }}>
              <i /><em className="mono">{t.label}</em>
            </span>
          ))}
          <div className="gc-rail-head" style={{ left: `${frac * 100}%` }} />
        </div>
      </div>

      {onSpeed && (
        <div className="gc-speed">
          {SPEEDS.map((s) => (
            <button key={s} className={`gc-speed-b mono ${speed === s ? "on" : ""}`}
              onClick={() => onSpeed(s)} data-testid={`speed-${s}`}>{s}×</button>
          ))}
        </div>
      )}
      {children}
    </div>
  );
}

/** The vertical tool rail on the right edge of a map surface. `tools` is
 *  [{id, icon, label, badge, disabled}]. */
export function ToolRail({ tools, value, onChange, className = "", testid = "tool-rail" }) {
  return (
    <div className={`gc-toolrail ${className}`} data-testid={testid} role="toolbar" aria-orientation="vertical">
      {tools.map((t) => (
        <button key={t.id} className={`gc-tool ${value === t.id ? "on" : ""}`}
          onClick={() => onChange(t.id)} title={t.label} aria-label={t.label}
          aria-pressed={value === t.id} disabled={t.disabled}
          data-testid={`tool-${t.id}`}>
          {t.icon}
          {t.badge ? <span className="gc-tool-badge">{t.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}
