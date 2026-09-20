/* Operations overview -- the home screen. The whole maritime situation on a
 * real globe, with the live numbers beside it.
 *
 * Every number on this page is read from an API response that was made in
 * the last few seconds and is polled: vessels from /ais/live, incidents from
 * /incidents, alerts from /alerts/summary, runs from /runs, the stream state
 * from /ais/status. When a source is unavailable or has no coverage the tile
 * says so in the provider's own words. Counters flash when they change.
 *
 * THE TIME TRANSPORT IS HONEST ABOUT WHAT IT CAN MOVE. Incidents, alerts and
 * runs carry timestamps, so scrubbing genuinely filters them. The live AIS
 * layer is a NOW layer -- the API returns current vessel state, not history --
 * so scrubbing away from NOW dims it and says so, rather than redrawing the
 * same positions under a past clock as if they had been observed then. The
 * one place history really exists is Incident Replay, and the transport links
 * to it.
 *
 * The page adapts to the role looking at it -- an administrator gets the
 * platform strip, an officer gets their desk summary -- but the PICTURE stays
 * global for everyone: an officer who cannot see a spill drifting toward
 * their boundary from the next zone is worse at the job, not more secure. */

import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity, AlertTriangle, Bell, ClipboardList, Crosshair, Film, FolderSearch, Globe2,
  Inbox, Layers, Map as MapIcon, Minus, PanelLeftClose, PanelLeftOpen, PanelRightClose,
  PanelRightOpen, Plus, Radar, Radio, Satellite, Server, Ship, Target, X,
} from "lucide-react";

import Globe, { GLOBE_INITIAL_VIEW, fmtLat, fmtLon } from "../components/Globe";
import { useGlobeCamera } from "../components/globe/GlobeScene";
import {
  CameraReadout, Compass, ScaleBar, ToolRail,
} from "../components/globe/GlobeChrome";
import { Badge, KV, LiveValue, Notice, Panel, Segmented, Switch } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";
import { hasRole, useSession } from "../lib/session";
import { useTheme } from "../lib/theme";
import { useGlobeData } from "../lib/useGlobeData";
import "../globe.css";

const LAYER_ROWS = [
  { key: "vessels", label: "Vessels (live AIS)", swatch: "var(--c-vessel)" },
  { key: "tracks", label: "Vessel tracks (run)", swatch: "var(--c-vessel)", run: true },
  { key: "slick", label: "Oil spill detections", swatch: "var(--c-slick)", run: true },
  { key: "origin", label: "Hindcast · origin cloud", swatch: "var(--c-hindcast)", run: true },
  { key: "forecast", label: "Forecast footprint", swatch: "var(--c-forecast)", run: true },
  { key: "incidents", label: "Incidents", swatch: "var(--c-incident)" },
  { key: "zones", label: "Operational zones", swatch: "var(--c-zone)" },
  { key: "zoneLabels", label: "Zone labels", swatch: null },
  { key: "scene", label: "Satellite scene footprint", swatch: "var(--c-protected)", run: true },
  { key: "graticule", label: "Lat / lon graticule", swatch: null },
];

const SEVERITY_TONE = {
  critical: "danger", high: "danger", warning: "warn", medium: "warn",
  info: "neutral", low: "neutral",
};


export default function Operations() {
  const { user } = useSession();
  const { theme } = useTheme();
  const cam = useGlobeCamera(GLOBE_INITIAL_VIEW);
  const data = useGlobeData({ liveInterval: 15000 });
  const { zones, incidents, vessels, live, stream, aisBadge, runLayers, runId } = data;

  const [basemap, setBasemap] = useState("canvas");
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [cursor, setCursor] = useState(null);
  const [selected, setSelected] = useState(null);
  const [tool, setTool] = useState("layers");
  const [layersOn, setLayersOn] = useState({
    vessels: true, tracks: true, slick: true, origin: false, forecast: true,
    incidents: true, zones: true, zoneLabels: true, graticule: true, scene: true,
  });

  const { data: alerts } = useApi(() => api.listAlerts({ limit: 30 }), [], { interval: 20000 });
  const { data: summary } = useApi(() => api.alertsSummary(), [], { interval: 20000 });
  const { data: invs } = useApi(() => api.listInvestigations(), [], { interval: 60000 });
  const { data: runs } = useApi(() => api.listRunsPaged({ limit: 20 }), [], { interval: 30000 });
  const isAdmin = hasRole(user);
  const { data: providers } = useApi(() => api.apiStatus(), [], { interval: 30000 });
  const { data: workers } = useApi(
    () => (isAdmin ? api.workers() : Promise.resolve(null)), [isAdmin], { interval: 30000 });
  const { data: mine } = useApi(
    () => (user?.role === "zone_officer" ? api.myZones() : Promise.resolve(null)), [user?.role]);

  const active = incidents.filter((i) => i.status === "open" || i.status === "investigating");
  const running = (runs?.items || []).filter((r) => r.status === "running");

  /* The event feed: real records from three stores, merged by time. */
  const feed = useMemo(() => {
    const items = [];
    for (const a of alerts?.alerts || []) {
      items.push({ t: Date.parse(a.created_utc), kind: "alert",
        tone: SEVERITY_TONE[a.severity] || "neutral", icon: <Bell size={11} />,
        title: a.title, sub: `${a.kind}${a.zone_id ? ` · ${a.zone_id}` : ""} · ${a.status}`,
        to: "/alerts" });
    }
    for (const i of incidents) {
      items.push({ t: Date.parse(i.created_utc || i.detected_utc), kind: "incident", tone: "danger",
        icon: <ClipboardList size={11} />, title: `${i.id} · ${i.title}`,
        sub: `${i.severity || "severity —"} · ${i.zone_id || "outside all zones"}${i.origin ? ` · ${i.origin}` : ""}`,
        to: `/incidents?focus=${i.id}` });
    }
    for (const r of runs?.items || []) {
      items.push({ t: Date.parse(r.started_utc), kind: "run",
        tone: r.status === "complete" ? "ok" : r.status === "failed" ? "danger" : "accent",
        icon: <Radar size={11} />, title: `Run ${r.status} · ${r.run_id}`,
        sub: `${r.scene_id || "no scene"} · ${r.stages_real ?? "?"}/${r.stages_total ?? "?"} stages real`,
        to: `/investigation?run=${r.run_id}` });
    }
    return items.filter((x) => Number.isFinite(x.t)).sort((a, b) => b.t - a.t);
  }, [alerts, incidents, runs]);

  const latestScene = runLayers?.sceneMeta;
  const latestSlick = runLayers?.slick?.features?.[0]?.properties;

  const onSelect = useCallback((layerId, obj) => {
    if (layerId === "globe-zones") setSelected({ kind: "zone", id: obj.properties?.id, object: obj.properties });
    else if (layerId === "globe-vessels") setSelected({ kind: "vessel", id: obj.mmsi, object: obj });
    else if (layerId === "globe-incidents") setSelected({ kind: "incident", id: obj.id, object: obj });
    else if (layerId === "globe-slick") setSelected({ kind: "slick", id: runId, object: obj.properties });
    else if (layerId === "globe-tracks") setSelected({ kind: "track", id: obj.mmsi, object: obj });
    setTool("selected");
  }, [runId]);

  const railHidden = rightOpen ? "" : "rail-hidden";

  return (
    <div className={`globe-root ${leftOpen ? "with-left" : ""}`} data-testid="operations-page">
      <Globe
        viewState={cam.viewState} onViewStateChange={cam.onViewStateChange}
        onPointerMove={cam.onPointerMove} onPointerLeave={cam.onPointerLeave}
        basemap={basemap} theme={theme}
        layersOn={layersOn}
        zones={layersOn.zones ? zones : null}
        incidents={incidents} vessels={vessels}
        runLayers={runLayers} selected={selected}
        onMapClick={(_, info) => { if (!info?.object) setSelected(null); }}
        onCursorMove={setCursor} onSelect={onSelect}
      />

      {/* ---------------------------------------------------------- left --- */}
      <button className="btn btn-sm btn-icon" onClick={() => setLeftOpen((o) => !o)}
        style={{ position: "absolute", top: 12, left: 12, zIndex: 6, background: "var(--glass)" }}
        title={leftOpen ? "Hide overview" : "Show overview"} data-testid="toggle-left">
        {leftOpen ? <PanelLeftClose size={13} /> : <PanelLeftOpen size={13} />}
      </button>

      <aside className={`gv-left ${leftOpen ? "" : "hidden"}`} style={{ paddingTop: 34 }}>
        <Panel title="Real-time overview" icon={<Activity size={12} />} collapsible
          right={<span className="tiny mono dim">{live?.as_of_utc ? fmt.utc(live.as_of_utc).slice(11) : ""}</span>}>
          <div className="ops-tiles">
            <Link className="ops-tile" to="/globe" data-testid="tile-vessels">
              <div className="k">Vessels · live</div>
              <LiveValue className="v" value={live ? fmt.int(live.total_in_view) : "—"} as="div" />
              <div className="s">
                {aisBadge ? <Badge tone={aisBadge.tone}>{aisBadge.text}</Badge> : <span>reading stream…</span>}
              </div>
            </Link>
            <Link className="ops-tile" to="/incidents" data-testid="tile-incidents">
              <div className="k">Active incidents</div>
              <LiveValue className={`v ${active.length ? "danger" : ""}`} value={active.length} as="div" />
              <div className="s">{incidents.length} total · {incidents.filter((i) => i.origin === "auto").length} auto-opened</div>
            </Link>
            <Link className="ops-tile" to="/investigations" data-testid="tile-investigations">
              <div className="k">Investigations</div>
              <LiveValue className="v" value={invs ? invs.length : "—"} as="div" />
              <div className="s">{running.length
                ? <span className="tone-accent">{running.length} running now</span>
                : `${runs?.total ?? "—"} runs recorded`}</div>
            </Link>
            <Link className="ops-tile" to="/alerts" data-testid="tile-alerts">
              <div className="k">Open alerts</div>
              <LiveValue className={`v ${summary?.by_severity?.critical ? "danger" : summary?.open ? "warn" : ""}`}
                value={summary ? summary.open : "—"} as="div" />
              <div className="s">{summary?.unrouted
                ? <span className="tone-warn">{summary.unrouted} routed to nobody</span> : "all routed"}</div>
            </Link>
          </div>
          {stream?.note && (
            <Notice icon={<Radio size={11} />} style={{ marginTop: 8 }} testid="ops-ais-note">{stream.note}</Notice>
          )}
        </Panel>

        {isAdmin && (
          <Panel title="Platform" icon={<Server size={12} />}
            right={<Link className="btn btn-xs" to="/system">Console</Link>}>
            <div className="kv-dense">
              <KV k="Providers working" v={providers
                ? `${providers.providers.filter((p) => p.status === "WORKING").length}/${providers.providers.length}` : "—"} />
              <KV k="Workers alive" v={workers
                ? `${workers.workers.filter((w) => w.alive === true).length}/${workers.workers.filter((w) => w.enabled).length}` : "—"} />
              <KV k="Jobs in flight" v={workers ? workers.jobs.in_flight_count : "—"} />
              <KV k="GPU" v={workers ? (workers.gpu?.measured ? "measured" : "not measured") : "—"} />
            </div>
          </Panel>
        )}

        {user?.role === "zone_officer" && (
          <Panel title="My desk" icon={<Inbox size={12} />}
            right={<Link className="btn btn-xs" to="/my-desk">Open</Link>}>
            <div className="kv-dense">
              <KV k="My zones" v={mine ? (mine.zones || []).map((z) => z.id).join(", ") || "none assigned" : "—"}
                tone={mine && !(mine.zones || []).length ? "danger" : ""} />
              <KV k="My open alerts" v={summary?.mine ?? "—"} />
              <KV k="Report scope" v={mine?.report_scope === "all" ? "all zones"
                : (mine?.zones || []).length ? "assigned zones only" : "none"} />
            </div>
          </Panel>
        )}

        <Panel title="Latest events" icon={<Bell size={12} />} flush
          right={<Link className="btn btn-xs" to="/alerts">View all</Link>}>
          {feed.length === 0 ? (
            <div className="state state-compact"><div className="state-title">No recent events</div></div>
          ) : (
            <div className="feed" data-testid="ops-feed">
              {feed.slice(0, 12).map((e, i) => (
                <Link key={`${e.kind}-${i}`} className="feed-item" to={e.to}>
                  <span className={`feed-ico ${e.tone}`}>{e.icon}</span>
                  <span className="feed-time">{new Date(e.t).toISOString().slice(11, 16)}</span>
                  <span style={{ minWidth: 0 }}>
                    <span className="feed-title">{e.title}</span>
                    <span className="feed-sub">{e.sub}</span>
                  </span>
                </Link>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="Live satellite" icon={<Satellite size={12} />}
          right={runId && <Link className="btn btn-xs" to={`/incident?run=${runId}`}><Film size={11} /> Replay</Link>}>
          {!runId ? (
            <div className="state state-compact">
              <div className="state-title">No satellite scene available</div>
              <div className="state-hint">No completed run holds a scene. Run an investigation to see its detection here.</div>
            </div>
          ) : (
            <>
              <div className="ops-scene">
                <img src={`/api/runs/${runId}/scene_png`} alt={`Sentinel-1 scene ${latestScene?.scene_id || ""}`}
                  loading="lazy" onError={(e) => { e.currentTarget.style.opacity = 0.12; }} />
                <span className="ops-scene-tag">
                  <Badge tone={latestSlick ? "danger" : "neutral"}>
                    {latestSlick ? "POSSIBLE OIL SPILL" : "NO SLICK IN SCENE"}
                  </Badge>
                </span>
                <div className="ops-scene-cap">
                  <span>SENTINEL-1 · {latestScene?.polarisation || "VV"}</span>
                  <span>{latestScene?.acquired_utc ? fmt.utc(latestScene.acquired_utc) : "—"}</span>
                </div>
              </div>
              <div className="kv-dense mt-2">
                <KV k="Scene" v={latestScene?.scene_id || runLayers?.run?.scene_id || "—"} wrap />
                <KV k="Slick area" v={latestSlick?.area_km2 != null ? `${Number(latestSlick.area_km2).toFixed(2)} km²` : "—"} />
                <KV k="Confidence" v={latestSlick?.confidence != null ? `${(latestSlick.confidence * 100).toFixed(1)}%` : "—"} />
                <KV k="Scene source" v={String(latestScene?.source || "unrecorded").toUpperCase()} />
              </div>
            </>
          )}
        </Panel>
      </aside>

      {/* ------------------------------------------------------ toolbar --- */}
      <div className="gv-toolbar" style={{ left: leftOpen ? 324 : 52 }}>
        <div className="map-toolbar">
          <Segmented value={basemap} onChange={setBasemap} testidPrefix="basemap" items={[
            { id: "canvas", label: "Canvas", icon: <Globe2 size={11} /> },
            { id: "relief", label: "Contrast", icon: <MapIcon size={11} /> },
            { id: "none", label: "None", icon: <X size={11} /> },
          ]} />
          <span className="sep" />
          <Link className="btn btn-sm" to="/globe"><Target size={12} /> Zones &amp; splitting</Link>
        </div>
      </div>

      <CameraReadout viewState={cam.viewState} className={railHidden} />
      <Compass bearing={cam.viewState.bearing || 0} pitch={cam.viewState.pitch || 0}
        className={`gv-compass ${railHidden}`}
        onReset={() => cam.flyTo({ bearing: 0, pitch: 0 }, 600)} />
      <ScaleBar zoom={cam.viewState.zoom} latitude={cursor?.lat ?? cam.viewState.latitude}
        className="gv-scale" />

      <div className={`gv-controls map-controls ${railHidden}`} style={{ bottom: 74 }}>
        <button className="btn" onClick={() => cam.zoomBy(0.8)} title="Zoom in" aria-label="Zoom in"><Plus size={14} /></button>
        <button className="btn" onClick={() => cam.zoomBy(-0.8)} title="Zoom out" aria-label="Zoom out"><Minus size={14} /></button>
        <button className="btn" title="Reset to the operational theatre" aria-label="Reset view"
          onClick={() => cam.flyTo({ longitude: GLOBE_INITIAL_VIEW.longitude,
            latitude: GLOBE_INITIAL_VIEW.latitude, zoom: GLOBE_INITIAL_VIEW.zoom }, 1200)}>
          <Crosshair size={14} />
        </button>
      </div>

      <ToolRail className={`gv-toolrail ${railHidden}`} value={tool}
        onChange={(id) => { setTool(id); setRightOpen(true); }} tools={[
          { id: "layers", icon: <Layers size={15} />, label: "Map layers" },
          { id: "selected", icon: <Crosshair size={15} />, label: "Selected object" },
          { id: "ais", icon: <Ship size={15} />, label: "Live AIS" },
          { id: "incidents", icon: <AlertTriangle size={15} />, label: "Incidents",
            badge: active.length || null },
        ]} />

      <button className="btn btn-sm btn-icon gv-rail-toggle" onClick={() => setRightOpen((o) => !o)}
        title={rightOpen ? "Hide panels" : "Show panels"} style={{ background: "var(--glass)" }}
        data-testid="toggle-right">
        {rightOpen ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
      </button>

      <aside className={`gv-rail ${rightOpen ? "" : "hidden"}`} style={{ paddingTop: 34, bottom: 74 }}>
        {tool === "layers" && (
          <Panel title="Map layers" icon={<Layers size={12} />}>
            {LAYER_ROWS.map((r) => (
              <div key={r.key} className="gv-layer">
                <Switch checked={layersOn[r.key] !== false} swatch={r.swatch || undefined}
                  disabled={r.run && !runLayers}
                  title={r.run && !runLayers ? "no completed run to draw" : undefined}
                  onChange={(v) => setLayersOn((s) => ({ ...s, [r.key]: v }))} label={r.label} />
                {r.key === "vessels" && aisBadge && <Badge tone={aisBadge.tone}>{aisBadge.text}</Badge>}
                {r.run && runLayers && <Badge tone={runLayers.run?.stages_mock ? "mock" : "ok"}>
                  {runLayers.run?.stages_mock ? "PARTLY MOCK" : "RUN"}</Badge>}
                {r.run && !runLayers && <Badge tone="ghost">NO RUN</Badge>}
              </div>
            ))}
            <div className="tiny dim mt-2" style={{ lineHeight: 1.55 }}>
              No shipping-route dataset is deployed, so route arcs are not drawn. Movement is shown
              from each vessel&apos;s own transmitted course.
            </div>
          </Panel>
        )}

        {tool === "selected" && (
          <SelectedObject selected={selected} runId={runId} onClose={() => setSelected(null)}
            onCentre={(lon, lat) => cam.flyTo({ longitude: lon, latitude: lat, zoom: Math.max(cam.base.zoom, 7) }, 900)} />
        )}

        {tool === "ais" && (
          <Panel title="Live AIS" icon={<Radio size={12} />}
            right={aisBadge && <Badge tone={aisBadge.tone}>{aisBadge.text}</Badge>}>
            <div className="kv-dense">
              <KV k="Vessels drawn" v={vessels.length} />
              <KV k="In view (server)" v={live?.total_in_view ?? "--"} />
              <KV k="Window" v={live ? `${live.max_age_minutes} min` : "--"} />
              <KV k="Provider" v={stream?.provider || "--"} />
              <KV k="Last message" v={stream?.last_message_utc ? fmt.ago(stream.last_message_utc) : "never"} />
              <KV k="Positions stored" v={stream?.counters?.positions ?? "--"} />
              <KV k="Reconnects" v={stream?.reconnects ?? "--"} />
              <KV k="Archived rows" v={data.aisStatus?.archive?.rows ?? "--"} />
            </div>
            {live?.truncated && (
              <Notice tone="warn">Truncated to the first {live.count} of {live.total_in_view} — zoom in for the rest.</Notice>
            )}
            {stream?.note && <Notice icon={<Radio size={11} />}>{stream.note}</Notice>}
          </Panel>
        )}

        {tool === "incidents" && (
          <Panel title="Incidents" icon={<AlertTriangle size={12} />} flush
            right={<Link className="btn btn-xs" to="/incidents">Register</Link>}>
            {incidents.length === 0 ? (
              <div className="state state-compact">
                <div className="state-title">No active incidents</div>
                <div className="state-hint">Nothing had been detected at this clock position.</div>
              </div>
            ) : (
              <div className="feed">
                {incidents.slice(0, 12).map((i) => (
                  <button key={i.id} className="feed-item" style={{ width: "100%", textAlign: "left", background: "none", border: "none", borderBottom: "1px solid var(--line)" }}
                    onClick={() => { setSelected({ kind: "incident", id: i.id, object: i }); setTool("selected");
                      if (i.lon != null) cam.flyTo({ longitude: i.lon, latitude: i.lat, zoom: 7 }, 1000); }}>
                    <span className={`feed-ico ${SEVERITY_TONE[i.severity] || "neutral"}`}><AlertTriangle size={11} /></span>
                    <span className="feed-time">{fmt.utc(i.detected_utc).slice(5, 16)}</span>
                    <span style={{ minWidth: 0 }}>
                      <span className="feed-title">{i.title}</span>
                      <span className="feed-sub">{i.id} · {i.zone_id || "outside all zones"} · {i.status}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Panel>
        )}
      </aside>
    </div>
  );
}

/* --------------------------------------------------------- selection ----- */

function SelectedObject({ selected, runId, onClose, onCentre }) {
  if (!selected) {
    return (
      <Panel title="Selected object" icon={<Crosshair size={12} />} testid="selected-object">
        <div className="state state-compact">
          <span className="state-icon"><Crosshair size={15} /></span>
          <div className="state-title">Nothing selected</div>
          <div className="state-hint">Click a vessel, incident, zone or detection on the globe.</div>
        </div>
      </Panel>
    );
  }
  const o = selected.object || {};
  const head = (icon, tone, title, sub) => (
    <div className="gv-sel-head">
      <span className={`gv-sel-ico ${tone}`}>{icon}</span>
      <div style={{ minWidth: 0 }}>
        <div className="gv-sel-title">{title}</div>
        <div className="gv-sel-sub">{sub}</div>
      </div>
    </div>
  );
  const closeBtn = <button className="btn btn-ghost btn-xs" onClick={onClose} aria-label="Clear selection"><X size={11} /></button>;

  if (selected.kind === "vessel") {
    return (
      <Panel title="Selected object" icon={<Ship size={12} />} right={closeBtn} testid="selected-object">
        {head(<Ship size={14} />, "accent", o.vessel_name || `MMSI ${o.mmsi}`,
          `${o.vessel_type || "type not transmitted"} · LIVE`)}
        <div className="kv-dense">
          <KV k="MMSI" v={o.mmsi} />
          {o.imo ? <KV k="IMO" v={o.imo} /> : null}
          <KV k="Latitude" v={fmtLat(o.lat)} />
          <KV k="Longitude" v={fmtLon(o.lon)} />
          <KV k="Speed" v={o.sog_kn == null ? "not transmitted" : `${Number(o.sog_kn).toFixed(1)} kn`} />
          <KV k="Course" v={o.cog_deg == null ? "not transmitted" : `${Number(o.cog_deg).toFixed(0)}°`} />
          {o.destination && <KV k="Destination" v={o.destination} />}
          <KV k="Zone" v={o.zone_id || "outside all zones"} />
          <KV k="Last report" v={fmt.utc(o.report_utc)} />
          <KV k="Source" v={`${o.provider || "AISStream"} · LIVE`} />
        </div>
        <div className="globe-actions">
          <Link className="btn btn-primary btn-sm" to={`/vessels?mmsi=${o.mmsi}`}>
            <Ship size={12} /> Vessel dossier
          </Link>
          <button className="btn btn-sm" onClick={() => onCentre(o.lon, o.lat)}><Crosshair size={12} /> Centre</button>
        </div>
      </Panel>
    );
  }
  if (selected.kind === "incident") {
    return (
      <Panel title="Selected object" icon={<ClipboardList size={12} />} right={closeBtn} testid="selected-object">
        {head(<AlertTriangle size={14} />, "danger", o.title, o.id)}
        <div className="row gap-1 mb-2 wrap">
          {o.severity && <Badge tone={SEVERITY_TONE[o.severity] || "neutral"}>{o.severity}</Badge>}
          <Badge tone="neutral">{o.status}</Badge>
          {o.origin && <Badge tone={o.origin === "auto" ? "warn" : "ok"}>{o.origin}-opened</Badge>}
        </div>
        <div className="kv-dense">
          <KV k="Location" v={o.lon != null ? `${fmtLat(o.lat)} ${fmtLon(o.lon)}` : "no geometry"} />
          <KV k="Zone" v={o.zone_id || "outside all zones"} />
          <KV k="Detected" v={fmt.utc(o.detected_utc)} />
          {o.area_km2 != null && <KV k="Area" v={`${Number(o.area_km2).toFixed(2)} km²`} />}
          {o.detection_confidence != null && <KV k="Confidence" v={`${(o.detection_confidence * 100).toFixed(1)}%`} />}
          {o.scene_id && <KV k="Source" v="Sentinel-1 (SAR)" />}
          <KV k="Runs" v={o.runs ?? 0} />
        </div>
        <div className="globe-actions">
          <Link className="btn btn-primary btn-sm"
            to={o.source_run_id ? `/investigation?run=${o.source_run_id}` : `/incidents?focus=${o.id}`}>
            <FolderSearch size={12} /> Open investigation
          </Link>
          {o.source_run_id && (
            <Link className="btn btn-sm" to={`/incident?run=${o.source_run_id}`}><Film size={12} /> Replay</Link>
          )}
        </div>
      </Panel>
    );
  }
  if (selected.kind === "zone") {
    const primary = (o.officers || []).find((x) => x.is_primary);
    return (
      <Panel title="Selected object" icon={<MapIcon size={12} />} right={closeBtn} testid="selected-object">
        {head(<MapIcon size={14} />, "teal", o.name || o.id, `${o.kind}${o.protected ? " · PROTECTED" : ""}`)}
        <div className="kv-dense">
          <KV k="Area" v={o.area_km2 != null ? `${Number(o.area_km2).toLocaleString()} km²` : "--"} />
          <KV k="Officer" v={primary ? (primary.display_name || primary.email) : "unassigned"}
            tone={primary ? "" : "danger"} />
          <KV k="Jurisdiction" v={o.jurisdiction || "--"} />
          <KV k="Open incidents" v={o.open_incident_count ?? 0} />
        </div>
        <div className="globe-actions">
          <Link className="btn btn-sm" to={`/globe?zone=${o.id}`}>Global View</Link>
          <Link className="btn btn-sm" to={`/zones?zone=${o.id}`}>Manage</Link>
        </div>
      </Panel>
    );
  }
  if (selected.kind === "slick") {
    return (
      <Panel title="Selected object" icon={<Crosshair size={12} />} right={closeBtn} testid="selected-object">
        {head(<Crosshair size={14} />, "danger", o.slick_id || "Oil spill (detected)", "OBSERVED · Sentinel-1 (SAR)")}
        <div className="kv-dense">
          <KV k="Area" v={o.area_km2 != null ? `${Number(o.area_km2).toFixed(2)} km²` : "--"} />
          <KV k="Perimeter" v={o.perimeter_km != null ? `${Number(o.perimeter_km).toFixed(2)} km` : "--"} />
          <KV k="Confidence" v={o.confidence != null ? `${(o.confidence * 100).toFixed(2)}` : "--"} />
          <KV k="Orientation" v={o.orientation_deg != null ? `${o.orientation_deg}°` : "--"} />
          <KV k="Source" v={String(o.source || "unrecorded").toUpperCase()} />
        </div>
        <div className="globe-actions">
          <Link className="btn btn-primary btn-sm" to={`/investigation?run=${runId}`}>
            <Radar size={12} /> Open investigation
          </Link>
          <Link className="btn btn-sm" to={`/incident?run=${runId}`}><Film size={12} /> Replay</Link>
        </div>
      </Panel>
    );
  }
  if (selected.kind === "track") {
    return (
      <Panel title="Selected object" icon={<Ship size={12} />} right={closeBtn} testid="selected-object">
        {head(<Ship size={14} />, o.rank === 1 ? "danger" : "accent", o.name || `MMSI ${o.mmsi}`,
          o.rank === 1 ? "HIGHEST-RANKED CANDIDATE" : o.rank ? `CANDIDATE · RANK #${o.rank}`
            : o.filtered ? "eliminated by gating" : "considered")}
        <div className="kv-dense">
          <KV k="MMSI" v={o.mmsi} />
          {o.score != null && <KV k="Attribution score" v={`${(o.score * 100).toFixed(0)}%`} />}
          {o.filterReason && <KV k="Eliminated" v={o.filterReason} wrap />}
          {o.distanceKm != null && <KV k="Distance in window" v={`${o.distanceKm} km`} />}
          <KV k="Source" v={String(o.source || "unrecorded").toUpperCase()} />
        </div>
        {o.rank && (
          <Notice style={{ marginTop: 8 }}>Potential source attribution — investigative support, not proof of guilt.</Notice>
        )}
        <div className="globe-actions">
          <Link className="btn btn-sm" to={`/investigation?run=${runId}`}>Workspace</Link>
          <Link className="btn btn-sm" to={`/vessels?mmsi=${o.mmsi}`}>Dossier</Link>
        </div>
      </Panel>
    );
  }
  return null;
}
