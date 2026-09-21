/* GlobeScene -- the planet the situational globe and the replay globe draw on.
 *
 * This used to BE the planet: a deck.gl `_GlobeView` with a flat-shaded sphere,
 * a 0.2-degree land mask and a graticule built in JS. It could not show a
 * country, a label, imagery or a SAR raster, because a deck globe cannot host
 * map tiles. It is now a thin adapter over components/maps/MaritimeGlobe -- the
 * one map engine (MapLibre GL globe + deck overlay) -- and keeps its old props
 * so the four surfaces that mount it moved engines without being rewritten.
 * New code mounts MaritimeGlobe directly.
 *
 * What did not come across, on purpose: the cursor parallax. It nudged the
 * planet on every animation frame (a React state update per frame on every
 * page with a globe) to make the Earth drift under a still mouse. The Earth
 * is anchored now. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useLocation, useNavigate } from "react-router-dom";

import MaritimeGlobe from "../maps/MaritimeGlobe";
import { DEFAULT_BASEMAP } from "../maps/basemaps";
import { decodeCamera, encodeCamera } from "../maps/camera";

/* The bundled coastline geometry, for callers that test points against land
 * (lib/geovalidate). Natural Earth countries: Polygon / MultiPolygon features. */
const LAND_URL = "/geo/ne/countries.json";
let landPromise = null;
function loadLand() {
  if (!landPromise) {
    landPromise = fetch(LAND_URL)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
  }
  return landPromise;
}

export function useLandGeometry() {
  const [land, setLand] = useState(null);
  useEffect(() => {
    let alive = true;
    loadLand().then((g) => { if (alive) setLand(g); });
    return () => { alive = false; };
  }, []);
  return land;
}

export const GLOBE_DEFAULT_VIEW = {
  longitude: 88.0,      // the Bay of Bengal, the operational theatre
  latitude: 13.0,
  zoom: 3.1,
  minZoom: 0.8,
  maxZoom: 17,
};

/* ---------------------------------------------------------------- camera --- */

/** The globe camera in the shape the pages were written against.
 *
 *  `viewState` is what the map last reported (tagged `__echo`), or a command:
 *  `flyTo` / `zoomBy` / `setBase` produce an untagged view, and GlobeScene
 *  moves the map to it. An echo is never re-applied, so a report from the
 *  middle of a flight cannot cancel the flight. */
export function useGlobeCamera(initial = GLOBE_DEFAULT_VIEW, { url = false } = {}) {
  // `url: true` keeps the view in `?c=lon,lat,zoom`: a link carries the camera
  // and a refresh restores it. Written when a move ENDS, with replace.
  const location = useLocation();
  const navigate = useNavigate();
  const loc = useRef(location);
  loc.current = location;
  const [base, setBaseState] = useState(() => {
    const fromUrl = url ? decodeCamera(new URLSearchParams(location.search).get("c")) : null;
    return fromUrl ? { ...initial, ...fromUrl } : initial;
  });

  const onViewStateChange = useCallback(({ viewState: v, end }) => {
    setBaseState((b) => ({ ...b, ...v, __echo: true, transitionDuration: undefined }));
    if (!url || !end) return;
    const n = new URLSearchParams(loc.current.search);
    const enc = encodeCamera(v);
    if (n.get("c") === enc) return;
    n.set("c", enc);
    navigate(`${loc.current.pathname}?${n.toString()}${loc.current.hash}`, { replace: true });
  }, [url, navigate]);

  const command = useCallback((next) => {
    setBaseState((b) => {
      const n = typeof next === "function" ? next(b) : next;
      const { __echo, ...rest } = n;
      return { ...rest, __cmd: (b.__cmd || 0) + 1 };
    });
  }, []);

  const flyTo = useCallback((to, ms = 1200) => {
    command((b) => ({ ...b, ...to, transitionDuration: ms }));
  }, [command]);

  const zoomBy = useCallback((delta) => {
    command((b) => ({
      ...b, transitionDuration: 320,
      zoom: Math.max(b.minZoom ?? 0.8, Math.min(b.maxZoom ?? 17, (b.zoom ?? 3) + delta)),
    }));
  }, [command]);

  const noop = useCallback(() => {}, []);
  return { viewState: base, base, setBase: command, onViewStateChange, onPointerMove: noop,
    onPointerLeave: noop, flyTo, zoomBy };
}

/* ---------------------------------------------------------------- scene ---- */

/* The old basemap ids were three restyles of one land mask. */
const LEGACY_BASEMAP = { canvas: "geopolitical", relief: "geopolitical", none: "dark",
  imagery: "satellite", ocean: "dark", light: "geopolitical" };

/**
 * @param {object}   props.viewState        from useGlobeCamera
 * @param {function} props.onViewStateChange
 * @param {string}   props.basemap          "geopolitical" | "satellite" | "dark"
 * @param {string}   props.theme            "dark" | "light"
 * @param {Array}    props.layers           deck.gl data layers
 * @param {boolean}  props.graticule
 * @param {function} props.onHover / onClick   deck picking info (`coordinate` set anywhere on the planet)
 * @param {function} props.getCursor
 */
export default function GlobeScene({
  viewState, onViewStateChange, basemap = DEFAULT_BASEMAP, theme = "dark", layers = [],
  graticule = true, onHover, onClick, getCursor, children, controller = true, testid = "globe",
  sar = null,
}) {
  const globe = useRef(null);
  const initial = useRef({ longitude: viewState?.longitude ?? GLOBE_DEFAULT_VIEW.longitude,
    latitude: viewState?.latitude ?? GLOBE_DEFAULT_VIEW.latitude, zoom: viewState?.zoom ?? GLOBE_DEFAULT_VIEW.zoom });
  const [hoverObj, setHoverObj] = useState(false);

  // Commands move the map; echoes do not.
  const seen = useRef(viewState?.__cmd);
  useEffect(() => {
    if (!viewState || viewState.__echo || viewState.__cmd === seen.current) return;
    seen.current = viewState.__cmd;
    globe.current?.flyTo(viewState, viewState.transitionDuration ?? 0);
  }, [viewState]);

  const handleCamera = useCallback((c, info) => {
    onViewStateChange?.({ viewState: c, interactionState: {}, end: Boolean(info?.end) });
  }, [onViewStateChange]);

  const handleHover = useCallback((info) => { setHoverObj(Boolean(info?.object)); onHover?.(info); }, [onHover]);
  const cursor = useMemo(
    () => (getCursor ? getCursor({ isDragging: false, isHovering: hoverObj }) : undefined),
    [getCursor, hoverObj]);

  return (
    <div className={`globe-wrap globe-${theme === "light" ? "light" : "dark"}`}>
      <MaritimeGlobe ref={globe} testid={testid}
        basemap={LEGACY_BASEMAP[basemap] || basemap} theme={theme === "light" ? "light" : "dark"}
        layers={layers} sar={sar} graticule={graticule} initialCamera={initial.current}
        onCameraChange={handleCamera} onClick={onClick} onHover={handleHover}
        cursor={cursor} interactive={controller !== false}>
        {children}
      </MaritimeGlobe>
    </div>
  );
}
