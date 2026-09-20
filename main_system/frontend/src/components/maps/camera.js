// @ts-check
/* The map camera, and its place in the URL.
 *
 * `?c=lon,lat,zoom` (plus `,bearing,pitch` when tilted) so a link carries the
 * view and a refresh restores it. Written on move END with `replace`, never
 * during a drag: the address bar is not an animation target and the history
 * stack must not fill with camera positions. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

/** @typedef {{longitude: number, latitude: number, zoom: number, bearing?: number, pitch?: number}} Camera */

export const WORLD_VIEW = { longitude: 78, latitude: 14, zoom: 1.6 };

/** @param {Camera} c */
export function encodeCamera(c) {
  const parts = [c.longitude.toFixed(4), c.latitude.toFixed(4), c.zoom.toFixed(2)];
  if (c.bearing || c.pitch) parts.push((c.bearing || 0).toFixed(1), (c.pitch || 0).toFixed(1));
  return parts.join(",");
}

/** @param {string|null|undefined} s @returns {Camera|null} */
export function decodeCamera(s) {
  if (!s) return null;
  const n = s.split(",").map(Number);
  if (n.length < 3 || n.slice(0, 3).some((v) => !Number.isFinite(v))) return null;
  const [longitude, latitude, zoom, bearing = 0, pitch = 0] = n;
  if (Math.abs(latitude) > 90 || Math.abs(longitude) > 360 || zoom < 0 || zoom > 24) return null;
  return { longitude, latitude, zoom, bearing: Number.isFinite(bearing) ? bearing : 0,
    pitch: Number.isFinite(pitch) ? pitch : 0 };
}

/** A zoom that frames a [w, s, e, n] box in a viewport, by the mercator rule
 *  (world = 512 * 2^z px). @param {number[]} bbox */
export function zoomForBbox(bbox, width = 1200, height = 700, padding = 0.25) {
  const [w, s, e, n] = bbox;
  const lonSpan = Math.max(1e-6, Math.abs(e - w));
  const y = (/** @type {number} */ lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
  const latSpan = Math.max(1e-6, Math.abs(y(n) - y(s)) * (180 / Math.PI));
  const zx = Math.log2((width * (1 - padding) * 360) / (512 * lonSpan));
  const zy = Math.log2((height * (1 - padding) * 360) / (512 * latSpan));
  return Math.max(0.5, Math.min(16, Math.min(zx, zy)));
}

/**
 * Camera state for a MaritimeGlobe.
 * @param {Camera} [initial]
 * @param {{url?: boolean, key?: string}} [opts]  `url: true` keeps the camera in `?c=`
 */
export function useMapCamera(initial = WORLD_VIEW, { url = false, key = "c" } = {}) {
  const location = useLocation();
  const navigate = useNavigate();
  const fromUrl = url ? decodeCamera(new URLSearchParams(location.search).get(key)) : null;
  const [camera, setCamera] = useState(/** @type {Camera} */ (fromUrl || initial));
  /** @type {import("react").MutableRefObject<any>} */
  const globe = useRef(null);
  const loc = useRef(location);
  loc.current = location;

  const onCameraChange = useCallback((/** @type {Camera} */ c, /** @type {{end?: boolean}} */ info = {}) => {
    setCamera(c);
    if (!url || !info.end) return;
    const n = new URLSearchParams(loc.current.search);
    const enc = encodeCamera(c);
    if (n.get(key) === enc) return;
    n.set(key, enc);
    navigate(`${loc.current.pathname}?${n.toString()}${loc.current.hash}`, { replace: true });
  }, [url, key, navigate]);

  // Back/forward (or a pasted link) changed `?c=` under us: follow it.
  const lastSeen = useRef(fromUrl ? encodeCamera(fromUrl) : null);
  useEffect(() => {
    if (!url) return;
    const raw = new URLSearchParams(location.search).get(key);
    if (!raw || raw === lastSeen.current) return;
    lastSeen.current = raw;
    const c = decodeCamera(raw);
    if (c && raw !== encodeCamera(camera)) globe.current?.flyTo(c, 600);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.search]);

  return useMemo(() => ({
    camera, globe, onCameraChange,
    /** @param {Partial<Camera>} to @param {number} [ms] */
    flyTo: (to, ms = 1400) => globe.current?.flyTo(to, ms),
    /** @param {number[]} bbox @param {number} [ms] */
    fitBounds: (bbox, ms = 1400) => globe.current?.fitBounds(bbox, ms),
    /** @param {number} d */
    zoomBy: (d) => globe.current?.zoomBy(d),
  }), [camera, onCameraChange]);
}
