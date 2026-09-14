/* The data every globe surface draws: zones, incidents, the live AIS picture
 * and the overlays of the run in context. One hook, so the Operations
 * overview and the Global View cannot disagree about what is on the planet.
 *
 * Every value returned came from an API response. The hook also returns the
 * honesty facts a page must render beside the data: whether the live layer
 * was truncated, whether the stream is functionally working, and the
 * provider's own coverage note -- "connected but no data" over water the
 * provider has no receivers for is a coverage fact, not an empty sea.
 */

import { useEffect, useMemo, useState } from "react";

import { api, useApi } from "./api";
import { useShell } from "./shell";

/** vessels_geojson features -> track objects the globe can draw. */
export function tracksFromGeojson(fc) {
  return (fc?.features || []).map((f) => {
    const p = f.properties || {};
    return {
      mmsi: p.mmsi, name: p.vessel_name, type: p.vessel_type,
      rank: p.rank ?? null, score: p.total_score ?? null,
      filtered: Boolean(p.filtered), filterReason: p.filter_reason || null,
      source: p.source, path: f.geometry?.coordinates || [],
      distanceKm: p.distance_km, durationH: p.duration_h,
    };
  }).filter((t) => t.path.length >= 2);
}

/** The AIS stream state as a badge the UI can show without re-deriving it. */
export function aisBadgeFor(stream) {
  if (!stream) return null;
  if (stream.state === "not_configured") return { tone: "neutral", text: "NOT CONFIGURED" };
  if (stream.functionally_working) return { tone: "ok", text: "LIVE" };
  if (stream.connected) return { tone: "warn", text: "CONNECTED · NO DATA" };
  if (stream.state === "stopped") return { tone: "neutral", text: "STOPPED" };
  return { tone: "danger", text: String(stream.state || "unknown").toUpperCase() };
}

export function useGlobeData({ liveInterval = 15000, withRun = true } = {}) {
  const { runId: contextRun } = useShell();

  const zonesQ = useApi(() => api.zonesGeojson({ status: "active" }), []);
  const zoneListQ = useApi(() => api.listZones({ counts: true }), [], { interval: 60000 });
  const incidentsQ = useApi(() => api.listIncidents({ limit: 200 }), [], { interval: 45000 });
  /* Polled, because it is a LIVE layer. 15 s is well inside the AIS report
   * interval and far outside anything that would hammer the API. */
  const liveQ = useApi(() => api.liveVessels({ max_age_minutes: 60, limit: 2000 }),
                       [], { interval: liveInterval });
  const aisStatusQ = useApi(() => api.aisStatus(), [], { interval: 30000 });

  /* The run whose overlays are shown: the run in context, else the most
   * recently completed run. Stated on screen by the caller, never implied. */
  const [latestRun, setLatestRun] = useState(null);
  useEffect(() => {
    if (!withRun || contextRun) return undefined;
    let alive = true;
    api.listRunsPaged({ status: "complete", limit: 1 })
      .then((r) => { if (alive) setLatestRun(r?.items?.[0]?.run_id || null); })
      .catch(() => { if (alive) setLatestRun(null); });
    return () => { alive = false; };
  }, [withRun, contextRun]);
  const runId = withRun ? (contextRun || latestRun) : null;

  const [runLayers, setRunLayers] = useState(null);
  useEffect(() => {
    if (!runId) { setRunLayers(null); return undefined; }
    let alive = true;
    Promise.all([
      api.layer(runId, "scene_meta").catch(() => null),
      api.layer(runId, "slick").catch(() => null),
      api.layer(runId, "origin_cloud", { lite: true }).catch(() => null),
      api.layer(runId, "forecast").catch(() => null),
      api.vesselsGeojson(runId).catch(() => null),
      api.getRun(runId).catch(() => null),
    ]).then(([sceneMeta, slick, origin, forecast, vessels, run]) => {
      if (!alive) return;
      setRunLayers({ runId, sceneMeta, slick, origin, forecast,
                     tracks: tracksFromGeojson(vessels), run });
    });
    return () => { alive = false; };
  }, [runId]);

  const incidents = useMemo(() => (incidentsQ.data?.items || [])
    .map((i) => {
      const g = i.geometry;
      if (!g) return { ...i, lon: null, lat: null };
      if (g.type === "Point" && Array.isArray(g.coordinates)) {
        return { ...i, lon: g.coordinates[0], lat: g.coordinates[1] };
      }
      // A polygon incident is placed at its bbox centre for the marker; the
      // marker is a locator, and the polygon itself is not redrawn here.
      const ring = g.type === "Polygon" ? g.coordinates?.[0]
        : g.type === "MultiPolygon" ? g.coordinates?.[0]?.[0] : null;
      if (!ring?.length) return { ...i, lon: null, lat: null };
      const lons = ring.map((c) => c[0]); const lats = ring.map((c) => c[1]);
      return { ...i, lon: (Math.min(...lons) + Math.max(...lons)) / 2,
               lat: (Math.min(...lats) + Math.max(...lats)) / 2 };
    }), [incidentsQ.data]);

  const stream = aisStatusQ.data?.stream || null;

  return {
    zones: zonesQ.data,
    zoneList: zoneListQ.data?.zones || [],
    zoneListMeta: zoneListQ.data,
    incidents,
    incidentsTotal: incidentsQ.data?.total ?? null,
    vessels: liveQ.data?.vessels || [],
    live: liveQ.data,
    stream,
    aisStatus: aisStatusQ.data,
    aisBadge: aisBadgeFor(stream),
    runId,
    runLayers,
    loading: zonesQ.loading || liveQ.loading,
    loadingZones: zoneListQ.loading && !zoneListQ.data,
    loadingIncidents: incidentsQ.loading && !incidentsQ.data,
    loadingLive: liveQ.loading && !liveQ.data,
    errors: { zones: zonesQ.error, incidents: incidentsQ.error, live: liveQ.error },
    reloadZones: () => Promise.all([zonesQ.reload(), zoneListQ.reload()]),
    reloadIncidents: incidentsQ.reload,
  };
}
