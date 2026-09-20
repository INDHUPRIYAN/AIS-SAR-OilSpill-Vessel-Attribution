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
