/* The situational globe: zones, incidents, live AIS, run overlays and the
 * boundary editor, drawn on the shared GlobeScene planet.
 *
 * This module is imported as `components/Globe` by the pages and the tests;
 * the file under components/globe/ is the implementation and components/Globe.jsx
 * re-exports it. The pure geometry (coordinate parsing, self-intersection,
 * the editor's undo) lives here because the tests pin it and because a
 * boundary that decides who receives an alert must be computed in one place.
 *
 * Every polygon, vessel and incident comes from an API response. Nothing here
 * invents a position, and a layer whose data has not arrived renders nothing
 * rather than a placeholder. The only synthetic motion on this surface is the
 * direction pulse that runs along a vessel's own transmitted course stick --
 * it conveys "this vessel is under way on this heading" and never a position
 * the vessel did not report.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  GeoJsonLayer, LineLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer,
} from "@deck.gl/layers";

import GlobeScene, { GLOBE_DEFAULT_VIEW } from "./GlobeScene";
import { MAP_COLORS } from "../maps/palette";
import { MapTip } from "../ui";

export const GLOBE_INITIAL_VIEW = GLOBE_DEFAULT_VIEW;

/* Colours come from the one map palette (components/maps/palette): a slick is
 * amber and a forecast is blue on every surface, not per screen. What stays
 * local is what only this globe draws -- vessel types and the zone editor. */
export const GLOBE_COLORS = {
  zone: MAP_COLORS.zone,
  zoneMine: MAP_COLORS.zoneMine,
  zoneProtected: [148, 163, 184],
  incident: MAP_COLORS.incident,
  vessel: [130, 148, 178],
  vesselCargo: [96, 165, 250],
  vesselTanker: [251, 146, 60],
  vesselPassenger: [167, 139, 250],
  vesselFishing: [74, 222, 128],
  slick: MAP_COLORS.slick,
  origin: MAP_COLORS.origin,
  forecast: MAP_COLORS.forecast,
  hindcast: MAP_COLORS.hindcastOld,
  track: MAP_COLORS.vessel,
  candidate: MAP_COLORS.candidate,
  suspect: MAP_COLORS.origin,
  editorEdge: [45, 212, 191],
  editorVertex: [255, 255, 255],
  editorInvalid: [239, 68, 68],
  selection: MAP_COLORS.selection,
};

const LIGHT_OVERRIDES = {
  vessel: [71, 85, 105],
  zoneProtected: [100, 116, 139],
  editorVertex: [15, 26, 43],
  selection: [15, 26, 43],
};

function vesselColour(type, colours) {
  const t = String(type || "").toLowerCase();
  if (t.includes("tanker")) return colours.vesselTanker;
  if (t.includes("cargo")) return colours.vesselCargo;
  if (t.includes("passenger")) return colours.vesselPassenger;
  if (t.includes("fish")) return colours.vesselFishing;
  return colours.vessel;
}

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

/** Shoelace area in square degrees. Sign tells ring orientation. Planar, and
 *  NOT reported to the user as an area -- real areas are geodesic and come
 *  from the server or from `sphericalAreaKm2` in lib/geodesy. */
function signedDegArea(ring) {
  let sum = 0;
  for (let i = 0; i < ring.length; i += 1) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[(i + 1) % ring.length];
    sum += x1 * y2 - x2 * y1;
  }
  return sum / 2;
}

/** Do segments (p1,p2) and (p3,p4) properly cross? Shared endpoints are not
 *  crossings -- consecutive edges always share one. */
function segmentsCross(p1, p2, p3, p4) {
  const d = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const d1 = d(p3, p4, p1);
  const d2 = d(p3, p4, p2);
  const d3 = d(p1, p2, p3);
  const d4 = d(p1, p2, p4);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0))
    && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

/** The indices of the first self-intersecting edge pair, or null. Returned
 *  as indices rather than a boolean so the editor can highlight the two
 *  offending edges; "invalid polygon" with nothing pointed at is not
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
      if (closed && i === 0 && j === edges.length - 1) continue;
      if (segmentsCross(edges[i][0], edges[i][1], edges[j][0], edges[j][1])) {
        return [edges[i][2], edges[j][2]];
      }
    }
  }
  return null;
}

/* ------------------------------------------------------------- the globe --- */

/* A geodesic stick of `km` from a position on bearing `deg`, so a course
 * indicator points true on a globe rather than screen-up. */
function stickEnd(lon, lat, deg, km) {
  const rad = (Number(deg) * Math.PI) / 180;
  const dLat = (km / 111.32) * Math.cos(rad);
  const dLon = (km / (111.32 * Math.cos((lat * Math.PI) / 180))) * Math.sin(rad);
  return [lon + dLon, lat + dLat];
}

/**
 * @param {object}   props.viewState / onViewStateChange   from useGlobeCamera
 * @param {string}   props.basemap          "dark" | "imagery" | "ocean" | "none"
 * @param {string}   props.theme            "dark" | "light"
 * @param {object}   props.layersOn         layer visibility flags
 * @param {object}   props.zones            GeoJSON FeatureCollection (or null)
 * @param {Array}    props.incidents        [{id, lon, lat, severity, ...}]
 * @param {Array}    props.vessels          live AIS rows
 * @param {object}   props.runLayers        {slick, origin, forecast, tracks, sceneMeta}
 * @param {object}   props.editor           zone-editor state, or null
 * @param {object}   props.selected         {kind, id} to highlight
 * @param {function} props.onMapClick       ({lon, lat}, info) => void
 * @param {function} props.onCursorMove     ({lon, lat}|null) => void
 * @param {function} props.onSelect         (layerId, object) => void
 */
export default function Globe({
  viewState, onViewStateChange, onPointerMove, onPointerLeave,
  basemap = "dark", theme = "dark", layersOn = {},
  zones = null, incidents = [], vessels = [], runLayers = null, editor = null,
  selected = null, onMapClick, onCursorMove, onSelect, children, animate = true,
}) {
  const [hover, setHover] = useState(null);
  const colours = useMemo(
    () => (theme === "light" ? { ...GLOBE_COLORS, ...LIGHT_OVERRIDES } : GLOBE_COLORS), [theme]);

  /* A slow phase for the direction pulses. Only runs while there is
   * something under way to animate. */
  const [tick, setTick] = useState(0);
  const moving = useMemo(() => (vessels || []).filter(
    (v) => v.cog_deg !== null && v.cog_deg !== undefined && (v.sog_kn ?? 0) > 0.5
      && v.lon !== null && v.lat !== null), [vessels]);
  useEffect(() => {
    if (!animate || !moving.length || layersOn.vessels === false) return undefined;
    let raf = 0;
    let last = performance.now();
    const loop = (now) => {
      // ~20 fps is plenty for a pulse and keeps 2000 vessels cheap.
      if (now - last > 50) { last = now; setTick((t) => (t + 1) % 100000); }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [animate, moving.length, layersOn.vessels]);

  const handleHover = useCallback((info) => {
    const coord = info?.coordinate;
    // `coordinate` is undefined when the pointer is off the globe. Reporting
    // null rather than the last known position is the difference between "no
    // reading" and a stale reading the operator would trust.
    if (onCursorMove) onCursorMove(coord ? { lon: coord[0], lat: coord[1] } : null);
    setHover(info?.object ? { object: info.object, layer: info.layer?.id, x: info.x, y: info.y } : null);
  }, [onCursorMove]);

  const handleClick = useCallback((info) => {
    if (info?.coordinate && onMapClick) {
      onMapClick({ lon: info.coordinate[0], lat: info.coordinate[1] }, info);
    }
    if (info?.object && onSelect) onSelect(info.layer?.id, info.object);
  }, [onMapClick, onSelect]);

  const layers = useMemo(() => {
    const out = [];
    const C = colours;

    /* --- zones --------------------------------------------------------- */
    if (layersOn.zones !== false && zones?.features?.length) {
      out.push(new GeoJsonLayer({
        id: "globe-zones",
        data: zones,
        filled: true,
        stroked: true,
        getFillColor: (f) => {
          const p = f.properties || {};
          if (p.kind === "jurisdiction") return [...C.zoneProtected, 8];
          if (selected?.kind === "zone" && selected.id === p.id) return [...C.zone, 60];
          return p.mine ? [...C.zoneMine, 34] : [...C.zone, 18];
        },
        getLineColor: (f) => {
          const p = f.properties || {};
          // A protected boundary reads differently from an operational one,
          // because they mean different things and one of them cannot be moved.
          if (p.kind === "jurisdiction") return [...C.zoneProtected, 230];
          if (selected?.kind === "zone" && selected.id === p.id) return [...C.zone, 255];
          return p.mine ? [...C.zoneMine, 220] : [...C.zone, 165];
        },
        getLineWidth: (f) => (f.properties?.kind === "jurisdiction" ? 2.2
          : selected?.kind === "zone" && selected.id === f.properties?.id ? 2.4 : 1.3),
        lineWidthUnits: "pixels",
        lineWidthMinPixels: 1,
        pickable: true,
        autoHighlight: true,
        highlightColor: [...C.zoneMine, 55],
        updateTriggers: { getFillColor: [zones, selected], getLineColor: [zones, selected],
                          getLineWidth: [selected] },
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
          getText: (d) => d.name.toUpperCase(),
          getSize: 10,
          sizeUnits: "pixels",
          getColor: theme === "light" ? [30, 50, 80, 220] : [190, 210, 232, 215],
          fontFamily: "JetBrains Mono, ui-monospace, monospace",
          characterSet: "auto",
          getTextAnchor: "middle",
          getAlignmentBaseline: "center",
          outlineWidth: 2,
          outlineColor: theme === "light" ? [255, 255, 255, 255] : [4, 10, 20, 255],
          fontSettings: { sdf: true },
          pickable: false,
        }));
      }
    }

    /* --- run overlays (identical definitions to the 2D view) ----------- */
    const rl = runLayers;
    if (rl?.sceneMeta?.bbox?.length === 4 && layersOn.scene !== false) {
      const [w, s, e, n] = rl.sceneMeta.bbox;
      out.push(new PathLayer({
        id: "globe-scene-extent",
        data: [{ path: [[w, s], [e, s], [e, n], [w, n], [w, s]] }],
        getPath: (d) => d.path,
        getColor: [...C.vessel, 150],
        getWidth: 1, widthUnits: "pixels",
        pickable: false,
      }));
    }
    if (rl?.forecast && layersOn.forecast !== false) {
      out.push(new GeoJsonLayer({
        id: "globe-forecast",
        data: rl.forecast,
        filled: true, stroked: true,
        // Opacity by horizon: the +24 h footprint is the least certain and
        // must not read as solid as the observed slick.
        getFillColor: (f) => {
          const h = Number(f.properties?.horizon_h ?? f.properties?.horizon_hours ?? 24);
          return [...C.forecast, h <= 6 ? 62 : h <= 12 ? 44 : 26];
        },
        getLineColor: [...C.forecast, 190],
        getLineWidth: 1.3, lineWidthUnits: "pixels",
        pickable: true,
      }));
    }
    if (rl?.origin && layersOn.origin !== false) {
      out.push(new GeoJsonLayer({
        id: "globe-origin",
        data: rl.origin,
        filled: true, stroked: true,
        pointType: "circle",
        getPointRadius: 60, pointRadiusMinPixels: 1, pointRadiusMaxPixels: 2.2,
        getFillColor: (f) => ((f.properties?.feature_type || f.properties?.kind) === "ellipse"
          ? [...C.origin, 26] : [...C.hindcast, 40 + (f.properties?.weight ?? 0.5) * 150]),
        getLineColor: [...C.origin, 220],
        getLineWidth: 1.4, lineWidthUnits: "pixels",
        pickable: true,
      }));
    }
    if (rl?.slick && layersOn.slick !== false) {
      out.push(new GeoJsonLayer({
        id: "globe-slick",
        data: rl.slick,
        filled: true, stroked: true,
        getFillColor: [...C.slick, 110],
        getLineColor: [...C.slick, 255],
        getLineWidth: 2, lineWidthUnits: "pixels", lineWidthMinPixels: 1,
        pickable: true,
      }));
    }
    if (rl?.tracks?.length && layersOn.tracks !== false) {
      out.push(new PathLayer({
        id: "globe-tracks",
        data: rl.tracks,
        getPath: (d) => d.path,
        getColor: (d) => (d.rank === 1 ? [...C.suspect, 240]
          : d.rank ? [...C.candidate, 220]
            : d.filtered ? [...C.vessel, 70] : [...C.track, 150]),
        getWidth: (d) => (d.rank === 1 ? 2.6 : d.rank ? 1.8 : 1.1),
        widthUnits: "pixels", widthMinPixels: 1,
        pickable: true, autoHighlight: true,
      }));
    }

    /* --- live AIS ------------------------------------------------------- */
    if (vessels?.length && layersOn.vessels !== false) {
      const placed = vessels.filter((v) => v.lon !== null && v.lon !== undefined
        && v.lat !== null && v.lat !== undefined);
      out.push(new ScatterplotLayer({
        id: "globe-vessels",
        data: placed,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 2.6,
        radiusUnits: "pixels", radiusMinPixels: 2, radiusMaxPixels: 6,
        getFillColor: (d) => [...vesselColour(d.vessel_type, C),
          (d.sog_kn ?? 0) > 0.5 ? 235 : 170],
        stroked: true,
        getLineColor: theme === "light" ? [255, 255, 255, 200] : [6, 10, 18, 220],
        getLineWidth: 1, lineWidthUnits: "pixels",
        pickable: true, autoHighlight: true,
        highlightColor: [...C.selection, 200],
        updateTriggers: { getFillColor: [theme] },
      }));

      /* Course sticks, drawn only for vessels that actually transmit a
       * course. A vessel with cog null gets a dot and no stick -- drawing
       * one pointing north would be a fabricated heading. */
      if (moving.length) {
        out.push(new LineLayer({
          id: "globe-vessel-course",
          data: moving,
          getSourcePosition: (d) => [d.lon, d.lat],
          getTargetPosition: (d) => stickEnd(d.lon, d.lat, d.cog_deg, 2.2),
          getColor: (d) => [...vesselColour(d.vessel_type, C), 150],
          getWidth: 1, widthUnits: "pixels",
          pickable: false,
        }));
        /* The direction pulse: a point that travels from the vessel to the
         * end of its own course stick. It never leaves the stick, so it
         * never implies a position that was not transmitted. */
        const phase = (tick % 40) / 40;
        out.push(new ScatterplotLayer({
          id: "globe-vessel-pulse",
          data: moving,
          getPosition: (d) => {
            const f = (phase + ((d.mmsi ?? 0) % 7) / 7) % 1;
            return stickEnd(d.lon, d.lat, d.cog_deg, 2.2 * f);
          },
          getRadius: 1.4, radiusUnits: "pixels", radiusMinPixels: 1, radiusMaxPixels: 2,
          getFillColor: (d) => [...vesselColour(d.vessel_type, C), 220 * (1 - phase * 0.7)],
          pickable: false,
          updateTriggers: { getPosition: [tick], getFillColor: [tick] },
        }));
      }

      const sel = selected?.kind === "vessel"
        ? placed.find((v) => String(v.mmsi) === String(selected.id)) : null;
      if (sel) {
        out.push(new ScatterplotLayer({
          id: "globe-vessel-selected",
          data: [sel],
          getPosition: (d) => [d.lon, d.lat],
          getRadius: 9 + 3 * Math.sin(tick / 6),
          radiusUnits: "pixels",
          stroked: true, filled: false,
          getLineColor: [...C.selection, 230],
          getLineWidth: 1.5, lineWidthUnits: "pixels",
          pickable: false,
          updateTriggers: { getRadius: [tick] },
        }));
      }
    }

    /* --- incidents ----------------------------------------------------- */
    if (incidents?.length && layersOn.incidents !== false) {
      const placed = incidents.filter((i) => i.lon !== null && i.lon !== undefined
        && i.lat !== null && i.lat !== undefined);
      // A soft halo so an incident reads as an event, not a vessel.
      out.push(new ScatterplotLayer({
        id: "globe-incident-halo",
        data: placed,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => (d.severity === "critical" ? 16 : d.severity === "high" ? 13 : 11),
        radiusUnits: "pixels",
        getFillColor: [...C.incident, 40],
        pickable: false,
      }));
      out.push(new ScatterplotLayer({
        id: "globe-incidents",
        data: placed,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: (d) => (d.severity === "critical" ? 6.5 : d.severity === "high" ? 5.5 : 4.5),
        radiusUnits: "pixels", radiusMinPixels: 4,
        stroked: true,
        getFillColor: (d) => (selected?.kind === "incident" && selected.id === d.id
          ? [...C.selection, 255] : [...C.incident, 190]),
        getLineColor: theme === "light" ? [255, 255, 255, 250] : [255, 235, 235, 245],
        getLineWidth: 1.4, lineWidthUnits: "pixels",
        pickable: true, autoHighlight: true,
        updateTriggers: { getFillColor: [selected] },
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
          filled: true, stroked: false,
          getFillColor: bad ? [...C.editorInvalid, 34] : [...C.editorEdge, 30],
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
          getColor: (d) => (d.offending ? [...C.editorInvalid, 255] : [...C.editorEdge, 235]),
          getWidth: (d) => (d.offending ? 3 : 2),
          widthUnits: "pixels", widthMinPixels: 2,
          pickable: false,
          updateTriggers: { getColor: [bad], getWidth: [bad] },
        }));
      }

      out.push(new ScatterplotLayer({
        id: "globe-editor-vertices",
        data: ring.map((p, i) => ({ position: p, index: i })),
        getPosition: (d) => d.position,
        getRadius: 5, radiusUnits: "pixels", radiusMinPixels: 4,
        stroked: true,
        getFillColor: (d) => (d.index === editor.selectedIndex
          ? [...C.editorEdge, 255] : [...C.editorVertex, 235]),
        getLineColor: theme === "light" ? [255, 255, 255, 255] : [8, 16, 28, 255],
        getLineWidth: 1.5, lineWidthUnits: "pixels",
        pickable: true, autoHighlight: true,
        updateTriggers: { getFillColor: [editor.selectedIndex] },
      }));

      /* Vertex ordinals. An operator moving "point 3" needs to know which one
       * point 3 is. */
      out.push(new TextLayer({
        id: "globe-editor-labels",
        data: ring.map((p, i) => ({ position: p, label: String(i + 1) })),
        getPosition: (d) => d.position,
        getText: (d) => d.label,
        getSize: 10, sizeUnits: "pixels",
        getColor: theme === "light" ? [255, 255, 255, 255] : [8, 16, 28, 255],
        characterSet: "0123456789",
        getTextAnchor: "middle", getAlignmentBaseline: "center",
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        pickable: false,
      }));
    }

    return out;
  }, [colours, theme, layersOn, zones, incidents, vessels, moving, runLayers, editor,
      selected, tick]);

  return (
    <GlobeScene
      viewState={viewState}
      onViewStateChange={onViewStateChange}
      onPointerMove={onPointerMove}
      onPointerLeave={onPointerLeave}
      basemap={basemap}
      theme={theme}
      graticule={layersOn.graticule !== false}
      layers={layers}
      onHover={handleHover}
      onClick={handleClick}
      getCursor={({ isDragging, isHovering }) =>
        (isDragging ? "grabbing" : editor?.active ? "crosshair" : isHovering ? "pointer" : "grab")}
    >
      {hover && <GlobeTooltip x={hover.x} y={hover.y} layer={hover.layer} object={hover.object} />}
      {children}
    </GlobeScene>
  );
}

/* Hover readout. Only fields the object actually carries are rendered --
 * a null vessel name shows "not transmitted", never a blank that reads as a
 * missing UI element, and never an invented name. */
function GlobeTooltip({ x, y, layer, object }) {
  const rows = [];
  let title = "";
  const p = object?.properties || {};

  if (layer === "globe-zones") {
    title = p.name || p.id;
    rows.push(["Kind", p.kind]);
    if (p.jurisdiction) rows.push(["Jurisdiction", p.jurisdiction]);
    if (p.protected) rows.push(["Boundary", "PROTECTED"]);
    if (p.area_km2 !== null && p.area_km2 !== undefined) {
      rows.push(["Area", `${Number(p.area_km2).toLocaleString()} km²`]);
    }
    const officer = (p.officers || []).find((o) => o.is_primary);
    rows.push(["Officer", officer ? (officer.display_name || officer.email) : "unassigned"]);
  } else if (layer === "globe-vessels") {
    title = object.vessel_name || `MMSI ${object.mmsi}`;
    rows.push(["MMSI", String(object.mmsi)]);
    if (!object.vessel_name) rows.push(["Name", "not transmitted"]);
    rows.push(["Type", object.vessel_type || "unknown"]);
    rows.push(["SOG", object.sog_kn === null || object.sog_kn === undefined
      ? "not transmitted" : `${Number(object.sog_kn).toFixed(1)} kn`]);
    rows.push(["COG", object.cog_deg === null || object.cog_deg === undefined
      ? "not transmitted" : `${Number(object.cog_deg).toFixed(0)}°`]);
    if (object.destination) rows.push(["Dest", object.destination]);
    if (object.report_utc) rows.push(["Report", String(object.report_utc).replace("T", " ").slice(0, 16) + "Z"]);
    rows.push(["Source", `${object.provider || "AISStream"} · LIVE`]);
  } else if (layer === "globe-incidents") {
    title = object.title || object.id;
    rows.push(["Incident", object.id]);
    if (object.severity) rows.push(["Severity", object.severity.toUpperCase()]);
    if (object.status) rows.push(["Status", object.status]);
    if (object.zone_id) rows.push(["Zone", object.zone_id]);
    if (object.area_km2 != null) rows.push(["Area", `${Number(object.area_km2).toFixed(2)} km²`]);
    if (object.detection_confidence != null) rows.push(["Confidence", `${(object.detection_confidence * 100).toFixed(0)}%`]);
  } else if (layer === "globe-slick") {
    title = `${p.slick_id || "Detected slick"} · OBSERVED`;
    if (p.area_km2 != null) rows.push(["Area", `${Number(p.area_km2).toFixed(2)} km²`]);
    if (p.confidence != null) rows.push(["Confidence", `${(p.confidence * 100).toFixed(1)}%`]);
    if (p.orientation_deg != null) rows.push(["Orientation", `${p.orientation_deg}°`]);
  } else if (layer === "globe-forecast") {
    title = `Forecast +${p.horizon_h ?? p.horizon_hours ?? "?"} h`;
    if (p.area_km2 != null) rows.push(["Area", `${p.area_km2} km²`]);
    if (p.confidence_level) rows.push(["Confidence", p.confidence_level]);
  } else if (layer === "globe-origin") {
    const kind = p.feature_type || p.kind;
    title = kind === "ellipse" ? "Origin uncertainty ellipse" : "Hindcast particle";
    if (p.step_index != null) rows.push(["Step", `T−${p.step_index} h`]);
    if (p.weight != null) rows.push(["Weight", Number(p.weight).toFixed(3)]);
    if (p.confidence_level) rows.push(["Confidence", p.confidence_level]);
  } else if (layer === "globe-tracks") {
    title = `${object.name || `MMSI ${object.mmsi}`}${object.rank ? ` · RANK #${object.rank}` : ""}`;
    rows.push(["MMSI", String(object.mmsi)]);
    if (object.type) rows.push(["Type", object.type]);
    if (object.rank) rows.push(["Score", `${(object.score * 100).toFixed(0)}%`]);
    if (object.filtered) rows.push(["Eliminated", object.filterReason || "yes"]);
    if (object.source) rows.push(["Source", String(object.source).toUpperCase()]);
  } else {
    return null;
  }

  return <MapTip x={x + 12} y={y + 12} title={title} rows={rows} testid="globe-tooltip" />;
}

/* ------------------------------------------------------- cursor readout --- */

/** The lat/lon strip. Required by the zone-splitting spec: the editor must
 *  show the actual coordinate under the cursor, because the operator is
 *  drawing a line that decides who gets an alert. */
export function CoordinateReadout({ cursor, zone, zoom, className = "" }) {
  return (
    <div className={`map-hud globe-coords ${className}`} data-testid="globe-coords">
      <span className="globe-coord-pair">
        <span className="map-hud-k">LAT</span>
        <span data-testid="cursor-lat">{fmtLat(cursor?.lat)}</span>
      </span>
      <span className="globe-coord-pair">
        <span className="map-hud-k">LON</span>
        <span data-testid="cursor-lon">{fmtLon(cursor?.lon)}</span>
      </span>
      {cursor && (
        <span className="globe-coord-pair dim">
          <span className="map-hud-k" />
          <span>{fmtDegMin(cursor.lat, true)} &nbsp; {fmtDegMin(cursor.lon, false)}</span>
        </span>
      )}
      {zoom != null && (
        <span className="globe-coord-pair">
          <span className="map-hud-k">ZOOM</span>
          <span>{Number(zoom).toFixed(2)}</span>
        </span>
      )}
      {/* Which zone the cursor is over, resolved server-side. "outside all
          operational zones" is a real answer and is shown as one. */}
      {zone !== undefined && (
        <span className="globe-coord-pair">
          <span className="map-hud-k">ZONE</span>
          <span data-testid="cursor-zone">{zone ? (zone.name || zone.id) : "outside all zones"}</span>
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
 *  bugs come from. */
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
    commit(points.map((p, i) => (i === index ? [lon, lat] : p)), undefined);
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
    points, closed, selectedIndex, setSelectedIndex, selfIntersection, geometry,
    canUndo: history.current.length > 0,
    canRedo: future.current.length > 0,
    canClose: points.length >= 3 && !selfIntersection,
    addPoint, movePoint, deletePoint, close, reopen, reset, undo, redo,
    /* Planar and never shown as an area -- only used to warn that a ring has
     * turned itself inside out while being dragged. */
    signedDegArea: points.length >= 3 ? signedDegArea(points) : 0,
  };
}
