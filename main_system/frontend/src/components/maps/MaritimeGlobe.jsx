// @ts-check
/* MaritimeGlobe -- the one map.
 *
 * A MapLibre GL map in globe projection with deck.gl data layers drawn into
 * it (MapboxOverlay, interleaved so the planet occludes what is behind it).
 * World, region and SAR scene are the same surface and the same camera:
 * MapLibre morphs the globe into mercator as the view closes in, so there is
 * no second map to cross-fade to and nothing to keep in sync.
 *
 * Every page that shows geography mounts this component. Basemap, data
 * layers, the SAR raster and the camera are props; the engine is not.
 * Architecture and the spike that chose it: docs/ux/GLOBE_ARCHITECTURE.md. */

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { AttributionControl, Map as MapGL, useControl } from "react-map-gl/maplibre";
import { MapboxOverlay } from "@deck.gl/mapbox";
import "maplibre-gl/dist/maplibre-gl.css";

import { LABELS_ANCHOR, MAP_CONFIG, buildStyle, resolveBasemap } from "./basemaps";
import { WORLD_VIEW, zoomForBbox } from "./camera";
import "./maps.css";

/* The SAR tile server reads the full-resolution raster for every tile and has
 * no overviews (measured: ~30 s per tile at z8, ~2 s at z10), and MapLibre's
 * default of 16 parallel image requests exhausted the API's connection pool.
 * See BACKEND_GAPS G15. */
maplibregl.config.MAX_PARALLEL_IMAGE_REQUESTS = 4;
export const SAR_TILE_MIN_ZOOM = 10;

/* Globe when the view is of the planet, flat chart when it is of a region.
 * While MapLibre reports a globe projection, deck draws through its globe
 * view at EVERY zoom, and that view cannot draw billboard text, dashed paths
 * or trips -- the workspace's callouts, AIS gaps and drift trail. So the
 * projection follows the zoom, with hysteresis so a view hovering at the
 * threshold does not flip back and forth. */
const FLAT_AT = 5.0;
const GLOBE_AT = 4.4;
/** @param {number} zoom @param {"globe"|"mercator"} current @returns {"globe"|"mercator"} */
export const projectionFor = (zoom, current) => (
  current === "mercator" ? (zoom < GLOBE_AT ? "globe" : "mercator") : (zoom >= FLAT_AT ? "mercator" : "globe"));

/** The run's quicklook. One URL for the whole app: the map and the analysis
 *  panels show the same image, so it is fetched once and served from cache
 *  rather than rendered three times from a 600 Mpx raster (BACKEND_GAPS G15). */
export const quicklookUrl = (/** @type {string} */ runId) =>
  `/api/runs/${encodeURIComponent(runId)}/scene_png?size=1024`;

/** @typedef {import("./camera").Camera} Camera */
/** @typedef {{runId: string, bbox: number[], opacity?: number, visible?: boolean}} SarSpec */

/** Add the SAR raster to a style: the run quicklook below z10, the tile
 *  pyramid from z10, both under the labels.
 *  @param {any} style @param {SarSpec|null|undefined} sar @param {string} origin */
export function withSar(style, sar, origin) {
  if (!sar?.runId || !Array.isArray(sar.bbox) || sar.bbox.length !== 4 || sar.visible === false) return style;
  const [w, s, e, n] = sar.bbox;
  const opacity = sar.opacity ?? 1;
  const at = style.layers.findIndex((/** @type {any} */ l) => l.id === LABELS_ANCHOR);
  const layers = [...style.layers];
  layers.splice(at, 0,
    { id: "ot-sar-quick", type: "raster", source: "sar-quick", maxzoom: SAR_TILE_MIN_ZOOM,
      paint: { "raster-opacity": opacity, "raster-fade-duration": 200 } },
    { id: "ot-sar-tiles", type: "raster", source: "sar-tiles", minzoom: SAR_TILE_MIN_ZOOM,
      paint: { "raster-opacity": opacity, "raster-fade-duration": 200 } });
  return {
    ...style,
    sources: {
      ...style.sources,
      "sar-quick": { type: "image", url: `${origin}${quicklookUrl(sar.runId)}`,
        coordinates: [[w, n], [e, n], [e, s], [w, s]] },
      "sar-tiles": { type: "raster", tileSize: 256, bounds: sar.bbox, minzoom: SAR_TILE_MIN_ZOOM, maxzoom: 16,
        tiles: [`${origin}/api/tiles/${encodeURIComponent(sar.runId)}/{z}/{x}/{y}.png`] },
    },
    layers,
  };
}

/* OVERLAID, not interleaved. Interleaved draws deck's layers into MapLibre's
 * own depth buffer, where everything at sea level fights the globe surface for
 * the same depth: fills break into specks, text disappears, glow trails thin
 * to nothing (seen side by side against the pre-refactor workspace). Overlaid
 * gives deck its own canvas above the map, registered to the same camera
 * (0.0 px in both projections -- GLOBE_ARCHITECTURE.md section 2).
 * @param {{layers: any[], onReady: (o: MapboxOverlay) => void}} props */
function DeckOverlay({ layers, onReady }) {
  const overlay = /** @type {MapboxOverlay} */ (/** @type {unknown} */ (
    useControl(() => /** @type {any} */ (new MapboxOverlay({ interleaved: false, layers: [] })))));
  useEffect(() => { onReady(overlay); }, [overlay, onReady]);
  overlay.setProps({ layers });
  return null;
}

/**
 * @typedef {object} GlobeProps
 * @property {string} [basemap]            "geopolitical" | "satellite" | "dark"
 * @property {"dark"|"light"} [theme]
 * @property {any[]} [layers]              deck.gl layers, drawn above the basemap
 * @property {SarSpec|null} [sar]          a run's SAR raster
 * @property {Camera} [initialCamera]
 * @property {(c: Camera, info: {end: boolean}) => void} [onCameraChange]
 * @property {(info: any) => void} [onClick]   deck-style picking info; `coordinate` is always set on the planet
 * @property {(info: any) => void} [onHover]
 * @property {boolean} [graticule]
 * @property {{countries?: boolean, coastlines?: boolean, labels?: boolean}} [show]
 * @property {boolean} [interactive]
 * @property {string} [cursor]
 * @property {string} [testid]
 * @property {any} [children]
 */

const MaritimeGlobe = forwardRef(function MaritimeGlobe(/** @type {GlobeProps} */ props, ref) {
  const {
    basemap = "geopolitical", theme = "dark", layers = [], sar = null, initialCamera = WORLD_VIEW,
    onCameraChange, onClick, onHover, graticule = true, show, interactive = true, cursor,
    testid = "maritime-globe", children,
  } = props;

  /** @type {import("react").MutableRefObject<any>} */
  const mapRef = useRef(null);
  /** @type {import("react").MutableRefObject<MapboxOverlay|null>} */
  const overlayRef = useRef(null);
  /** @type {import("react").MutableRefObject<HTMLDivElement|null>} */
  const wrapRef = useRef(null);
  /** A flight asked for before the map loaded (a deep link's first act). */
  const pending = useRef(/** @type {null | [Partial<Camera>, number]} */ (null));
  const ready = useRef(false);
  const pendingFit = useRef(/** @type {null | [number[], number, any]} */ (null));
  const [hovering, setHovering] = useState(false);
  const [sarState, setSarState] = useState(/** @type {"idle"|"loading"|"ready"|"error"} */ ("idle"));
  const [view, setView] = useState(/** @type {{zoom: number, projection: "globe"|"mercator"}} */ ({
    zoom: initialCamera.zoom, projection: projectionFor(initialCamera.zoom, "globe") }));

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const resolved = resolveBasemap(basemap, MAP_CONFIG);
  const showKey = JSON.stringify(show || {});
  const sarKey = sar && sar.visible !== false ? `${sar.runId}|${sar.bbox?.join(",")}` : "";
  const style = useMemo(
    () => withSar(buildStyle({ basemap, theme, graticule, show, origin, projection: view.projection }), sar, origin),
    // `show` and `sar` are compared by value: callers pass fresh literals.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [basemap, theme, graticule, showKey, sarKey, origin, view.projection]);

  const cameraOf = () => {
    const m = mapRef.current?.getMap?.();
    if (!m) return null;
    const c = m.getCenter();
    return { longitude: c.lng, latitude: c.lat, zoom: m.getZoom(), bearing: m.getBearing(), pitch: m.getPitch() };
  };

  const api = useMemo(() => ({
    getMap: () => mapRef.current?.getMap?.() || null,
    getCamera: cameraOf,
    /** @param {Partial<Camera>} to @param {number} [ms] */
    flyTo: (to, ms = 1400) => {
      const m = mapRef.current?.getMap?.();
      // `ready` is the load event, not `m.loaded()`: that one also goes false
      // whenever tiles are in flight, which would swallow flights mid-session.
      if (!m || !ready.current) { pending.current = [to, ms]; return; }
      const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      const opts = { center: /** @type {[number, number]} */ ([to.longitude ?? m.getCenter().lng, to.latitude ?? m.getCenter().lat]),
        zoom: to.zoom ?? m.getZoom(), bearing: to.bearing ?? m.getBearing(), pitch: to.pitch ?? m.getPitch() };
      if (reduce || ms <= 0) m.jumpTo(opts);
      // `curve` near 1 keeps the flight low: the Earth stays anchored rather
      // than the camera looping out to orbit and back.
      else m.flyTo({ ...opts, duration: ms, curve: 1.1, essential: true });
    },
    /** Frame a [w, s, e, n] box. The map does the fitting: it knows its real
     *  size, and a caller computing a zoom before the map exists gets it wrong.
     *  @param {number[]} bbox @param {number} [ms] @param {{padding?: number, maxZoom?: number}} [o] */
    fitBounds: (bbox, ms = 1400, o = {}) => {
      const m = mapRef.current?.getMap?.();
      if (!m || !ready.current) { pendingFit.current = [bbox, ms, o]; return; }
      const el = m.getContainer();
      const cam = m.cameraForBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: o.padding ?? 70 });
      const zoom = Math.min(cam?.zoom ?? zoomForBbox(bbox, el.clientWidth, el.clientHeight), o.maxZoom ?? 16);
      api.flyTo({ longitude: (bbox[0] + bbox[2]) / 2, latitude: (bbox[1] + bbox[3]) / 2, zoom, bearing: 0, pitch: 0 }, ms);
    },
    /** @param {number} d */
    zoomBy: (d) => mapRef.current?.getMap?.()?.easeTo({ zoom: (mapRef.current.getMap().getZoom() || 0) + d, duration: 300 }),
    resetNorth: () => mapRef.current?.getMap?.()?.easeTo({ bearing: 0, pitch: 0, duration: 400 }),
    /** @param {[number, number]} lngLat */
    project: (lngLat) => { const p = mapRef.current?.getMap?.()?.project(lngLat); return p ? [p.x, p.y] : null; },
    /** @param {number} x @param {number} y */
    pick: (x, y) => overlayRef.current?.pickObject({ x, y, radius: 5 }) || null,
  }), []);
  useImperativeHandle(ref, () => api, [api]);

  /* Track the container every frame it changes size. The workspace collapses
   * its panels over a 260 ms grid transition, and a map that only resizes when
   * the transition ends leaves a band of stale pixels beside the canvas for a
   * quarter of a second. */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => mapRef.current?.getMap?.()?.resize());
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Reachable from the element, so a browser test can ask the map what it drew.
  useEffect(() => { if (wrapRef.current) /** @type {any} */ (wrapRef.current).__globe = api; }, [api]);

  /* deck delivers a pick to the LAYER's own onClick/onHover; picking by hand
   * here would silently retire every handler the layers already carry, so the
   * dispatch is reproduced. A layer returning true has handled the event. */
  const toLayer = (/** @type {string} */ kind, /** @type {any} */ info) =>
    Boolean(info.layer?.props?.[kind]?.(info));

  /** @param {any} e */
  const infoAt = (e) => {
    const picked = overlayRef.current?.pickObject({ x: e.point.x, y: e.point.y, radius: 5 });
    return { ...(picked || {}), object: picked?.object ?? null, layer: picked?.layer ?? null,
      x: e.point.x, y: e.point.y, coordinate: [e.lngLat.lng, e.lngLat.lat] };
  };

  const hoverFrame = useRef(0);
  /** @param {any} e */
  const handleMove = (e) => {
    if (hoverFrame.current) return;
    hoverFrame.current = requestAnimationFrame(() => {
      hoverFrame.current = 0;
      const info = infoAt(e);
      setHovering(Boolean(info.object));
      if (!toLayer("onHover", info)) onHover?.(info);
    });
  };
  useEffect(() => () => cancelAnimationFrame(hoverFrame.current), []);

  /* The camera is usable as soon as the map object exists and its style has
   * arrived. It must NOT wait for MapLibre's `load`: that event waits for every
   * source, and the SAR quicklook of a large scene takes ~20 s to render, so a
   * deep link to the detection stage sat at the default view for 20 seconds. */
  const flushPending = () => {
    if (!mapRef.current?.getMap?.()) return;
    ready.current = true;
    const p = pending.current; pending.current = null;
    const f = pendingFit.current; pendingFit.current = null;
    if (f) api.fitBounds(f[0], Math.min(f[1], 900), f[2]); else if (p) api.flyTo(p[0], Math.min(p[1], 900));
  };

  /** @param {boolean} end */
  const report = (end) => {
    const c = cameraOf();
    if (!c) return;
    // Decided when a move ENDS, never during one: changing projection swaps
    // the style, and a style swap mid-flight aborts the flight (a flight from
    // the default view to the Gulf of Mexico stopped over Mozambique).
    if (end) setView((v) => ({ zoom: c.zoom, projection: projectionFor(c.zoom, v.projection) }));
    onCameraChange?.(c, { end });
  };
  const lastMove = useRef(0);

  /* The presentation fades the SAR raster in over a couple of seconds. That is
   * a paint property: rebuilding the style for it would drop and re-add the
   * image source -- and re-fetch a scene that takes tens of seconds to render
   * -- sixty times a second. */
  const sarOpacity = sar && sar.visible !== false ? (sar.opacity ?? 1) : 1;
  useEffect(() => {
    const m = mapRef.current?.getMap?.();
    if (!m || !sarKey) return;
    for (const id of ["ot-sar-quick", "ot-sar-tiles"]) {
      if (m.getLayer(id)) m.setPaintProperty(id, "raster-opacity", sarOpacity);
    }
  }, [sarOpacity, sarKey]);

  // SAR quicklook state: it is slow on large scenes, and slow must not look broken.
  useEffect(() => {
    if (!sarKey) { setSarState("idle"); return undefined; }
    setSarState("loading");
    const m = mapRef.current?.getMap?.();
    if (!m) return undefined;
    const onData = (/** @type {any} */ e) => {
      if (e.sourceId === "sar-quick" && m.isSourceLoaded("sar-quick")) setSarState("ready");
    };
    const onError = (/** @type {any} */ e) => { if (e.sourceId === "sar-quick") setSarState("error"); };
    m.on("sourcedata", onData); m.on("error", onError);
    return () => { m.off("sourcedata", onData); m.off("error", onError); };
  }, [sarKey]);

  return (
    <div ref={wrapRef} className="mg" data-testid={testid} data-basemap={resolved.id}
      data-projection={view.projection} data-zoom={view.zoom.toFixed(2)} data-sar={sarState}
      style={{ background: style.metadata["ot:space"] }}>
      <MapGL
        ref={mapRef}
        initialViewState={initialCamera}
        mapStyle={style}
        minZoom={0.8} maxZoom={17}
        attributionControl={false}
        interactive={interactive}
        dragRotate={interactive} touchPitch={false}
        doubleClickZoom={false}
        renderWorldCopies={false}
        pixelRatio={Math.min(typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1, 2)}
        cursor={cursor || (hovering ? "pointer" : "grab")}
        onMove={() => { const now = performance.now(); if (now - lastMove.current > 120) { lastMove.current = now; report(false); } }}
        onMoveEnd={() => report(true)}
        onStyleData={flushPending}
        onLoad={() => { flushPending(); report(true); }}
        onClick={(e) => { const info = infoAt(e); if (!toLayer("onClick", info)) onClick?.(info); }}
        onMouseMove={handleMove}
        onMouseOut={() => { setHovering(false); onHover?.({ object: null, layer: null, coordinate: undefined }); }}
      >
        <DeckOverlay layers={layers} onReady={(o) => { overlayRef.current = o; }} />
        <AttributionControl compact position="bottom-right" />
      </MapGL>
      {resolved.fellBack && (
        <div className="mg-note" role="status" data-testid="basemap-note">
          Satellite basemap not configured — showing the vector basemap
        </div>
      )}
      {/* Only while the quicklook is what the view needs: from z10 the tile
          pyramid draws the scene and the quicklook is irrelevant. */}
      {sarState === "loading" && view.zoom < SAR_TILE_MIN_ZOOM && (
        <div className="mg-note mg-note-busy" role="status" data-testid="sar-note">
          <span className="spinner" /> Loading SAR scene…
        </div>
      )}
      {sarState === "error" && (
        <div className="mg-note mg-note-err" role="status" data-testid="sar-note">SAR scene raster unavailable</div>
      )}
      {children}
    </div>
  );
});

export default MaritimeGlobe;
