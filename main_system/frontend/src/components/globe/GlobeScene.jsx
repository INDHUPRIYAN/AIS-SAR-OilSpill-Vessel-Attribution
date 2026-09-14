/* GlobeScene: the one 3D Earth every globe surface in OceanTrace draws on.
 *
 * The Global View, the incident replay, the workspace, the zone editor and the
 * vessel dossier all put different data on the same planet. This component
 * owns the planet -- the sphere that occludes the far hemisphere, the
 * atmosphere rim, the raster basemap, the graticule, the camera and the
 * cursor behaviour -- and takes the data layers as a prop. That keeps the
 * Earth genuinely round everywhere (deck.gl `_GlobeView`, never a flat map
 * pretending) and keeps the interaction identical from page to page.
 *
 * Interaction rules:
 *   - the globe never spins on its own. It responds to the cursor: a subtle
 *     parallax nudge while the pointer moves over it, inertia after a drag,
 *     and eased flights when the app moves the camera. An operator analysing
 *     a boundary needs a planet that stays where it was put.
 *   - the parallax is suspended while dragging, while a flight is in
 *     progress, and while an editor is active (a boundary vertex must land
 *     where the pointer is, not where the parallax moved the planet to).
 *
 * Everything drawn here is context (planet, tiles, grid). Data comes from the
 * caller and every value it plots came from an API response.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import {
  AmbientLight, COORDINATE_SYSTEM, LightingEffect, LinearInterpolator,
  _GlobeView as GlobeView,
} from "@deck.gl/core";
import { GeoJsonLayer, PathLayer } from "@deck.gl/layers";
import { SimpleMeshLayer } from "@deck.gl/mesh-layers";
import { SphereGeometry } from "@luma.gl/engine";

/* Mean Earth radius, metres. The value deck.gl's GlobeView itself uses; the
 * sphere must match it or the basemap floats above or sinks below the data. */
export const EARTH_RADIUS_M = 6370972;

/* LAND GEOMETRY, not raster tiles.
 *
 * `_GlobeView` cannot host a MapLibre vector style, and a raster basemap does
 * not survive the globe projection either: `BitmapLayer` reprojects each tile
 * against the viewport and on a sphere the tiles render as small skewed quads
 * instead of curving -- a lattice of diamonds over a bare planet. deck.gl's own
 * globe example draws land as vector geometry for exactly this reason.
 *
 * The file is generated offline by `scripts/build_globe_land.py` from the
 * `global_land_mask` raster this project already depends on, so no third-party
 * asset is vendored and nothing is fetched from another origin at runtime. It
 * is a COASTLINE for orientation: it carries no country identity and asserts
 * nothing about maritime jurisdiction. Operational zones are separate, come
 * from the server, and are drawn above it.
 */
export const LAND_URL = "/geo/land.json";

/* Fetched once per page load and shared by every globe surface. A module-level
 * promise rather than per-component state: the Operations overview and the
 * Global View would otherwise each pull the same 370 KB. */
let landPromise = null;
export function loadLand() {
  if (!landPromise) {
    landPromise = fetch(LAND_URL)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`land geometry ${r.status}`))))
      .catch((e) => { landPromise = null; throw e; });
  }
  return landPromise;
}

/** The land polygons, or null until they arrive. Never throws into render: a
 *  globe with no coastline is degraded, not broken, and the graticule plus the
 *  zone polygons still orient the operator. */
export function useLandGeometry() {
  const [land, setLand] = useState(null);
  useEffect(() => {
    let alive = true;
    loadLand().then((d) => { if (alive) setLand(d); }).catch(() => { if (alive) setLand(false); });
    return () => { alive = false; };
  }, []);
  return land;
}

/* Basemap styles. Each is a way of drawing the SAME land geometry, so
 * switching costs no download and cannot fail differently. */
export const BASEMAP_OPTIONS = [
  { id: "canvas", label: "Canvas" },
  { id: "relief", label: "Contrast" },
  { id: "none", label: "None" },
];

const LAND_STYLE = {
  dark: {
    canvas: { fill: [24, 38, 56, 255], line: [72, 104, 140, 210], width: 0.8 },
    relief: { fill: [42, 60, 82, 255], line: [120, 160, 200, 235], width: 1.1 },
  },
  light: {
    canvas: { fill: [240, 243, 247, 255], line: [125, 148, 172, 235], width: 0.9 },
    relief: { fill: [250, 251, 253, 255], line: [74, 100, 130, 255], width: 1.2 },
  },
};

/* Ocean colour of the bare sphere, per theme. */
const OCEAN = { dark: [9, 20, 34], light: [205, 224, 238] };
const ATMOSPHERE = { dark: [34, 195, 238], light: [3, 105, 161] };
const GRATICULE = {
  dark: { minor: [70, 90, 115, 80], major: [110, 135, 165, 130] },
  light: { minor: [100, 120, 145, 70], major: [70, 95, 125, 120] },
};

/* Built once at module scope: the grid never changes, and rebuilding ~50
 * paths on every render is pure waste on a surface that redraws while
 * dragging. */
function buildGraticule(stepDeg) {
  const out = [];
  for (let lon = -180; lon <= 180; lon += stepDeg) {
    const path = [];
    for (let lat = -80; lat <= 80; lat += 2) path.push([lon, lat]);
    out.push({ path, major: lon % 90 === 0 });
  }
  for (let lat = -80; lat <= 80; lat += stepDeg) {
    const path = [];
    for (let lon = -180; lon <= 180; lon += 2) path.push([lon, lat]);
    out.push({ path, major: lat === 0 });
  }
  return out;
}
const GRATICULE_15 = buildGraticule(15);
const GRATICULE_5 = buildGraticule(5);

export const GLOBE_DEFAULT_VIEW = {
  longitude: 88.0,      // the Bay of Bengal, the operational theatre
  latitude: 13.0,
  zoom: 3.1,
  minZoom: 0.4,
  maxZoom: 14,
};

/* Flat lighting for the whole scene.
 *
 * deck.gl's default rig is an ambient light plus two directional lights, which
 * shade the ocean sphere into a grey gradient running across the planet. That
 * reads as a rendering, not a chart, and in the light theme it turns the sea
 * murky. One ambient light at full intensity makes every lit layer render its
 * own flat colour, which is what an instrument should do -- the sense of depth
 * comes from the CSS field behind the canvas, not from a fake sun.
 *
 * (`material: false` on the mesh alone does not achieve this: the phong module
 * is compiled into SimpleMeshLayer's shader regardless.)
 */
const FLAT_LIGHTING = new LightingEffect({
  ambient: new AmbientLight({ color: [255, 255, 255], intensity: 1.0 }),
});

const FLY = new LinearInterpolator({ transitionProps: ["longitude", "latitude", "zoom"] });

/* ---------------------------------------------------------------- camera --- */

/** The globe camera, with eased flights and cursor parallax.
 *
 *  `base` is where the operator put the planet. `offset` is the transient
 *  parallax nudge. What deck renders is base + offset; what deck reports back
 *  after a drag has the offset removed, so the nudge never accumulates into
 *  the operator's own placement. */
export function useGlobeCamera(initial = GLOBE_DEFAULT_VIEW, { parallax = true, strength = 0.9 } = {}) {
  const [base, setBase] = useState(initial);
  const [offset, setOffset] = useState({ lon: 0, lat: 0 });
  const target = useRef({ lon: 0, lat: 0 });
  const offsetRef = useRef(offset);
  const raf = useRef(0);
  const flying = useRef(false);
  const dragging = useRef(false);
  const suspended = useRef(!parallax);

  useEffect(() => { suspended.current = !parallax; }, [parallax]);

  /* Ease the rendered offset toward the target each frame. */
  useEffect(() => {
    const tick = () => {
      const cur = offsetRef.current;
      const tgt = (flying.current || dragging.current || suspended.current)
        ? { lon: 0, lat: 0 } : target.current;
      const nl = cur.lon + (tgt.lon - cur.lon) * 0.08;
      const nt = cur.lat + (tgt.lat - cur.lat) * 0.08;
      if (Math.abs(nl - cur.lon) > 1e-4 || Math.abs(nt - cur.lat) > 1e-4) {
        offsetRef.current = { lon: nl, lat: nt };
        setOffset(offsetRef.current);
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, []);

  const onPointerMove = useCallback((e) => {
    if (suspended.current || dragging.current) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const dx = ((e.clientX - rect.left) / rect.width - 0.5) * 2;   // -1..1
    const dy = ((e.clientY - rect.top) / rect.height - 0.5) * 2;
    // Degrees of nudge scale down with zoom so the effect is a breath at
    // orbit and imperceptible at a harbour.
    const scale = strength / Math.pow(2, Math.max(0, (base.zoom ?? 3) - 3));
    target.current = { lon: dx * scale, lat: -dy * scale * 0.6 };
  }, [base.zoom, strength]);

  const onPointerLeave = useCallback(() => { target.current = { lon: 0, lat: 0 }; }, []);

  const onViewStateChange = useCallback(({ viewState: v, interactionState }) => {
    dragging.current = Boolean(interactionState?.isDragging || interactionState?.isPanning
      || interactionState?.isRotating || interactionState?.isZooming);
    if (interactionState?.inTransition) return;   // deck is mid-flight; do not fight it
    const cur = offsetRef.current;
    const { transitionDuration, transitionInterpolator, transitionEasing, ...rest } = v;
    setBase({ ...rest, longitude: v.longitude - cur.lon, latitude: v.latitude - cur.lat });
  }, []);

  /** Ease the camera to `to` over `ms`. */
  const flyTo = useCallback((to, ms = 1200) => {
    flying.current = true;
    setBase((b) => ({
      ...b, ...to,
      transitionDuration: ms,
      transitionInterpolator: FLY,
      transitionEasing: (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2),
      onTransitionEnd: () => { flying.current = false; },
      onTransitionInterrupt: () => { flying.current = false; },
    }));
    // Belt and braces: a transition that never reports back (unmount, a
    // second flight) must not leave the parallax frozen.
    setTimeout(() => { flying.current = false; }, ms + 120);
  }, []);

  const zoomBy = useCallback((delta) => {
    setBase((b) => ({
      ...b,
      zoom: Math.max(b.minZoom ?? 0.4, Math.min(b.maxZoom ?? 14, (b.zoom ?? 3) + delta)),
      transitionDuration: 320, transitionInterpolator: FLY,
    }));
  }, []);

  const viewState = useMemo(() => ({
    ...base,
    longitude: base.longitude + offset.lon,
    latitude: Math.max(-89, Math.min(89, base.latitude + offset.lat)),
  }), [base, offset]);

  return { viewState, base, setBase, onViewStateChange, onPointerMove, onPointerLeave, flyTo, zoomBy };
}

/* ---------------------------------------------------------------- scene ---- */

/**
 * @param {object}   props.viewState        from useGlobeCamera
 * @param {function} props.onViewStateChange
 * @param {string}   props.basemap          "dark" | "light" | "imagery" | "ocean" | "none"
 * @param {string}   props.theme            "dark" | "light" -- picks ocean/grid/rim colours
 * @param {Array}    props.layers           data layers, drawn above the planet
 * @param {boolean}  props.graticule
 * @param {boolean}  props.atmosphere
 * @param {function} props.onHover / onClick   deck picking info
 * @param {function} props.getCursor
 * @param {function} props.onPointerMove / onPointerLeave  from useGlobeCamera
 */
export default function GlobeScene({
  viewState, onViewStateChange, basemap = "canvas", theme = "dark", layers = [],
  graticule = true, atmosphere = true, onHover, onClick, getCursor,
  onPointerMove, onPointerLeave, children, controller = true, testid = "globe",
}) {
  const sphere = useMemo(
    () => new SphereGeometry({ radius: EARTH_RADIUS_M, nlat: 40, nlong: 80 }), []);

  const palette = theme === "light" ? "light" : "dark";
  const land = useLandGeometry();
  const landStyle = basemap === "none" ? null
    : LAND_STYLE[palette][basemap] || LAND_STYLE[palette].canvas;

  const contextLayers = useMemo(() => {
    const out = [];

    /* --- the planet ------------------------------------------------------ */
    out.push(new SimpleMeshLayer({
      id: "globe-sphere",
      data: [0],
      mesh: sphere,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: [0, 0, 0],
      getColor: OCEAN[palette],
      /* Render the ocean at EXACTLY its token colour.
       *
       * Two things had to be neutralised to get there. `FLAT_LIGHTING` removes
       * deck's directional lights, which were shading the sphere into a grey
       * gradient. This material removes the second half: phong's ambient
       * reflectance defaults to 0.35, so even under a full ambient light the
       * mesh came out at roughly a third of its colour -- a light-blue sea
       * rendering as dark slate. Full ambient, no diffuse, no specular means
       * the sphere shows the colour it was given and nothing else. */
      material: { ambient: 1, diffuse: 0, shininess: 1, specularColor: [0, 0, 0] },
      // Not pickable: the sphere covers the whole surface, so a pickable
      // sphere would swallow every click meant for a vessel.
      pickable: false,
    }));

    /* --- atmosphere ------------------------------------------------------ *
     * Deliberately NOT a second sphere. `_GlobeView` applies a non-linear
     * vertex transform, so a concentric mesh is not concentric on screen and
     * a back-face "rim" renders as a crescent hanging off one edge. The glow
     * is a CSS field painted behind the canvas instead (`.globe-dark` /
     * `.globe-light`), which is correct at every camera angle, costs no draw
     * call, and stays subtle in both themes. */

    /* --- land ------------------------------------------------------------ */
    if (land && landStyle) {
      out.push(new GeoJsonLayer({
        id: `globe-land-${basemap}-${palette}`,
        data: land,
        filled: true,
        stroked: true,
        getFillColor: landStyle.fill,
        getLineColor: landStyle.line,
        getLineWidth: landStyle.width,
        lineWidthUnits: "pixels",
        lineWidthMinPixels: 0.5,
        // Not pickable: land is orientation. A click on it must reach the
        // water beneath for the zone lookup, or for a boundary vertex.
        pickable: false,
        updateTriggers: { getFillColor: [basemap, palette], getLineColor: [basemap, palette] },
      }));
    }

    /* --- graticule ------------------------------------------------------- */
    if (graticule) {
      const grid = (viewState?.zoom ?? 3) > 5 ? GRATICULE_5 : GRATICULE_15;
      out.push(new PathLayer({
        id: "globe-graticule",
        data: grid,
        getPath: (d) => d.path,
        getColor: (d) => (d.major ? GRATICULE[palette].major : GRATICULE[palette].minor),
        getWidth: (d) => (d.major ? 1.4 : 1),
        widthUnits: "pixels",
        widthMinPixels: 1,
        pickable: false,
      }));
    }
    return out;
  }, [sphere, palette, land, landStyle, basemap, graticule, viewState?.zoom > 5]);

  const allLayers = useMemo(() => [...contextLayers, ...(layers || [])], [contextLayers, layers]);

  const views = useMemo(() => new GlobeView({ id: "globe", resolution: 12 }), []);

  const controllerOpts = controller === false ? false : {
    dragRotate: true, doubleClickZoom: false, inertia: 320, keyboard: false,
    ...(typeof controller === "object" ? controller : {}),
  };

  return (
    <div className={`globe-wrap globe-${palette}`} data-testid={testid}
      onPointerMove={onPointerMove} onPointerLeave={onPointerLeave}>
      <DeckGL
        views={views}
        viewState={viewState}
        onViewStateChange={onViewStateChange}
        controller={controllerOpts}
        layers={allLayers}
        onHover={onHover}
        onClick={onClick}
        getCursor={getCursor || (({ isDragging, isHovering }) =>
          (isDragging ? "grabbing" : isHovering ? "pointer" : "grab"))}
        effects={[FLAT_LIGHTING]}
        parameters={{ cullMode: "back", depthTest: true }}
        useDevicePixels={Math.min(window.devicePixelRatio || 1, 2)}
      />
      {children}
    </div>
  );
}
