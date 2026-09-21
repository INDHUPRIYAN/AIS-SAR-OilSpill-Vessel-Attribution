/* What is known about a vessel, and what is not.
 *
 * One component for the two places a vessel is looked at: the Attribution
 * stage of the workspace, and the Vessels register. They used to disagree --
 * the workspace showed a "DWT" row that was always a dash and a slot for a
 * photograph no provider supplies, while the register showed the dimensions,
 * the first and last time the archive heard from it and every other run it
 * appeared in. An analyst reading a candidate saw less than one browsing.
 *
 * The rule here: a field the AIS archive did not supply says so, in words,
 * and no row exists for a field nothing in the system can ever fill.
 */

import { Link } from "react-router-dom";
import { Ship } from "lucide-react";

import { url } from "../../lib/urls";

const NOT_SUPPLIED = <span className="dim">not supplied</span>;

function Field({ k, v, mono = false, testid }) {
  return (
    <div className="vi-f">
      <span className="vi-k">{k}</span>
      <span className={`vi-v ${mono ? "mono" : ""}`} data-testid={testid}>{v ?? NOT_SUPPLIED}</span>
    </div>
  );
}

const metres = (v) => (v == null ? null : `${Number(v).toFixed(1)} m`);

/**
 * @param {object}  props.dossier    /api/vessels/{mmsi}, when it has been read
 * @param {string}  props.mmsi       always known: it is how a vessel is addressed
 * @param {string}  props.name       the name the run recorded, if the dossier is absent
 * @param {string}  props.type
 * @param {boolean} props.linkOut    show a link to the vessel's own page
 */
export default function VesselIdentity({ dossier, mmsi, name, type, linkOut = false }) {
  const d = dossier || {};
  const id = d.mmsi || mmsi;
  const size = [metres(d.length_m), metres(d.width_m)].filter(Boolean).join(" \u00d7 ");

  return (
    <div className="vi" data-testid="vessel-identity">
      <div className="vi-head">
        <span className="vi-ico"><Ship size={15} /></span>
        <div style={{ minWidth: 0 }}>
          <div className="vi-name">{d.name || name || NOT_SUPPLIED}</div>
          <div className="vi-sub mono">MMSI {id}</div>
        </div>
        {linkOut && id && (
          <Link className="btn btn-sm" to={url.vessel(id)} data-testid="vi-open">Vessel page</Link>
        )}
      </div>

      <div className="vi-grid">
        <Field k="IMO" v={d.imo} mono testid="vi-imo" />
        <Field k="Call sign" v={d.call_sign} mono />
        <Field k="Type" v={d.vessel_type || type} testid="vi-type" />
        <Field k="Flag" v={d.flag} />
        {size && <Field k="Size" v={size} mono />}
        {d.draught_m != null && <Field k="Draught" v={metres(d.draught_m)} mono />}
      </div>

      {dossier && !d.identity_available && (
        <div className="vi-note" data-testid="vi-no-identity">
          The AIS archive carries no name, IMO or call sign for this MMSI, and none is inferred here.
        </div>
      )}
    </div>
  );
}

/** Every other run this MMSI has appeared in: the question "have we seen this
 *  ship before?", which only the cross-run index can answer. */
export function VesselHistory({ dossier, exceptRun }) {
  const all = dossier?.appearance_list || [];
  const others = exceptRun ? all.filter((a) => a.run_id !== exceptRun) : all;
  if (!dossier) return null;
  if (!others.length) {
    return (
      <div className="vi-note" data-testid="vessel-history-empty">
        {all.length ? "This is the only run in which this vessel has appeared."
          : "No other run has indexed this vessel."}
      </div>
    );
  }
  return (
    <div className="vi-hist" data-testid="vessel-history">
      {others.slice(0, 8).map((a) => (
        <Link key={`${a.run_id}-${a.rank ?? "x"}`} className="vi-hist-row"
          to={url.workspace({ run: a.run_id, stage: "attribution" })}>
          <span className="mono vi-hist-run">{a.run_id}</span>
          <span className={`badge ${a.filtered ? "badge-neutral" : a.rank === 1 ? "badge-ok" : "badge-warn"}`}>
            {a.filtered ? "excluded" : a.rank ? `rank ${a.rank}` : "considered"}
          </span>
          {a.total_score != null && <span className="mono dim">{Number(a.total_score).toFixed(2)}</span>}
        </Link>
      ))}
      {others.length > 8 && <div className="dim tiny">and {others.length - 8} more</div>}
    </div>
  );
}
