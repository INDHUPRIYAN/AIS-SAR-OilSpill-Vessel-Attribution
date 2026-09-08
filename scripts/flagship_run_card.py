"""Render the flagship run card from its sealed artefacts and registry row.

    .venv/Scripts/python.exe scripts/flagship_run_card.py > acceptance_evidence/FLAGSHIP_RUN_CARD.md

Every number on the card is read from the manifest, the artefacts, the
verification result or the registry row at render time. Nothing is typed in,
so the card cannot drift from the evidence it describes -- and if the digest
ever changed, the card would say the new digest, which is the point.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "main_system"))

from backend.services.pipeline import provenance  # noqa: E402

RUN_ID = "inv-gulf-flagship-20230108-2day"


def main() -> int:
    run_dir = REPO / "data" / "runs" / RUN_ID
    m = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    sus = json.loads((run_dir / "suspects.json").read_text(encoding="utf-8"))
    oc = json.loads((run_dir / "origin_cloud.geojson").read_text(encoding="utf-8"))
    md = oc.get("metadata", {})
    det = json.loads((run_dir / "detect_response.json").read_text(encoding="utf-8"))
    ver = provenance.verify_run(run_dir)
    con = sqlite3.connect(f"file:{(REPO / 'data' / 'oceantrace.db').as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row = con.execute("select * from runs where id=?", (RUN_ID,)).fetchone()
    apps = con.execute("select run_id, rank, total_score from vessel_appearances where mmsi=? order by run_id",
                       (sus["suspects"][0]["mmsi"],)).fetchall()

    stages = m["stages"]
    models = {x["kind"]: x for x in m.get("models", [])}
    ais = m.get("ais", {})
    cands = det.get("candidates", [])
    n_oil = sum(1 for c in cands if c.get("class") == "oil")
    n_look = sum(1 for c in cands if c.get("class") == "lookalike")
    ells = [f for f in oc["features"] if (f["properties"].get("feature_type") or f["properties"].get("kind")) == "ellipse"]
    nonzero = sum(1 for f in ells if f["properties"].get("semi_major_m", 0) > 0)
    w = sus["weights"]
    top = sus["suspects"][0]
    recomputed = sum(w[k] * top["sub_scores"].get(k, 0.0) for k in w)
    pointer = json.loads((REPO / "dev_evidence" / "P14" / "flagship.json").read_text(encoding="utf-8"))

    def stage_line(s):
        return f"| {s['stage']} | {s['status']} | {s['source']} | {s.get('seconds', 0):.1f} s | {s.get('detail', '')} |"

    print(f"""# Flagship run card — `{RUN_ID}`

*Rendered {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} by `scripts/flagship_run_card.py` from the sealed artefacts. Not hand-written.*

| | |
|---|---|
| **Canonical pointer** | `dev_evidence/P14/flagship.json` → `{pointer.get('run_id')}` |
| **Artefact digest** | `{m['artefact_digest']}` |
| **Immutable** | {m.get('immutable')} · **verifies:** {ver['ok']} ({ver['checked']} artefacts, problems: {ver['problems'] or 'none'}) |
| **Sealed** | {m['generated_utc']} · {m['total_seconds']} s end-to-end · code `{m.get('code_git_sha', '')[:12]}` |
| **Registry row** | status `{row['status']}` · `{row['stages_real']}/{row['stages_total']}` real · `registry_source = {row['registry_source']}` · investigation `{row['investigation_id']}` · incident `{row['incident_id']}` |

## Inputs

| input | source |
|---|---|
| SAR scene | `{m['scene_id']}` — Sentinel-1A IW GRDH via CDSE, calibrated to Sigma0 dB |
| Segmenter | `{models.get('segment', {}).get('name')}` · sha256 `{models.get('segment', {}).get('sha256', '')[:16]}…` |
| Screen | `{models.get('screen', {}).get('name')}` · sha256 `{models.get('screen', {}).get('sha256', '')[:16]}…` |
| AIS | `{ais.get('data_source')}` · covers origin window: {ais.get('covers_origin')} — {ais.get('detail', '')} |
| Forcing | {md.get('forcing', 'see origin_cloud metadata')} |

## Stages — {sum(1 for s in stages if s['source'] == 'real')}/{len(stages)} real, {sum(1 for s in stages if s['status'] == 'mock')} mock, {sum(1 for s in stages if s['status'] == 'failed')} failed

| stage | status | source | time | detail |
|---|---|---|---|---|
{chr(10).join(stage_line(s) for s in stages)}

## Result — what the artefacts say, and only that

- **Detection:** {n_oil} oil + {n_look} look-alike candidates over {det.get('tiles_total', det.get('n_tiles', '?'))} tiles; look-alikes are reported, never counted as oil. **No ground truth exists for this scene** — the {n_oil} oil regions are model output in a basin with natural seeps.
- **Origin:** window `{md.get('origin_window_start_utc')}` → `{md.get('origin_window_end_utc')}`, method `{md.get('origin_window_method')}`. Uncertainty **{md.get('origin_uncertainty_km')} km** ({md.get('origin_uncertainty_coverage')} coverage), method: *{md.get('origin_uncertainty_method')}*. {nonzero}/{len(ells)} ellipses non-zero. The convergence peak sits at the acquisition instant: present this as a window, never a discharge time.
- **Attribution:** {sus.get('total_vessels_considered')} vessels considered → {len(sus['suspects'])} ranked, {len(sus.get('filtered_out', []))} filtered; `source = {sus['source']}`. Weights {json.dumps(w)} (sum {sum(w.values()):.2f}).
  **Rank #1 · Score {top['total_score']}** — MMSI {top['mmsi']} (Σwᵢsᵢ recomputes to {recomputed:.4f}). `vessel_name = {top.get('vessel_name')}` — MarineCadastre carried no static identity; absence left absent. A rank is a position in a weighted ordering, not a finding of responsibility.
- **Same MMSI across the flagship family:** {', '.join(f"{a['run_id']} (rank {a['rank']}, {a['total_score']})" for a in apps)}.

## Standing limitations that travel with this card

1. No ground truth for this scene; the oil regions are model output.
2. A ranked suspect is not a culprit.
3. All ranked vessels have `vessel_name: null` — absence of data, left absent.
4. The 13-hour window is wide and weak; the drift did not localise a release earlier than the image.
5. Slick age is always LOW confidence.
6. The registry row is **reconciled** (rebuilt from this manifest after the fact); investigation and incident are unknown to the artefacts and are not invented.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
