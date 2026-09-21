/* The workspace map: the persistent centre of the investigation.
 *
 * Layer order is bottom-up and deliberate: basemap, SAR raster, tile grid,
 * hindcast cloud, forecast, origin ring, vessel tracks, slick + geometry,
 * annotations — the thing under investigation is never hidden by the
 * evidence around it.
 *
 * Every geometry is read from a contract file or an API response; nothing
 * here invents data. The time value (epoch ms) drives vessel interpolation,
 * the hindcast timestep shown, and which forecast horizon is emphasised.
 *
 * Basemaps (frames 02-15 switch between them):
 *   satellite   ESRI World Imagery raster
 *   geographic  OpenStreetMap raster, desaturated
 *   maritime    dark vector style, for traffic
 *   sar         no basemap: the calibrated SAR raster is the surface
 * Every raster basemap fails soft — MapLibre keeps the background colour and
 * the SAR raster becomes the de-facto basemap, which is the offline rule.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  GeoJsonLayer, ScatterplotLayer, PathLayer, PolygonLayer,
  TextLayer,
} from "@deck.gl/layers";
import { PathStyleExtension } from "@deck.gl/extensions";

import { WS } from "./palette";
import { MAP_COLORS } from "../maps/palette";
import {
  circleRing, destination, trackStateAt, trackPathUntil, bearingDeg, fmtUtc, haversineKm,
  aisGaps, AIS_GAP_MIN,
} from "../../lib/replay";
import { segments as measureSegments } from "../../lib/geodesy";
import { originEstimate, vectorArrows } from "../../lib/drift";
import { gateOf } from "../../lib/cinematic";
import MaritimeGlobe from "../maps/MaritimeGlobe";

const span = (t, a, b) => Math.max(0, Math.min(1, (t - a) / (b - a)));

const dashExt = [new PathStyleExtension({ dash: true })];
const CHARSET = "auto";

/* The workspace names a basemap after the ANALYSIS it belongs to -- the drift
 * stage wants satellite, the tiling stage wants a plain chart. Those names map
 * onto the three real basemaps the one engine offers (components/maps/basemaps);
 * "sar" and "environmental" are the dark chart with the run's own raster or
 * forcing vectors over it, which is what made them distinct in the first place. */
export const BASEMAP_FOR = {
  satellite: "satellite",
  geographic: "geopolitical",
  maritime: "dark",
  sar: "dark",
  environmental: "dark",
};

export const BASEMAPS = [
  { id: "satellite", label: "Satellite" },
  { id: "geographic", label: "Geographic" },
  { id: "sar", label: "SAR (VV)" },
  { id: "maritime", label: "Maritime" },
  { id: "environmental", label: "Environmental" },
];

/** Ellipse ring from slick geometry properties (centroid + axes + bearing). */
function slickEllipse(p, n = 72) {
  const [cx, cy] = p.centroid || [];
  if (cx == null) return null;
  const a = (p.major_axis_m || 0) / 2000;   // km, semi
  const b = (p.minor_axis_m || 0) / 2000;
  const th = ((p.orientation_deg || 0) * Math.PI) / 180;
  const ring = [];
  for (let i = 0; i <= n; i++) {
    const t = (i / n) * 2 * Math.PI;
    const dx = a * Math.cos(t), dy = b * Math.sin(t);
    const km = Math.hypot(dx, dy);
    const brg = ((Math.atan2(dx * Math.sin(th) + dy * Math.cos(th),
                             dx * Math.cos(th) - dy * Math.sin(th)) * 180) /
                 Math.PI + 360) % 360;
    ring.push(destination(cx, cy, km, brg));
  }
  return ring;
}

/** Diagonal hatch lines clipped to a bbox — the look-alike fill pattern.
 *  Hatching (not a solid fill) is the visual statement that this region is
 *  reported but NOT counted as oil. */
function hatchLines(w, s, e, n, count = 7) {
  const lines = [];
  for (let i = 1; i < count; i++) {
    const t = i / count;
    lines.push([[w, s + (n - s) * t], [w + (e - w) * t, s]]);
    lines.push([[w + (e - w) * t, n], [e, s + (n - s) * t]]);
  }
  return lines;
}

const rect = ([w, s, e, n]) => [[w, s], [e, s], [e, n], [w, n], [w, s]];

/** Outer rings of a slick feature (Polygon or MultiPolygon). */
function outerRings(f) {
  const g = f?.geometry;
  if (g?.type === "Polygon") return [g.coordinates[0]];
  if (g?.type === "MultiPolygon") return g.coordinates.map((p) => p[0]);
  return [];
}

/** The first `frac` of a ring, for the boundary tracing itself (frame 10). */
function partialRing(ring, frac) {
  const n = Math.max(2, Math.ceil(ring.length * Math.max(0, Math.min(1, frac))));
  return ring.slice(0, n);
}

/**
 * `reveal` (all optional; null = fully drawn) is the presentation layer's
 * only handle on this map. Each value slices a REAL artefact:
 *   sar          0..1 opacity of the calibrated SAR raster (the crossfade)
 *   detectBox    0..1 the screening box that holds the analysed slick
 *   slick        0..1 candidate boxes → mask fill → boundary traced
 *   wind/currents 0..1 share of the forcing arrows, nearest the slick first
 *   origin       0..1 growth of the published uncertainty ring
 *   tracksUntil  draw each AIS track only up to the clock (by its own fixes)
 *   gate         0..3 how many attribution gates have dimmed their vessels
 *   ranked       how many ranked candidates are lit, in rank order
 *   forecastUpTo horizon (h) up to which forecast footprints are shown
 *   vectorDim    subdue the forcing arrows behind the drift/AIS layers
 *   dimOthers    attribution: everything but the selected track subdued
 */
export default function WorkspaceMap({
  view, onViewChange, show, layers, timeMs, sceneT0, runId,
  selectedMmsi, onSelect, onHover, maxStep, measure, forcing,
  basemap = "satellite", onCursor, onViewport, tiles, aoi, footprints,
  draw, candidateLabels = false, dimOthers = false, onClickMap, reveal = null,
}) {
  const { sceneMeta, slick, origin, forecast, vessels, suspects, detect } = layers;
  const [pinned, setPinned] = useState(null);
  const hover = (info) => onHover?.(info ?? pinned);
  const globe = useRef(null);

  /* The map owns its camera; `view` is a COMMAND channel. A view the parent
   * built (a stage flight, a zoom button) is flown; a view that is only the
   * map's own report coming back is tagged `__echo` and ignored, so a report
   * from the middle of a flight cannot cancel the flight. */
  const initialCamera = useRef({
    longitude: view?.longitude ?? 80.32, latitude: view?.latitude ?? 13.05, zoom: view?.zoom ?? 5.5,
  });
  useEffect(() => {
    if (!view || view.__echo) return;
    // "Frame this box" is the map's to work out; "go here" is a plain flight.
    if (view.fitBbox) globe.current?.fitBounds(view.fitBbox, view.transitionDuration ?? 0, { padding: view.fitPad ?? 70, maxZoom: 13.2 });
    else globe.current?.flyTo(view, view.transitionDuration ?? 0);
  }, [view]);

  const handleCamera = useCallback((camera, info) => {
    onViewChange?.({ viewState: { ...camera, __echo: true } });
    const map = globe.current?.getMap?.();
    if (!map || !info?.end) return;
    const el = map.getContainer();
    // What the parent needs to anchor HTML callouts to map points. A plain
    // object rather than a WebMercatorViewport: the projection is the map's
    // now, and on the globe there is no mercator viewport to hand out.
    onViewport?.({
      width: el.clientWidth, height: el.clientHeight,
      project: (lonlat) => globe.current?.project(lonlat) || null,
    });
  }, [onViewChange, onViewport]);
  const rv = reveal || {};
  const slickP = slick?.features?.[0]?.properties;
  const slickC = slickP?.centroid || null;

  const bbox = sceneMeta?.bbox;
  const suspectRank = useMemo(() => {
    const m = new Map();
    for (const s of suspects?.suspects ?? []) m.set(s.mmsi, s);
    return m;
  }, [suspects]);


  /* vessels_geojson features -> track objects with per-fix epoch times */
  const tracks = useMemo(() => (vessels?.features ?? []).map((f) => {
    const p = f.properties;
    const s = suspectRank.get(p.mmsi);
    return {
      mmsi: p.mmsi, name: p.vessel_name, type: p.vessel_type,
      rank: s?.rank ?? p.rank ?? null, score: s?.total_score ?? p.total_score ?? null,
      filtered: Boolean(p.filtered), filterReason: p.filter_reason,
      source: p.source, distanceKm: p.distance_km, durationH: p.duration_h,
      path: f.geometry.coordinates,
      times: (p.times_epoch ?? []).map((v) => (v == null ? null : v * 1000)),
      sog: p.sog_kn ?? [], headings: p.headings_deg ?? [],
    };
  }), [vessels, suspectRank]);

  /* hindcast particles grouped by timestep, ellipses by step */
  const hind = useMemo(() => {
    const pts = [], ells = [];
    let max = 0;
    for (const f of origin?.features ?? []) {
      const p = f.properties || {};
      const kind = p.feature_type || p.kind;
      if (kind === "ellipse") { ells.push(f); continue; }
      if (p.particle_id === undefined) continue;
      const s = p.step_index ?? 0;
      max = Math.max(max, s);
      pts.push({ pos: f.geometry.coordinates, w: p.weight ?? 0.5, step: s });
    }
    ells.sort((a, b) => (a.properties.step_index ?? 0) - (b.properties.step_index ?? 0));
    return { pts, ells, max };
  }, [origin]);

  /* current hindcast step from the global clock (past of sceneT0 only) */
  const step = useMemo(() => {
    if (timeMs == null || !sceneT0) return 0;
    const back = Math.round((sceneT0 - timeMs) / 3.6e6);
    return Math.max(0, Math.min(hind.max || maxStep || 24, back));
  }, [timeMs, sceneT0, hind.max, maxStep]);

  /* forecast horizon emphasised by the clock (future of sceneT0) */
  const aheadH = timeMs != null && sceneT0 ? (timeMs - sceneT0) / 3.6e6 : 0;

  const deck = [];

  /* --------------------------------------------------- SAR scene raster --
   * Tiled, not a single stretched preview, so the map reaches native 10 m
   * resolution. `sarStretch` is passed through to the server so what is drawn
   * is what the segmenter saw. */
  /* The SAR raster is not a deck layer any more: the tile server reads the
   * full-resolution scene for every tile and has no overviews (~30 s a tile at
   * z8, measured -- BACKEND_GAPS G15), so a deck TileLayer at minZoom 0 asks
   * for tiles that will not arrive. MaritimeGlobe draws the run quicklook
   * below z10 and the tile pyramid above it, with a loading state. */

  /* --------------------------------------------------------- tile grid --
   * The grid the detector actually walked (raster shape / model tile size),
   * from /tiles/{run}/info. The selected tile is the one holding the analysed
   * slick's centroid. Frame 08 draws exactly this: a lattice, one cyan cell. */
  if (show.tiles && tiles?.grid) {
    const { cols, rows, bounds } = tiles.grid;
    const [w, s, e, n] = bounds;
    const lines = [];
    for (let c = 0; c <= cols; c++) {
      const x = w + (c / cols) * (e - w);
      lines.push({ path: [[x, s], [x, n]] });
    }
    for (let r = 0; r <= rows; r++) {
      const y = s + (r / rows) * (n - s);
      lines.push({ path: [[w, y], [e, y]] });
    }
    deck.push(new PathLayer({
      id: "ws-tile-grid", data: lines,
      getPath: (d) => d.path, getColor: [...WS.tile, 70],
      getWidth: 1, widthUnits: "pixels",
    }));
    if (tiles.selected?.bbox) {
      deck.push(new PathLayer({
        id: "ws-tile-selected", data: [{ path: rect(tiles.selected.bbox) }],
        getPath: (d) => d.path, getColor: [...WS.tile, 255],
        getWidth: 2.2, widthUnits: "pixels",
      }));
      deck.push(new PolygonLayer({
        id: "ws-tile-selected-fill", data: [{ poly: rect(tiles.selected.bbox) }],
        getPolygon: (d) => d.poly, filled: true, stroked: false,
        getFillColor: [...WS.tile, 22],
      }));
    }
  }

  /* --------------------------------------------------- hindcast (magenta) */
  /* The backward drift, drawn to be read from across the room: a thick
   * glowing trail through the engine's own ellipse centres, arrowheads
   * pointing BACK in time, a labelled tick every six hours, the uncertainty
   * ellipses left behind as ghosts, and the particle cloud at the current
   * step. While the presentation plays the trail grows with the clock; when
   * the analyst is driving, the whole trail to the last step is shown. */
  const estO = originEstimate(origin);
  if (show.hindcast && hind.pts.length) {
    const upto = reveal ? step : (hind.max || step);
    /* A step can carry more than one contour (Engine B writes 0.5 beside 0.9).
     * The WIDEST is the uncertainty ellipse -- the trail, the ghosts and the
     * origin all read from it -- and the narrower ones are drawn inside it so
     * the analyst can see where the cloud is dense, not only how far it spread. */
    const byStep = new Map();
    const innerAt = new Map();
    for (const f of hind.ells) {
      const q = f.properties || {}; const k = q.step_index ?? 0;
      if (!Array.isArray(q.center)) continue;
      const old = byStep.get(k);
      if (!old) { byStep.set(k, f); continue; }
      const wider = (q.confidence_level ?? 0) > (old.properties.confidence_level ?? 0);
      byStep.set(k, wider ? f : old);
      innerAt.set(k, wider ? old : f);
    }
    const stepsSorted = [...byStep.keys()].sort((x, y) => x - y).filter((k) => k <= upto);
    /* The run integrates back `backtrack_hours`, but it only CLAIMS an origin
     * inside its published origin window. Drawing both at the same weight
     * reads as "the oil came from out there": on the Gulf flagship the window
     * ends 7 km from the slick and the integration runs on to 32 km. So the
     * supported part is the bright trail and the rest is a faint, dashed
     * "explored, not supported" tail that is labelled as such. */
    const winStart = Date.parse(origin?.metadata?.origin_window_start_utc ?? "");
    const atOf = (k) => Date.parse(byStep.get(k).properties.t_utc ?? "");
    const inWin = (k) => !Number.isFinite(winStart) || !Number.isFinite(atOf(k)) || atOf(k) >= winStart;
    const posOf = (k) => byStep.get(k).properties.center;
    const stepsIn = stepsSorted.filter(inWin);
    const stepsOut = stepsSorted.filter((k) => !inWin(k));
    const trail = stepsIn.map(posOf);
    const beyond = stepsOut.length
      ? [...(stepsIn.length ? [posOf(stepsIn[stepsIn.length - 1])] : []), ...stepsOut.map(posOf)] : [];

    /* ghosts of where the cloud has been, every third hour */
    const ghosts = stepsSorted.filter((k) => k % 3 === 0 && k !== step).map((k) => ({ f: byStep.get(k), win: inWin(k) }));
    if (ghosts.length) {
      deck.push(new GeoJsonLayer({
        id: "ws-hindcast-ghosts", data: { type: "FeatureCollection", features: ghosts.map((g) => ({ ...g.f, properties: { ...g.f.properties, _win: g.win } })) },
        stroked: true, filled: true,
        getFillColor: (f) => [...WS.hindcast, f.properties._win ? 14 : 4],
        getLineColor: (f) => [...WS.hindcast, f.properties._win ? 80 : 26],
        getLineWidth: 1, lineWidthUnits: "pixels",
        updateTriggers: { getFillColor: upto, getLineColor: upto },
      }));
    }
    const vis = hind.pts.filter((d) => d.step === step || (!reveal && estO?.stepIndex != null && d.step === estO.stepIndex));
    deck.push(new ScatterplotLayer({
      id: "ws-hindcast", data: vis,
      getPosition: (d) => d.pos, getRadius: 90,
      radiusMinPixels: 2.2, radiusMaxPixels: 5,
      getFillColor: (d) => [...WS.hindcast, 90 + d.w * 165],
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind: "hindcast", title: `Hindcast particle · T−${i.object.step} h`,
        rows: [["weight", i.object.w.toFixed(3)],
               ["time", fmtUtc(sceneT0 - i.object.step * 3.6e6)]],
      } : null),
      updateTriggers: { getPosition: step },
    }));
    const inner = innerAt.get(Math.min(step, hind.max));
    if (inner) {
      deck.push(new GeoJsonLayer({
        id: "ws-hindcast-inner",
        data: { type: "FeatureCollection", features: [inner] },
        stroked: true, filled: true,
        getFillColor: [...WS.hindcast, 70],
        getLineColor: [...WS.hindcast, 200],
        getLineWidth: 1.4, lineWidthUnits: "pixels",
        getDashArray: [4, 3], extensions: dashExt,
        updateTriggers: { getLineColor: step },
      }));
    }
    const ell = byStep.get(Math.min(step, hind.max)) || hind.ells[Math.min(step, hind.ells.length - 1)];
    if (ell) {
      deck.push(new GeoJsonLayer({
        id: "ws-hindcast-ellipse",
        data: { type: "FeatureCollection", features: [ell] },
        stroked: true, filled: true, getFillColor: [...WS.hindcast, 38],
        getLineColor: [...WS.hindcast, 255],
        getLineWidth: 2.6, lineWidthUnits: "pixels",
        updateTriggers: { getLineColor: step },
      }));
    }
    if (beyond.length >= 2) {
      deck.push(new PathLayer({
        id: "ws-hindcast-beyond", data: [{ path: beyond }],
        getPath: (d) => d.path, getColor: [...WS.hindcast, 95],
        getWidth: 1.4, widthUnits: "pixels", getDashArray: [5, 5], extensions: dashExt,
        updateTriggers: { getPath: upto },
      }));
      const end = beyond[beyond.length - 1];
      deck.push(new TextLayer({
        id: "ws-hindcast-beyond-label", data: [{ pos: end }],
        getPosition: (d) => d.pos,
        getText: () => `BACKTRACK LIMIT  T−${stepsOut[stepsOut.length - 1]} h\nexplored, outside the origin window`,
        getSize: 11, getColor: [216, 180, 254, 215], fontFamily: "Inter, sans-serif", fontWeight: 600,
        getTextAnchor: "middle", getAlignmentBaseline: "top", getPixelOffset: [0, 12], characterSet: CHARSET,
        background: true, getBackgroundColor: [30, 8, 40, 190], backgroundPadding: [6, 3, 6, 3],
        updateTriggers: { getPosition: upto, getText: upto },
      }));
    }
    if (trail.length >= 2) {
      deck.push(new PathLayer({
        id: "ws-hindcast-glow", data: [{ path: trail }],
        getPath: (d) => d.path, getColor: [...WS.hindcast, 70],
        getWidth: 11, widthUnits: "pixels", capRounded: true, jointRounded: true,
        updateTriggers: { getPath: upto },
      }));
      deck.push(new PathLayer({
        id: "ws-hindcast-trail", data: [{ path: trail }],
        getPath: (d) => d.path, getColor: [240, 171, 252, 255],
        getWidth: 3.6, widthUnits: "pixels", capRounded: true, jointRounded: true,
        updateTriggers: { getPath: upto },
      }));
      /* arrowheads: the direction of travel BACK in time */
      const heads = [];
      const stride = Math.max(2, Math.round(trail.length / 7));
      for (let i = stride; i < trail.length; i += stride) {
        const [x1, y1] = trail[i - 1], [x2, y2] = trail[i];
        if (x1 === x2 && y1 === y2) continue;
        heads.push({ pos: trail[i], ang: -bearingDeg(y1, x1, y2, x2) });
      }
      deck.push(new TextLayer({
        id: "ws-hindcast-heads", data: heads,
        getPosition: (d) => d.pos, getText: () => "▲", getSize: 17, getAngle: (d) => d.ang,
        getColor: [250, 232, 255, 255], fontFamily: "Segoe UI Symbol, sans-serif", characterSet: CHARSET,
        outlineWidth: 2, outlineColor: [88, 12, 110, 255], fontSettings: { sdf: true },
        updateTriggers: { getPosition: upto },
      }));
      /* a labelled tick every six hours back */
      const ticks = stepsIn.filter((k) => k > 0 && k % 6 === 0).map((k) => ({ pos: posOf(k), k }));
      if (stepsOut.length && stepsIn.length) {
        const edge = stepsIn[stepsIn.length - 1];
        deck.push(new ScatterplotLayer({
          id: "ws-hindcast-winedge", data: [{ pos: posOf(edge) }],
          getPosition: (d) => d.pos, getRadius: 7, radiusUnits: "pixels",
          stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
          getFillColor: [...WS.hindcast, 255], getLineColor: [255, 255, 255, 255],
        }));
        deck.push(new TextLayer({
          id: "ws-hindcast-winedge-label", data: [{ pos: posOf(edge) }],
          getPosition: (d) => d.pos, getText: () => `ORIGIN WINDOW STARTS  T−${edge} h`,
          getSize: 12, getColor: [250, 232, 255, 255], fontFamily: "Inter, sans-serif", fontWeight: 700,
          getTextAnchor: "end", getAlignmentBaseline: "center", getPixelOffset: [-14, 0], characterSet: CHARSET,
          background: true, getBackgroundColor: [88, 12, 110, 235], backgroundPadding: [7, 4, 7, 4],
        }));
      }
      deck.push(new ScatterplotLayer({
        id: "ws-hindcast-ticks", data: ticks,
        getPosition: (d) => d.pos, getRadius: 1, radiusMinPixels: 6, radiusMaxPixels: 6,
        stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
        getFillColor: [...WS.hindcast, 255], getLineColor: [255, 255, 255, 255],
      }));
      deck.push(new TextLayer({
        id: "ws-hindcast-ticklabels", data: ticks,
        getPosition: (d) => d.pos, getText: (d) => `T−${d.k} h`,
        getSize: 13, getColor: [250, 232, 255, 255], fontFamily: "JetBrains Mono, monospace", fontWeight: 700,
        getTextAnchor: "start", getAlignmentBaseline: "center", getPixelOffset: [11, 0],
        characterSet: CHARSET, background: true, getBackgroundColor: [40, 8, 52, 225], backgroundPadding: [5, 3, 5, 3],
      }));
      if (reveal && step > 0) {
        const head = trail[trail.length - 1];
        deck.push(new ScatterplotLayer({
          id: "ws-hindcast-head", data: [{ pos: head }],
          getPosition: (d) => d.pos, getRadius: 1, radiusMinPixels: 9, radiusMaxPixels: 9,
          stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2.5,
          getFillColor: [...WS.hindcast, 255], getLineColor: [255, 255, 255, 255],
          updateTriggers: { getPosition: step },
        }));
        deck.push(new TextLayer({
          id: "ws-hindcast-headlabel", data: [{ pos: head }],
          getPosition: (d) => d.pos, getText: () => `HINDCAST POINT\nT−${step} h`,
          getSize: 13, getColor: [255, 255, 255, 255], fontFamily: "Inter, sans-serif", fontWeight: 700,
          getTextAnchor: "end", getAlignmentBaseline: "center", getPixelOffset: [-16, 0],
          characterSet: CHARSET, background: true, getBackgroundColor: [134, 25, 143, 235], backgroundPadding: [7, 4, 7, 4],
          updateTriggers: { getPosition: step, getText: step },
        }));
      }
    }
  }

  /* ------------------------------------------ wind / current vectors ---- */
  for (const [kind, color] of [["wind", WS.wind], ["currents", WS.current]]) {
    if (!show[kind] || !forcing?.[kind]) continue;
    const f = forcing[kind];
    let { segments, timeUtc } = vectorArrows(f, timeMs);
    if (!segments.length) continue;
    /* Progressive reveal: arrows come in nearest the slick first, so the
     * field appears to be sampled outward from the thing under study. Every
     * arrow is still the grid's own; only the order of appearance is ours. */
    const frac = rv[kind];
    if (frac != null && frac < 1) {
      const pairs = [];
      for (let k = 0; k + 1 < segments.length; k += 2) pairs.push([segments[k], segments[k + 1]]);
      const ref = slickC || [(bbox?.[0] + bbox?.[2]) / 2 || 0, (bbox?.[1] + bbox?.[3]) / 2 || 0];
      pairs.sort((a, b) => haversineKm(ref[1], ref[0], a[0].path[0][1], a[0].path[0][0])
        - haversineKm(ref[1], ref[0], b[0].path[0][1], b[0].path[0][0]));
      segments = pairs.slice(0, Math.ceil(pairs.length * frac)).flat();
      if (!segments.length) continue;
    }
    const alpha = rv.vectorDim ? 95 : 200;
    deck.push(new PathLayer({
      id: `ws-vectors-${kind}`, data: segments,
      getPath: (d) => d.path,
      getColor: [...color, alpha], getWidth: 1.4, widthUnits: "pixels",
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind, title: kind === "wind" ? "Wind (10 m)" : "Surface current",
        rows: [
          ["speed", `${i.object.speed.toFixed(2)} m/s`],
          ["towards", `${Math.round(i.object.toDeg)}°`],
          ["valid", timeUtc ? `${String(timeUtc).slice(0, 16).replace("T", " ")} UTC` : "—"],
          ["source", f.provider || f.file || "—"],
          ...(f.matches_run === false ? [["note", "not the grid this run's drift used"]] : []),
        ],
      } : null),
      updateTriggers: { getPath: [timeMs, frac, alpha], getColor: alpha },
    }));
  }

  /* ------------------------------------------------------ forecast (amber) */
  /* The forward drift: every footprint filled and outlined in a colour that
   * warms with the horizon, a thick path from the slick through each
   * horizon's centroid with arrowheads pointing FORWARD, and a labelled
   * FORECAST POINT at each one carrying its real distance from the slick. */
  if (show.forecast && forecast?.features?.length) {
    const RAMP = [[250, 204, 21], [245, 158, 11], [249, 115, 22], [239, 68, 68]];
    const horizons = [...new Set(forecast.features.map((f) => f.properties.horizon_h ?? 0))].sort((x, y) => x - y);
    const colourOf = (h) => RAMP[Math.min(RAMP.length - 1, Math.max(0, horizons.indexOf(h)))];
    const shown = forecast.features.filter((f) => rv.forecastUpTo == null || (f.properties.horizon_h ?? 0) <= rv.forecastUpTo + 0.01);
    const nearest = horizons.reduce((bst, h) => (Math.abs(h - aheadH) < Math.abs(bst - aheadH) ? h : bst), horizons[0]);
    [...shown].sort((x, y) => (y.properties.confidence_level ?? 0) - (x.properties.confidence_level ?? 0)).forEach((f, i) => {
      const h = f.properties.horizon_h ?? 0;
      const active = aheadH > 0 && h === nearest;
      const col = colourOf(h);
      deck.push(new GeoJsonLayer({
        id: `ws-forecast-${h}-${i}`,
        data: { type: "FeatureCollection", features: [f] },
        stroked: true, filled: true,
        getFillColor: [...col, active ? 105 : 52],
        getLineColor: [...col, 255],
        getLineWidth: active ? 3.2 : 2.2, lineWidthUnits: "pixels",
        pickable: true,
        onHover: (x) => hover(x.object ? {
          kind: "forecast", title: `Forecast +${h} h`,
          rows: [["valid", (f.properties.valid_utc || "").replace("T", " ").slice(0, 16) + " UTC"],
                 ["area", `${f.properties.area_km2 ?? "—"} km²`],
                 ["envelope", f.properties.confidence_level != null ? `${Math.round(f.properties.confidence_level * 100)} %` : "—"]],
        } : null),
        updateTriggers: { getFillColor: [aheadH, h], getLineWidth: aheadH },
      }));
    });
    /* the forward path: slick -> the widest envelope's centroid at each horizon */
    const cOf = (f) => { const r = outerRings(f)[0]; if (!r?.length) return null; let x = 0, y = 0; for (const q of r) { x += q[0]; y += q[1]; } return [x / r.length, y / r.length]; };
    const stops = horizons.map((h) => {
      const env = shown.filter((f) => (f.properties.horizon_h ?? 0) === h).sort((x, y) => (y.properties.confidence_level ?? 0) - (x.properties.confidence_level ?? 0))[0];
      const c = env ? cOf(env) : null;
      return c ? { h, pos: c, km: slickC ? haversineKm(slickC[1], slickC[0], c[1], c[0]) : null, col: colourOf(h) } : null;
    }).filter(Boolean);
    if (slickC && stops.length) {
      const path = [slickC, ...stops.map((d) => d.pos)];
      deck.push(new PathLayer({ id: "ws-forecast-glow", data: [{ path }], getPath: (d) => d.path, getColor: [245, 158, 11, 75],
        getWidth: 11, widthUnits: "pixels", capRounded: true, jointRounded: true, updateTriggers: { getPath: stops.length } }));
      deck.push(new PathLayer({ id: "ws-forecast-path", data: [{ path }], getPath: (d) => d.path, getColor: [254, 215, 140, 255],
        getWidth: 3.6, widthUnits: "pixels", capRounded: true, jointRounded: true, getDashArray: [10, 5], extensions: dashExt,
        updateTriggers: { getPath: stops.length } }));
      const heads = [];
      for (let i = 1; i < path.length; i++) {
        const [x1, y1] = path[i - 1], [x2, y2] = path[i];
        if (x1 === x2 && y1 === y2) continue;
        heads.push({ pos: [(x1 + x2) / 2, (y1 + y2) / 2], ang: -bearingDeg(y1, x1, y2, x2) });
      }
      deck.push(new TextLayer({ id: "ws-forecast-heads", data: heads, getPosition: (d) => d.pos, getText: () => "▲", getSize: 17,
        getAngle: (d) => d.ang, getColor: [255, 237, 190, 255], fontFamily: "Segoe UI Symbol, sans-serif", characterSet: CHARSET,
        outlineWidth: 2, outlineColor: [110, 55, 5, 255], fontSettings: { sdf: true }, updateTriggers: { getPosition: stops.length } }));
    }
    deck.push(new ScatterplotLayer({ id: "ws-forecast-points", data: stops, getPosition: (d) => d.pos, getRadius: 1,
      radiusMinPixels: 8, radiusMaxPixels: 8, stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2.5,
      getFillColor: (d) => [...d.col, 255], getLineColor: [255, 255, 255, 255], updateTriggers: { getPosition: stops.length } }));
    deck.push(new TextLayer({ id: "ws-forecast-labels", data: stops, getPosition: (d) => d.pos,
      getText: (d) => `FORECAST POINT  +${d.h} h${d.km != null ? `\n${d.km.toFixed(1)} km from the slick` : ""}`,
      getSize: 13, getColor: [20, 12, 2, 255], fontFamily: "Inter, sans-serif", fontWeight: 700,
      getTextAnchor: "start", getAlignmentBaseline: "center", getPixelOffset: [15, 0], characterSet: CHARSET,
      background: true, getBackgroundColor: (d) => [...d.col, 240], backgroundPadding: [7, 4, 7, 4],
      updateTriggers: { getPosition: stops.length, getText: stops.length } }));
  }

  /* the slick's own position, named: everything above is measured from here */
  if (slickC && (show.hindcast || show.forecast)) {
    deck.push(new TextLayer({ id: "ws-now-label", data: [{ pos: slickC }], getPosition: (d) => d.pos,
      getText: () => "DETECTED SLICK · NOW", getSize: 13, getColor: [20, 8, 0, 255], fontFamily: "Inter, sans-serif", fontWeight: 700,
      getTextAnchor: "middle", getAlignmentBaseline: "top", getPixelOffset: [0, 16], characterSet: CHARSET,
      background: true, getBackgroundColor: [...WS.slick, 240], backgroundPadding: [7, 4, 7, 4] }));
  }

  /* --------------------------------------------------- origin zone ------ */
  /* The run's own published estimate (lib/drift): the hindcast ellipse nearest
   * the middle of the origin window, radius = origin_uncertainty_km. A
   * sub-kilometre radius is invisible at investigation zoom, so a fixed-pixel
   * target (ring + bullseye) marks the ORIGIN POINT and the label states its
   * coordinates and the true uncertainty. */
  const est = estO;
  if (show.origin && est) {
    const c = est.center;
    const rows = [
      ["window", `${fmtUtc(Date.parse(est.windowStartUtc))} → ${fmtUtc(Date.parse(est.windowEndUtc))}`],
      ["centre", `${c[1].toFixed(4)}, ${c[0].toFixed(4)}`],
      ["radius", `${est.radiusKm.toFixed(2)} km (${est.radiusBasis})`],
    ];
    if (est.weak) rows.push(["note", "peak at image time — window, not a release time"]);
    const onHoverOrigin = (i) => hover(i.object ? {
      kind: "origin", title: "Estimated origin (hindcast)", rows,
    } : null);
    /* origin beat: the ring grows from the centre to its published radius */
    const grow = rv.origin ?? 1;
    const ring = circleRing(c[0], c[1], Math.max(est.radiusKm * Math.max(grow, 0.02), 0.01), 72);
    deck.push(new PolygonLayer({
      id: "ws-origin-fill", data: [{ poly: ring }],
      getPolygon: (d) => d.poly, filled: true, stroked: false,
      getFillColor: [...WS.origin, Math.round(46 * grow)],
      updateTriggers: { getPolygon: grow, getFillColor: grow },
    }));
    deck.push(new PathLayer({
      id: "ws-origin-ring",
      data: [{ path: ring }],
      getPath: (d) => d.path,
      getColor: [...WS.origin, Math.round(255 * Math.min(1, grow * 1.5))], getWidth: 2.4, widthUnits: "pixels",
      getDashArray: [7, 5], extensions: dashExt,
      pickable: true, onHover: onHoverOrigin,
      updateTriggers: { getPath: grow, getColor: grow },
    }));
    deck.push(new ScatterplotLayer({
      id: "ws-origin-target", data: [{ pos: c, r: 17 }, { pos: c, r: 10 }],
      getPosition: (d) => d.pos, radiusUnits: "pixels", getRadius: (d) => d.r,
      stroked: true, filled: false, lineWidthUnits: "pixels", getLineWidth: 2,
      getLineColor: [255, 255, 255, Math.round(235 * Math.min(1, grow * 1.4))],
      updateTriggers: { getLineColor: grow },
    }));
    deck.push(new ScatterplotLayer({
      id: "ws-origin-marker",
      data: [{ pos: c }],
      getPosition: (d) => d.pos,
      getRadius: 5, radiusUnits: "pixels",
      stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
      getFillColor: [...WS.origin, 255], getLineColor: [7, 12, 22, 255],
      pickable: true, onHover: onHoverOrigin,
    }));
    if (grow > 0.6) {
      const ns = c[1] >= 0 ? "N" : "S", ew = c[0] >= 0 ? "E" : "W";
      deck.push(new TextLayer({
        id: "ws-origin-label",
        data: [{ pos: c }],
        getPosition: (d) => d.pos,
        getText: () => `ORIGIN POINT (estimated)\n${Math.abs(c[1]).toFixed(4)}° ${ns}  ${Math.abs(c[0]).toFixed(4)}° ${ew}\n± ${est.radiusKm.toFixed(est.radiusKm < 1 ? 2 : 1)} km`,
        getSize: 13, getColor: [7, 12, 22, Math.round(255 * Math.min(1, (grow - 0.6) / 0.4))],
        fontFamily: "Inter, sans-serif", fontWeight: 700,
        getTextAnchor: "start", getAlignmentBaseline: "center", getPixelOffset: [24, 0],
        characterSet: CHARSET,
        background: true, getBackgroundColor: [241, 245, 249, Math.round(240 * Math.min(1, (grow - 0.6) / 0.4))], backgroundPadding: [8, 5, 8, 5],
        updateTriggers: { getColor: grow, getBackgroundColor: grow },
      }));
    }
  }

  /* ------------------------------------------------------- AIS tracks ---- */
  if (show.vessels && tracks.length) {
    const isSel = (t) => t.mmsi === selectedMmsi;
    /* Presentation state of a track: before the gates have run it is plain
     * traffic; a filtered vessel dims once ITS gate has run; a ranked one
     * lights up once the ranking has reached its rank. All three facts
     * (filtered, reason, rank) are the backend's. */
    const gateDone = (t) => rv.gate == null || gateOf(t.filterReason) <= rv.gate;
    const litRank = (t) => (t.rank ? (rv.ranked == null ? t.rank : t.rank <= rv.ranked ? t.rank : null) : null);
    const dim = rv.dimOthers || dimOthers;
    const colorOf = (t) => {
      const r = litRank(t);
      return isSel(t) ? WS.selected
        : r === 1 ? WS.suspect
          : r ? WS.candidate
            : t.filtered && gateDone(t) ? WS.filtered : WS.vessel;
    };
    /* Attribution frames dim everything but the selected track. */
    const alphaOf = (t) => {
      const r = litRank(t);
      if (isSel(t)) return 255;
      if (dim) return r ? 120 : 60;
      if (t.filtered) return gateDone(t) ? (rv.gate != null ? 45 : 70) : 150;
      return r === 1 ? 240 : r ? 200 : 150;
    };
    const pathOf = (t) => (rv.tracksUntil && timeMs != null ? trackPathUntil(t, timeMs) : t.path);
    const drawn = rv.tracksUntil && timeMs != null ? tracks.filter((t) => pathOf(t)) : tracks;
    const trig = [selectedMmsi, dim, rv.gate, rv.ranked];

    deck.push(new PathLayer({
      id: "ws-tracks", data: drawn,
      getPath: pathOf,
      getColor: (d) => [...colorOf(d), alphaOf(d)],
      getWidth: (d) => (isSel(d) ? 3.4 : litRank(d) === 1 ? 2.6 : litRank(d) ? 1.8 : 1.1),
      widthUnits: "pixels",
      getDashArray: (d) => (litRank(d) || isSel(d) ? [0, 0] : [5, 4]),
      extensions: dashExt,
      pickable: true,
      onClick: (i) => {
        if (!i.object) return;
        onSelect?.(i.object.mmsi);
        if (i.object.filtered) {
          setPinned({
            kind: "filtered", title: `${i.object.name ?? i.object.mmsi} · EXCLUDED`,
            rows: [["reason", i.object.filterReason || "—"],
                   ["type", i.object.type || "—"], ["source", i.object.source]],
          });
        } else setPinned(null);
      },
      onHover: (i) => hover(i.object ? {
        kind: i.object.filtered ? "filtered" : "vessel",
        title: `${i.object.name ?? "MMSI " + i.object.mmsi}${i.object.rank ? ` · candidate ${String(i.object.rank).padStart(2, "0")}` : i.object.filtered ? " · excluded" : ""}`,
        rows: [
          ["mmsi", i.object.mmsi], ["type", i.object.type || "—"],
          ...(i.object.filtered ? [["reason", i.object.filterReason || "—"]] : []),
          ...(i.object.score != null ? [["score", Number(i.object.score).toFixed(2)]] : []),
          ["distance", `${i.object.distanceKm ?? "—"} km`],
          ["source", i.object.source],
        ],
      } : null),
      updateTriggers: { getPath: [rv.tracksUntil ? timeMs : 0], getColor: trig, getWidth: trig, getDashArray: trig },
    }));

    /* direction arrows: sparse rotated glyphs along each visible track */
    const arrows = [];
    for (const t of drawn) {
      if (t.filtered && gateDone(t) && !isSel(t)) continue;
      const path = pathOf(t);
      const stride = Math.max(6, Math.floor(path.length / 5));
      for (let i = stride; i < path.length - 1; i += stride) {
        const [x1, y1] = path[i - 1], [x2, y2] = path[i];
        arrows.push({ pos: path[i], ang: -bearingDeg(y1, x1, y2, x2),
                      c: colorOf(t), a: alphaOf(t) });
      }
    }
    deck.push(new TextLayer({
      id: "ws-arrows", data: arrows,
      getPosition: (d) => d.pos, getText: () => "▲",
      getSize: 11, getAngle: (d) => d.ang,
      getColor: (d) => [...d.c, Math.min(d.a, 210)],
      fontFamily: "Segoe UI Symbol, sans-serif", characterSet: CHARSET,
      updateTriggers: { getColor: trig },
    }));

    /* interpolated vessel positions at the scrubbed time */
    if (timeMs != null) {
      const now = tracks.map((t) => {
        const st = trackStateAt(t, timeMs);
        return st ? { ...t, pos: st.pos, heading: st.heading, sogNow: st.sog } : null;
      }).filter(Boolean);
      deck.push(new ScatterplotLayer({
        id: "ws-vessel-now", data: now,
        getPosition: (d) => d.pos,
        getRadius: (d) => (litRank(d) === 1 ? 500 : 320),
        radiusMinPixels: 3, radiusMaxPixels: 8,
        getFillColor: (d) => [...colorOf(d), d.filtered && gateDone(d) ? 90 : 255],
        stroked: true, getLineColor: [10, 16, 32, 220], getLineWidth: 1,
        lineWidthUnits: "pixels",
        pickable: true,
        onHover: (i) => hover(i.object ? {
          kind: "vessel-now",
          title: `${i.object.name ?? i.object.mmsi} @ ${fmtUtc(timeMs)}`,
          rows: [["speed", i.object.sogNow != null ? `${i.object.sogNow.toFixed(1)} kn` : "—"],
                 ["heading", i.object.heading != null ? `${Math.round(i.object.heading)}°` : "—"]],
        } : null),
        updateTriggers: { getPosition: timeMs, getFillColor: trig },
      }));
    }

    /* the top-ranked (and the selected) track glow, so the eye finds them first */
    const glow = drawn.filter((t) => isSel(t) || litRank(t) === 1);
    if (glow.length) {
      deck.push(new PathLayer({
        id: "ws-tracks-glow", data: glow, getPath: pathOf, getColor: [...WS.selected, 70],
        getWidth: 12, widthUnits: "pixels", capRounded: true, jointRounded: true,
        updateTriggers: { getPath: [rv.tracksUntil ? timeMs : 0, trig] },
      }));
    }
    /* closest approach: a dashed tie from each lit candidate to the origin */
    if (candidateLabels && est) {
      const ties = tracks.filter((t) => litRank(t)).map((t) => {
        let best = null, bd = Infinity;
        for (const q of t.path) { const d = haversineKm(est.center[1], est.center[0], q[1], q[0]); if (d < bd) { bd = d; best = q; } }
        return best ? { path: [best, est.center], t, at: best, km: bd } : null;
      }).filter(Boolean);
      deck.push(new PathLayer({
        id: "ws-cand-ties", data: ties, getPath: (d) => d.path,
        getColor: (d) => (d.t.rank === 1 ? [...WS.suspect, 230] : [226, 232, 240, 150]),
        getWidth: (d) => (d.t.rank === 1 ? 2.2 : 1.3), widthUnits: "pixels", getDashArray: [4, 4], extensions: dashExt,
        updateTriggers: { getColor: trig },
      }));
      deck.push(new ScatterplotLayer({
        id: "ws-cand-closest", data: ties, getPosition: (d) => d.at, getRadius: (d) => (d.t.rank === 1 ? 7 : 5), radiusUnits: "pixels",
        stroked: true, filled: true, lineWidthUnits: "pixels", getLineWidth: 2,
        getFillColor: (d) => (d.t.rank === 1 ? [...WS.suspect, 255] : [226, 232, 240, 255]), getLineColor: [7, 12, 22, 255],
        updateTriggers: { getFillColor: trig },
      }));
      deck.push(new TextLayer({
        id: "ws-cand-badges", data: ties, getPosition: (d) => d.at,
        getText: (d) => `#${d.t.rank}  ${d.t.score != null ? Number(d.t.score).toFixed(2) : "—"}`,
        getSize: (d) => (d.t.rank === 1 ? 17 : 14), fontFamily: "JetBrains Mono, monospace", fontWeight: 700,
        getColor: (d) => (d.t.rank === 1 ? [4, 18, 31, 255] : [232, 238, 248, 255]),
        /* candidates often share a closest point (all pass through the origin):
         * fan the badges around it so every rank stays readable */
        getTextAnchor: "middle", getAlignmentBaseline: "center",
        getPixelOffset: (d) => { const a = (-90 + (d.t.rank - 1) * 58) * Math.PI / 180; const r = d.t.rank === 1 ? 46 : 58; return [Math.cos(a) * r, Math.sin(a) * r]; },
        characterSet: CHARSET,
        background: true, getBackgroundColor: (d) => (d.t.rank === 1 ? [...WS.suspect, 250] : [15, 23, 42, 235]),
        getBorderColor: (d) => (d.t.rank === 1 ? [255, 255, 255, 255] : [100, 116, 139, 255]), getBorderWidth: 1.5,
        backgroundPadding: [8, 4, 8, 4], updateTriggers: { getColor: trig, getBackgroundColor: trig, getSize: trig },
      }));
    }

    /* candidate labels beside the ranked tracks (frames 12-15) */
    if (candidateLabels && !est) {
      const labelled = tracks.filter((t) => litRank(t));
      deck.push(new TextLayer({
        id: "ws-candidate-labels", data: labelled,
        getPosition: (d) => d.path[Math.floor(d.path.length / 2)],
        getText: (d) => `${d.name || `Candidate ${String(d.rank).padStart(2, "0")}`} — ${d.score != null ? Number(d.score).toFixed(2) : "—"}${d.rank === 1 ? "\n(Highest-Ranked Candidate)" : ""}`,
        getSize: 12, getColor: (d) => (d.rank === 1 ? [...WS.suspect, 255] : [232, 238, 248, 235]),
        fontFamily: "Inter, sans-serif", fontWeight: 600,
        getTextAnchor: "start", getAlignmentBaseline: "bottom",
        getPixelOffset: [8, -6],
        characterSet: CHARSET,
        background: true, getBackgroundColor: [7, 12, 22, 200],
        backgroundPadding: [6, 4, 6, 4],
        getBorderColor: (d) => (d.rank === 1 ? [...WS.suspect, 200] : [60, 78, 104, 220]),
        getBorderWidth: 1,
        updateTriggers: { getColor: trig },
      }));
    }

    /* selection halo */
    const sel = tracks.find((t) => t.mmsi === selectedMmsi);
    if (sel && timeMs != null) {
      const st = trackStateAt(sel, timeMs);
      if (st) {
        deck.push(new ScatterplotLayer({
          id: "ws-selected", data: [st],
          getPosition: (d) => d.pos, getRadius: 900,
          radiusMinPixels: 13, radiusMaxPixels: 24,
          stroked: true, filled: false,
          getLineColor: [...WS.selected, 230], getLineWidth: 1.6,
          lineWidthUnits: "pixels",
          updateTriggers: { getPosition: timeMs },
        }));
      }
    }
  }

  /* -------------------------------------------------------- AIS gaps --
   * Where a transponder stopped reporting, drawn dashed in the alarm colour
   * across the silence. The run records one total per candidate, which says a
   * vessel went dark but never where; these come from the intervals between
   * the fixes themselves, so a gap is only drawn over a stretch the archive
   * really has no data for. */
  if (show.vessels && show.aisGaps !== false && tracks.length) {
    const gaps = [];
    for (const t of tracks) {
      if (t.filtered && show.excluded === false) continue;
      for (const g of aisGaps(t)) gaps.push({ ...g, mmsi: t.mmsi, name: t.name });
    }
    if (gaps.length) {
      deck.push(new PathLayer({
        id: "ws-ais-gaps", data: gaps,
        getPath: (d) => [d.from, d.to],
        getColor: MAP_COLORS.aisGap,
        getWidth: (d) => (d.mmsi === selectedMmsi ? 2.6 : 1.6),
        widthUnits: "pixels", widthMinPixels: 1.4,
        getDashArray: [6, 4], extensions: dashExt,
        pickable: true,
        onHover: (i) => hover(i.object ? {
          kind: "gap", title: `${i.object.name || `MMSI ${i.object.mmsi}`} · AIS gap`,
          rows: [["silent for", `${Math.round(i.object.minutes)} min`],
                 ["from", fmtUtc(i.object.startMs)], ["to", fmtUtc(i.object.endMs)],
                 ["drawn above", `${AIS_GAP_MIN} min`]],
        } : null),
        updateTriggers: { getWidth: selectedMmsi },
      }));
    }
  }

  /* ------------------------------------- detection box (frames 09-10) --- */
  /* The screening box that contains the analysed slick's centroid -- the
   * detector's own candidate, drawn as the cyan rectangle that precedes the
   * mask. Falls back to the slick's bounding box when the run recorded no
   * candidate boxes (the threshold fallback engine). */
  const oilBox = (() => {
    if (!slickC) return null;
    const hit = (detect?.candidates ?? []).find((x) => x.class === "oil" && Array.isArray(x.bbox)
      && slickC[0] >= x.bbox[0] && slickC[0] <= x.bbox[2] && slickC[1] >= x.bbox[1] && slickC[1] <= x.bbox[3]);
    if (hit) return hit.bbox;
    const ring = outerRings(slick.features[0])[0];
    if (!ring?.length) return null;
    const xs = ring.map((c) => c[0]), ys = ring.map((c) => c[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  })();
  if (rv.detectBox != null && rv.detectBox > 0 && oilBox) {
    const a = rv.detectBox;
    deck.push(new PolygonLayer({
      id: "ws-detect-box", data: [{ poly: rect(oilBox) }],
      getPolygon: (d) => d.poly, stroked: true, filled: true,
      getFillColor: [...WS.tile, Math.round(18 * a)],
      getLineColor: [...WS.tile, Math.round(255 * a)],
      getLineWidth: 2, lineWidthUnits: "pixels",
      updateTriggers: { getFillColor: a, getLineColor: a },
    }));
  }

  /* --------------------------------------------- slick + geometry ------- */
  if (show.slick && slick?.features?.length && rv.slick != null && rv.slick < 1) {
    /* Segmentation beat (frame 10): the mask does not simply appear.
     *   0.00–0.35  the detector's oil candidate boxes tint red
     *   0.30–0.70  the real mask fill rises
     *   0.55–1.00  the real boundary traces itself around the slick
     * Every geometry is the run's own; only the reveal order is presentation. */
    const p = rv.slick;
    const boxes = (detect?.candidates ?? []).filter((x) => x.class === "oil" && Array.isArray(x.bbox));
    const boxA = Math.min(1, p / 0.35) * (p > 0.7 ? Math.max(0, 1 - (p - 0.7) / 0.3) : 1);
    if (boxes.length && boxA > 0) {
      deck.push(new PolygonLayer({
        id: "ws-slick-candidates", data: boxes,
        getPolygon: (c) => rect(c.bbox), filled: true, stroked: false,
        getFillColor: [220, 60, 40, Math.round(70 * boxA)],
        updateTriggers: { getFillColor: boxA },
      }));
    }
    const fillA = span(p, 0.3, 0.7);
    if (fillA > 0) {
      deck.push(new GeoJsonLayer({
        id: "ws-slick-fill", data: slick,
        stroked: false, filled: true,
        getFillColor: [...WS.slick, Math.round(95 * fillA)],
        updateTriggers: { getFillColor: fillA },
      }));
    }
    const traceF = span(p, 0.55, 1);
    if (traceF > 0) {
      const rings = slick.features.flatMap((f) => outerRings(f)).map((r) => ({ path: partialRing(r, traceF) }));
      deck.push(new PathLayer({
        id: "ws-slick-trace", data: rings,
        getPath: (d) => d.path, getColor: [...WS.slickEdge, 255],
        getWidth: 2.2, widthUnits: "pixels",
        updateTriggers: { getPath: traceF },
      }));
    }
  } else if (show.slick && slick?.features?.length) {
    deck.push(new GeoJsonLayer({
      id: "ws-slick", data: slick,
      stroked: true, filled: true,
      getFillColor: [...WS.slick, 95],
      getLineColor: [...WS.slickEdge, 255],
      getLineWidth: 2.2, lineWidthUnits: "pixels",
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind: "slick", title: i.object.properties?.slick_id || "Detected slick",
        rows: [["area", `${Number(i.object.properties?.area_km2).toFixed(2)} km²`],
               ["confidence", `${(Number(i.object.properties?.confidence) * 100).toFixed(1)}%`]],
      } : null),
    }));
  }
  if (show.geometry && slick?.features?.length) {
    const p = slick.features[0].properties || {};
    const ring = slickEllipse(p);
    const [cx, cy] = p.centroid || [];
    if (ring) {
      deck.push(new PathLayer({
        id: "ws-ellipse", data: [{ path: ring }],
        getPath: (d) => d.path, getColor: [...WS.geometry, 235],
        getWidth: 1.6, widthUnits: "pixels",
        getDashArray: [7, 4], extensions: dashExt,
      }));
    }
    if (cx != null) {
      const axisKm = (p.major_axis_m || 0) / 2000;
      deck.push(new PathLayer({
        id: "ws-orientation",
        data: [{ path: [destination(cx, cy, axisKm, p.orientation_deg || 0),
                        destination(cx, cy, axisKm, (p.orientation_deg || 0) + 180)] }],
        getPath: (d) => d.path, getColor: [...WS.geometry, 210],
        getWidth: 1.2, widthUnits: "pixels",
      }));
      deck.push(new ScatterplotLayer({
        id: "ws-centroid", data: [{ pos: [cx, cy] }],
        getPosition: (d) => d.pos, getRadius: 120,
        radiusMinPixels: 3.5, radiusMaxPixels: 6,
        stroked: true, filled: false,
        getLineColor: [...WS.geometry, 255], getLineWidth: 2,
        lineWidthUnits: "pixels",
      }));
    }
  }

  /* ------------------------------------ look-alikes (teal, hatched) ----- */
  const lookalikes = (detect?.candidates ?? [])
    .filter((c) => c.class === "lookalike" && Array.isArray(c.bbox));
  if (show.lookalikes && lookalikes.length) {
    const hoverBox = (c) => ({
      kind: "lookalike", title: "Look-alike — reported, not counted as oil",
      rows: [["phenomenon", c.phenomenon || "unclassified"],
             ["score", c.score != null ? Number(c.score).toFixed(2) : "—"],
             ["status", "excluded from slick + attribution"]],
    });
    deck.push(new PolygonLayer({
      id: "ws-lookalike-box", data: lookalikes,
      getPolygon: (c) => {
        const [w, s, e, n] = c.bbox;
        return [[w, s], [e, s], [e, n], [w, n]];
      },
      stroked: true, filled: true,
      getFillColor: [...WS.lookalike, 14],
      getLineColor: [...WS.lookalike, 230],
      getLineWidth: 1.8, lineWidthUnits: "pixels",
      getDashArray: [5, 4], extensions: dashExt,
      pickable: true,
      onHover: (i) => hover(i.object ? hoverBox(i.object) : null),
    }));
    deck.push(new PathLayer({
      id: "ws-lookalike-hatch",
      data: lookalikes.flatMap((c) => {
        const [w, s, e, n] = c.bbox;
        return hatchLines(w, s, e, n).map((path) => ({ path }));
      }),
      getPath: (d) => d.path,
      getColor: [...WS.lookalike, 110],
      getWidth: 1, widthUnits: "pixels",
    }));
    /* One label per box is unreadable at 361 boxes (the gulf scene); label
     * only when few enough to read. The hover carries the rest. */
    if (lookalikes.length <= 40) {
      deck.push(new TextLayer({
        id: "ws-lookalike-label", data: lookalikes,
        getPosition: (c) => [(c.bbox[0] + c.bbox[2]) / 2, c.bbox[3]],
        getText: (c) => `LOOK-ALIKE${c.phenomenon ? ` · ${c.phenomenon.replace(/_/g, " ")}` : ""} — not oil`,
        getSize: 10, getColor: [...WS.lookalike, 240],
        fontFamily: "JetBrains Mono, monospace",
        getTextAnchor: "middle", getAlignmentBaseline: "bottom",
        characterSet: CHARSET,
      }));
    }
  }

  /* ------------------------------------------------ measure (MAP tools) -- */
  const mPoints = measure?.points ?? [];
  if (mPoints.length) {
    const legs = measureSegments(mPoints);
    if (legs.length) {
      deck.push(new PathLayer({
        id: "ws-measure-line", data: [{ path: mPoints }],
        getPath: (d) => d.path, getColor: [56, 189, 248, 235],
        getWidth: 1.6, widthUnits: "pixels",
        getDashArray: [7, 4], extensions: dashExt,
      }));
      deck.push(new TextLayer({
        id: "ws-measure-label", data: legs,
        getPosition: (d) => d.labelAt,
        getText: (d) => `${d.km.toFixed(2)} km · ${d.nm.toFixed(2)} nm · ${d.bearingDeg.toFixed(0)}°`,
        getSize: 11, getColor: [224, 242, 254, 245],
        fontFamily: "JetBrains Mono, monospace",
        getTextAnchor: "middle", getAlignmentBaseline: "bottom",
        characterSet: CHARSET,
      }));
    }
    deck.push(new ScatterplotLayer({
      id: "ws-measure-points", data: mPoints.map((pos, i) => ({ pos, i })),
      getPosition: (d) => d.pos, getRadius: 90,
      radiusMinPixels: 3, radiusMaxPixels: 6,
      stroked: true, filled: true,
      getFillColor: [7, 11, 20, 220], getLineColor: [56, 189, 248, 255],
      getLineWidth: 1.8, lineWidthUnits: "pixels",
    }));
  }

  /* -------------------------------------------- catalogue footprints ---- */
  /* Search hits and cached scenes as dim outlines; the selected one is the
   * cyan handle rectangle of frames 02-05. */
  if (show.footprints && footprints?.length) {
    deck.push(new PathLayer({
      id: "ws-footprints",
      data: footprints.filter((f) => Array.isArray(f.bbox) && !f.selected).map((f) => ({ path: rect(f.bbox), f })),
      getPath: (d) => d.path, getColor: [148, 163, 184, 170],
      getWidth: 1.2, widthUnits: "pixels",
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind: "footprint", title: i.object.f.label || i.object.f.id,
        rows: [["acquired", i.object.f.acquired_utc || "—"], ["status", i.object.f.status || "—"]],
      } : null),
      onClick: (i) => i.object && onClickMap?.({ footprint: i.object.f }),
    }));
  }

  /* ---------------------------------------------- AOI / selected scene -- */
  const boxOf = aoi?.bbox || footprints?.find((f) => f.selected)?.bbox;
  if (show.aoi !== false && boxOf) {
    const r = rect(boxOf);
    deck.push(new PolygonLayer({
      id: "ws-aoi-fill", data: [{ poly: r }],
      getPolygon: (d) => d.poly, filled: true, stroked: false,
      getFillColor: [...WS.aoi, 28],
    }));
    deck.push(new PathLayer({
      id: "ws-aoi", data: [{ path: r }],
      getPath: (d) => d.path, getColor: [...WS.aoi, 255],
      getWidth: 1.8, widthUnits: "pixels",
    }));
    deck.push(new ScatterplotLayer({
      id: "ws-aoi-handles", data: r.slice(0, 4).map((pos) => ({ pos })),
      getPosition: (d) => d.pos, getRadius: 1,
      radiusMinPixels: 4, radiusMaxPixels: 4,
      getFillColor: [...WS.aoi, 255],
    }));
  }

  /* AOI being drawn: first corner + live rectangle to the cursor */
  if (draw?.active && draw.points?.length) {
    const pts = draw.points;
    const cur = draw.cursor;
    if (pts.length === 1 && cur) {
      const [x1, y1] = pts[0];
      const r = rect([Math.min(x1, cur[0]), Math.min(y1, cur[1]), Math.max(x1, cur[0]), Math.max(y1, cur[1])]);
      deck.push(new PathLayer({
        id: "ws-draw", data: [{ path: r }],
        getPath: (d) => d.path, getColor: [...WS.aoi, 220],
        getWidth: 1.4, widthUnits: "pixels", getDashArray: [4, 3], extensions: dashExt,
        updateTriggers: { getPath: cur },
      }));
    }
    deck.push(new ScatterplotLayer({
      id: "ws-draw-points", data: pts.map((pos) => ({ pos })),
      getPosition: (d) => d.pos, getRadius: 1, radiusMinPixels: 4, radiusMaxPixels: 4,
      getFillColor: [...WS.aoi, 255],
    }));
  }

  /* scene footprint always */
  if (bbox && !boxOf) {
    deck.push(new PathLayer({
      id: "ws-extent",
      data: [{ path: rect(bbox) }],
      getPath: (d) => d.path, getColor: [...WS.aoi, 200],
      getWidth: 1.2, widthUnits: "pixels",
    }));
  }

  const crosshair = Boolean(measure?.active || draw?.active);

  return (
    <MaritimeGlobe
      ref={globe}
      testid="globe"
      basemap={BASEMAP_FOR[basemap] || "satellite"}
      layers={deck}
      sar={show.sar && runId && bbox
        ? { runId, bbox, opacity: (basemap === "sar" ? 1 : 0.9) * (rv.sar ?? 1) }
        : null}
      initialCamera={initialCamera.current}
      onCameraChange={handleCamera}
      cursor={crosshair ? "crosshair" : undefined}
      onHover={(i) => {
        if (i.coordinate) onCursor?.({ lon: i.coordinate[0], lat: i.coordinate[1] });
        if (draw?.active && i.coordinate) draw.onCursor?.(i.coordinate);
        if (!i.object) hover(null);
      }}
      onClick={(i) => {
        // While measuring or drawing, a click is a vertex -- including a
        // click that lands on a vessel. Silently selecting the vessel
        // instead would drop the point the analyst just placed.
        if (measure?.active) {
          if (i.coordinate) measure.onAddPoint(i.coordinate);
          return;
        }
        if (draw?.active) {
          if (i.coordinate) draw.onAddPoint(i.coordinate);
          return;
        }
        // A pick reached its own layer's handler already; this is the
        // "clicked open water" case.
        if (i.object) return;
        setPinned(null);
        onClickMap?.({ coordinate: i.coordinate });
      }}
    />
  );
}
