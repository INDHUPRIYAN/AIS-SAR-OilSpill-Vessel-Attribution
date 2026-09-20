/* Compact map chrome for the workspace: zoom, reset, fullscreen, the basemap
 * selector, a 3D/2D switch, the cursor readout, a scale bar and a north
 * indicator. Nothing here holds data: every control changes the view or the
 * surface, and every readout is computed from the live viewport.
 *
 * Frames 02-15 place the +/− stack top-left under the basemap select, the
 * fullscreen button top-right, the scale bar bottom-right above the
 * coordinate readout, and the compass top-right when the map is rotated. */

import { useState } from "react";
import {
  ChevronDown, Crosshair, Globe2, Layers, Maximize2, Minimize2, Minus, Plus,
} from "lucide-react";

import { BASEMAPS } from "./WorkspaceMap";

/** Metres per screen pixel at a latitude and zoom (Web Mercator). */
export function metresPerPixel(lat, zoom) {
  return (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** (zoom ?? 0);
}

/** A round scale-bar length in km for a bar of about `px` pixels. */
export function scaleBar(lat, zoom, px = 180) {
  const mpp = metresPerPixel(lat, zoom);
  if (!Number.isFinite(mpp) || mpp <= 0) return null;
  const targetKm = (mpp * px) / 1000;
  const steps = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000];
  let km = steps[0];
  for (const s of steps) if (s <= targetKm) km = s;
  const width = (km * 1000) / mpp;
  const ticks = [0, km / 2, km].map((v, i) => ({
    at: (v / km) * 100,
    label: i === 2 ? `${v.toLocaleString()} km` : v.toLocaleString(),
  }));
  return { km, width, ticks };
}

export function fmtLat(v) {
  return v == null ? "—" : `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "N" : "S"}`;
}
export function fmtLon(v) {
  return v == null ? "—" : `${Math.abs(v).toFixed(4)}° ${v >= 0 ? "E" : "W"}`;
}

export function BasemapSelect({ value, onChange, disabled, testid = "basemap-select" }) {
  const [open, setOpen] = useState(false);
  const current = BASEMAPS.find((b) => b.id === value) || BASEMAPS[0];
  return (
    <div className="mc-basemap" data-testid={testid}>
      <button className="mc-basemap-btn" onClick={() => setOpen((o) => !o)} disabled={disabled}
        aria-haspopup="listbox" aria-expanded={open}>
        <Layers size={14} />
        <span>{current.label}</span>
        <ChevronDown size={13} />
      </button>
      {open && (
        <div className="mc-basemap-menu" role="listbox">
          {BASEMAPS.map((b) => (
            <button key={b.id} role="option" aria-selected={b.id === value}
              className={`mc-basemap-item ${b.id === value ? "on" : ""}`}
              onClick={() => { onChange(b.id); setOpen(false); }}
              data-testid={`basemap-${b.id}`}>
              {b.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function MapChrome({
  view, onZoom, onReset, fullscreen, onFullscreen, basemap, onBasemap,
  surface, onSurface, cursor, layersOpen, onLayers, showBasemap = true,
}) {
  const bar = view ? scaleBar(view.latitude ?? 0, view.zoom ?? 0) : null;
  const bearing = view?.bearing ?? 0;
  return (
    <>
      {/* top-left: basemap + zoom */}
      <div className="mc-topleft">
        <div className="mc-stack">
          <button className="mc-btn" onClick={() => onZoom(1)} title="Zoom in" data-testid="map-zoom-in"><Plus size={16} /></button>
          <button className="mc-btn" onClick={() => onZoom(-1)} title="Zoom out" data-testid="map-zoom-out"><Minus size={16} /></button>
          <button className="mc-btn" onClick={onReset} title="Reset the view to the scene" data-testid="map-reset"><Crosshair size={15} /></button>
        </div>
        {showBasemap && <BasemapSelect value={basemap} onChange={onBasemap} />}
      </div>

      {/* top-right: north + fullscreen */}
      <div className="mc-topright">
        {Math.abs(bearing) > 0.5 && (
          <button className="mc-compass" title="Reset north" onClick={onReset}
            style={{ transform: `rotate(${-bearing}deg)` }}>
            <span className="mc-compass-n">N</span>
            <span className="mc-compass-needle" />
          </button>
        )}
        <button className="mc-btn" onClick={onFullscreen} title={fullscreen ? "Exit full screen" : "Full screen"}
          data-testid="map-fullscreen">
          {fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
      </div>

      {/* bottom-right: surface switch, scale bar, coordinates */}
      <div className="mc-bottomright">
        {onSurface && (
          <div className="mc-seg" data-testid="surface-switch">
            <button className={`mc-seg-b ${surface === "globe" ? "on" : ""}`} onClick={() => onSurface("globe")}
              title="3D globe" data-testid="surface-globe"><Globe2 size={12} /> 3D</button>
            <button className={`mc-seg-b ${surface === "map" ? "on" : ""}`} onClick={() => onSurface("map")}
              title="2D map" data-testid="surface-map">2D</button>
          </div>
        )}
        {onLayers && (
          <button className={`mc-btn mc-layers ${layersOpen ? "on" : ""}`} onClick={onLayers}
            title="Layers" data-testid="map-layers-toggle"><Layers size={14} /></button>
        )}
        {bar && (
          <div className="mc-scale" data-testid="scale-bar" title={`${bar.km} km`}>
            <div className="mc-scale-labels">
              {bar.ticks.map((t, i) => <span key={t.at} className={i === 2 ? "last" : ""} style={{ left: `${t.at}%` }}>{t.label}</span>)}
            </div>
            <div className="mc-scale-bar" style={{ width: bar.width }}>
              {bar.ticks.map((t) => <i key={t.at} style={{ left: `${t.at}%` }} />)}
            </div>
          </div>
        )}
        <div className="mc-coords mono" data-testid="cursor-coords">
          <span>Lat: {fmtLat(cursor?.lat)}</span>
          <span>Lon: {fmtLon(cursor?.lon)}</span>
        </div>
      </div>
    </>
  );
}
