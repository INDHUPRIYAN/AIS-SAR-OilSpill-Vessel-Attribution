// @ts-check
/* Basemaps: real geography, and an honest account of what is configured.
 *
 * The geopolitical and dark-maritime basemaps are drawn from the bundled
 * Natural Earth data (public/geo/ne, built by scripts/build_basemap_natural_earth.py
 * from the India point-of-view country file). They need no network, so the
 * globe looks like Earth in an air-gapped deployment and in the offline e2e test.
 *
 * Satellite imagery is a provider, configured by environment:
 *   VITE_MAP_SATELLITE_URL          raster tile template ({z}/{x}/{y})
 *   VITE_MAP_SATELLITE_ATTRIBUTION  credit line the provider requires
 *   VITE_MAP_SATELLITE_MAXZOOM      deepest zoom the provider serves
 *   VITE_MAP_DETAIL_URL             optional raster detail under the labels, z >= 7
 *   VITE_MAP_DETAIL_ATTRIBUTION
 * With no satellite URL the mode is reported as not configured and the vector
 * basemap is shown; nothing is substituted for imagery.
 * Every provider is documented in docs/ux/MAP_DATA_SOURCES.md. */

/** @typedef {"geopolitical" | "satellite" | "dark"} BasemapId */

// Spelled exactly `import.meta.env`: Vite replaces that expression at build
// time and nothing cleverer (a parenthesised or aliased form reads as empty).
// @ts-ignore -- `env` is Vite's addition to ImportMeta
const VITE_ENV = import.meta.env || {};

/** @param {Record<string, any>} [env] */
export function readMapConfig(env = VITE_ENV) {
  const clean = (/** @type {unknown} */ v) => (typeof v === "string" && v.trim() ? v.trim() : null);
  return {
    satelliteUrl: clean(env.VITE_MAP_SATELLITE_URL),
    satelliteAttribution: clean(env.VITE_MAP_SATELLITE_ATTRIBUTION),
    satelliteMaxZoom: Number(env.VITE_MAP_SATELLITE_MAXZOOM) || 17,
    detailUrl: clean(env.VITE_MAP_DETAIL_URL),
    detailAttribution: clean(env.VITE_MAP_DETAIL_ATTRIBUTION),
  };
}

export const MAP_CONFIG = readMapConfig();

/** What every map opens on: satellite imagery when a provider is configured,
 *  the bundled geopolitical chart when none is (an offline host still gets a
 *  real Earth). One constant so no page can disagree about it.
 *  @type {BasemapId} */
export const DEFAULT_BASEMAP = MAP_CONFIG.satelliteUrl ? "satellite" : "geopolitical";

/** The basemap choices, with whether each can actually be shown.
 *  @param {ReturnType<typeof readMapConfig>} [config] */
export function basemapOptions(config = MAP_CONFIG) {
  return [
    { id: "geopolitical", label: "Geopolitical", available: true, source: "Natural Earth" },
    { id: "satellite", label: "Satellite", available: Boolean(config.satelliteUrl),
      source: config.satelliteUrl ? (config.satelliteAttribution || "Configured imagery provider") : null,
      unavailable: "Satellite basemap not configured" },
    { id: "dark", label: "Dark Maritime", available: true, source: "Natural Earth" },
  ];
}

/** What is drawn for a request: the satellite mode falls back to the vector
 *  basemap when no provider is configured.
 *  @param {string} requested @param {ReturnType<typeof readMapConfig>} [config]
 *  @returns {{ id: BasemapId, fellBack: boolean }} */
export function resolveBasemap(requested, config = MAP_CONFIG) {
  if (requested === "satellite") {
    return config.satelliteUrl ? { id: "satellite", fellBack: false } : { id: "geopolitical", fellBack: true };
  }
  return { id: requested === "dark" ? "dark" : "geopolitical", fellBack: false };
}

const INK = {
  geopolitical: {
    dark: { ocean: "#0b2036", land: "#26384a", coast: "#5b7690", border: "#7f95aa", label: "#d3dde8",
      halo: "#0b2036", marine: "#6f93b8", graticule: "rgba(148, 178, 208, 0.10)", space: "#050b14" },
    light: { ocean: "#c9dcec", land: "#f1eee6", coast: "#7b8b9a", border: "#9a8f86", label: "#2b3440",
      halo: "#f6f4ee", marine: "#4a739c", graticule: "rgba(40, 70, 100, 0.12)", space: "#e8eef4" },
  },
  dark: {
    dark: { ocean: "#071523", land: "#111c28", coast: "#34485c", border: "#2a3a4b", label: "#7d8ea1",
      halo: "#071523", marine: "#4f7396", graticule: "rgba(120, 150, 180, 0.08)", space: "#03070d" },
    light: { ocean: "#b7cbdc", land: "#dfe3e6", coast: "#7b8b9a", border: "#a9b1b8", label: "#4a5561",
      halo: "#e9edf0", marine: "#3f6488", graticule: "rgba(40, 70, 100, 0.10)", space: "#dfe7ee" },
  },
};

const FONT = ["Open Sans Semibold"];

function graticule(step = 15) {
  const features = [];
  for (let lon = -180; lon <= 180; lon += step) {
    const c = []; for (let lat = -85; lat <= 85; lat += 2.5) c.push([lon, lat]);
    features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: c } });
  }
  for (let lat = -75; lat <= 75; lat += step) {
    const c = []; for (let lon = -180; lon <= 180; lon += 2.5) c.push([lon, lat]);
    features.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: c } });
  }
  return { type: "FeatureCollection", features };
}

/** The layer every data overlay is inserted beneath, so place names stay legible. */
export const LABELS_ANCHOR = "ot-labels-anchor";

/**
 * A complete MapLibre style for a basemap.
 * @param {object} o
 * @param {string} o.basemap   requested basemap id
 * @param {"dark"|"light"} [o.theme]
 * @param {boolean} [o.graticule]
 * @param {{countries?: boolean, coastlines?: boolean, labels?: boolean}} [o.show]
 * @param {string} [o.origin]  where /geo and /fonts are served from
 * @param {ReturnType<typeof readMapConfig>} [o.config]
 * @param {"globe"|"mercator"} [o.projection]
 */
export function buildStyle({ basemap, theme = "dark", graticule: grid = true, show = {}, origin, config = MAP_CONFIG,
  projection = "globe" }) {
  const { id } = resolveBasemap(basemap, config);
  const base = origin ?? (typeof window !== "undefined" ? window.location.origin : "");
  const ink = INK[id === "dark" ? "dark" : "geopolitical"][theme === "light" ? "light" : "dark"];
  const sat = id === "satellite";
  const vis = (/** @type {boolean | undefined} */ on) => ({ visibility: on === false ? "none" : "visible" });

  /** @type {Record<string, any>} */
  const sources = {
    countries: { type: "geojson", data: `${base}/geo/ne/countries.json`, attribution: "Natural Earth" },
    borders: { type: "geojson", data: `${base}/geo/ne/borders.json` },
    coast: { type: "geojson", data: `${base}/geo/ne/coast.json` },
    "country-labels": { type: "geojson", data: `${base}/geo/ne/country_labels.json` },
    "marine-labels": { type: "geojson", data: `${base}/geo/ne/marine_labels.json` },
  };
  if (grid) sources.graticule = { type: "geojson", data: graticule() };
  if (sat) {
    sources.satellite = { type: "raster", tiles: [config.satelliteUrl], tileSize: 256,
      maxzoom: config.satelliteMaxZoom, attribution: config.satelliteAttribution || "" };
  }
  if (!sat && config.detailUrl) {
    sources.detail = { type: "raster", tiles: [config.detailUrl], tileSize: 256, minzoom: 7,
      attribution: config.detailAttribution || "" };
  }

  const layers = [
    { id: "ot-ocean", type: "background", paint: { "background-color": ink.ocean } },
    // Land is always drawn: under imagery it is what shows while tiles load,
    // and what remains if the provider is unreachable.
    { id: "ot-land", type: "fill", source: "countries", layout: vis(show.countries),
      paint: { "fill-color": ink.land, "fill-antialias": true } },
    sat && { id: "ot-satellite", type: "raster", source: "satellite",
      paint: { "raster-fade-duration": 300 } },
    sources.detail && { id: "ot-detail", type: "raster", source: "detail", minzoom: 7 },
    grid && { id: "ot-graticule", type: "line", source: "graticule",
      paint: { "line-color": sat ? "rgba(255,255,255,0.10)" : ink.graticule, "line-width": 0.7 } },
    { id: "ot-coast", type: "line", source: "coast", layout: vis(show.coastlines),
      paint: { "line-color": sat ? "rgba(255,255,255,0.35)" : ink.coast,
        "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.5, 6, 1.1] } },
    { id: "ot-borders", type: "line", source: "borders", layout: vis(show.countries),
      paint: { "line-color": sat ? "rgba(255,255,255,0.55)" : ink.border,
        "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.4, 6, 1],
        "line-dasharray": [3, 2] } },
    // Data overlays are inserted before this empty layer.
    { id: LABELS_ANCHOR, type: "background", layout: { visibility: "none" } },
    { id: "ot-marine-labels", type: "symbol", source: "marine-labels", layout: {
        ...vis(show.labels), "text-field": ["get", "name"], "text-font": FONT, "text-letter-spacing": 0.18,
        "text-transform": "uppercase", "text-max-width": 7,
        "text-size": ["interpolate", ["linear"], ["zoom"], 1, 9, 5, 12],
        "symbol-sort-key": ["get", "rank"] },
      // Oceans at world zoom; seas, bays and gulfs as the view closes in.
      filter: ["<=", ["get", "rank"], ["step", ["zoom"], 0, 1.5, 1, 3, 3, 4.5, 6]],
      paint: { "text-color": sat ? "#cfe3f7" : ink.marine, "text-halo-color": sat ? "rgba(0,0,0,0.6)" : ink.halo,
        "text-halo-width": 1.2 } },
    id !== "dark" && { id: "ot-country-labels", type: "symbol", source: "country-labels", layout: {
        ...vis(show.labels), "text-field": ["get", "name"], "text-font": FONT, "text-max-width": 8,
        "text-size": ["interpolate", ["linear"], ["zoom"], 1, 10, 6, 14],
        "symbol-sort-key": ["get", "rank"] },
      filter: ["<=", ["get", "min_zoom"], ["+", ["zoom"], 1.2]],
      paint: { "text-color": sat ? "#ffffff" : ink.label, "text-halo-color": sat ? "rgba(0,0,0,0.7)" : ink.halo,
        "text-halo-width": 1.4 } },
  ].filter(Boolean);

  return {
    version: 8,
    name: `oceantrace-${id}-${theme}`,
    projection: { type: projection === "mercator" ? "mercator" : "globe" },
    glyphs: `${base}/fonts/{fontstack}/{range}.pbf`,
    sky: { "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 0.6, 5, 0.15, 7, 0] },
    sources,
    layers,
    metadata: { "ot:space": ink.space, "ot:basemap": id },
  };
}
