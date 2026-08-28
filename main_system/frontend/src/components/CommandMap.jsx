/* The command-center map. deck.gl over MapLibre, driven by a per-frame
 * `frame` object from the replay engine.
 *
 * Visual semantics are fixed and never overlap:
 *   observed slick   solid amber fill + locked boundary  ("what was seen")
 *   hindcast         cyan particle trails moving backwards, dashed ellipse
 *                                                        ("what we reconstructed")
 *   probable origin  pulsing teal target                 ("where it likely began")
 *   forecast         violet dashed corridors, translucent ("what we predict")
 *   vessels          slate tracks; candidates rose; #1 highlighted
 *   wind             pale streaks     currents           teal streaks
 *
 * Every geometry comes from the run's contract files. The only synthetic
 * motion is the radar sweep and the reveal timing -- positions never are.
 */

import { useMemo, useRef } from "react";
import DeckGL from "@deck.gl/react";
import {
  GeoJsonLayer, ScatterplotLayer, PathLayer, PolygonLayer, BitmapLayer,
  TextLayer, LineLayer,
} from "@deck.gl/layers";
import { HeatmapLayer } from "@deck.gl/aggregation-layers";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import { Map } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";

import {
  circleRing, destination, trackPathUntil, trackStateAt,
  span, easeOut, clamp01, lerp, fmtLat, fmtLon, MODE_BASEMAP,
} from "../lib/replay";

/* ------------------------------------------------------------ basemaps --- */

const TILES = {
  esri: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
};

/* In the light app theme, "Dark Maritime" would leave a black hole in a pale
 * page -- swap it (and the SAR backdrop) for the light-ocean basemap. Modes
 * the user picked explicitly (satellite, etc) are respected as-is. */
export function resolveBasemap(mode, appTheme) {
  const base = MODE_BASEMAP[mode] ?? "darkmatter";
  if (appTheme === "light" && base === "darkmatter") return "voyager";
  if (appTheme === "light" && base === "none") return "voyager";
  return base;
}

const basemapStyle = (kind) => {
  if (kind === "voyager") {
    return "https://tiles.openfreemap.org/styles/positron";
  }
  if (kind === "darkmatter") {
    return "https://tiles.openfreemap.org/styles/dark";
  }
  return {
    version: 8,
    sources: kind === "none" ? {} : {
      base: { type: "raster", tiles: [TILES[kind]], tileSize: 256 },
    },
    layers: [
      { id: "bg", type: "background",
        paint: { "background-color": kind === "voyager" ? "#dfe8ef" : "#04070f" } },
      ...(kind === "none" ? [] : [{
        id: "base", type: "raster", source: "base",
        paint: kind === "darkmatter"
          ? { "raster-opacity": 0.85, "raster-contrast": 0.05 }
          : kind === "esri"
            ? { "raster-opacity": 0.92, "raster-saturation": -0.25, "raster-brightness-max": 0.85 }
            : { "raster-opacity": 0.95, "raster-saturation": -0.35 },
      }]),
    ],
  };
};

export const SEMANTIC = {
  slick: [245, 158, 11],
  hindcast: [56, 189, 248],
  origin: [45, 212, 191],
  forecast: [167, 139, 250],
  vessel: [130, 148, 178],
  candidate: [244, 63, 94],
  wind: [226, 232, 240],
  current: [45, 212, 191],
  radar: [74, 222, 128],
};

const CHARSET = "auto";
const dashExt = [new PathStyleExtension({ dash: true })];

/* ========================================================================= */

export default function CommandMap({
  bundle, frame, effects, mode, toggles, view, onViewChange,
  windParts, currentParts, driftFwd,
  selectedMmsi, onSelect, onHoverInfo, runId, appTheme,
}) {
  const b = bundle;
  const { tick, simT } = frame;
  const bbox = b?.sceneMeta?.bbox;
  const layers = [];
  const hoverRef = useRef(null);
  const hover = (info) => {
    const key = info ? `${info.kind}:${info.title}` : null;
    if (hoverRef.current !== key) { hoverRef.current = key; onHoverInfo?.(info); }
  };

  /* ------------------------------------------------------ SAR + reveal --- */

  const sarUrl = useMemo(
    () => (runId ? `/api/runs/${runId}/scene_png` : null), [runId]);
  const maskUrl = useMemo(
    () => (runId ? `/api/runs/${runId}/mask_png` : null), [runId]);

  if (bbox && sarUrl && (toggles.sar || effects.sarAlpha > 0)) {
    layers.push(new BitmapLayer({
      id: "sar-scene", image: sarUrl,
      bounds: [bbox[0], bbox[1], bbox[2], bbox[3]],
      opacity: toggles.sar ? Math.max(0.85, effects.sarAlpha) : effects.sarAlpha,
      desaturate: 0, tintColor: [190, 210, 235],
    }));
  }
  if (bbox && effects.scanX != null) {
    const x = lerp(bbox[0], bbox[2], effects.scanX);
    const wpx = (bbox[2] - bbox[0]) * 0.006;
    layers.push(new PolygonLayer({
      id: "scanline",
      data: [0.9, 0.45, 0.18].map((a, i) => ({
        a, poly: [[x - wpx * (i + 1), bbox[1]], [x + wpx * (i + 1), bbox[1]],
                  [x + wpx * (i + 1), bbox[3]], [x - wpx * (i + 1), bbox[3]]],
      })),
      getPolygon: (d) => d.poly, stroked: false,
      getFillColor: (d) => [140, 220, 255, d.a * 200],
    }));
  }
  if (bbox && maskUrl && effects.maskAlpha > 0) {
    layers.push(new BitmapLayer({
      id: "sar-mask", image: maskUrl,
      bounds: [bbox[0], bbox[1], bbox[2], bbox[3]],
      opacity: effects.maskAlpha,
    }));
  }

  /* ------------------------------------------------------------ radar ---- */

  if (effects.radar > 0 && b) {
    const [cx, cy] = b.sceneCenter;
    const maxKm = bbox
      ? Math.max(20, 0.62 * 111 * Math.hypot(bbox[2] - bbox[0],
          (bbox[3] - bbox[1]))) : 30;
    const a = effects.radar;
    const rings = [0.25, 0.5, 0.75, 1].map((f) => ({
      km: maxKm * f, path: circleRing(cx, cy, maxKm * f, 120),
    }));
    layers.push(new PathLayer({
      id: "radar-rings", data: rings, getPath: (d) => d.path,
      getColor: [...SEMANTIC.radar, 60 * a], getWidth: 1,
      widthUnits: "pixels",
    }));
    layers.push(new TextLayer({
      id: "radar-ring-labels",
      data: rings.slice(1),
      getPosition: (d) => destination(cx, cy, d.km, 0),
      getText: (d) => `${d.km.toFixed(0)} km`,
      getSize: 10, getColor: [...SEMANTIC.radar, 150 * a],
      getTextAnchor: "middle", getAlignmentBaseline: "bottom",
      fontFamily: "JetBrains Mono, monospace", characterSet: CHARSET,
    }));
    /* the rotating sweep: a translucent trailing sector */
    const ang = (tick * 1.6) % 360;
    const sector = [[cx, cy]];
    for (let d = 55; d >= 0; d -= 5) {
      sector.push(destination(cx, cy, maxKm, ang - d));
    }
    layers.push(new PolygonLayer({
      id: "radar-sweep", data: [{ poly: [...sector, [cx, cy]] }],
      getPolygon: (d) => d.poly, stroked: false,
      getFillColor: [...SEMANTIC.radar, 34 * a],
      updateTriggers: { getPolygon: tick },
    }));
    layers.push(new PathLayer({
      id: "radar-beam",
      data: [{ path: [[cx, cy], destination(cx, cy, maxKm, ang)] }],
      getPath: (d) => d.path, getColor: [...SEMANTIC.radar, 210 * a],
      getWidth: 1.6, widthUnits: "pixels",
      updateTriggers: { getPath: tick },
    }));
    /* blips ping as the beam passes their bearing */
    const blips = b.tracks.map((t) => {
      const st = trackStateAt(t, b.t0);
      if (!st) return null;
      const brg = (Math.atan2(st.pos[0] - cx, st.pos[1] - cy) * 180 / Math.PI + 360) % 360;
      const behind = (ang - brg + 360) % 360;
      return { pos: st.pos, glow: Math.max(0, 1 - behind / 140), mmsi: t.mmsi };
    }).filter(Boolean);
    layers.push(new ScatterplotLayer({
      id: "radar-blips", data: blips,
      getPosition: (d) => d.pos,
      getRadius: (d) => 300 + 500 * d.glow,
      radiusMinPixels: 2, radiusMaxPixels: 8,
      getFillColor: (d) => [...SEMANTIC.radar, a * (60 + 195 * d.glow)],
      updateTriggers: { getFillColor: tick, getRadius: tick },
    }));
  }

  /* -------------------------------------------------- wind and current --- */

  const flowLayers = (parts, color, id, on) => {
    if (!on || !parts?.length) return;
    layers.push(new LineLayer({
      id: `${id}-tails`, data: parts,
      getSourcePosition: (d) => (d.trail[d.trail.length - 1] ?? [d.lon, d.lat]),
      getTargetPosition: (d) => [d.lon, d.lat],
      getColor: (d) => [...color, Math.min(150, 30 + d.speed * 160)],
      getWidth: 1.1, widthUnits: "pixels",
      updateTriggers: { getSourcePosition: tick, getTargetPosition: tick, getColor: tick },
    }));
    layers.push(new ScatterplotLayer({
      id: `${id}-heads`, data: parts,
      getPosition: (d) => [d.lon, d.lat],
      getRadius: 60, radiusMinPixels: 0.8, radiusMaxPixels: 1.8,
      getFillColor: (d) => [...color, Math.min(220, 60 + d.speed * 220)],
      updateTriggers: { getPosition: tick, getFillColor: tick },
    }));
  };
  flowLayers(currentParts, SEMANTIC.current, "current",
    toggles.currents && effects.envAlpha > 0);
  flowLayers(windParts,
    appTheme === "light" ? [71, 85, 105] : SEMANTIC.wind, "wind",
    toggles.wind && effects.envAlpha > 0);

  /* ----------------------------------------- hindcast: real trajectories -- */

  if (toggles.hindcast && b?.trajectories?.length && effects.hindTime != null) {
    const ht = effects.hindTime;             // 0 (NOW) .. maxStep (past)
    layers.push(new TripsLayer({
      id: "hindcast-trips", data: b.trajectories,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.steps,
      currentTime: ht, trailLength: 5,
      getColor: (d) => [...SEMANTIC.hindcast, 30 + d.weight * 190],
      getWidth: 1.4, widthUnits: "pixels", capRounded: true,
      updateTriggers: { currentTime: ht },
    }));
    /* density cloud of where the particles are at this instant */
    const k = Math.round(ht);
    const heads = [];
    for (const t of b.trajectories) {
      const idx = t.steps.indexOf(k);
      if (idx !== -1) heads.push({ pos: t.path[idx], w: t.weight });
    }
    if (heads.length) {
      layers.push(new HeatmapLayer({
        id: "hindcast-heat", data: heads,
        getPosition: (d) => d.pos, getWeight: (d) => d.w,
        radiusPixels: 40, intensity: 0.9, threshold: 0.06,
        colorRange: [[8, 47, 73], [12, 74, 110], [14, 116, 144],
                     [34, 158, 189], [56, 189, 248], [165, 233, 255]],
        updateTriggers: { getPosition: k },
      }));
    }
    /* the confidence ellipse for this timestep -- dashed: reconstruction */
    const ell = b.ellipses[Math.min(k, b.ellipses.length - 1)];
    if (ell && k > 0) {
      layers.push(new GeoJsonLayer({
        id: "hindcast-ellipse",
        data: { type: "FeatureCollection", features: [ell] },
        stroked: true, filled: false,
        getLineColor: [...SEMANTIC.hindcast, 200],
        getLineWidth: 1.8, lineWidthUnits: "pixels",
        getDashArray: [7, 5], dashJustified: true, extensions: dashExt,
        updateTriggers: { getLineColor: k },
      }));
    }
  }

  /* -------------------------------------------------- probable origin ---- */

  if (toggles.origin && b?.originCenter && effects.originAlpha > 0) {
    const [ox, oy] = b.originCenter;
    const a = effects.originAlpha;
    const baseKm = Math.max(b.uncertaintyKm ?? 1.2, 0.9);
    /* two expanding pulse rings, phase-shifted */
    [0, 0.5].forEach((ph, i) => {
      const p = ((tick / 90) + ph) % 1;
      layers.push(new PathLayer({
        id: `origin-pulse-${i}`,
        data: [{ path: circleRing(ox, oy, baseKm * (0.4 + p * 2.2), 72) }],
        getPath: (d) => d.path,
        getColor: [...SEMANTIC.origin, a * 190 * (1 - p)],
        getWidth: 1.6, widthUnits: "pixels",
        updateTriggers: { getPath: tick, getColor: tick },
      }));
    });
    /* uncertainty disc + crosshair */
    layers.push(new PolygonLayer({
      id: "origin-uncertainty",
      data: [{ poly: circleRing(ox, oy, baseKm, 72) }],
      getPolygon: (d) => d.poly,
      getFillColor: [...SEMANTIC.origin, 26 * a], stroked: true,
      getLineColor: [...SEMANTIC.origin, 160 * a],
      getLineWidth: 1.4, lineWidthUnits: "pixels",
      getDashArray: [4, 4], extensions: dashExt,
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind: "origin", title: "Probable origin",
        rows: [
          ["position", `${fmtLat(oy)}  ${fmtLon(ox)}`],
          ["window", `${b.sceneMeta?.acquired_utc?.slice(11, 16)}−${b.backtrackH}h → acq.`],
          ["uncertainty", `± ${baseKm.toFixed(1)} km`],
          ["confidence", b.originEllipse?.properties?.confidence_level ?? "—"],
        ],
      } : null),
    }));
    const ck = baseKm * 0.5;
    layers.push(new PathLayer({
      id: "origin-cross",
      data: [
        { path: [destination(ox, oy, ck, 0), destination(ox, oy, ck * 0.35, 0)] },
        { path: [destination(ox, oy, ck, 90), destination(ox, oy, ck * 0.35, 90)] },
        { path: [destination(ox, oy, ck, 180), destination(ox, oy, ck * 0.35, 180)] },
        { path: [destination(ox, oy, ck, 270), destination(ox, oy, ck * 0.35, 270)] },
      ],
      getPath: (d) => d.path, getColor: [...SEMANTIC.origin, 230 * a],
      getWidth: 2, widthUnits: "pixels",
    }));
    layers.push(new TextLayer({
      id: "origin-label",
      data: [{ pos: destination(ox, oy, baseKm * 1.5, 0) }],
      getPosition: (d) => d.pos,
      getText: () => "PROBABLE ORIGIN",
      getSize: 11, getColor: [...SEMANTIC.origin, 235 * a],
      fontFamily: "Inter, sans-serif", fontWeight: 700,
      getTextAnchor: "middle", getAlignmentBaseline: "bottom",
      characterSet: CHARSET,
    }));
  }

  /* ---------------------------------------------------------- forecast --- */

  if (toggles.forecast && b?.forecast?.features?.length && effects.fcAlpha > 0) {
    const feats = [...b.forecast.features].sort(
      (x, y) => x.properties.horizon_h - y.properties.horizon_h);
    feats.forEach((f, i) => {
      const reveal = clamp01(effects.fcAlpha * feats.length - i);
      if (reveal <= 0) return;
      layers.push(new GeoJsonLayer({
        id: `forecast-${i}`, data: { type: "FeatureCollection", features: [f] },
        stroked: false, filled: true,
        getFillColor: [...SEMANTIC.forecast, reveal * (34 - i * 8)],
        pickable: true,
        onHover: (info) => hover(info.object ? {
          kind: "forecast", title: `Forecast +${f.properties.horizon_h}h`,
          rows: [["valid", f.properties.valid_utc?.slice(0, 16).replace("T", " ")],
                 ["area", `${f.properties.area_km2} km²`],
                 ["confidence", f.properties.confidence_level]],
        } : null),
      }));
      layers.push(new GeoJsonLayer({
        id: `forecast-line-${i}`,
        data: { type: "FeatureCollection", features: [f] },
        stroked: true, filled: false,
        getLineColor: [...SEMANTIC.forecast, reveal * 200],
        getLineWidth: i === feats.length - 1 ? 1.9 : 1.3,
        lineWidthUnits: "pixels",
        getDashArray: [3, 5], extensions: dashExt,
      }));
    });
    if (driftFwd?.length) {
      layers.push(new ScatterplotLayer({
        id: "forecast-particles", data: driftFwd,
        getPosition: (d) => [d.lon, d.lat],
        getRadius: 70, radiusMinPixels: 0.8, radiusMaxPixels: 2,
        getFillColor: [...SEMANTIC.forecast, 130 * effects.fcAlpha],
        updateTriggers: { getPosition: tick },
      }));
    }
  }

  /* ------------------------------------------------------------ vessels -- */

  if (toggles.vessels && b?.tracks?.length) {
    const fadeFor = (t) => {
      if (t.rank) return 1;
      if (!effects.filterFade) return t.filtered ? 0.55 : 0.85;
      /* filtering animation: eliminated groups sink to a dim floor */
      const g = effects.filterFade(t);
      return g;
    };
    const growing = effects.aisGrow;   // trails grow with the clock
    const trackData = b.tracks.map((t) => ({
      ...t,
      alpha: fadeFor(t),
      visPath: growing ? trackPathUntil(t, simT) : t.path,
    })).filter((t) => t.visPath && t.alpha > 0.02);

    layers.push(new PathLayer({
      id: "vessel-trails-dim", data: trackData.filter((t) => !t.rank),
      getPath: (d) => d.visPath,
      getColor: (d) => [...SEMANTIC.vessel, 90 * d.alpha],
      getWidth: 1.1, widthUnits: "pixels",
      pickable: true,
      onClick: (i) => i.object && onSelect?.(i.object.mmsi),
      onHover: (i) => hover(i.object ? vesselTip(i.object) : null),
      updateTriggers: { getPath: [simT, growing], getColor: effects.filterTick },
    }));
    /* recency-faded live trail while the clock moves */
    if (growing) {
      layers.push(new TripsLayer({
        id: "vessel-trips", data: trackData,
        getPath: (d) => d.path,
        // Rebased to the AIS window: raw epoch seconds exceed float32
        // precision on the GPU and the trail head snaps in ~2 min jumps.
        getTimestamps: (d) => d.times.map((x) => ((x ?? b.t0) - b.aisStart) / 1000),
        currentTime: (simT - b.aisStart) / 1000, trailLength: 3 * 3600,
        getColor: (d) => (d.rank
          ? [...SEMANTIC.candidate, 230]
          : [...SEMANTIC.vessel, 190 * d.alpha]),
        getWidth: (d) => (d.rank === 1 ? 3 : 1.7),
        widthUnits: "pixels", capRounded: true,
        updateTriggers: { currentTime: simT },
      }));
    }
    layers.push(new PathLayer({
      id: "vessel-trails-candidates",
      data: trackData.filter((t) => t.rank),
      getPath: (d) => d.visPath,
      getColor: (d) => (d.rank === 1
        ? [...SEMANTIC.candidate, 245]
        : [...SEMANTIC.candidate, 130]),
      getWidth: (d) => (d.rank === 1 ? 3.2 : 1.8),
      widthUnits: "pixels",
      pickable: true,
      onClick: (i) => i.object && onSelect?.(i.object.mmsi),
      onHover: (i) => hover(i.object ? vesselTip(i.object) : null),
      updateTriggers: { getPath: [simT, growing] },
    }));

    /* ship glyphs at their exact interpolated position, rotated to heading */
    const ships = trackData.map((t) => {
      const st = trackStateAt(t, simT);
      return st ? { ...t, pos: st.pos, heading: st.heading, sogNow: st.sog } : null;
    }).filter(Boolean);
    layers.push(new TextLayer({
      id: "vessel-glyphs", data: ships,
      getPosition: (d) => d.pos,
      getText: () => "▲",
      getSize: (d) => (d.rank === 1 ? 21 : d.rank ? 17 : 13),
      getAngle: (d) => -(d.heading ?? 0),
      getColor: (d) => (d.rank === 1
        ? [...SEMANTIC.candidate, 255]
        : d.rank ? [...SEMANTIC.candidate, 210]
          : [...SEMANTIC.vessel, 60 + 195 * d.alpha]),
      fontFamily: "Segoe UI Symbol, sans-serif",
      characterSet: CHARSET, billboard: true,
      pickable: true,
      onClick: (i) => i.object && onSelect?.(i.object.mmsi),
      onHover: (i) => hover(i.object ? vesselTip(i.object, simT) : null),
      updateTriggers: { getPosition: simT, getAngle: simT, getColor: effects.filterTick },
    }));

    /* selection ring */
    const sel = ships.find((s) => s.mmsi === selectedMmsi);
    if (sel) {
      layers.push(new ScatterplotLayer({
        id: "vessel-selected", data: [sel],
        getPosition: (d) => d.pos, getRadius: 900,
        radiusMinPixels: 14, radiusMaxPixels: 26,
        stroked: true, filled: false,
        getLineColor: [240, 244, 250, 220], getLineWidth: 1.6,
        lineWidthUnits: "pixels",
        updateTriggers: { getPosition: simT },
      }));
    }

    /* attribution lock-on around the top candidate */
    if (effects.lockOn > 0 && b.top) {
      const topTrack = b.tracks.find((t) => t.rank === 1);
      const st = topTrack && trackStateAt(topTrack, simT);
      if (st) {
        const lk = easeOut(effects.lockOn);
        const rKm = lerp(6, 1.1, lk);
        [0, 90, 180, 270].forEach((off) => {
          const a0 = off + tick * 0.8;
          const arc = [];
          for (let d = 0; d <= 55; d += 5) {
            arc.push(destination(st.pos[0], st.pos[1], rKm, a0 + d));
          }
          layers.push(new PathLayer({
            id: `lockon-${off}`, data: [{ path: arc }],
            getPath: (d) => d.path,
            getColor: [...SEMANTIC.candidate, 235 * lk],
            getWidth: 2.2, widthUnits: "pixels",
            updateTriggers: { getPath: tick },
          }));
        });
        layers.push(new TextLayer({
          id: "lockon-label",
          data: [{ pos: destination(st.pos[0], st.pos[1], rKm * 1.7, 45) }],
          getPosition: (d) => d.pos,
          getText: () => `${(b.top.total_score * 100).toFixed(0)}%  ${b.top.vessel_name ?? b.top.mmsi}`,
          getSize: 12, getColor: [...SEMANTIC.candidate, 245 * lk],
          fontFamily: "JetBrains Mono, monospace",
          getTextAnchor: "start", characterSet: CHARSET,
          updateTriggers: { getPosition: tick },
        }));
      }
    }
  }

  /* -------------------------------------------------- observed slick ----- */

  if (toggles.slick && b?.slick?.features?.length && effects.slickAlpha > 0) {
    const a = effects.slickAlpha;
    const lockPulse = effects.slickLock
      ? 1 + 0.7 * Math.max(0, Math.sin(effects.slickLock * Math.PI)) : 1;
    /* soft outer glow: three widening strokes */
    [[9, 22], [5.5, 55], [3, 120]].forEach(([wd, al], i) => {
      layers.push(new GeoJsonLayer({
        id: `slick-glow-${i}`, data: b.slick,
        stroked: true, filled: false,
        getLineColor: [...SEMANTIC.slick, al * a],
        getLineWidth: wd * lockPulse, lineWidthUnits: "pixels",
      }));
    });
    layers.push(new GeoJsonLayer({
      id: "slick-body", data: b.slick,
      stroked: true, filled: true,
      getFillColor: [...SEMANTIC.slick, 80 * a],
      getLineColor: [...SEMANTIC.slick, 255 * a],
      getLineWidth: 2.2, lineWidthUnits: "pixels",
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind: "slick",
        title: `${i.object.properties?.slick_id ?? "Detected slick"} · OBSERVED`,
        rows: [
          ["area", `${i.object.properties?.area_km2} km²`],
          ["confidence", `${(i.object.properties?.confidence * 100).toFixed(1)}%`],
          ["axis", `${(i.object.properties?.major_axis_m / 1000).toFixed(1)} km @ ${i.object.properties?.orientation_deg}°`],
          ["damping", `${i.object.properties?.damping_ratio} dB`],
        ],
      } : null),
    }));
    if (effects.slickLabel && b.slickProps?.centroid) {
      layers.push(new TextLayer({
        id: "slick-label",
        data: [{ pos: b.slickProps.centroid }],
        getPosition: (d) => d.pos,
        getText: () => "OBSERVED SLICK",
        getSize: 10.5, getColor: [8, 12, 20, 235],
        backgroundColor: [245, 158, 11, 230], backgroundPadding: [6, 3],
        background: true,
        fontFamily: "Inter, sans-serif", fontWeight: 700,
        getPixelOffset: [0, -18], characterSet: CHARSET,
      }));
    }
  }

  /* ------------------------------------------------------ scene extent --- */

  if (bbox) {
    const [w, s, e, n] = bbox;
    layers.push(new PathLayer({
      id: "scene-extent",
      data: [{ path: [[w, s], [e, s], [e, n], [w, n], [w, s]] }],
      getPath: (d) => d.path,
      getColor: [130, 148, 178, 120], getWidth: 1, widthUnits: "pixels",
      getDashArray: [6, 4], extensions: dashExt,
    }));
    layers.push(new TextLayer({
      id: "scene-extent-label",
      data: [{ pos: [w, n] }],
      getPosition: (d) => d.pos,
      getText: () => `SENTINEL-1 SAR · ${b?.sceneMeta?.acquired_utc?.replace("T", " ").slice(0, 16) ?? ""} UTC`,
      getSize: 10, getColor: [130, 148, 178, 210],
      getTextAnchor: "start", getAlignmentBaseline: "bottom",
      getPixelOffset: [2, -4],
      fontFamily: "JetBrains Mono, monospace", characterSet: CHARSET,
    }));
  }

  const basemap = useMemo(
    () => basemapStyle(resolveBasemap(mode, appTheme)), [mode, appTheme]);

  return (
    <DeckGL
      viewState={view}
      onViewStateChange={onViewChange}
      controller={{ dragRotate: true, touchRotate: true, inertia: 260 }}
      layers={layers}
      style={{ position: "absolute", inset: 0 }}
      getCursor={({ isHovering }) => (isHovering ? "pointer" : "grab")}
    >
      <Map mapStyle={basemap} attributionControl={false} />
    </DeckGL>
  );
}

function vesselTip(t, simT) {
  const st = simT != null ? trackStateAt(t, simT) : null;
  return {
    kind: t.rank ? "candidate" : "vessel",
    title: `${t.name ?? "MMSI " + t.mmsi}${t.rank ? ` · RANK #${t.rank}` : ""}`,
    rows: [
      ["mmsi", t.mmsi],
      ["type", t.type ?? "—"],
      ...(st?.sog != null ? [["speed", `${st.sog.toFixed(1)} kn`]] : []),
      ["distance", `${t.distanceKm} km over ${t.durationH ?? "—"} h`],
      ...(t.rank ? [["score", `${(t.score * 100).toFixed(0)}%`]]
        : t.filtered ? [["eliminated", t.filterReason ?? ""]] : [["status", "under analysis"]]),
    ],
  };
}
