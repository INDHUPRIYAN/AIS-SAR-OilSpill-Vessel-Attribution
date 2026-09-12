/* The 3D globe: a maritime situational-awareness surface, not an animation.
 *
 * SCOPE, and why it is this and not more
 * --------------------------------------
 * Architect decision D3 struck the 450 ms projection morph from the OSINT
 * spec: `_GlobeView` cannot host a MapLibre basemap, so a 2D-to-3D morph
 * between a MapLibre map and a deck.gl sphere is not a thing that can be
 * built. What is buildable, and what this is, is a globe rendered entirely
 * from deck.gl layers, switched to by a canvas swap that preserves camera
 * target, timeline `t` and selection. 2D remains the default and the
 * fallback.
 *
 * The far hemisphere is occluded by a real sphere mesh. Without it deck.gl
 * draws vessels on the back of the planet straight through the front, and a
 * globe that shows you traffic behind the Earth is worse than a flat map --
 * it is a flat map that lies about being a globe.
 *
 * ZONE SPLITTING
 * --------------
 * The same canvas is the boundary editor. `editor` turns clicks into vertices,
 * and the live lon/lat under the cursor is displayed at full precision because
 * the operator is drawing a boundary that decides who receives an alert --
 * "somewhere around 87 East" is not a boundary.
 *
 * Every polygon, vessel and incident comes from an API response. Nothing here
 * invents a position, and a layer whose data has not arrived renders nothing
 * rather than a placeholder (standing rule 14: if a value is not in an API
 * response, it is not displayed).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { COORDINATE_SYSTEM, _GlobeView as GlobeView } from "@deck.gl/core";
import {
  GeoJsonLayer, LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer,
} from "@deck.gl/layers";
import { SimpleMeshLayer } from "@deck.gl/mesh-layers";
import { TileLayer } from "@deck.gl/geo-layers";
import { BitmapLayer } from "@deck.gl/layers";
import { SphereGeometry } from "@luma.gl/engine";

/* Mean Earth radius, metres. The value deck.gl's GlobeView itself uses; the
 * sphere must match it or the basemap floats above or sinks below the data. */
const EARTH_RADIUS_M = 6370972;

/* A dark, low-chroma canvas basemap. Same host the 2D map already uses for
 * imagery, so the globe adds no new external origin. Raster, because
 * `_GlobeView` cannot consume a vector style. */
const DARK_CANVAS =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const IMAGERY =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export const GLOBE_INITIAL_VIEW = {
  longitude: 88.0,      // centred on the Bay of Bengal, the operational theatre
  latitude: 13.0,
  zoom: 3.1,
  minZoom: 0.5,
  maxZoom: 14,
};

/* Restrained, and deliberately not neon. These are status colours on an
 * instrument, and a saturated glow on everything means nothing stands out. */
export const GLOBE_COLORS = {
  ocean: [8, 16, 28],
  graticule: [70, 90, 115, 90],
  graticuleMajor: [110, 135, 165, 140],
  zone: [56, 189, 248],
  zoneMine: [45, 212, 191],
  zoneProtected: [148, 163, 184],
  incident: [239, 68, 68],
  vessel: [130, 148, 178],
  vesselTanker: [251, 146, 60],
  slick: [220, 38, 38],
  origin: [251, 191, 36],
  forecast: [245, 158, 11],
  drift: [192, 38, 211],
  editorEdge: [45, 212, 191],
  editorVertex: [255, 255, 255],
  editorInvalid: [239, 68, 68],
};

/* ------------------------------------------------------------- graticule --- */

/* Built once at module scope: it never changes, and rebuilding ~50 paths on
 * every render is pure waste on a surface that redraws while dragging. */
function buildGraticule(stepDeg) {
  const meridians = [];
  const parallels = [];
  for (let lon = -180; lon <= 180; lon += stepDeg) {
    const path = [];
    for (let lat = -80; lat <= 80; lat += 2) path.push([lon, lat]);
    meridians.push({ path, major: lon % 90 === 0 });
  }
  for (let lat = -80; lat <= 80; lat += stepDeg) {
    const path = [];
    for (let lon = -180; lon <= 180; lon += 2) path.push([lon, lat]);
    parallels.push({ path, major: lat === 0 });
  }
  return [...meridians, ...parallels];
}

const GRATICULE_15 = buildGraticule(15);
const GRATICULE_5 = buildGraticule(5);

/* ------------------------------------------------------- coordinate text --- */

/** Degrees to a signed decimal string with a hemisphere letter.
 *
 *  Six decimals is ~11 cm at the equator. That is more precision than AIS
 *  carries and exactly the precision a boundary vertex is stored at, so the
 *  editor shows what it will actually save rather than a rounded version the
 *  operator then cannot reproduce. */
export function fmtLat(lat) {
  if (lat === null || lat === undefined || Number.isNaN(lat)) return "--";
  return `${Math.abs(lat).toFixed(6)}° ${lat >= 0 ? "N" : "S"}`;
}

export function fmtLon(lon) {
  if (lon === null || lon === undefined || Number.isNaN(lon)) return "--";
  return `${Math.abs(lon).toFixed(6)}° ${lon >= 0 ? "E" : "W"}`;
}

/** "13 12.345 N" — degrees and decimal minutes, which is what a bridge
 *  officer and a chart both use. Shown alongside decimal degrees rather than
 *  instead of them, because the API speaks decimal. */
export function fmtDegMin(value, isLat) {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  const min = (abs - deg) * 60;
  const hemi = isLat ? (value >= 0 ? "N" : "S") : (value >= 0 ? "E" : "W");
  return `${String(deg).padStart(isLat ? 2 : 3, "0")}° ${min.toFixed(3).padStart(6, "0")}' ${hemi}`;
}

/** Parse a coordinate the user typed. Accepts decimal degrees and
 *  degrees-decimal-minutes, with or without hemisphere letters, comma or
 *  space separated. Returns null rather than a guess: a search box that
 *  silently reinterprets "13 12" as something the user did not mean would
 *  fly the camera somewhere unexplained. */
export function parseCoordinate(text) {
  if (!text) return null;
  const cleaned = String(text).trim().replace(/[°'"]/g, " ")
    .replace(/,/g, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) return null;

  const tokens = cleaned.split(" ");
  const nums = [];
  const hemis = [];
  for (const tok of tokens) {
    const upper = tok.toUpperCase();
    if (["N", "S", "E", "W"].includes(upper)) { hemis.push(upper); continue; }
    const asNum = Number(tok);
    if (Number.isNaN(asNum)) return null;
    nums.push(asNum);
  }

  let lat = null;
  let lon = null;
  if (nums.length === 2) {
    [lat, lon] = nums;
  } else if (nums.length === 4) {
    // deg min deg min
    lat = Math.abs(nums[0]) + nums[1] / 60;
    lon = Math.abs(nums[2]) + nums[3] / 60;
    if (nums[0] < 0) lat = -lat;
    if (nums[2] < 0) lon = -lon;
  } else {
    return null;
  }

  // Hemisphere letters win over sign, in the order they appeared.
  if (hemis.length === 2) {
    const [h1, h2] = hemis;
    if (h1 === "S") lat = -Math.abs(lat);
    if (h1 === "N") lat = Math.abs(lat);
    if (h2 === "W") lon = -Math.abs(lon);
    if (h2 === "E") lon = Math.abs(lon);
    // "E 88 N 13" — letters given lat-second.
    if ((h1 === "E" || h1 === "W") && (h2 === "N" || h2 === "S")) {
      const swap = lat; lat = lon; lon = swap;
      if (h2 === "S") lat = -Math.abs(lat); else lat = Math.abs(lat);
      if (h1 === "W") lon = -Math.abs(lon); else lon = Math.abs(lon);
    }
  }

  if (Number.isNaN(lat) || Number.isNaN(lon)) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}

/* ------------------------------------------------------------- geometry --- */

/** Shoelace area in square degrees. Sign tells ring orientation.
 *
 *  Planar, and NOT reported to the user as an area -- it is only used to
 *  decide whether a ring the operator is drawing has turned itself inside
 *  out. Real areas are geodesic and come from the server. */
function signedDegArea(ring) {
  let sum = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[(i + 1) % ring.length];
    sum += x1 * y2 - x2 * y1;
  }
  return sum / 2;
}

/** Do segments (p1,p2) and (p3,p4) properly cross?
 *
 *  Used to tell an operator their boundary self-intersects WHILE they draw,
 *  rather than letting the server reject the whole polygon on save. Shared
 *  endpoints are not crossings -- consecutive edges always share one. */
function segmentsCross(p1, p2, p3, p4) {
  const d = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const d1 = d(p3, p4, p1);
  const d2 = d(p3, p4, p2);
  const d3 = d(p1, p2, p3);
  const d4 = d(p1, p2, p4);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0))
    && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

/** The indices of the first self-intersecting edge pair, or null.
 *
 *  Returned as indices rather than a boolean so the editor can highlight the
 *  two offending edges; "invalid polygon" with nothing pointed at is not
 *  actionable. */
export function findSelfIntersection(ring, closed) {
  const edges = [];
  const n = ring.length;
  const last = closed ? n : n - 1;
  for (let i = 0; i < last; i += 1) {
    edges.push([ring[i], ring[(i + 1) % n], i]);
  }
  for (let i = 0; i < edges.length; i += 1) {
    for (let j = i + 2; j < edges.length; j += 1) {
      // Adjacent edges share a vertex; the first and last do too when closed.
      if (closed && i === 0 && j === edges.length - 1) continue;
      if (segmentsCross(edges[i][0], edges[i][1], edges[j][0], edges[j][1])) {
        return [edges[i][2], edges[j][2]];
      }
    }
  }
  return null;
}

/* ------------------------------------------------------------- the globe --- */

/**
 * @param {object}   props
 * @param {object}   props.viewState        controlled camera
 * @param {function} props.onViewStateChange
 * @param {string}   props.basemap          "dark" | "imagery" | "none"
 * @param {object}   props.layersOn         layer visibility flags
 * @param {object}   props.zones            GeoJSON FeatureCollection (or null)
 * @param {Array}    props.incidents        [{id, lon, lat, severity, ...}]
 * @param {Array}    props.vessels          [{mmsi, lon, lat, cog_deg, vessel_type}]
 * @param {object}   props.runLayers        {slick, origin, forecast, tracks}
 * @param {object}   props.editor           zone-editor state, or null
 * @param {function} props.onMapClick       (lngLat, info) => void
 * @param {function} props.onCursorMove     ({lon, lat}|null) => void
 * @param {function} props.onSelect         (kind, object) => void
 */
export default function Globe({
  viewState,
  onViewStateChange,
  basemap = "dark",
  layersOn = {},
  zones = null,
  incidents = [],
  vessels = [],
  runLayers = null,
  editor = null,
  onMapClick,
  onCursorMove,
  onSelect,
  graticuleStep = 15,
  children,
}) {
  const [hover, setHover] = useState(null);

  /* The sphere that occludes the far hemisphere. Built once -- constructing a
   * SphereGeometry per render allocates GPU buffers every frame. */
  const sphere = useMemo(
    () => new SphereGeometry({ radius: EARTH_RADIUS_M, nlat: 36, nlong: 72 }),
    []);

  const handleHover = useCallback((info) => {
    const coord = info?.coordinate;
    // `coordinate` is undefined when the pointer is off the globe (the void
    // around the sphere). Reporting null rather than the last known position
    // is the difference between "no reading" and a stale reading the operator
    // would trust.
    if (onCursorMove) {
      onCursorMove(coord ? { lon: coord[0], lat: coord[1] } : null);
    }
    setHover(info?.object ? { object: info.object, layer: info.layer?.id,
                              x: info.x, y: info.y } : null);
  }, [onCursorMove]);

  const handleClick = useCallback((info) => {
    if (info?.coordinate && onMapClick) {
      onMapClick({ lon: info.coordinate[0], lat: info.coordinate[1] }, info);
    }
    if (info?.object && onSelect) {
      onSelect(info.layer?.id, info.object);
    }
  }, [onMapClick, onSelect]);

  const layers = useMemo(() => {
    const out = [];

    /* --- the planet ---------------------------------------------------- */
    out.push(new SimpleMeshLayer({
      id: "globe-sphere",
      data: [0],
      mesh: sphere,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: [0, 0, 0],
      getColor: GLOBE_COLORS.ocean,
      // Not pickable: the sphere covers the whole surface, so a pickable
      // sphere would swallow every click meant for a vessel.
      pickable: false,
    }));

    if (basemap !== "none") {
      out.push(new TileLayer({
        id: "globe-basemap",
        data: basemap === "imagery" ? IMAGERY : DARK_CANVAS,
        minZoom: 0,
        maxZoom: 10,
        tileSize: 256,
        // The globe asks for far more tiles per frame than a flat map; without
        // a cap, spinning the planet evicts and refetches continuously.
        maxRequests: 12,
        renderSubLayers: (props) => {
          const { boundingBox } = props.tile;
          return new BitmapLayer(props, {
            data: null,
            image: props.data,
            bounds: [boundingBox[0][0], boundingBox[0][1],
                     boundingBox[1][0], boundingBox[1][1]],
            opacity: basemap === "imagery" ? 0.85 : 0.65,
          });
        },
      }));
    }

    /* --- graticule ----------------------------------------------------- */
    if (layersOn.graticule !== false) {
      const grid = graticuleStep <= 5 ? GRATICULE_5 : GRATICULE_15;
      out.push(new PathLayer({
        id: "globe-graticule",
        data: grid,
        getPath: (d) => d.path,
        getColor: (d) => (d.major ? GLOBE_COLORS.graticuleMajor
                                  : GLOBE_COLORS.graticule),
        getWidth: (d) => (d.major ? 1.6 : 1),
        widthUnits: "pixels",
        widthMinPixels: 1,
        pickable: false,
      }));
    }

    /* --- zones --------------------------------------------------------- */
    if (layersOn.zones !== false && zones?.features?.length) {
      out.push(new GeoJsonLayer({
        id: "globe-zones",
        data: zones,
        filled: true,
        stroked: true,
        getFillColor: (f) => {
          const p = f.properties || {};
          if (p.kind === "jurisdiction") return [...GLOBE_COLORS.zoneProtected, 10];
          return p.mine ? [...GLOBE_COLORS.zoneMine, 34]
                        : [...GLOBE_COLORS.zone, 20];
        },
        getLineColor: (f) => {
          const p = f.properties || {};
          // A protected boundary reads differently from an operational one,
          // because they mean different things and one of them cannot be moved.
          if (p.kind === "jurisdiction") return [...GLOBE_COLORS.zoneProtected, 235];
          return p.mine ? [...GLOBE_COLORS.zoneMine, 220]
                        : [...GLOBE_COLORS.zone, 170];
        },
        getLineWidth: (f) => (f.properties?.kind === "jurisdiction" ? 2.4 : 1.4),
        lineWidthUnits: "pixels",
        lineWidthMinPixels: 1,
        pickable: true,
        autoHighlight: true,
        highlightColor: [...GLOBE_COLORS.zoneMine, 60],
        updateTriggers: {
          getFillColor: [zones],
          getLineColor: [zones],
        },
      }));

      if (layersOn.zoneLabels !== false) {
        const labels = zones.features
          .filter((f) => f.properties?.kind === "operational")
          .map((f) => {
            const bbox = f.properties?.bbox;
            if (!bbox) return null;
            return {
              name: f.properties.name || f.properties.id,
              // bbox centre, not a computed centroid: a centroid of a
              // crescent-shaped zone can land outside it.
              position: [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
            };
          })
          .filter(Boolean);
        out.push(new TextLayer({
          id: "globe-zone-labels",
          data: labels,
          getPosition: (d) => d.position,
          getText: (d) => d.name,
          getSize: 11,
          sizeUnits: "pixels",
          getColor: [190, 210, 232, 215],
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          characterSet: "auto",
          getTextAnchor: "middle",
          getAlignmentBaseline: "center",
          outlineWidth: 2,
          outlineColor: [4, 10, 20, 255],
          fontSettings: { sdf: true },
          pickable: false,
        }));
      }
    }

    /* --- run overlays (identical definitions to the 2D view) ----------- */
    if (runLayers?.slick && layersOn.slick !== false) {
      out.push(new GeoJsonLayer({
        id: "globe-slick",
        data: runLayers.slick,
        filled: true,
        stroked: true,
        getFillColor: [...GLOBE_COLORS.slick, 120],
        getLineColor: [...GLOBE_COLORS.slick, 255],
        getLineWidth: 2,
        lineWidthUnits: "pixels",
        lineWidthMinPixels: 1,
        pickable: true,
      }));
    }

    if (runLayers?.forecast && layersOn.forecast !== false) {
      out.push(new GeoJsonLayer({
        id: "globe-forecast",
        data: runLayers.forecast,
        filled: true,
        stroked: true,
        // Opacity by horizon: the +24 h footprint is the least certain and
        // must not read as solid as the observed slick.
        getFillColor: (f) => {
          const h = Number(f.properties?.horizon_hours ?? 24);
          const alpha = h <= 6 ? 62 : h <= 12 ? 44 : 26;
          return [...GLOBE_COLORS.forecast, alpha];
        },
        getLineColor: [...GLOBE_COLORS.forecast, 190],
        getLineWidth: 1.4,
        lineWidthUnits: "pixels",
        pickable: true,
      }));
    }

    if (runLayers?.origin && layersOn.origin !== false) {
      out.push(new GeoJsonLayer({
        id: "globe-origin",
        data: runLayers.origin,
        filled: true,
        stroked: true,
        getFillColor: [...GLOBE_COLORS.origin, 48],
        getLineColor: [...GLOBE_COLORS.origin, 225],
        getLineWidth: 1.6,
        lineWidthUnits: "pixels",
        pickable: true,
      }));
    }

    if (runLayers?.tracks?.length && layersOn.tracks !== false) {
      out.push(new PathLayer({
        id: "globe-tracks",
        data: runLayers.tracks,
        getPath: (d) => d.path,
        getColor: (d) => (d.candidate ? [...GLOBE_COLORS.vesselTanker, 220]
                                      : [...GLOBE_COLORS.vessel, 140]),
        getWidth: (d) => (d.candidate ? 2.2 : 1.2),
        widthUnits: "pixels",
        widthMinPixels: 1,
        pickable: true,
      }));
    }

    /* --- live AIS ------------------------------------------------------- */
    if (vessels?.length && layersOn.vessels !== false) {
      out.push(new ScatterplotLayer({
        id: "globe-vessels",
        data: vessels,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 2.4,
        radiusUnits: "pixels",
        radiusMinPixels: 2,
        radiusMaxPixels: 6,
        getFillColor: (d) => (d.vessel_type === "tanker"
          ? [...GLOBE_COLORS.vesselTanker, 230]
          : [...GLOBE_COLORS.vessel, 190]),
        pickable: true,
        autoHighlight: true,
      }));

      /* Course sticks, drawn only for vessels that actually transmit a
       * course. A vessel with cog null gets a dot and no stick -- drawing
       * one pointing north would be a fabricated heading. */
      const moving = vessels.filter(
        (v) => v.cog_deg !== null && v.cog_deg !== undefined
          && (v.sog_kn ?? 0) > 0.5);
      if (moving.length) {
        out.push(new LineLayer({
          id: "globe-vessel-course",
          data: moving,
          getSourcePosition: (d) => [d.lon, d.lat],
          getTargetPosition: (d) => {
            const rad = (Number(d.cog_deg) * Math.PI) / 180;
            // ~2 km stick, scaled for latitude so it points true on a globe.
            const km = 2;
            const dLat = (km / 111.32) * Math.cos(rad);
            const dLon = (km / (111.32 * Math.cos((d.lat * Math.PI) / 180)))
              * Math.sin(rad);
            return [d.lon + dLon, d.lat + dLat];
          },
          getColor: [...GLOBE_COLORS.vessel, 150],
          getWidth: 1,
          widthUnits: "pixels",
          pickable: false,
        }));
      }
    }

    /* --- incidents ----------------------------------------------------- */
    if (incidents?.length && layersOn.incidents !== false) {
      out.push(new ScatterplotLayer({
        id: "globe-incidents",
        data: incidents.filter((i) => i.lon !== null && i.lon !== undefined
                                      && i.lat !== null && i.lat !== undefined),
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => (d.severity === "critical" ? 7
          : d.severity === "high" ? 6 : 5),
        radiusUnits: "pixels",
        radiusMinPixels: 4,
        stroked: true,
        getFillColor: [...GLOBE_COLORS.incident, 170],
        getLineColor: [255, 235, 235, 245],
        getLineWidth: 1.4,
        lineWidthUnits: "pixels",
        pickable: true,
        autoHighlight: true,
      }));
    }

    /* --- the zone editor ------------------------------------------------ */
    if (editor?.active) {
      const ring = editor.points || [];
      const bad = editor.selfIntersection;

      if (ring.length >= 3) {
        out.push(new PolygonLayer({
          id: "globe-editor-fill",
          data: [{ polygon: ring }],
          getPolygon: (d) => d.polygon,
          filled: true,
          stroked: false,
          getFillColor: bad ? [...GLOBE_COLORS.editorInvalid, 34]
                            : [...GLOBE_COLORS.editorEdge, 30],
          pickable: false,
        }));
      }

      if (ring.length >= 2) {
        const segments = [];
        const limit = editor.closed ? ring.length : ring.length - 1;
        for (let i = 0; i < limit; i += 1) {
          segments.push({
            path: [ring[i], ring[(i + 1) % ring.length]],
            offending: bad ? (bad[0] === i || bad[1] === i) : false,
          });
        }
        out.push(new PathLayer({
          id: "globe-editor-edges",
          data: segments,
          getPath: (d) => d.path,
          getColor: (d) => (d.offending ? [...GLOBE_COLORS.editorInvalid, 255]
                                        : [...GLOBE_COLORS.editorEdge, 235]),
          getWidth: (d) => (d.offending ? 3 : 2),
          widthUnits: "pixels",
          widthMinPixels: 2,
          pickable: false,
          updateTriggers: { getColor: [bad], getWidth: [bad] },
        }));
      }

      out.push(new ScatterplotLayer({
        id: "globe-editor-vertices",
        data: ring.map((p, i) => ({ position: p, index: i })),
        getPosition: (d) => d.position,
        getRadius: 5,
        radiusUnits: "pixels",
        radiusMinPixels: 4,
        stroked: true,
        getFillColor: (d) => (d.index === editor.selectedIndex
          ? [...GLOBE_COLORS.editorEdge, 255]
          : [...GLOBE_COLORS.editorVertex, 235]),
        getLineColor: [8, 16, 28, 255],
        getLineWidth: 1.5,
        lineWidthUnits: "pixels",
        pickable: true,
        autoHighlight: true,
        updateTriggers: { getFillColor: [editor.selectedIndex] },
      }));

      /* Vertex ordinals. An operator moving "point 3" needs to know which one
       * point 3 is. */
      out.push(new TextLayer({
        id: "globe-editor-labels",
        data: ring.map((p, i) => ({ position: p, label: String(i + 1) })),
        getPosition: (d) => d.position,
        getText: (d) => d.label,
        getSize: 10,
        sizeUnits: "pixels",
        getColor: [8, 16, 28, 255],
        getPixelOffset: [0, 0],
        characterSet: "0123456789",
        getTextAnchor: "middle",
        getAlignmentBaseline: "center",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        pickable: false,
      }));
    }

    return out;
  }, [basemap, layersOn, zones, incidents, vessels, runLayers, editor, sphere,
      graticuleStep]);

  return (
    <div className="globe-wrap" data-testid="globe">
      <DeckGL
        views={new GlobeView({ id: "globe", resolution: 12 })}
        viewState={viewState}
        onViewStateChange={onViewStateChange}
        controller={{ dragRotate: true, doubleClickZoom: false,
                      inertia: 250, keyboard: false }}
        layers={layers}
        onHover={handleHover}
        onClick={handleClick}
        // Off the sphere the pointer is over nothing; the default crosshair
        // would suggest it is over a coordinate.
        getCursor={({ isDragging, isHovering }) =>
          (isDragging ? "grabbing" : (editor?.active ? "crosshair"
            : (isHovering ? "pointer" : "grab")))}
        parameters={{ cullMode: "back", depthTest: true }}
      />
      {hover && (
        <GlobeTooltip x={hover.x} y={hover.y} layer={hover.layer}
                      object={hover.object} />
      )}
      {children}
    </div>
  );
}

/* Hover readout. Only fields the object actually carries are rendered --
 * a null vessel name shows "not transmitted", never a blank that reads as a
 * missing UI element, and never an invented name. */
function GlobeTooltip({ x, y, layer, object }) {
  const rows = [];
  let title = "";

  if (layer === "globe-zones") {
    const p = object.properties || {};
    title = p.name || p.id;
    rows.push(["Kind", p.kind]);
    if (p.jurisdiction) rows.push(["Jurisdiction", p.jurisdiction]);
    if (p.protected) rows.push(["Boundary", "PROTECTED"]);
    if (p.area_km2 !== null && p.area_km2 !== undefined) {
      rows.push(["Area", `${Number(p.area_km2).toLocaleString()} km2`]);
    }
    const officer = (p.officers || []).find((o) => o.is_primary);
    rows.push(["Officer", officer ? (officer.display_name || officer.email)
                                  : "unassigned"]);
  } else if (layer === "globe-vessels") {
    title = object.vessel_name || `MMSI ${object.mmsi}`;
    rows.push(["MMSI", String(object.mmsi)]);
    if (!object.vessel_name) rows.push(["Name", "not transmitted"]);
    rows.push(["Type", object.vessel_type || "unknown"]);
    rows.push(["SOG", object.sog_kn === null || object.sog_kn === undefined
      ? "not transmitted" : `${Number(object.sog_kn).toFixed(1)} kn`]);
    rows.push(["COG", object.cog_deg === null || object.cog_deg === undefined
      ? "not transmitted" : `${Number(object.cog_deg).toFixed(0)}°`]);
    if (object.last_report_utc) rows.push(["Last report", object.last_report_utc]);
  } else if (layer === "globe-incidents") {
    title = object.title || object.id;
    rows.push(["Incident", object.id]);
    if (object.severity) rows.push(["Severity", object.severity.toUpperCase()]);
    if (object.status) rows.push(["Status", object.status]);
    if (object.zone_id) rows.push(["Zone", object.zone_id]);
  } else {
    return null;
  }

  return (
    <div className="globe-tip" style={{ left: x + 12, top: y + 12 }}
         data-testid="globe-tooltip">
      <div className="globe-tip-title">{title}</div>
      {rows.map(([k, v]) => (
        <div key={k} className="globe-tip-row">
          <span className="globe-tip-k">{k}</span>
          <span className="globe-tip-v mono">{v}</span>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------- cursor readout --- */

/** The lat/lon strip. Required by the zone-splitting spec: the editor must
 *  show the actual coordinate under the cursor, because the operator is
 *  drawing a line that decides who gets an alert. */
export function CoordinateReadout({ cursor, zone }) {
  return (
    <div className="globe-coords mono" data-testid="globe-coords">
      <span className="globe-coord-pair">
        <span className="globe-coord-k">LAT</span>
        <span data-testid="cursor-lat">{fmtLat(cursor?.lat)}</span>
      </span>
      <span className="globe-coord-pair">
        <span className="globe-coord-k">LON</span>
        <span data-testid="cursor-lon">{fmtLon(cursor?.lon)}</span>
      </span>
      {cursor && (
        <span className="globe-coord-pair tiny muted">
          {fmtDegMin(cursor.lat, true)} &nbsp; {fmtDegMin(cursor.lon, false)}
        </span>
      )}
      {/* Which zone the cursor is over, resolved server-side. "outside all
          operational zones" is a real answer and is shown as one. */}
      {zone !== undefined && (
        <span className="globe-coord-pair">
          <span className="globe-coord-k">ZONE</span>
          <span data-testid="cursor-zone">
            {zone ? (zone.name || zone.id) : "outside all zones"}
          </span>
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------ editor reducer --- */

/** Boundary-editor state, with undo.
 *
 *  A plain array plus a history stack rather than a reducer with patches: the
 *  operations an operator performs (add, move, delete, close) are each a whole
 *  new ring, the rings are small, and "undo" that replays diffs is where undo
 *  bugs come from.
 */
export function useBoundaryEditor(initialRing = null) {
  const [points, setPoints] = useState(initialRing ? [...initialRing] : []);
  const [closed, setClosed] = useState(Boolean(initialRing));
  const [selectedIndex, setSelectedIndex] = useState(null);
  const history = useRef([]);
  const future = useRef([]);

  const commit = useCallback((nextPoints, nextClosed) => {
    history.current.push({ points, closed });
    // A new edit invalidates anything that was undone -- otherwise redo
    // reapplies a branch the operator has already replaced.
    future.current = [];
    setPoints(nextPoints);
    if (nextClosed !== undefined) setClosed(nextClosed);
  }, [points, closed]);

  const addPoint = useCallback((lon, lat) => {
    if (closed) return;
    commit([...points, [lon, lat]], undefined);
  }, [points, closed, commit]);

  const movePoint = useCallback((index, lon, lat) => {
    if (index < 0 || index >= points.length) return;
    const next = points.map((p, i) => (i === index ? [lon, lat] : p));
    commit(next, undefined);
  }, [points, commit]);

  const deletePoint = useCallback((index) => {
    if (index < 0 || index >= points.length) return;
    const next = points.filter((_, i) => i !== index);
    // Dropping below three points re-opens the ring: a closed two-point
    // polygon is not a shape, and leaving `closed` true would let it be saved.
    commit(next, next.length < 3 ? false : closed);
    setSelectedIndex(null);
  }, [points, closed, commit]);

  const close = useCallback(() => {
    if (points.length < 3) return false;
    commit(points, true);
    return true;
  }, [points, commit]);

  const reopen = useCallback(() => commit(points, false), [points, commit]);

  const reset = useCallback((ring = []) => {
    history.current = [];
    future.current = [];
    setPoints([...ring]);
    setClosed(ring.length >= 3);
    setSelectedIndex(null);
  }, []);

  const undo = useCallback(() => {
    const prev = history.current.pop();
    if (!prev) return false;
    future.current.push({ points, closed });
    setPoints(prev.points);
    setClosed(prev.closed);
    setSelectedIndex(null);
    return true;
  }, [points, closed]);

  const redo = useCallback(() => {
    const next = future.current.pop();
    if (!next) return false;
    history.current.push({ points, closed });
    setPoints(next.points);
    setClosed(next.closed);
    return true;
  }, [points, closed]);

  const selfIntersection = useMemo(
    () => (points.length >= 4 ? findSelfIntersection(points, closed) : null),
    [points, closed]);

  /* GeoJSON for the API, or null when the ring is not yet a polygon. Returning
   * null rather than a partial ring is what stops a half-drawn boundary from
   * reaching the server. */
  const geometry = useMemo(() => {
    if (points.length < 3) return null;
    const ring = points.map(([lon, lat]) => [lon, lat]);
    ring.push([...ring[0]]);
    return { type: "Polygon", coordinates: [ring] };
  }, [points]);

  return {
    active: true,
    points,
    closed,
    selectedIndex,
    setSelectedIndex,
    selfIntersection,
    geometry,
    canUndo: history.current.length > 0,
    canRedo: future.current.length > 0,
    canClose: points.length >= 3 && !selfIntersection,
    addPoint,
    movePoint,
    deletePoint,
    close,
    reopen,
    reset,
    undo,
    redo,
    /* Planar and never shown as an area -- only used to warn that a ring has
     * turned itself inside out while being dragged. */
    signedDegArea: points.length >= 3 ? signedDegArea(points) : 0,
  };
}
