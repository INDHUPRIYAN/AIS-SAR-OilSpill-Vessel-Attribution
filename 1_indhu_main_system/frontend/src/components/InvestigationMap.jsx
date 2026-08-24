/* The investigation map.
 *
 * deck.gl over a MapLibre basemap. Layer order is deliberate and bottom-up:
 * origin heatmap, forecast, vessel tracks, slick, suspect track, so the thing
 * under investigation is never hidden by the evidence around it.
 *
 * The time slider drives the drift particles. Backtracked particles carry a
 * `step_index` counting into the past, so scrubbing replays the hindcast --
 * which is far more legible than a static point cloud of 7,500 particles.
 */

import { useEffect, useMemo, useState } from "react";
import DeckGL from "@deck.gl/react";
import { GeoJsonLayer, ScatterplotLayer, PathLayer, PolygonLayer } from "@deck.gl/layers";
import { HeatmapLayer } from "@deck.gl/aggregation-layers";
import { Map } from "react-map-gl/maplibre";
import "maplibre-gl/dist/maplibre-gl.css";

const LIGHT_BASEMAP = {
  version: 8,
  sources: {
    base: {
      type: "raster",
      tiles: ["https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "(c) OpenStreetMap contributors, (c) CARTO",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#cfe3f2" } },
    { id: "base", type: "raster", source: "base",
      paint: { "raster-opacity": 0.95, "raster-saturation": -0.2 } },
  ],
};

const BASEMAP = {
  version: 8,
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#070b14" } },
    {
      id: "osm",
      type: "raster",
      source: "osm",
      paint: {
        // Desaturated and dimmed: the basemap is context, not content. At full
        // strength it competes with the slick and the drift cloud.
        "raster-opacity": 0.42,
        "raster-saturation": -0.75,
        "raster-contrast": 0.12,
      },
    },
  ],
};

const RGB = {
  slick: [245, 158, 11],
  slickFill: [245, 158, 11, 90],
  origin: [251, 191, 36],
  forecast: [56, 189, 248],
  vessel: [107, 124, 153],
  suspect: [244, 63, 94],
};

export default function InvestigationMap({
  viewState, onViewStateChange, layers: show, slick, origin, forecast,
  vessels, suspects, sceneMeta, timeStep, onHover,
}) {
  const [light, setLight] = useState(
    () => document.documentElement.getAttribute("data-theme") === "light");
  useEffect(() => {
    const obs = new MutationObserver(() => setLight(
      document.documentElement.getAttribute("data-theme") === "light"));
    obs.observe(document.documentElement,
      { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  /* Particles split by backward timestep so the slider can replay the run. */
  const { particles, ellipses, maxStep } = useMemo(() => {
    if (!origin?.features) return { particles: [], ellipses: [], maxStep: 0 };
    const p = [], e = [];
    let max = 0;
    for (const f of origin.features) {
      const props = f.properties || {};
      const kind = props.feature_type || props.kind;
      if (kind === "ellipse" || kind === "confidence_ellipse") {
        e.push(f);
      } else if (kind === "particle" || props.weight !== undefined) {
        const step = Number(props.step_index ?? Math.abs(props.timestep_h ?? 0)) || 0;
        max = Math.max(max, step);
        const c = f.geometry?.coordinates;
        if (Array.isArray(c) && c.length >= 2) {
          p.push({ position: [c[0], c[1]], weight: Number(props.weight ?? 0.5), step });
        }
      }
    }
    return { particles: p, ellipses: e, maxStep: max };
  }, [origin]);

  const visibleParticles = useMemo(
    () => (timeStep == null ? particles : particles.filter((d) => d.step <= timeStep)),
    [particles, timeStep]
  );

  /* Vessel tracks, keyed by MMSI, with the suspect rank attached so the
   * prime suspect can be drawn differently from background traffic. */
  const tracks = useMemo(() => {
    const feats = vessels?.features;
    if (!feats?.length) return [];
    return feats.map((f) => ({
      mmsi: f.properties.mmsi,
      path: f.geometry.coordinates,
      rank: f.properties.rank,
      score: f.properties.total_score,
      name: f.properties.vessel_name,
      type: f.properties.vessel_type,
      filtered: Boolean(f.properties.filtered),
      filterReason: f.properties.filter_reason,
      source: f.properties.source,
    }));
  }, [vessels]);

  const deckLayers = [];

  if (show.origin && visibleParticles.length) {
    deckLayers.push(
      new HeatmapLayer({
        id: "origin-heat",
        data: visibleParticles,
        getPosition: (d) => d.position,
        getWeight: (d) => d.weight,
        radiusPixels: 46,
        intensity: 1.1,
        threshold: 0.05,
        colorRange: [
          [69, 39, 6], [120, 63, 4], [180, 83, 9],
          [217, 119, 6], [245, 158, 11], [252, 211, 77],
        ],
      })
    );
    deckLayers.push(
      new ScatterplotLayer({
        id: "origin-particles",
        data: visibleParticles,
        getPosition: (d) => d.position,
        getRadius: 55,
        radiusMinPixels: 0.7,
        radiusMaxPixels: 2.4,
        getFillColor: (d) => [...RGB.origin, 40 + d.weight * 150],
      })
    );
  }

  if (show.origin && ellipses.length) {
    deckLayers.push(
      new GeoJsonLayer({
        id: "origin-ellipses",
        data: { type: "FeatureCollection", features: ellipses },
        stroked: true, filled: false,
        getLineColor: [251, 191, 36, 110],
        getLineWidth: 1.6, lineWidthUnits: "pixels",
      })
    );
  }

  if (show.forecast && forecast?.features?.length) {
    deckLayers.push(
      new GeoJsonLayer({
        id: "forecast",
        data: forecast,
        stroked: true, filled: true,
        getFillColor: [56, 189, 248, 26],
        getLineColor: [56, 189, 248, 190],
        getLineWidth: 1.8, lineWidthUnits: "pixels",
        pickable: true,
        onHover: (i) => onHover?.(i.object ? {
          kind: "forecast",
          title: `Forecast +${i.object.properties?.horizon_h}h`,
          rows: [
            ["area", `${i.object.properties?.area_km2 ?? "—"} km²`],
            ["confidence", i.object.properties?.confidence_level ?? "—"],
          ],
        } : null),
      })
    );
  }

  if (show.vessels && tracks.length) {
    // Excluded vessels stay on the map, dimmed. Hiding them would hide the
    // gating decision; showing WHY each was excluded is what makes the
    // filtering auditable rather than a black box.
    deckLayers.push(
      new PathLayer({
        id: "vessel-tracks",
        data: tracks.filter((t) => !t.rank),
        getPath: (d) => d.path,
        getColor: (d) => (d.filtered ? [...RGB.vessel, 42] : [...RGB.vessel, 95]),
        getWidth: 1.2, widthUnits: "pixels", widthMinPixels: 1,
        pickable: true,
        onHover: (i) => onHover?.(i.object ? {
          kind: "vessel",
          title: `MMSI ${i.object.mmsi}`,
          rows: [
            ["type", i.object.type || "—"],
            ["status", i.object.filtered ? "filtered out" : "considered"],
            ...(i.object.filterReason ? [["reason", i.object.filterReason]] : []),
            ["source", i.object.source],
          ],
        } : null),
      })
    );
    deckLayers.push(
      new PathLayer({
        id: "suspect-tracks",
        data: tracks.filter((t) => t.rank),
        getPath: (d) => d.path,
        getColor: (d) => (d.rank === 1 ? [...RGB.suspect, 255] : [244, 63, 94, 150]),
        getWidth: (d) => (d.rank === 1 ? 3.4 : 2),
        widthUnits: "pixels", widthMinPixels: 2,
        pickable: true,
        onHover: (i) => onHover?.(i.object ? {
          kind: "suspect",
          title: `#${i.object.rank} · ${i.object.name || i.object.mmsi}`,
          rows: [
            ["mmsi", i.object.mmsi],
            ["score", Number(i.object.score).toFixed(3)],
            ["type", i.object.type || "—"],
            ["source", i.object.source],
          ],
        } : null),
      })
    );
  }

  if (show.slick && slick?.features?.length) {
    deckLayers.push(
      new GeoJsonLayer({
        id: "slick",
        data: slick,
        stroked: true, filled: true,
        getFillColor: RGB.slickFill,
        getLineColor: [...RGB.slick, 255],
        getLineWidth: 2.4, lineWidthUnits: "pixels",
        pickable: true,
        onHover: (i) => onHover?.(i.object ? {
          kind: "slick",
          title: i.object.properties?.slick_id || "Detected slick",
          rows: [
            ["area", `${i.object.properties?.area_km2 ?? "—"} km²`],
            ["length", `${((i.object.properties?.major_axis_m ?? 0) / 1000).toFixed(1)} km`],
            ["bearing", `${i.object.properties?.orientation_deg ?? "—"}°`],
            ["damping", `${i.object.properties?.damping_ratio ?? "—"} dB`],
          ],
        } : null),
      })
    );
  }

  /* Scene footprint, so it is obvious what the sensor actually saw. */
  if (sceneMeta?.bbox?.length === 4) {
    const [w, s, e, n] = sceneMeta.bbox;
    deckLayers.push(
      new PolygonLayer({
        id: "scene-extent",
        data: [{ polygon: [[w, s], [e, s], [e, n], [w, n], [w, s]] }],
        getPolygon: (d) => d.polygon,
        stroked: true, filled: false,
        getLineColor: [107, 124, 153, 120],
        getLineWidth: 1, lineWidthUnits: "pixels",
        getDashArray: [6, 4],
      })
    );
  }

  return (
    <DeckGL
      viewState={viewState}
      onViewStateChange={onViewStateChange}
      controller={{ dragRotate: false }}
      layers={deckLayers}
      style={{ position: "absolute", inset: 0 }}
    >
      <Map mapStyle={light ? LIGHT_BASEMAP : BASEMAP} attributionControl={false} />
    </DeckGL>
  );
}

export { RGB };
