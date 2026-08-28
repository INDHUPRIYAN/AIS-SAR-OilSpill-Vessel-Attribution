/* The workspace map: eight individually toggleable layers, spec palette.
 *
 * Layer order is bottom-up and deliberate: SAR raster, hindcast cloud,
 * forecast, origin ring, vessel tracks, slick + geometry annotations on top —
 * the thing under investigation is never hidden by the evidence around it.
 *
 * Every geometry is read from a contract file; nothing here invents data.
 * The time value (epoch ms) drives vessel interpolation, the hindcast
 * timestep shown, and which forecast horizon is emphasised.
 */

import { useMemo, useState } from "react";
import DeckGL from "@deck.gl/react";
import {
  GeoJsonLayer, ScatterplotLayer, PathLayer, PolygonLayer, BitmapLayer,
  TextLayer,
} from "@deck.gl/layers";
import { PathStyleExtension } from "@deck.gl/extensions";
import { Map as MapGL } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";

import { WS } from "./palette";
import {
  circleRing, destination, trackStateAt, trackPathUntil, bearingDeg, fmtUtc,
} from "../../lib/replay";

const dashExt = [new PathStyleExtension({ dash: true })];
const CHARSET = "auto";

/* Offline-first basemap: OSM raster when reachable; if tiles fail (airplane
 * mode) MapLibre keeps the background color and the SAR raster becomes the
 * de-facto basemap — the spec's required fallback. */
const BASEMAP = {
  version: 8,
  sources: {
    osm: { type: "raster", tileSize: 256,
           tiles: ["https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"] },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0a1020" } },
    { id: "osm", type: "raster", source: "osm",
      paint: { "raster-opacity": 0.5, "raster-saturation": -0.7 } },
  ],
};

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

export default function WorkspaceMap({
  view, onViewChange, show, layers, timeMs, sceneT0, runId,
  selectedMmsi, onSelect, onHover, maxStep,
}) {
  const { sceneMeta, slick, origin, forecast, vessels, suspects } = layers;
  const [pinned, setPinned] = useState(null);
  const hover = (info) => onHover?.(info ?? pinned);

  const bbox = sceneMeta?.bbox;
  const suspectRank = useMemo(() => {
    const m = new Map();
    for (const s of suspects?.suspects ?? []) m.set(s.mmsi, s.rank);
    return m;
  }, [suspects]);

  /* vessels_geojson features -> track objects with per-fix epoch times */
  const tracks = useMemo(() => (vessels?.features ?? []).map((f) => {
    const p = f.properties;
    return {
      mmsi: p.mmsi, name: p.vessel_name, type: p.vessel_type,
      rank: suspectRank.get(p.mmsi) ?? null,
      filtered: Boolean(p.filtered), filterReason: p.filter_reason,
      source: p.source, distanceKm: p.distance_km, durationH: p.duration_h,
      path: f.geometry.coordinates,
      times: (p.times_epoch ?? []).map((s) => (s == null ? null : s * 1000)),
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

  /* --------------------------------------------------- SAR scene raster -- */
  if (show.sar && bbox && runId) {
    deck.push(new BitmapLayer({
      id: "ws-sar", image: `/api/runs/${runId}/scene_png`,
      bounds: [bbox[0], bbox[1], bbox[2], bbox[3]],
      opacity: 0.85, desaturate: 0,
    }));
  }

  /* --------------------------------------------------- hindcast (magenta) */
  if (show.hindcast && hind.pts.length) {
    const vis = hind.pts.filter((d) => d.step === step);
    deck.push(new ScatterplotLayer({
      id: "ws-hindcast", data: vis,
      getPosition: (d) => d.pos, getRadius: 60,
      radiusMinPixels: 1, radiusMaxPixels: 2.6,
      getFillColor: (d) => [...WS.hindcast, 60 + d.w * 170],
      pickable: true,
      onHover: (i) => hover(i.object ? {
        kind: "hindcast", title: `Hindcast particle · T−${step} h`,
        rows: [["weight", i.object.w.toFixed(3)],
               ["time", fmtUtc(sceneT0 - step * 3.6e6)]],
      } : null),
      updateTriggers: { getPosition: step },
    }));
    const ell = hind.ells[Math.min(step, hind.ells.length - 1)];
    if (ell) {
      deck.push(new GeoJsonLayer({
        id: "ws-hindcast-ellipse",
        data: { type: "FeatureCollection", features: [ell] },
        stroked: true, filled: false,
        getLineColor: [...WS.hindcast, 210],
        getLineWidth: 1.7, lineWidthUnits: "pixels",
        getDashArray: [6, 4], extensions: dashExt,
        updateTriggers: { getLineColor: step },
      }));
    }
  }

  /* ------------------------------------------------------ forecast (amber) */
  if (show.forecast && forecast?.features?.length) {
    const feats = [...forecast.features].sort(
      (a, b) => (a.properties.horizon_h ?? 0) - (b.properties.horizon_h ?? 0));
    feats.forEach((f, i) => {
      const h = f.properties.horizon_h ?? 0;
      const active = aheadH > 0 &&
        Math.abs(h - Math.min(24, Math.max(6, aheadH))) ===
        Math.min(...feats.map((x) =>
          Math.abs((x.properties.horizon_h ?? 0) -
                   Math.min(24, Math.max(6, aheadH)))));
      deck.push(new GeoJsonLayer({
        id: `ws-forecast-${i}`,
        data: { type: "FeatureCollection", features: [f] },
        stroked: true, filled: true,
        getFillColor: [...WS.forecast, active ? 70 : 36 - i * 8],
        getLineColor: [...WS.forecast, active ? 240 : 150],
        getLineWidth: active ? 2.2 : 1.3, lineWidthUnits: "pixels",
        getDashArray: [4, 4], extensions: dashExt,
        pickable: true,
        onHover: (x) => hover(x.object ? {
          kind: "forecast", title: `Forecast +${h} h`,
          rows: [["valid", (f.properties.valid_utc || "").replace("T", " ").slice(0, 16) + " UTC"],
                 ["area", `${f.properties.area_km2 ?? "—"} km²`],
                 ["confidence", f.properties.confidence_level ?? "—"]],
        } : null),
        updateTriggers: { getFillColor: aheadH, getLineColor: aheadH },
      }));
    });
  }

  /* --------------------------------------------------- origin zone (gold) */
  if (show.origin && hind.ells.length) {
    const winMidStep = hind.ells.length - 1;
    const oe = hind.ells[Math.min(Math.round((hind.max || winMidStep) / 2),
                                  hind.ells.length - 1)];
    const c = oe?.properties?.center;
    if (c) {
      const md = origin?.metadata || {};
      deck.push(new PathLayer({
        id: "ws-origin-ring",
        data: [1.0, 1.35].map((f) => ({ path: circleRing(c[0], c[1], 2.2 * f, 72) })),
        getPath: (d) => d.path,
        getColor: [...WS.origin, 220], getWidth: 2, widthUnits: "pixels",
        pickable: true,
        onHover: (i) => hover(i.object ? {
          kind: "origin", title: "Probable origin zone",
          rows: [
            ["window", `${(md.origin_window_start_utc || "").slice(11, 16)}–${(md.origin_window_end_utc || "").slice(11, 16)} UTC`],
            ["centre", `${c[1].toFixed(4)}, ${c[0].toFixed(4)}`],
          ],
        } : null),
      }));
      deck.push(new TextLayer({
        id: "ws-origin-label",
        data: [{ pos: destination(c[0], c[1], 3.4, 0) }],
        getPosition: (d) => d.pos,
        getText: () => `ORIGIN ${(origin?.metadata?.origin_window_start_utc || "").slice(11, 16)}–${(origin?.metadata?.origin_window_end_utc || "").slice(11, 16)} UTC`,
        getSize: 10.5, getColor: [...WS.origin, 240],
        fontFamily: "JetBrains Mono, monospace",
        getTextAnchor: "middle", getAlignmentBaseline: "bottom",
        characterSet: CHARSET,
      }));
    }
  }

  /* ------------------------------------------------------- AIS (cyan set) */
  if (show.vessels && tracks.length) {
    const colorOf = (t) =>
      t.rank === 1 ? WS.suspect :
      t.rank ? WS.candidate :
      t.filtered ? WS.filtered : WS.vessel;
    const alphaOf = (t) => (t.filtered ? 70 : t.rank === 1 ? 255 : 190);

    deck.push(new PathLayer({
      id: "ws-tracks", data: tracks,
      getPath: (d) => d.path,
      getColor: (d) => [...colorOf(d), alphaOf(d)],
      getWidth: (d) => (d.rank === 1 ? 3.2 : d.rank ? 2 :
                        d.mmsi === selectedMmsi ? 2.4 : 1.2),
      widthUnits: "pixels",
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
        title: `${i.object.name ?? "MMSI " + i.object.mmsi}${i.object.rank ? ` · #${i.object.rank}` : i.object.filtered ? " · excluded" : ""}`,
        rows: [
          ["mmsi", i.object.mmsi], ["type", i.object.type || "—"],
          ...(i.object.filtered ? [["reason", i.object.filterReason || "—"]] : []),
          ["distance", `${i.object.distanceKm ?? "—"} km`],
          ["source", i.object.source],
        ],
      } : null),
    }));

    /* direction arrows: sparse rotated glyphs along each visible track */
    const arrows = [];
    for (const t of tracks) {
      if (t.filtered && t.mmsi !== selectedMmsi) continue;
      const stride = Math.max(6, Math.floor(t.path.length / 5));
      for (let i = stride; i < t.path.length - 1; i += stride) {
        const [x1, y1] = t.path[i - 1], [x2, y2] = t.path[i];
        arrows.push({ pos: t.path[i], ang: -bearingDeg(y1, x1, y2, x2),
                      c: colorOf(t) });
      }
    }
    deck.push(new TextLayer({
      id: "ws-arrows", data: arrows,
      getPosition: (d) => d.pos, getText: () => "▲",
      getSize: 11, getAngle: (d) => d.ang,
      getColor: (d) => [...d.c, 210],
      fontFamily: "Segoe UI Symbol, sans-serif", characterSet: CHARSET,
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
        getRadius: (d) => (d.rank === 1 ? 500 : 320),
        radiusMinPixels: 3, radiusMaxPixels: 8,
        getFillColor: (d) => [...colorOf(d), d.filtered ? 90 : 255],
        stroked: true, getLineColor: [10, 16, 32, 220], getLineWidth: 1,
        lineWidthUnits: "pixels",
        pickable: true,
        onHover: (i) => hover(i.object ? {
          kind: "vessel-now",
          title: `${i.object.name ?? i.object.mmsi} @ ${fmtUtc(timeMs)}`,
          rows: [["speed", i.object.sogNow != null ? `${i.object.sogNow.toFixed(1)} kn` : "—"],
                 ["heading", i.object.heading != null ? `${Math.round(i.object.heading)}°` : "—"]],
        } : null),
        updateTriggers: { getPosition: timeMs },
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
          getLineColor: [255, 255, 255, 230], getLineWidth: 1.6,
          lineWidthUnits: "pixels",
          updateTriggers: { getPosition: timeMs },
        }));
      }
    }
  }

  /* --------------------------------------------- slick + geometry (red) -- */
  if (show.slick && slick?.features?.length) {
    deck.push(new GeoJsonLayer({
      id: "ws-slick", data: slick,
      stroked: true, filled: true,
      getFillColor: [...WS.slick, 80],
      getLineColor: [...WS.slick, 255],
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

  /* scene footprint always */
  if (bbox) {
    const [w, s, e, n] = bbox;
    deck.push(new PathLayer({
      id: "ws-extent",
      data: [{ path: [[w, s], [e, s], [e, n], [w, n], [w, s]] }],
      getPath: (d) => d.path, getColor: [148, 163, 184, 120],
      getWidth: 1, widthUnits: "pixels",
      getDashArray: [6, 4], extensions: dashExt,
    }));
  }

  return (
    <DeckGL
      viewState={view}
      onViewStateChange={onViewChange}
      controller={{ dragRotate: true }}
      layers={deck}
      style={{ position: "absolute", inset: 0 }}
      getCursor={({ isHovering }) => (isHovering ? "pointer" : "grab")}
      onClick={(i) => { if (!i.object) setPinned(null); }}
    >
      <MapGL mapStyle={BASEMAP} attributionControl={false} />
    </DeckGL>
  );
}
