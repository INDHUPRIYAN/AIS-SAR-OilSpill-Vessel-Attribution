/* Vessel dossier (P11).
 *
 * Two things this page must never do, because the system it belongs to ranks
 * vessels as pollution suspects:
 *
 *  - show a name for a vessel that does not have one. The AIS contract carries
 *    no identity, so a synthetic vessel HAS no name; the page says "not
 *    supplied" rather than leaving a blank that reads like an omission.
 *  - show only the runs where a vessel scored well. Exclusions are listed with
 *    the gate that produced them, or this becomes a prosecution file.
 */

import { useState } from "react";
import { Ship } from "lucide-react";

import { Badge, Card, Empty, Spinner, Stat } from "../components/ui";
import { api, fmt, useApi } from "../lib/api";

export default function Vessels() {
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [mmsi, setMmsi] = useState(null);

  const { data, loading } = useApi(
    () => api.listVessels({ q: q || undefined, source: source || undefined, limit: 100 }),
    [q, source],
  );
  const { data: dossier } = useApi(
    () => (mmsi ? api.getVessel(mmsi) : Promise.resolve(null)), [mmsi]);

  const items = data?.items || [];

  return (
    <div className="page">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <Ship size={19} color="var(--accent)" />
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>Vessels</div>
          <div className="tiny muted">
            What the system knows about one MMSI, across every run it appeared in.
          </div>
        </div>
      </div>

      <Card style={{ marginBottom: 18 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <div style={{ flex: "1 1 220px" }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Search</div>
            <input value={q} onChange={(e) => setQ(e.target.value)}
                   placeholder="name, IMO, call sign or MMSI" style={{ width: "100%" }} />
          </div>
          <div style={{ flex: "0 1 160px" }}>
            <div className="stat-label" style={{ marginBottom: 5 }}>Source</div>
            <select value={source} onChange={(e) => setSource(e.target.value)}
                    style={{ width: "100%" }}>
              <option value="">all</option>
              <option value="real">real</option>
              <option value="synthetic">synthetic</option>
            </select>
          </div>
        </div>
      </Card>

      {loading && <Card><Spinner /> Loading…</Card>}
      {!loading && !items.length && <Empty title="No vessels match." />}

      {!!items.length && (
        <Card title={`${data.total} vessel${data.total === 1 ? "" : "s"}`}
              style={{ marginBottom: 18 }}>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr><th>MMSI</th><th>Name</th><th>Type</th><th>Source</th>
                    <th>Runs</th><th /></tr>
              </thead>
              <tbody>
                {items.slice(0, 60).map((v) => (
                  <tr key={v.mmsi}>
                    <td className="mono">{v.mmsi}</td>
                    <td>{v.name || <span className="tiny muted">not supplied</span>}</td>
                    <td className="tiny muted">{v.vessel_type || "—"}</td>
                    <td>
                      <Badge status={v.source === "real" ? "OK" : "MOCK"}>
                        {v.source === "real" ? "REAL" : "SYNTHETIC"}
                      </Badge>
                    </td>
                    <td>{v.appearances}</td>
                    <td>
                      <button className="btn btn-sm" onClick={() => setMmsi(v.mmsi)}>
                        Open
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {dossier && (
        <Card title={`MMSI ${dossier.mmsi}`}
              right={<Badge status={dossier.source === "real" ? "OK" : "MOCK"}>
                {dossier.source === "real" ? "REAL" : "SYNTHETIC"}
              </Badge>}>
          {dossier.source !== "real" && (
            <div className="tiny muted" style={{ marginBottom: 10 }}>
              ⚠ This vessel exists only inside scenario data. It is not a record of a
              real ship.
            </div>
          )}
          {!dossier.identity_available && (
            <div className="tiny muted" style={{ marginBottom: 10 }}>
              No identity was supplied by the source. The AIS contract carries no name,
              IMO or call sign, and none is inferred here.
            </div>
          )}

          <div className="grid grid-4" style={{ marginBottom: 14 }}>
            <Stat label="Name" value={dossier.name || "—"} />
            <Stat label="IMO" value={dossier.imo || "—"} />
            <Stat label="Ranked in" value={dossier.ranked_in} />
            <Stat label="Excluded in" value={dossier.filtered_in} />
          </div>

          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr><th>Run</th><th>Scene</th><th>Outcome</th><th>Score</th><th>Why</th></tr>
              </thead>
              <tbody>
                {dossier.appearance_list.map((a, i) => (
                  <tr key={`${a.run_id}-${i}`}>
                    <td className="mono tiny">{a.run_id}</td>
                    <td className="tiny muted">{a.scene_id || "—"}</td>
                    <td>
                      {a.filtered
                        ? <Badge status="MOCK">excluded</Badge>
                        : <Badge status="OK">{`Rank #${a.rank ?? "—"}`}</Badge>}
                    </td>
                    <td className="mono">
                      {a.total_score == null ? "—" : fmt.num(a.total_score, 2)}
                    </td>
                    <td className="tiny muted">{a.filter_reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
