/* Vessel intelligence -- what the system knows about one MMSI.
 *
 * Two things this page must never do, because the system it belongs to ranks
 * vessels as pollution suspects:
 *
 *   - show a name for a vessel that does not have one. The AIS contract
 *     carries no identity for a synthetic vessel, so the page says "not
 *     supplied" rather than leaving a blank that reads like an omission, and
 *     never infers one.
 *   - show only the runs where a vessel scored well. Exclusions are listed
 *     with the gate that produced them, or this becomes a prosecution file.
 *
 * The language is fixed throughout: CANDIDATE, HIGHEST-RANKED CANDIDATE,
 * POTENTIAL SOURCE. Never "guilty", never "responsible". Attribution here is
 * investigative support, and the page says so wherever a score is shown.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity, AlertTriangle, Anchor, Crosshair, Film, Gauge, Navigation, Radar, Radio,
  RefreshCw, Scale, Search, Ship,
} from "lucide-react";

import {
  Badge, DataState, FactorBar, KV, Notice, PageHeader, Panel, ProvenanceBadge, Segmented, Tile,
} from "../components/ui";
import { fmtLat, fmtLon } from "../components/Globe";
import { api, fmt, useApi } from "../lib/api";
import VesselIdentity from "../components/vessels/VesselIdentity";
import { url, useVesselParams } from "../lib/urls";

const num = (v, d = 1) => (v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(d));

export default function Vessels() {
  const [params, setParams] = useVesselParams();
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");

  const mmsi = params.get("mmsi");

  const listQ = useApi(
    () => api.listVessels({ q: q || undefined, source: source || undefined, limit: 120 }),
    [q, source]);
  /* The live picture, so a vessel in the index can be shown as it is RIGHT
   * NOW when the stream has heard from it. */
  const liveQ = useApi(() => (mmsi ? api.liveVessel(mmsi).catch(() => null) : Promise.resolve(null)),
                       [mmsi], { interval: 20000 });
  const dossierQ = useApi(() => (mmsi ? api.getVessel(mmsi) : Promise.resolve(null)), [mmsi]);
  const aisQ = useApi(() => api.aisStatus(), [], { interval: 30000 });

  const items = listQ.data?.items || [];
  const dossier = dossierQ.data;
  const live = liveQ.data;
  const stream = aisQ.data?.stream;

  const select = (m) => setParams(m ? { mmsi: m } : {}, { replace: true });

  return (
    <div className="page" data-testid="vessels-page">
      <PageHeader icon={<Ship size={17} />} kicker="Intelligence" title="Vessel Intelligence"
        sub="What the system knows about one MMSI, across every run it appeared in — including the runs that excluded it."
        actions={<>
          <button className="btn btn-sm" onClick={listQ.reload}><RefreshCw size={12} /> Refresh</button>
          <Link className="btn btn-sm" to={url.map()}><Crosshair size={12} /> On globe</Link>
        </>} />

      <div className="grid grid-4 mb-3">
        <Tile label="Vessels indexed" value={listQ.data ? fmt.int(listQ.data.total) : "—"} tone="accent"
          sub="seen by at least one run" />
        <Tile label="Live now" value={aisQ.data?.live_vessels ?? "—"}
          tone={stream?.functionally_working ? "ok" : "warn"}
          sub={stream ? (stream.functionally_working ? "stream working" : String(stream.state)) : "reading"} />
        <Tile label="Archived AIS rows" value={aisQ.data?.archive?.rows != null
          ? fmt.int(aisQ.data.archive.rows) : "—"} sub="day-partitioned store" />
        <Tile label="Selected" value={mmsi || "none"} sub={dossier?.name || (mmsi ? "no name supplied" : "")} />
      </div>

      {/* The coverage fact, wherever live vessels are discussed. */}
      {stream?.note && (
        <Notice icon={<Radio size={12} />} style={{ marginBottom: 12 }} testid="vessels-ais-note">
          {stream.note}
        </Notice>
      )}

      <div className="split-wide">
        <Panel title={`Vessel index${listQ.data ? ` · ${listQ.data.total}` : ""}`} icon={<Ship size={12} />} flush
          right={<>
            <span className="search" style={{ height: 24, minWidth: 200 }}>
              <Search size={11} />
              <input className="sm" value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="name, IMO, call sign or MMSI" aria-label="Search vessels"
                data-testid="vessel-search" />
            </span>
            <Segmented value={source} onChange={setSource} testidPrefix="vessel-source" items={[
              { id: "", label: "All" }, { id: "real", label: "Real" },
              { id: "synthetic", label: "Synthetic" },
            ]} />
          </>}>
          {listQ.loading && !listQ.data ? <DataState kind="loading" title="Reading the vessel index" />
            : listQ.error ? <DataState kind="error" error={listQ.error} />
              : items.length === 0 ? (
                <DataState kind="empty" title="No vessels match"
                  hint={q || source
                    ? "No vessel in the index matches these filters."
                    : "The index is built from vessels that appeared in a run. Complete an investigation to populate it."} />
              ) : (
                <div className="table-wrap" style={{ maxHeight: "calc(100vh - 360px)" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>MMSI</th><th>Name</th><th>Type</th><th>Source</th>
                        <th className="num">Runs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((v) => (
                        <tr key={v.mmsi} data-clickable="true"
                          className={String(mmsi) === String(v.mmsi) ? "row-on" : ""}
                          onClick={() => select(v.mmsi)} data-testid={`vessel-${v.mmsi}`}>
                          <td className="mono tiny" style={{ color: "var(--accent)" }}>{v.mmsi}</td>
                          <td className="tiny">
                            {v.name || <span className="dim">not supplied</span>}
                          </td>
                          <td className="tiny dim">{v.vessel_type || "—"}</td>
                          <td><ProvenanceBadge source={v.source} /></td>
                          <td className="num mono tiny">{v.appearances}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
        </Panel>

        {mmsi ? (
          <VesselDossier mmsi={mmsi} dossier={dossier} live={live} loading={dossierQ.loading}
            error={dossierQ.error} />
        ) : (
          <Panel title="Dossier" icon={<Anchor size={12} />}>
            <DataState kind="empty" compact title="No vessel selected"
              hint="Pick an MMSI from the index, or click a vessel on the globe." />
          </Panel>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- dossier --- */

function VesselDossier({ mmsi, dossier, live, loading, error }) {
  if (loading && !dossier) {
    return <Panel title="Dossier" icon={<Anchor size={12} />}>
      <DataState kind="loading" compact title="Reading the dossier" /></Panel>;
  }
  if (error) {
    return <Panel title="Dossier" icon={<Anchor size={12} />}>
      <DataState kind="error" compact error={error} /></Panel>;
  }
  if (!dossier) return null;

  const appearances = dossier.appearance_list || [];
  const ranked = appearances.filter((a) => !a.filtered);
  const excluded = appearances.filter((a) => a.filtered);
  const best = ranked.reduce((b, a) => (a.total_score > (b?.total_score ?? -1) ? a : b), null);

  return (
    <div className="stack" style={{ gap: 12 }}>
      <Panel title="Vessel dossier" icon={<Anchor size={12} />}
        right={<ProvenanceBadge source={dossier.source} />}>
        <VesselIdentity dossier={dossier} mmsi={mmsi} />

        {dossier.source !== "real" && (
          <Notice tone="mock" style={{ marginBottom: 8 }}>
            This vessel exists only inside scenario data. It is not a record of a real ship.
          </Notice>
        )}

        <div className="kv-dense">
          <KV k="Appearances" v={dossier.appearances ?? appearances.length} />
          <KV k="Ranked in" v={dossier.ranked_in ?? ranked.length} />
          <KV k="Excluded in" v={dossier.filtered_in ?? excluded.length} />
          {/* When the archive first and last heard from this MMSI: the coverage
              behind every other number on this page. */}
          <KV k="First heard" v={dossier.first_seen_utc ? fmt.utc(dossier.first_seen_utc) : "not recorded"} />
          <KV k="Last heard" v={dossier.last_seen_utc ? fmt.utc(dossier.last_seen_utc) : "not recorded"} />
        </div>
      </Panel>

      {/* The live picture, only when the stream has actually heard from it. */}
      <Panel title="Live position" icon={<Radio size={12} />}
        right={live ? <Badge tone="ok">LIVE</Badge> : <Badge tone="ghost">NOT HEARD</Badge>}>
        {!live ? (
          <DataState kind="no-coverage" compact title="No live report"
            hint="The live stream holds no current position for this MMSI. It may be outside receiver coverage, or it may have stopped transmitting long enough to be pruned — its observations remain in the archive either way." />
        ) : (
          <div className="kv-dense">
            <KV k="Latitude" v={fmtLat(live.lat)} />
            <KV k="Longitude" v={fmtLon(live.lon)} />
            <KV k="Speed" v={live.sog_kn == null ? "not transmitted" : `${num(live.sog_kn)} kn`} />
            <KV k="Course" v={live.cog_deg == null ? "not transmitted" : `${num(live.cog_deg, 0)}°`} />
            {/* 46% of real vessels transmit no heading. Stored NULL, never 0 --
                a 0 reads as due north. */}
            <KV k="Heading" v={live.heading_deg == null ? "not transmitted" : `${num(live.heading_deg, 0)}°`} />
            <KV k="Destination" v={live.destination || "not transmitted"} />
            <KV k="Zone" v={live.zone_id || "outside all zones"} />
            <KV k="Last report" v={fmt.utc(live.report_utc)} />
            <KV k="Reports received" v={live.message_count ?? "—"} />
            <KV k="Provider" v={live.provider || "AISStream"} />
          </div>
        )}
      </Panel>

      {best && (
        <Panel title="Highest attribution score" icon={<Scale size={12} />}
          right={<Badge tone={best.rank === 1 ? "danger" : "warn"}>
            {best.rank === 1 ? "HIGHEST-RANKED CANDIDATE" : `CANDIDATE · RANK #${best.rank ?? "—"}`}
          </Badge>}>
          <div className="kv-dense">
            <KV k="Score" v={best.total_score != null ? fmt.num(best.total_score, 3) : "—"} tone="danger" />
            <KV k="Run" v={best.run_id} wrap />
            <KV k="Scene" v={best.scene_id || "—"} wrap />
            {best.ais_gap_minutes != null && (
              <KV k="AIS gap" v={`${num(best.ais_gap_minutes, 0)} min`} />
            )}
          </div>
          <Notice style={{ marginTop: 8 }}>
            Potential source attribution — investigative support, not proof of guilt. No legal
            determination is made or implied by this system.
          </Notice>
          <div className="globe-actions">
            <Link className="btn btn-primary btn-sm" to={url.workspace({ run: best.run_id })}>
              <Radar size={12} /> Open investigation
            </Link>
            <Link className="btn btn-sm" to={url.replay(best.run_id)}><Film size={12} /> Replay</Link>
          </div>
        </Panel>
      )}

      <Panel title={`Appearances · ${appearances.length}`} icon={<Activity size={12} />} flush>
        {appearances.length === 0 ? (
          <DataState kind="empty" compact title="No appearances recorded" />
        ) : (
          <div className="table-wrap" style={{ maxHeight: 320 }}>
            <table>
              <thead>
                <tr><th>Run</th><th>Outcome</th><th className="num">Score</th><th>Why</th></tr>
              </thead>
              <tbody>
                {appearances.map((a, i) => (
                  <tr key={`${a.run_id}-${i}`} className={a.filtered ? "row-dim" : ""}>
                    <td className="mono tiny">
                      <Link to={url.workspace({ run: a.run_id })}>{a.run_id}</Link>
                      {a.scene_id && <div className="sub ellipsis" style={{ maxWidth: 150 }}>{a.scene_id}</div>}
                    </td>
                    <td>
                      {/* Exclusions are shown with the gate that produced them.
                          A file that listed only the good scores would be a
                          prosecution file, not a dossier. */}
                      {a.filtered
                        ? <Badge tone="neutral">excluded</Badge>
                        : <Badge tone={a.rank === 1 ? "danger" : "warn"}>
                          {a.rank ? `rank #${a.rank}` : "considered"}
                        </Badge>}
                    </td>
                    <td className="num mono tiny">
                      {a.total_score == null ? "—" : fmt.num(a.total_score, 2)}
                    </td>
                    <td className="tiny dim" style={{ maxWidth: 220, whiteSpace: "normal" }}>
                      {a.filter_reason || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
