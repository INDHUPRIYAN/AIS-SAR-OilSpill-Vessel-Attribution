/* The Global View: the maritime picture on a real globe, and the zone
 * boundary editor on the same canvas.
 *
 * Two modes on ONE canvas, which is the whole reason this is a single page
 * rather than two. Zone splitting is a spatial judgement made against the
 * traffic and the incidents you are dividing up -- an editor on a blank
 * projection would make an operator draw a boundary blind, then discover on
 * another screen what it had captured.
 *
 *   NORMAL    zones, incidents, live vessels, run overlays
 *   SPLITTING the same picture, plus a vertex editor
 *
 * EVERY number on this page comes from an API response. The coordinate
 * readout is the pointer's own position; the zone, its officer and its area
 * come from /api/zones; vessels from /api/ais/live. Where a value is absent
 * the page says which kind of absent it is -- "not transmitted" for a vessel
 * that sent no name, "outside all zones" for water no zone covers, "no
 * receiver coverage" for a live layer the provider cannot see.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, Check, ClipboardList, Crosshair, Eye, Globe2, Layers, Lock, Map as MapIcon,
  Minus, PanelRightClose, PanelRightOpen, Pencil, Plus, Radio, RotateCcw, Save, Search, Ship,
  Trash2, Undo2, Redo2, UserCheck, X, Film, Radar,
} from "lucide-react";

import Globe, { CoordinateReadout, GLOBE_INITIAL_VIEW, fmtLat, fmtLon, parseCoordinate,
  useBoundaryEditor } from "../components/Globe";
import { useGlobeCamera } from "../components/globe/GlobeScene";
import { Badge, DataState, KV, Notice, Panel, Segmented, Spinner, Switch } from "../components/ui";
import { api, fmt } from "../lib/api";
import { url } from "../lib/urls";
import { sphericalAreaKm2 } from "../lib/geodesy";
import { hasRole, useSession } from "../lib/session";
import { useTheme } from "../lib/theme";
import { useGlobeData } from "../lib/useGlobeData";
import "../globe.css";

const MODES = { NORMAL: "normal", SPLITTING: "splitting" };

const LAYER_ROWS = [
  { key: "vessels", label: "Vessels (live AIS)", swatch: "var(--c-vessel)" },
  { key: "tracks", label: "Vessel tracks (run)", swatch: "var(--c-vessel)" },
  { key: "slick", label: "Oil spill detection", swatch: "var(--c-slick)" },
  { key: "origin", label: "Hindcast · origin", swatch: "var(--c-hindcast)" },
  { key: "forecast", label: "Forecast footprint", swatch: "var(--c-forecast)" },
  { key: "incidents", label: "Incidents", swatch: "var(--c-incident)" },
  { key: "zones", label: "Operational zones", swatch: "var(--c-zone)" },
  { key: "zoneLabels", label: "Zone labels", swatch: null },
  { key: "scene", label: "Scene footprint", swatch: "var(--c-protected)" },
  { key: "graticule", label: "Lat / lon graticule", swatch: null },
];

const SEVERITY_TONE = { critical: "danger", high: "danger", medium: "warn", low: "neutral" };

export default function GlobeViewPage() {
  const { user } = useSession();
  const { theme } = useTheme();
  const [params] = useSearchParams();
  const [mode, setMode] = useState(MODES.NORMAL);
  const [basemap, setBasemap] = useState("canvas");
  const [railOpen, setRailOpen] = useState(true);
  const [layersOn, setLayersOn] = useState({
    zones: true, zoneLabels: true, incidents: true, vessels: true, graticule: true,
    tracks: true, slick: true, origin: true, forecast: true, scene: true,
  });
  const [cursor, setCursor] = useState(null);
  const [cursorZone, setCursorZone] = useState(undefined);
  const [selected, setSelected] = useState(null);       // {kind, id, object}
  const [search, setSearch] = useState("");
  const [searchError, setSearchError] = useState(null);
  const [saveState, setSaveState] = useState(null);

  const editorActive = mode === MODES.SPLITTING;
  const cam = useGlobeCamera(GLOBE_INITIAL_VIEW, { parallax: !editorActive });

  /* --- data -------------------------------------------------------------- */
  const data = useGlobeData();
  const { zones, zoneList, incidents, vessels, live, stream, aisBadge, runLayers, runId } = data;
  const selectedZone = selected?.kind === "zone"
    ? zoneList.find((z) => z.id === selected.id) || null : null;

  /* Deep link: /globe?zone=… or ?incident=… selects and flies. */
  useEffect(() => {
    const zid = params.get("zone");
    if (zid && zoneList.length) {
      const z = zoneList.find((x) => x.id === zid);
      if (z) { setSelected({ kind: "zone", id: z.id }); if (z.bbox) flyToBbox(z.bbox); }
    }
    const iid = params.get("incident");
    if (iid && incidents.length) {
      const i = incidents.find((x) => x.id === iid);
      if (i && i.lon != null) { setSelected({ kind: "incident", id: i.id, object: i });
        cam.flyTo({ longitude: i.lon, latitude: i.lat, zoom: 7 }, 1400); }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, zoneList.length, incidents.length]);

  const flyToBbox = useCallback((bbox) => {
    const [w, s, e, n] = bbox;
    const span = Math.max(e - w, n - s, 0.5);
    cam.flyTo({ longitude: (w + e) / 2, latitude: (s + n) / 2,
                zoom: Math.max(2.5, Math.min(8, Math.log2(120 / span) + 1)) }, 1200);
  }, [cam]);

  /* --- the boundary editor ----------------------------------------------- */
  const editor = useBoundaryEditor();
  const [editTarget, setEditTarget] = useState(null);  // zone being reshaped
  const [newZone, setNewZone] = useState({ id: "", name: "", parent_id: "", officer: "" });
  const [officers, setOfficers] = useState(null);      // null = not available to this role

  useEffect(() => {
    if (!editorActive || officers !== null) return;
    api.listUsers({ role: "zone_officer" })
      .then((r) => setOfficers((r?.users || []).filter((u) => u.active)))
      .catch(() => setOfficers(false));
  }, [editorActive, officers]);

  const onMapClick = useCallback((lngLat, info) => {
    if (!editorActive) {
      if (info?.object) return;   // a click on an object selects it (onSelect)
      // Clicking water asks the server which zone owns it, which is the same
      // call routing makes -- so the answer shown is the answer an alert gets.
      api.lookupZone(lngLat.lon, lngLat.lat)
        .then((r) => {
          setCursorZone(r.zone);
          setSelected(r.zone ? { kind: "zone", id: r.zone.id } : null);
        })
        .catch(() => setCursorZone(null));
      return;
    }
    if (info?.layer?.id === "globe-editor-vertices" && info.object) {
      editor.setSelectedIndex(info.object.index);
      return;
    }
    editor.addPoint(lngLat.lon, lngLat.lat);
  }, [editorActive, editor]);

  const onSelect = useCallback((layerId, obj) => {
    if (editorActive) return;
    if (layerId === "globe-zones") setSelected({ kind: "zone", id: obj.properties?.id });
    else if (layerId === "globe-vessels") setSelected({ kind: "vessel", id: obj.mmsi, object: obj });
    else if (layerId === "globe-incidents") setSelected({ kind: "incident", id: obj.id, object: obj });
    else if (layerId === "globe-slick") setSelected({ kind: "slick", id: runId, object: obj });
    else if (layerId === "globe-tracks") setSelected({ kind: "track", id: obj.mmsi, object: obj });
  }, [editorActive, runId]);

  /* Keyboard: the editor needs Delete and undo, and a text input must not
   * have its keys stolen. */
  useEffect(() => {
    if (!editorActive) return undefined;
    const handler = (e) => {
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if ((e.key === "Delete" || e.key === "Backspace") && editor.selectedIndex !== null) {
        e.preventDefault(); editor.deletePoint(editor.selectedIndex);
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault(); if (e.shiftKey) editor.redo(); else editor.undo();
      } else if (e.key === "Escape") {
        editor.setSelectedIndex(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [editorActive, editor]);

  const startNewZone = () => {
    setEditTarget(null);
    editor.reset([]);
    setNewZone({ id: "", name: "", parent_id: selectedZone?.id || "zone-bob", officer: "" });
    setMode(MODES.SPLITTING);
    setSaveState(null);
    setRailOpen(true);
  };

  const startReshape = (zone) => {
    if (!zone?.id) return;
    // The list response omits polygons, so fetch the one being edited.
    api.getZone(zone.id).then((full) => {
      const ring = (full.geometry?.coordinates?.[0] || []).slice(0, -1);
      editor.reset(ring);
      setEditTarget(full);
      setMode(MODES.SPLITTING);
      setSaveState(null);
      setRailOpen(true);
      if (full.bbox) flyToBbox(full.bbox);
    }).catch((e) => setSaveState({ error: e.message }));
  };

  const cancelEdit = () => {
    editor.reset([]); setEditTarget(null); setMode(MODES.NORMAL); setSaveState(null);
  };

  const save = async () => {
    if (!editor.geometry) return;
    setSaveState({ busy: true });
    try {
      let out;
      if (editTarget) {
        // `revision` is mandatory for a geometry change. The server rejects a
        // stale one with a 409 rather than silently overwriting whoever moved
        // the boundary first.
        out = await api.updateZone(editTarget.id, {
          geometry: editor.geometry, revision: editTarget.revision,
          reason: "boundary edited on the globe",
        });
        setSaveState({ ok: `${out.name} saved at revision ${out.revision}` });
      } else {
        out = await api.createZone({
          id: newZone.id.trim(), name: newZone.name.trim() || newZone.id.trim(),
          parent_id: newZone.parent_id || null, geometry: editor.geometry,
        });
        let msg = `${out.name} created (${Number(out.area_km2).toLocaleString()} km²)`;
        if (newZone.officer) {
          try {
            await api.assignZoneOfficer(out.id, Number(newZone.officer), true);
            msg += " · officer assigned";
          } catch (e) {
            msg += ` · officer NOT assigned: ${e.message}`;
          }
        }
        setSaveState({ ok: msg });
      }
      await data.reloadZones();
      editor.reset([]); setEditTarget(null); setMode(MODES.NORMAL);
      setSelected({ kind: "zone", id: out.id });
    } catch (e) {
      // The server's message is the message. It names the offending point, the
      // parent it fell outside, or the revision that moved -- all of which are
      // actionable, and none of which this page could work out itself.
      setSaveState({ error: e.message });
    }
  };

  const runSearch = (e) => {
    e?.preventDefault();
    const parsed = parseCoordinate(search);
    if (!parsed) {
      setSearchError("Could not read that as a coordinate. Try \"13.5, 89.5\" or \"13 30 N 89 30 E\".");
      return;
    }
    setSearchError(null);
    cam.flyTo({ longitude: parsed.lon, latitude: parsed.lat, zoom: Math.max(cam.base.zoom, 6) }, 900);
    api.lookupZone(parsed.lon, parsed.lat)
      .then((r) => setCursorZone(r.zone)).catch(() => setCursorZone(null));
  };

  const canDraw = hasRole(user, "zone_officer");

  const liveTruncated = live?.truncated;
  const showLoading = data.loading;

  return (
    <div className="globe-root" data-testid="globe-page">
      <Globe
        viewState={cam.viewState}
        onViewStateChange={cam.onViewStateChange}
        onPointerMove={cam.onPointerMove}
        onPointerLeave={cam.onPointerLeave}
        basemap={basemap}
        theme={theme}
        layersOn={layersOn}
        zones={layersOn.zones ? zones : null}
        incidents={incidents}
        vessels={vessels}
        runLayers={runLayers}
        editor={editorActive ? editor : null}
        selected={selected}
        onMapClick={onMapClick}
        onCursorMove={setCursor}
        onSelect={onSelect}
      />

      {/* Mode switch. Labelled with what each mode DOES, not with an icon
          alone -- "Zone splitting" is a consequential action. */}
      <div className="gv-toolbar">
        <div className="map-toolbar">
          <button className={`btn btn-sm ${!editorActive ? "btn-on" : ""}`}
            onClick={() => (editorActive ? cancelEdit() : setMode(MODES.NORMAL))}
            data-testid="mode-normal">
            <Eye size={12} /> View
          </button>
          <button className={`btn btn-sm ${editorActive ? "btn-on" : ""}`} onClick={startNewZone}
            disabled={!canDraw}
            title={canDraw ? "Draw a new operational zone on the globe"
                           : `Your role (${user?.role}) may not edit zones`}
            data-testid="mode-splitting">
            <Pencil size={12} /> Zone splitting
          </button>
          <span className="sep" />
          <Segmented value={basemap} onChange={setBasemap} testidPrefix="basemap" items={[
            { id: "canvas", label: "Canvas", icon: <Globe2 size={11} /> },
            { id: "relief", label: "Contrast", icon: <MapIcon size={11} /> },
            { id: "none", label: "None", icon: <X size={11} /> },
          ]} />
        </div>
      </div>

      {editorActive && (
        <div className="gv-mode-banner"><Pencil size={11} /> Zone splitting mode · click the globe to add boundary points</div>
      )}

      <CoordinateReadout cursor={cursor} zone={cursorZone} zoom={cam.viewState.zoom} />

      <div className={`gv-controls map-controls ${railOpen ? "" : "rail-hidden"}`}>
        <button className="btn" onClick={() => cam.zoomBy(0.8)} title="Zoom in"><Plus size={14} /></button>
        <button className="btn" onClick={() => cam.zoomBy(-0.8)} title="Zoom out"><Minus size={14} /></button>
        <button className="btn" title="Reset to the operational theatre"
          onClick={() => cam.flyTo({ longitude: GLOBE_INITIAL_VIEW.longitude,
            latitude: GLOBE_INITIAL_VIEW.latitude, zoom: GLOBE_INITIAL_VIEW.zoom }, 1200)}>
          <Crosshair size={14} />
        </button>
      </div>

      {showLoading && (
        <div className={`map-panel gv-loading ${railOpen ? "" : "rail-hidden"}`}>
          <Spinner label="loading layers" />
        </div>
      )}

      <button className="btn btn-sm btn-icon gv-rail-toggle" onClick={() => setRailOpen((o) => !o)}
        title={railOpen ? "Hide panels" : "Show panels"} style={{ background: "var(--glass)" }}>
        {railOpen ? <PanelRightClose size={13} /> : <PanelRightOpen size={13} />}
      </button>

      {/* ------------------------------------------------------------- rail */}
      <aside className={`gv-rail ${railOpen ? "" : "hidden"}`} style={{ paddingTop: 34 }}>
        <form className="gv-search map-panel" onSubmit={runSearch} style={{ borderRadius: "var(--r-sm)" }}>
          <Search size={13} className="muted" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="13.5, 89.5  ·  13 30 N 89 30 E"
            aria-label="Search a coordinate" data-testid="coord-search" />
          <button className="btn btn-xs" type="submit">Go</button>
        </form>
        {searchError && <Notice tone="danger" testid="coord-error">{searchError}</Notice>}

        {editorActive ? (
          <EditorPanel editor={editor} editTarget={editTarget} newZone={newZone}
            setNewZone={setNewZone} zoneList={zoneList} officers={officers}
            onSave={save} onCancel={cancelEdit} saveState={saveState} />
        ) : (
          <>
            <Panel title="Layers" icon={<Layers size={12} />} collapsible>
              {LAYER_ROWS.map((r) => {
                const disabledReason = (r.key === "tracks" || r.key === "slick" || r.key === "origin"
                  || r.key === "forecast" || r.key === "scene") && !runLayers ? "no run in context" : null;
                return (
                  <div key={r.key} className="gv-layer" data-testid={`globe-layer-${r.key}`}>
                    <Switch checked={layersOn[r.key] !== false} swatch={r.swatch || undefined}
                      disabled={Boolean(disabledReason)} title={disabledReason || undefined}
                      onChange={(v) => setLayersOn((s) => ({ ...s, [r.key]: v }))} label={r.label} />
                    {r.key === "vessels" && aisBadge && <Badge tone={aisBadge.tone}>{aisBadge.text}</Badge>}
                  </div>
                );
              })}
              {runLayers && (
                <div className="tiny muted mt-2">
                  Run overlays: <Link to={`/investigation?run=${runId}`} className="mono">{runId}</Link>
                  {runLayers.run?.stages_mock ? <Badge tone="mock" className="ml-auto" style={{ marginLeft: 6 }}>{runLayers.run.stages_mock} MOCK STAGES</Badge> : null}
                </div>
              )}
            </Panel>

            <Panel title="Live AIS" icon={<Radio size={12} />}
              right={aisBadge && <Badge tone={aisBadge.tone}>{aisBadge.text}</Badge>}>
              <div className="kv-dense">
                <KV k="Vessels shown" v={vessels.length} />
                <KV k="In view (server)" v={live?.total_in_view ?? "--"} />
                <KV k="Window" v={live ? `${live.max_age_minutes} min` : "--"} />
                <KV k="Provider" v={stream?.provider || "--"} />
                <KV k="Archived rows" v={data.aisStatus?.archive?.rows ?? "--"} />
              </div>
              {liveTruncated && (
                /* Never silent. A client that got 2000 of 4200 must say so. */
                <Notice tone="warn">truncated to the first {live.count} — zoom in for the rest</Notice>
              )}
              {stream?.note && (
                /* The coverage sentence. This is the difference between
                   "no traffic" and "nothing is listening to this ocean". */
                <Notice testid="ais-note" icon={<Radio size={11} />}>{stream.note}</Notice>
              )}
              {!stream && data.aisStatus === null && (
                <DataState kind="loading" compact title="Reading stream state" />
              )}
            </Panel>

            <SelectedPanel selected={selected} zone={selectedZone} zoneList={zoneList}
              zonesLoading={data.loadingZones} zonesError={data.errors.zones}
              incidents={incidents} runLayers={runLayers} canDraw={canDraw}
              onReshape={startReshape} onSelect={(sel) => setSelected(sel)}
              onFly={(bbox) => flyToBbox(bbox)} cam={cam} />
          </>
        )}
      </aside>
    </div>
  );
}

/* ----------------------------------------------------------- selection --- */

function SelectedPanel({ selected, zone, zoneList, zonesLoading, zonesError, incidents,
                         runLayers, canDraw, onReshape, onSelect, onFly, cam }) {
  if (selected?.kind === "vessel" && selected.object) {
    const v = selected.object;
    return (
      <Panel title="Selected · Vessel" icon={<Ship size={12} />} testid="selected-vessel"
        right={<button className="btn btn-ghost btn-xs" onClick={() => onSelect(null)}><X size={11} /></button>}>
        <div className="gv-sel-head">
          <span className="gv-sel-ico accent"><Ship size={14} /></span>
          <div style={{ minWidth: 0 }}>
            <div className="gv-sel-title">{v.vessel_name || `MMSI ${v.mmsi}`}</div>
            <div className="gv-sel-sub">{v.vessel_type || "type not transmitted"} · LIVE · {v.provider || "AISStream"}</div>
          </div>
        </div>
        <div className="kv-dense">
          <KV k="MMSI" v={v.mmsi} />
          {v.imo ? <KV k="IMO" v={v.imo} /> : null}
          <KV k="Position" v={`${fmtLat(v.lat)} ${fmtLon(v.lon)}`} />
          <KV k="SOG" v={v.sog_kn == null ? "not transmitted" : `${Number(v.sog_kn).toFixed(1)} kn`} />
          <KV k="COG" v={v.cog_deg == null ? "not transmitted" : `${Number(v.cog_deg).toFixed(0)}°`} />
          <KV k="Heading" v={v.heading_deg == null ? "not transmitted" : `${Number(v.heading_deg).toFixed(0)}°`} />
          {v.destination ? <KV k="Destination" v={v.destination} /> : null}
          <KV k="Zone" v={v.zone_id || "outside all zones"} />
          <KV k="Last report" v={fmt.utc(v.report_utc)} />
          <KV k="Reports" v={v.message_count ?? "--"} />
        </div>
        <div className="globe-actions">
          <Link className="btn btn-sm" to={`/vessels?mmsi=${v.mmsi}`}>Vessel dossier</Link>
          <button className="btn btn-sm" onClick={() => cam.flyTo({ longitude: v.lon, latitude: v.lat, zoom: 8 }, 900)}>
            <Crosshair size={12} /> Centre
          </button>
        </div>
      </Panel>
    );
  }

  if (selected?.kind === "incident" && selected.object) {
    const i = selected.object;
    return (
      <Panel title="Selected · Incident" icon={<ClipboardList size={12} />} testid="selected-incident"
        right={<button className="btn btn-ghost btn-xs" onClick={() => onSelect(null)}><X size={11} /></button>}>
        <div className="gv-sel-head">
          <span className="gv-sel-ico danger"><AlertTriangle size={14} /></span>
          <div style={{ minWidth: 0 }}>
            <div className="gv-sel-title">{i.title}</div>
            <div className="gv-sel-sub">{i.id}</div>
          </div>
        </div>
        <div className="row gap-1 mb-2 wrap">
          {i.severity && <Badge tone={SEVERITY_TONE[i.severity] || "neutral"}>{i.severity}</Badge>}
          <Badge tone="neutral">{i.status}</Badge>
          {i.origin && <Badge tone={i.origin === "auto" ? "warn" : "ok"}>{i.origin}</Badge>}
        </div>
        <div className="kv-dense">
          <KV k="Detected" v={fmt.utc(i.detected_utc)} />
          <KV k="Zone" v={i.zone_id || "outside all zones"} />
          {i.area_km2 != null && <KV k="Area" v={`${Number(i.area_km2).toFixed(2)} km²`} />}
          {i.detection_confidence != null && <KV k="Confidence" v={`${(i.detection_confidence * 100).toFixed(1)}%`} />}
          {i.scene_id && <KV k="Scene" v={i.scene_id} wrap />}
          <KV k="Runs" v={i.runs ?? 0} />
        </div>
        <div className="globe-actions">
          <Link className="btn btn-primary btn-sm" to={`/incidents?focus=${i.id}`}>Open incident</Link>
          {i.source_run_id && (
            <Link className="btn btn-sm" to={`/investigation?run=${i.source_run_id}`}><Radar size={12} /> Investigation</Link>
          )}
          {i.source_run_id && (
            <Link className="btn btn-sm" to={`/incident?run=${i.source_run_id}`}><Film size={12} /> Replay</Link>
          )}
        </div>
      </Panel>
    );
  }

  if ((selected?.kind === "slick" || selected?.kind === "track") && selected.object) {
    const p = selected.object.properties || selected.object;
    return (
      <Panel title={selected.kind === "slick" ? "Selected · Detected slick" : "Selected · Track"}
        icon={<Crosshair size={12} />}
        right={<button className="btn btn-ghost btn-xs" onClick={() => onSelect(null)}><X size={11} /></button>}>
        <div className="kv-dense">
          {selected.kind === "slick" ? (<>
            <KV k="Slick" v={p.slick_id || "detected"} />
            <KV k="Area" v={p.area_km2 != null ? `${Number(p.area_km2).toFixed(2)} km²` : "--"} />
            <KV k="Confidence" v={p.confidence != null ? `${(p.confidence * 100).toFixed(1)}%` : "--"} />
            <KV k="Orientation" v={p.orientation_deg != null ? `${p.orientation_deg}°` : "--"} />
            <KV k="Source" v={String(p.source || "unrecorded").toUpperCase()} />
          </>) : (<>
            <KV k="MMSI" v={p.mmsi} />
            <KV k="Name" v={p.name || "not supplied"} />
            <KV k="Rank" v={p.rank ? `#${p.rank}` : p.filtered ? "eliminated" : "considered"} />
            {p.score != null && <KV k="Score" v={`${(p.score * 100).toFixed(0)}%`} />}
            {p.filterReason && <KV k="Reason" v={p.filterReason} wrap />}
            <KV k="Source" v={String(p.source || "unrecorded").toUpperCase()} />
          </>)}
        </div>
        <div className="globe-actions">
          {runLayers?.runId && <Link className="btn btn-sm" to={url.workspace({ run: runLayers.runId })}><Radar size={12} /> Open in workspace</Link>}
          {selected.kind === "track" && <Link className="btn btn-sm" to={`/vessels?mmsi=${p.mmsi}`}>Dossier</Link>}
        </div>
      </Panel>
    );
  }

  return (
    <ZonePanel zone={zone} zoneList={zoneList} loading={zonesLoading} error={zonesError}
      canDraw={canDraw} onReshape={onReshape}
      onSelect={(id) => onSelect(id ? { kind: "zone", id } : null)} onFly={onFly}
      incidents={incidents} />
  );
}

function ZonePanel({ zone, zoneList, loading, error, canDraw, onReshape, onSelect, onFly,
                    incidents }) {
  if (!zone) {
    return (
      <Panel title="Zones" icon={<MapIcon size={12} />}>
        {/* Three distinct answers, never collapsed into one: the request has
            not come back, the request failed, or the system genuinely holds no
            zones. "No zones" printed during the first fetch is a claim about
            data nobody has looked at yet. */}
        {loading ? (
          <DataState kind="loading" compact title="Loading zones" />
        ) : error ? (
          <DataState kind="error" compact error={error} title="Zones unavailable" />
        ) : zoneList.length === 0 ? (
          <DataState kind="empty" compact title="No zones defined"
            hint="No operational zones exist yet. An administrator can draw one in zone-splitting mode." />
        ) : (
          <>
            <div className="tiny muted mb-2">Click a zone on the globe, or pick one:</div>
            {zoneList.map((z) => (
              <button key={z.id} className="gv-zone-row" data-testid={`zone-row-${z.id}`}
                onClick={() => { onSelect(z.id); if (z.bbox) onFly(z.bbox); }}>
                <span className="row gap-2" style={{ minWidth: 0 }}>
                  <span className="gv-zone-swatch" style={{ background: z.kind === "jurisdiction"
                    ? "var(--c-protected)" : z.mine ? "var(--c-zone-mine)" : "var(--c-zone)" }} />
                  {z.protected && <Lock size={10} className="dim" />}
                  <span className="ellipsis">{z.name}</span>
                </span>
                <span className="tiny mono muted nowrap">
                  {z.kind === "jurisdiction" ? "PROTECTED" : `${z.open_incident_count ?? 0} open`}
                </span>
              </button>
            ))}
          </>
        )}
      </Panel>
    );
  }

  const primary = (zone.officers || []).find((o) => o.is_primary);
  const inZone = incidents.filter((i) => i.zone_id === zone.id);

  return (
    <Panel title={zone.name} icon={zone.protected ? <Lock size={12} /> : <MapIcon size={12} />}
      testid="zone-detail"
      right={<>
        {zone.protected && <Badge tone="neutral">PROTECTED</Badge>}
        {zone.mine && <Badge tone="teal">YOURS</Badge>}
        <button className="btn btn-ghost btn-xs" onClick={() => onSelect(null)}><X size={11} /></button>
      </>}>
      <div className="kv-dense">
        <KV k="Kind" v={zone.kind} />
        <KV k="Jurisdiction" v={zone.jurisdiction || "--"} />
        <KV k="Area" v={zone.area_km2 == null ? "--" : `${Number(zone.area_km2).toLocaleString()} km²`} />
        <KV k="Revision" v={zone.revision} />
        {/* "unassigned" is a real state and is shown as one, because an
            unassigned zone routes its alerts to nobody. */}
        <KV k="Officer" v={primary ? (primary.display_name || primary.email) : "unassigned"}
          tone={primary ? "" : "danger"} />
        <KV k="Open incidents" v={zone.open_incident_count ?? 0} />
        <KV k="Status" v={zone.status} />
      </div>

      {zone.notes && (
        /* The seeded disclaimer travels on the row and is displayed, not
           hidden behind a tooltip: these boundaries assert nothing about
           maritime jurisdiction and the page has to say so. */
        <Notice testid="zone-notes">{zone.notes}</Notice>
      )}

      <div className="globe-actions">
        <button className="btn btn-sm" disabled={!canDraw || !zone.can_edit} onClick={() => onReshape(zone)}
          title={zone.can_edit ? "Edit this boundary" : (zone.cannot_edit_reason || "You may not edit this zone")}
          data-testid="reshape-zone">
          <Pencil size={12} /> Edit boundary
        </button>
        {zone.bbox && (
          <button className="btn btn-sm" onClick={() => onFly(zone.bbox)}><Crosshair size={12} /> Centre</button>
        )}
        <Link className="btn btn-sm" to={`/zones?zone=${zone.id}`}>Manage</Link>
      </div>
      {!zone.can_edit && zone.cannot_edit_reason && (
        /* The server's own reason, shown verbatim. An operator told only
           "forbidden" files a bug; one told which zones they own does not. */
        <Notice testid="cannot-edit">{zone.cannot_edit_reason}</Notice>
      )}
      {inZone.length > 0 && (
        <div className="globe-sublist">
          <div className="label mb-2">Incidents in this zone</div>
          {inZone.slice(0, 6).map((i) => (
            <div key={i.id} className="kv">
              <span className="kv-k mono">{i.id}</span>
              <span className="kv-v tiny">{(i.severity || "").toUpperCase()} · {i.status}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* ---------------------------------------------------------------- editor --- */

function EditorPanel({ editor, editTarget, newZone, setNewZone, zoneList, officers,
                       onSave, onCancel, saveState }) {
  const bad = editor.selfIntersection;
  const parents = zoneList.filter((z) => z.can_edit || z.kind === "jurisdiction");
  const areaKm2 = editor.points.length >= 3 ? sphericalAreaKm2(editor.points) : null;
  const parent = zoneList.find((z) => z.id === (editTarget?.parent_id || newZone.parent_id));
  const parentOfficer = (parent?.officers || []).find((o) => o.is_primary);

  return (
    <Panel className="gv-editor" icon={<Pencil size={12} />}
      title={editTarget ? `Editing ${editTarget.name}` : "New zone"} testid="zone-editor">
      {!editTarget && (
        <>
          <label className="globe-field">
            <span>Zone id</span>
            <input value={newZone.id} onChange={(e) => setNewZone((s) => ({ ...s, id: e.target.value }))}
              placeholder="zone-bob-07" data-testid="new-zone-id" />
          </label>
          <label className="globe-field">
            <span>Name</span>
            <input value={newZone.name} onChange={(e) => setNewZone((s) => ({ ...s, name: e.target.value }))}
              placeholder="Zone 07 — Eastern Approaches" data-testid="new-zone-name" />
          </label>
          <label className="globe-field">
            <span>Inside</span>
            <select value={newZone.parent_id}
              onChange={(e) => setNewZone((s) => ({ ...s, parent_id: e.target.value }))}
              data-testid="new-zone-parent">
              {parents.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
            </select>
          </label>
          {officers && officers.length > 0 && (
            <label className="globe-field">
              <span>Primary officer (optional)</span>
              <select value={newZone.officer}
                onChange={(e) => setNewZone((s) => ({ ...s, officer: e.target.value }))}
                data-testid="new-zone-officer">
                <option value="">— assign later —</option>
                {officers.map((u) => (
                  <option key={u.id} value={u.id}>{u.display_name || u.email}</option>
                ))}
              </select>
            </label>
          )}
        </>
      )}

      <div className="gv-editor-stats">
        <div className="gv-editor-stat">
          <div className="k">Points</div>
          <div className="v">{editor.points.length}</div>
        </div>
        <div className={`gv-editor-stat ${bad ? "bad" : editor.geometry ? "good" : ""}`}>
          <div className="k">Geometry</div>
          <div className="v">{bad ? "INVALID" : editor.geometry ? (editor.closed ? "CLOSED" : "OPEN") : "—"}</div>
        </div>
        <div className="gv-editor-stat" title="Spherical estimate while drawing; the server computes the geodesic figure on save.">
          <div className="k">Area · approx</div>
          <div className="v">{areaKm2 == null ? "—" : `${Math.round(areaKm2).toLocaleString()} km²`}</div>
        </div>
      </div>

      <div className="kv-dense mb-2">
        <KV k="Parent" v={parent?.name || editTarget?.parent_id || "—"} />
        <KV k="Routes to" v={editTarget
          ? ((editTarget.officers || []).find((o) => o.is_primary)?.display_name || "unassigned")
          : (newZone.officer
            ? (officers || []).find((u) => String(u.id) === String(newZone.officer))?.display_name || "selected officer"
            : parentOfficer ? `${parentOfficer.display_name || parentOfficer.email} (escalation)` : "unassigned")} />
      </div>

      <div className="tiny muted mb-2">
        Click the globe to add a boundary point. Click a point to select it,
        Delete to remove it, ⌘Z to undo.
      </div>

      {/* The vertex list, at full stored precision. An operator drawing a
          line that decides who receives an alert needs to see the numbers
          that will actually be saved. */}
      <div className="globe-vertices" data-testid="vertex-list">
        {editor.points.length === 0 ? (
          <div className="tiny muted" style={{ padding: 8 }}>No points yet.</div>
        ) : editor.points.map((p, i) => (
          <button key={i} className={`globe-vertex ${editor.selectedIndex === i ? "on" : ""}`}
            onClick={() => editor.setSelectedIndex(i)}>
            <span className="globe-vertex-n mono">{i + 1}</span>
            <span className="mono tiny">{fmtLat(p[1])}</span>
            <span className="mono tiny">{fmtLon(p[0])}</span>
            <Trash2 size={11} onClick={(e) => { e.stopPropagation(); editor.deletePoint(i); }} />
          </button>
        ))}
      </div>

      {bad && (
        /* Told while drawing, with the offending edges highlighted on the
           canvas, rather than discovered as a 422 on save. */
        <Notice tone="danger" testid="self-intersect" style={{ marginTop: 8 }}>
          The boundary crosses itself between points {bad[0] + 1} and {bad[1] + 1}. Move one of them before saving.
        </Notice>
      )}

      <div className="globe-actions">
        <button className="btn btn-sm" onClick={editor.undo} disabled={!editor.canUndo} data-testid="editor-undo">
          <Undo2 size={12} /> Undo
        </button>
        <button className="btn btn-sm" onClick={editor.redo} disabled={!editor.canRedo} data-testid="editor-redo">
          <Redo2 size={12} />
        </button>
        <button className="btn btn-sm" onClick={() => editor.deletePoint(editor.selectedIndex)}
          disabled={editor.selectedIndex === null} title="Remove the selected point">
          <Trash2 size={12} /> Point
        </button>
        <button className="btn btn-sm" onClick={() => editor.reset([])} disabled={editor.points.length === 0}>
          <RotateCcw size={12} /> Reset
        </button>
        <button className="btn btn-sm" onClick={editor.close} disabled={!editor.canClose || editor.closed}
          data-testid="editor-close">
          <Check size={12} /> Close ring
        </button>
      </div>

      <div className="globe-actions">
        <button className="btn btn-primary btn-sm" onClick={onSave} data-testid="editor-save"
          disabled={!editor.geometry || Boolean(bad) || saveState?.busy || (!editTarget && !newZone.id.trim())}>
          <Save size={12} />
          {saveState?.busy ? "Saving…" : editTarget ? "Save boundary" : "Create zone"}
        </button>
        <button className="btn btn-sm" onClick={onCancel} data-testid="editor-cancel">
          <X size={12} /> Cancel
        </button>
      </div>

      {saveState?.error && <Notice tone="danger" testid="editor-error" style={{ marginTop: 8 }}>{saveState.error}</Notice>}
      {saveState?.ok && <Notice tone="ok" icon={<Check size={11} />} testid="editor-ok" style={{ marginTop: 8 }}>{saveState.ok}</Notice>}

      {editTarget?.protected && (
        <Notice style={{ marginTop: 8 }} icon={<UserCheck size={11} />}>
          This is a protected {editTarget.kind} boundary. Only a super_admin may
          move it; the server enforces that regardless of this form.
        </Notice>
      )}
    </Panel>
  );
}
