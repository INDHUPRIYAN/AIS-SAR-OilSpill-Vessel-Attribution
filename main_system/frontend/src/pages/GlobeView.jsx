/* The command globe: global maritime picture, and the zone boundary editor.
 *
 * Two modes on ONE canvas, which is the whole reason this is a single page
 * rather than two. Zone splitting is a spatial judgement made against the
 * traffic and the incidents you are dividing up — an editor on a blank
 * projection would make an operator draw a boundary blind, then discover on
 * another screen what it had captured.
 *
 *   NORMAL    zones, incidents, live vessels, run overlays
 *   SPLITTING the same picture, plus a vertex editor
 *
 * Switching preserves the camera, the layer toggles and the selection (D3: a
 * canvas swap, not a morph).
 *
 * EVERY number on this page comes from an API response. The coordinate
 * readout is the pointer's own position; the zone, its officer and its area
 * come from /api/zones; vessels from /api/ais/live. Where a value is absent
 * the page says which kind of absent it is — "not transmitted" for a vessel
 * that sent no name, "outside all zones" for water no zone covers, "no
 * receiver coverage" for a live layer the provider cannot see.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, Check, Crosshair, Eye, Globe2, Layers, Map as MapIcon,
  Pencil, Radio, RotateCcw, Save, Search, Ship, Trash2, Undo2, X,
} from "lucide-react";

import Globe, {
  CoordinateReadout, GLOBE_INITIAL_VIEW, fmtLat, fmtLon, parseCoordinate,
  useBoundaryEditor,
} from "../components/Globe";
import { Badge, Empty, Spinner } from "../components/ui";
import { api, useApi } from "../lib/api";
import { useSession } from "../lib/session";
import "../globe.css";

const MODES = { NORMAL: "normal", SPLITTING: "splitting" };

const LAYER_ROWS = [
  { key: "zones", label: "Operational zones", swatch: "#38bdf8" },
  { key: "zoneLabels", label: "Zone labels", swatch: "transparent" },
  { key: "incidents", label: "Incidents", swatch: "#ef4444" },
  { key: "vessels", label: "Live AIS", swatch: "#8294b2" },
  { key: "graticule", label: "Lat/lon graticule", swatch: "#46596f" },
];

export default function GlobeViewPage() {
  const { user } = useSession();
  const [mode, setMode] = useState(MODES.NORMAL);
  const [basemap, setBasemap] = useState("dark");
  const [viewState, setViewState] = useState(GLOBE_INITIAL_VIEW);
  const [layersOn, setLayersOn] = useState({
    zones: true, zoneLabels: true, incidents: true, vessels: true,
    graticule: true,
  });
  const [cursor, setCursor] = useState(null);
  const [cursorZone, setCursorZone] = useState(undefined);
  const [selectedZoneId, setSelectedZoneId] = useState(null);
  const [search, setSearch] = useState("");
  const [searchError, setSearchError] = useState(null);
  const [saveState, setSaveState] = useState(null);

  /* --- data -------------------------------------------------------------- */

  const zonesQ = useApi(() => api.zonesGeojson({ status: "active" }), []);
  const zoneListQ = useApi(() => api.listZones({ counts: true }), []);
  const incidentsQ = useApi(() => api.listIncidents({ limit: 200 }), []);
  /* Polled, because it is a LIVE layer. 15 s is well inside the AIS report
   * interval and far outside anything that would hammer the API. */
  const liveQ = useApi(() => api.liveVessels({ max_age_minutes: 60,
                                               limit: 2000 }),
                       [], { interval: 15000 });
  const aisStatusQ = useApi(() => api.aisStatus(), [], { interval: 30000 });

  const zones = zonesQ.data;
  const zoneList = zoneListQ.data?.zones || [];
  const selectedZone = zoneList.find((z) => z.id === selectedZoneId) || null;

  const incidents = useMemo(() => (incidentsQ.data?.items
    || incidentsQ.data?.incidents || [])
    .map((i) => {
      const c = i.geometry?.coordinates;
      // Point geometry only. An incident with a polygon or no geometry is not
      // given an invented marker position.
      if (i.geometry?.type !== "Point" || !Array.isArray(c)) return null;
      return { ...i, lon: c[0], lat: c[1] };
    })
    .filter(Boolean), [incidentsQ.data]);

  const vessels = liveQ.data?.vessels || [];

  /* --- the boundary editor ----------------------------------------------- */

  const editor = useBoundaryEditor();
  const [editTarget, setEditTarget] = useState(null);  // zone being reshaped
  const [newZone, setNewZone] = useState({ id: "", name: "", parent_id: "" });

  const editorActive = mode === MODES.SPLITTING;

  const onMapClick = useCallback((lngLat, info) => {
    if (!editorActive) {
      // Normal mode: clicking water asks the server which zone owns it, which
      // is the same call routing makes — so the answer shown is the answer an
      // alert would get.
      api.lookupZone(lngLat.lon, lngLat.lat)
        .then((r) => {
          setCursorZone(r.zone);
          if (r.zone) setSelectedZoneId(r.zone.id);
        })
        .catch(() => setCursorZone(null));
      return;
    }
    // Splitting mode. A click on an existing vertex selects it instead of
    // adding another on top of it.
    if (info?.layer?.id === "globe-editor-vertices" && info.object) {
      editor.setSelectedIndex(info.object.index);
      return;
    }
    editor.addPoint(lngLat.lon, lngLat.lat);
  }, [editorActive, editor]);

  /* Keyboard: the editor needs Delete and undo, and a text input must not
   * have its keys stolen. */
  useEffect(() => {
    if (!editorActive) return undefined;
    const handler = (e) => {
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if ((e.key === "Delete" || e.key === "Backspace")
          && editor.selectedIndex !== null) {
        e.preventDefault();
        editor.deletePoint(editor.selectedIndex);
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) editor.redo(); else editor.undo();
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
    setNewZone({ id: "", name: "",
                 parent_id: selectedZone?.id || "zone-bob" });
    setMode(MODES.SPLITTING);
    setSaveState(null);
  };

  const startReshape = (zone) => {
    if (!zone?.geometry && !zone?.id) return;
    // The list response omits polygons, so fetch the one being edited.
    api.getZone(zone.id).then((full) => {
      const ring = (full.geometry?.coordinates?.[0] || []).slice(0, -1);
      editor.reset(ring);
      setEditTarget(full);
      setMode(MODES.SPLITTING);
      setSaveState(null);
    }).catch((e) => setSaveState({ error: e.message }));
  };

  const cancelEdit = () => {
    editor.reset([]);
    setEditTarget(null);
    setMode(MODES.NORMAL);
    setSaveState(null);
  };

  const save = async () => {
    if (!editor.geometry) return;
    setSaveState({ busy: true });
    try {
      if (editTarget) {
        // `revision` is mandatory for a geometry change. The server rejects a
        // stale one with a 409 rather than silently overwriting whoever moved
        // the boundary first.
        const out = await api.updateZone(editTarget.id, {
          geometry: editor.geometry,
          revision: editTarget.revision,
          reason: "boundary edited on the globe",
        });
        setSaveState({ ok: `${out.name} saved at revision ${out.revision}` });
      } else {
        const out = await api.createZone({
          id: newZone.id.trim(),
          name: newZone.name.trim() || newZone.id.trim(),
          parent_id: newZone.parent_id || null,
          geometry: editor.geometry,
        });
        setSaveState({ ok: `${out.name} created (${out.area_km2} km²)` });
      }
      await Promise.all([zonesQ.reload(), zoneListQ.reload()]);
      editor.reset([]);
      setEditTarget(null);
      setMode(MODES.NORMAL);
    } catch (e) {
      // The server's message is the message. It names the offending point, the
      // parent it fell outside, or the revision that moved — all of which are
      // actionable, and none of which this page could work out itself.
      setSaveState({ error: e.message });
    }
  };

  const runSearch = (e) => {
    e?.preventDefault();
    const parsed = parseCoordinate(search);
    if (!parsed) {
      setSearchError("Could not read that as a coordinate. Try "
                     + "\"13.5, 89.5\" or \"13 30 N 89 30 E\".");
      return;
    }
    setSearchError(null);
    setViewState((v) => ({ ...v, longitude: parsed.lon, latitude: parsed.lat,
                           zoom: Math.max(v.zoom, 6),
                           transitionDuration: 600 }));
    api.lookupZone(parsed.lon, parsed.lat)
      .then((r) => setCursorZone(r.zone)).catch(() => setCursorZone(null));
  };

  const canDraw = ["super_admin", "admin", "zone_officer"]
    .includes(user?.role);

  /* --- live-AIS honesty -------------------------------------------------- */

  const ais = aisStatusQ.data?.stream;
  /* The distinction that matters: an empty vessel layer because nothing is
   * transmitting, versus because the provider cannot hear this ocean. The
   * second is a coverage fact and is shown as one. */
  const aisNote = ais?.note;
  const aisBadge = !ais ? null
    : ais.state === "not_configured" ? { tone: "neutral", text: "NOT CONFIGURED" }
      : ais.functionally_working ? { tone: "ok", text: "LIVE" }
        : ais.connected ? { tone: "warn", text: "CONNECTED · NO DATA" }
          : { tone: "danger", text: ais.state.toUpperCase() };

  return (
    <div className="globe-page" data-testid="globe-page">
      {/* ---------------------------------------------------------- canvas */}
      <div className="globe-canvas">
        <Globe
          viewState={viewState}
          onViewStateChange={({ viewState: v }) => setViewState(v)}
          basemap={basemap}
          layersOn={layersOn}
          zones={layersOn.zones ? zones : null}
          incidents={incidents}
          vessels={vessels}
          editor={editorActive ? editor : null}
          onMapClick={onMapClick}
          onCursorMove={setCursor}
          onSelect={(layerId, obj) => {
            if (layerId === "globe-zones") setSelectedZoneId(obj.properties?.id);
          }}
          graticuleStep={viewState.zoom > 5 ? 5 : 15}
        />

        {/* Mode switch. Labelled with what each mode DOES, not with an icon
            alone — "Zone splitting" is a consequential action. */}
        <div className="globe-modebar">
          <button className={`btn btn-sm ${mode === MODES.NORMAL ? "btn-on" : ""}`}
            onClick={() => (editorActive ? cancelEdit() : setMode(MODES.NORMAL))}
            data-testid="mode-normal">
            <Eye size={12} /> Normal
          </button>
          <button
            className={`btn btn-sm ${editorActive ? "btn-on" : ""}`}
            onClick={startNewZone}
            disabled={!canDraw}
            title={canDraw ? "Draw a new operational zone"
                           : `Your role (${user?.role}) may not edit zones`}
            data-testid="mode-splitting">
            <Pencil size={12} /> Zone splitting
          </button>
          <span className="globe-modebar-sep" />
          {["dark", "imagery", "none"].map((b) => (
            <button key={b}
              className={`btn btn-sm ${basemap === b ? "btn-on" : ""}`}
              onClick={() => setBasemap(b)}>
              {b === "dark" ? <Globe2 size={12} />
                : b === "imagery" ? <MapIcon size={12} /> : <X size={12} />}
              {b}
            </button>
          ))}
        </div>

        <CoordinateReadout cursor={cursor} zone={cursorZone} />

        {(zonesQ.loading || liveQ.loading) && (
          <div className="globe-loading"><Spinner label="loading layers" /></div>
        )}
      </div>

      {/* ------------------------------------------------------------- rail */}
      <aside className="globe-rail">
        {/* Coordinate search */}
        <form className="globe-search" onSubmit={runSearch}>
          <Search size={13} className="muted" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="13.5, 89.5  ·  13 30 N 89 30 E"
            aria-label="Search a coordinate"
            data-testid="coord-search" />
          <button className="btn btn-sm" type="submit">Go</button>
        </form>
        {searchError && (
          <div className="globe-inline-error tiny" data-testid="coord-error">
            {searchError}
          </div>
        )}

        {editorActive ? (
          <EditorPanel editor={editor} editTarget={editTarget}
            newZone={newZone} setNewZone={setNewZone}
            zoneList={zoneList} onSave={save} onCancel={cancelEdit}
            saveState={saveState} />
        ) : (
          <>
            <LayerRail layersOn={layersOn} setLayersOn={setLayersOn} />

            <section className="globe-card">
              <header className="globe-card-head">
                <Radio size={12} /> LIVE AIS
                {aisBadge && (
                  <span className={`badge badge-${aisBadge.tone}`}>
                    {aisBadge.text}
                  </span>
                )}
              </header>
              <div className="globe-card-body">
                <Row k="Vessels shown" v={vessels.length} />
                <Row k="In view (server)"
                  v={liveQ.data?.total_in_view ?? "--"} />
                {liveQ.data?.truncated && (
                  /* Never silent. A client that got 2000 of 4200 must say so. */
                  <div className="globe-warn tiny">
                    <AlertTriangle size={11} /> truncated to the first 2000 —
                    zoom in for the rest
                  </div>
                )}
                <Row k="Provider" v={ais?.provider || "--"} />
                <Row k="Archived rows"
                  v={aisStatusQ.data?.archive?.rows ?? "--"} />
                {aisNote && (
                  /* The coverage sentence. This is the difference between
                     "no traffic" and "nothing is listening to this ocean". */
                  <div className="globe-note tiny" data-testid="ais-note">
                    {aisNote}
                  </div>
                )}
              </div>
            </section>

            <ZonePanel zone={selectedZone} zoneList={zoneList}
              canDraw={canDraw} onReshape={startReshape}
              onSelect={setSelectedZoneId}
              incidents={incidents} />
          </>
        )}
      </aside>
    </div>
  );
}

/* ------------------------------------------------------------------ rail --- */

function Row({ k, v }) {
  return (
    <div className="globe-row">
      <span className="globe-row-k">{k}</span>
      <span className="globe-row-v mono">{v}</span>
    </div>
  );
}

function LayerRail({ layersOn, setLayersOn }) {
  return (
    <section className="globe-card">
      <header className="globe-card-head"><Layers size={12} /> LAYERS</header>
      <div className="globe-card-body">
        {LAYER_ROWS.map((r) => (
          <label key={r.key} className="globe-layer"
            data-testid={`globe-layer-${r.key}`}>
            <input type="checkbox" checked={layersOn[r.key] !== false}
              onChange={(e) => setLayersOn((s) => ({ ...s,
                                                     [r.key]: e.target.checked }))} />
            {r.swatch !== "transparent" && (
              <span className="globe-swatch" style={{ background: r.swatch }} />
            )}
            <span>{r.label}</span>
          </label>
        ))}
      </div>
    </section>
  );
}

function ZonePanel({ zone, zoneList, canDraw, onReshape, onSelect, incidents }) {
  if (!zone) {
    return (
      <section className="globe-card">
        <header className="globe-card-head">ZONES</header>
        <div className="globe-card-body">
          {zoneList.length === 0 ? (
            <Empty icon={<Globe2 size={20} />} title="No zones"
              hint="No operational zones are defined. An administrator can draw one." />
          ) : (
            <>
              <div className="tiny muted" style={{ marginBottom: 8 }}>
                Click a zone on the globe, or pick one:
              </div>
              {zoneList.map((z) => (
                <button key={z.id} className="globe-zone-row"
                  onClick={() => onSelect(z.id)}
                  data-testid={`zone-row-${z.id}`}>
                  <span>{z.name}</span>
                  <span className="tiny mono muted">
                    {z.kind === "jurisdiction" ? "PROTECTED"
                      : `${z.open_incident_count ?? 0} open`}
                  </span>
                </button>
              ))}
            </>
          )}
        </div>
      </section>
    );
  }

  const primary = (zone.officers || []).find((o) => o.is_primary);
  const inZone = incidents.filter((i) => i.zone_id === zone.id);

  return (
    <section className="globe-card" data-testid="zone-detail">
      <header className="globe-card-head">
        {zone.name}
        {zone.protected && <span className="badge badge-neutral">PROTECTED</span>}
        {zone.mine && <span className="badge badge-ok">YOURS</span>}
      </header>
      <div className="globe-card-body">
        <Row k="Kind" v={zone.kind} />
        <Row k="Jurisdiction" v={zone.jurisdiction || "--"} />
        <Row k="Area"
          v={zone.area_km2 == null ? "--"
            : `${Number(zone.area_km2).toLocaleString()} km²`} />
        <Row k="Revision" v={zone.revision} />
        {/* "unassigned" is a real state and is shown as one, because an
            unassigned zone routes its alerts to nobody. */}
        <Row k="Officer"
          v={primary ? (primary.display_name || primary.email) : "unassigned"} />
        <Row k="Open incidents" v={zone.open_incident_count ?? 0} />

        {zone.notes && (
          /* The seeded disclaimer travels on the row and is displayed, not
             hidden behind a tooltip: these boundaries assert nothing about
             maritime jurisdiction and the page has to say so. */
          <div className="globe-note tiny" data-testid="zone-notes">
            {zone.notes}
          </div>
        )}

        <div className="globe-actions">
          <button className="btn btn-sm" disabled={!canDraw || !zone.can_edit}
            onClick={() => onReshape(zone)}
            title={zone.can_edit ? "Edit this boundary"
                                 : (zone.cannot_edit_reason
                                    || "You may not edit this zone")}
            data-testid="reshape-zone">
            <Pencil size={12} /> Edit boundary
          </button>
          <button className="btn btn-sm" onClick={() => onSelect(null)}>
            <X size={12} /> Clear
          </button>
        </div>
        {!zone.can_edit && zone.cannot_edit_reason && (
          /* The server's own reason, shown verbatim. An operator told only
             "forbidden" files a bug; one told which zones they own does not. */
          <div className="globe-note tiny" data-testid="cannot-edit">
            {zone.cannot_edit_reason}
          </div>
        )}
        {inZone.length > 0 && (
          <div className="globe-sublist">
            <div className="tiny muted">INCIDENTS IN THIS ZONE</div>
            {inZone.slice(0, 6).map((i) => (
              <div key={i.id} className="globe-row">
                <span className="globe-row-k mono">{i.id}</span>
                <span className="globe-row-v tiny">
                  {(i.severity || "").toUpperCase()} · {i.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- editor --- */

function EditorPanel({ editor, editTarget, newZone, setNewZone, zoneList,
                       onSave, onCancel, saveState }) {
  const bad = editor.selfIntersection;
  const parents = zoneList.filter((z) => z.can_edit || z.kind === "jurisdiction");

  return (
    <section className="globe-card globe-card-editor" data-testid="zone-editor">
      <header className="globe-card-head">
        <Pencil size={12} />
        {editTarget ? `EDITING ${editTarget.name}` : "NEW ZONE"}
      </header>
      <div className="globe-card-body">
        {!editTarget && (
          <>
            <label className="globe-field">
              <span>Zone id</span>
              <input value={newZone.id}
                onChange={(e) => setNewZone((s) => ({ ...s, id: e.target.value }))}
                placeholder="zone-bob-07"
                data-testid="new-zone-id" />
            </label>
            <label className="globe-field">
              <span>Name</span>
              <input value={newZone.name}
                onChange={(e) => setNewZone((s) => ({ ...s, name: e.target.value }))}
                placeholder="Zone 07 — Eastern Approaches"
                data-testid="new-zone-name" />
            </label>
            <label className="globe-field">
              <span>Inside</span>
              <select value={newZone.parent_id}
                onChange={(e) => setNewZone((s) => ({ ...s,
                                                      parent_id: e.target.value }))}
                data-testid="new-zone-parent">
                {parents.map((z) => (
                  <option key={z.id} value={z.id}>{z.name}</option>
                ))}
              </select>
            </label>
          </>
        )}

        <div className="tiny muted" style={{ margin: "10px 0 6px" }}>
          Click the globe to add a boundary point. Click a point to select it,
          Delete to remove it, ⌘Z to undo.
        </div>

        {/* The vertex list, at full stored precision. An operator drawing a
            line that decides who receives an alert needs to see the numbers
            that will actually be saved. */}
        <div className="globe-vertices" data-testid="vertex-list">
          {editor.points.length === 0 ? (
            <div className="tiny muted">No points yet.</div>
          ) : editor.points.map((p, i) => (
            <button key={i}
              className={`globe-vertex ${editor.selectedIndex === i ? "on" : ""}`}
              onClick={() => editor.setSelectedIndex(i)}>
              <span className="globe-vertex-n mono">{i + 1}</span>
              <span className="mono tiny">{fmtLat(p[1])}</span>
              <span className="mono tiny">{fmtLon(p[0])}</span>
              <Trash2 size={11}
                onClick={(e) => { e.stopPropagation(); editor.deletePoint(i); }} />
            </button>
          ))}
        </div>

        {bad && (
          /* Told while drawing, with the offending edges highlighted on the
             canvas, rather than discovered as a 422 on save. */
          <div className="globe-inline-error tiny" data-testid="self-intersect">
            <AlertTriangle size={11} /> The boundary crosses itself between
            points {bad[0] + 1} and {bad[1] + 1}. Move one of them before saving.
          </div>
        )}

        <div className="globe-actions">
          <button className="btn btn-sm" onClick={editor.undo}
            disabled={!editor.canUndo} data-testid="editor-undo">
            <Undo2 size={12} /> Undo
          </button>
          <button className="btn btn-sm" onClick={() => editor.reset([])}
            disabled={editor.points.length === 0}>
            <RotateCcw size={12} /> Clear
          </button>
          <button className="btn btn-sm" onClick={editor.close}
            disabled={!editor.canClose || editor.closed}
            data-testid="editor-close">
            <Check size={12} /> Close ring
          </button>
        </div>

        <div className="globe-actions">
          <button className="btn btn-primary btn-sm" onClick={onSave}
            disabled={!editor.geometry || Boolean(bad) || saveState?.busy
                      || (!editTarget && !newZone.id.trim())}
            data-testid="editor-save">
            <Save size={12} />
            {saveState?.busy ? "Saving…"
              : editTarget ? "Save boundary" : "Create zone"}
          </button>
          <button className="btn btn-sm" onClick={onCancel}
            data-testid="editor-cancel">
            <X size={12} /> Cancel
          </button>
        </div>

        {saveState?.error && (
          <div className="globe-inline-error tiny" data-testid="editor-error">
            <AlertTriangle size={11} /> {saveState.error}
          </div>
        )}
        {saveState?.ok && (
          <div className="globe-ok tiny" data-testid="editor-ok">
            <Check size={11} /> {saveState.ok}
          </div>
        )}

        {editTarget?.protected && (
          <div className="globe-note tiny">
            This is a protected {editTarget.kind} boundary. Only a super_admin
            may move it; the server enforces that regardless of this form.
          </div>
        )}
      </div>
    </section>
  );
}
