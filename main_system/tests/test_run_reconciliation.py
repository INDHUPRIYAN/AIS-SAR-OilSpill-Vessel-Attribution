"""Reconciling sealed runs on disk with the registry that indexes them.

PROMPT 20. The defect that prompted this was quiet and total: the frozen
flagship — the run the whole acceptance case rests on — existed as verified
artefacts on disk and did **not** exist as far as the API was concerned. It
could not be searched, did not appear in the runs list, and the top bar
described it as `RUN UNREADABLE`. Nothing was corrupt; a row was simply never
inserted, because only the API-driven path inserts one and the run came from
the CLI.

Three kinds of thing are in play, and the tests below exist to keep them
distinct:

* **sealed evidence** — the artefacts and the manifest that hashes them. The
  `artefact_digest` is computed from those files alone, so registering a run
  must not be able to change it. That is asserted, not assumed;
* **index metadata** — the `runs` row. Rebuildable from the evidence, and
  therefore safe to reconstruct;
* **how the row came to exist** — `registry_source`. A reconciled row is
  derived from a manifest after the fact; an `api` row was observed as the run
  happened. Facts only the API could have known (which investigation, which
  incident) stay NULL on a reconciled row rather than being invented to make
  the record look complete.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

GENERATED = "2026-09-07T08:04:36Z"
TOTAL_SECONDS = 384.3


@pytest.fixture
def runs_root(tmp_path, monkeypatch):
    """A private data root, so nothing here can touch the real registry."""
    root = tmp_path / "data"
    (root / "runs").mkdir(parents=True)
    monkeypatch.setenv("DATA_ROOT", str(root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    return root / "runs"


def seal_run(runs_root: Path, run_id: str, *, stages=None,
             generated=GENERATED, seconds=TOTAL_SECONDS) -> Path:
    """Write a run directory the way the CLI pipeline leaves one: artefacts,
    then a manifest that hashes them and carries the digest."""
    from backend.services.pipeline import provenance

    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "slick.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"area_km2": 59.624},
                      "geometry": None}]}), encoding="utf-8")
    (run_dir / "suspects.json").write_text(json.dumps({
        # Shaped like a real suspects.json entry: the index reads `rank` from
        # the artefact rather than inferring it from list position.
        "suspects": [{"mmsi": 367653160, "rank": 1, "total_score": 0.6691}]}),
        encoding="utf-8")
    (run_dir / "scene_meta.json").write_text(json.dumps({"scene_id": "S1A_TEST"}),
                                             encoding="utf-8")
    manifest = {
        "run_id": run_id,
        "scene_id": "S1A_IW_GRDH_TEST_E5A1_COG",
        "generated_utc": generated,
        "total_seconds": seconds,
        "stages": stages or [
            {"stage": s, "status": "ok", "source": "real"}
            for s in ("detect", "characterise", "drift_hindcast",
                      "drift_forecast", "attribution")],
    }
    # The pipeline's own seal, not a copy of it: these tests are worthless
    # if the fixture hashes artefacts differently from the code under test.
    provenance.seal(run_dir, manifest)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1),
                                           encoding="utf-8")
    return run_dir


def reconcile(*argv):
    from backend import backfill_runs
    return backfill_runs.main(list(argv))


def get_run(run_id):
    # init_db() so this is usable before the first reconcile, when the temp
    # database file has no tables yet.
    from backend.models.db import Run, SessionLocal, init_db
    init_db()
    with SessionLocal() as db:
        return db.get(Run, run_id)


# --------------------------------------------------------------------------
# the defect itself
# --------------------------------------------------------------------------

def test_a_cli_run_on_disk_is_adopted_into_the_registry(runs_root, capsys):
    """The flagship's exact situation: sealed artefacts, no row."""
    seal_run(runs_root, "inv-gulf-flagship-20230108-2day")
    assert get_run("inv-gulf-flagship-20230108-2day") is None

    reconcile()

    row = get_run("inv-gulf-flagship-20230108-2day")
    assert row is not None, "a sealed run on disk was left invisible to the API"
    assert row.status == "complete"
    assert (row.stages_real, row.stages_total) == (5, 5)


def test_reconciliation_does_not_touch_the_sealed_evidence(runs_root):
    """The whole point. The digest is computed from the artefact files, so a
    registry write must be incapable of moving it -- and the manifest itself
    must come back byte-identical."""
    import hashlib

    run_dir = seal_run(runs_root, "inv-flagship")
    before_digest = json.loads((run_dir / "manifest.json").read_text())["artefact_digest"]
    before_bytes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(run_dir.iterdir())}

    reconcile()

    after = json.loads((run_dir / "manifest.json").read_text())
    after_bytes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(run_dir.iterdir())}
    assert after["artefact_digest"] == before_digest
    assert after_bytes == before_bytes, "reconciliation modified sealed evidence"


def test_the_run_still_verifies_after_being_registered(runs_root):
    run_dir = seal_run(runs_root, "inv-flagship")
    reconcile()
    from backend.services.pipeline import provenance
    check = provenance.verify_run(run_dir)
    assert check["ok"], check["problems"]


# --------------------------------------------------------------------------
# derived, not invented
# --------------------------------------------------------------------------

def test_a_reconciled_row_says_it_was_reconciled(runs_root):
    """An index entry rebuilt from a manifest must not be mistakable for a
    record the API watched being made."""
    seal_run(runs_root, "inv-flagship")
    reconcile()
    assert get_run("inv-flagship").registry_source == "reconciled"


def test_facts_only_the_api_could_know_are_left_null(runs_root):
    """Which investigation opened the run, and which incident it was evidence
    for, are not in the artefacts. A plausible value here would be a fabricated
    provenance claim on the project's headline run."""
    seal_run(runs_root, "inv-flagship")
    reconcile()
    row = get_run("inv-flagship")
    assert row.investigation_id is None
    assert row.incident_id is None
    assert row.region is None


def test_the_start_time_is_derived_from_the_manifest_not_faked(runs_root):
    """`generated_utc` is stamped when the manifest is written -- the END of the
    run. Using it for both ends made every reconciled run look instantaneous.
    The duration is in the manifest, so the start is a subtraction."""
    seal_run(runs_root, "inv-flagship")
    reconcile()
    row = get_run("inv-flagship")
    finished = datetime.strptime(GENERATED, "%Y-%m-%dT%H:%M:%SZ")
    assert row.finished_utc.replace(tzinfo=None) == finished
    assert row.started_utc.replace(tzinfo=None) == finished - timedelta(seconds=TOTAL_SECONDS)
    assert row.seconds == pytest.approx(TOTAL_SECONDS)


def test_the_outcome_summary_comes_from_the_artefacts(runs_root):
    """Top suspect and slick area are read out of the sealed files, not
    recomputed and not guessed."""
    seal_run(runs_root, "inv-flagship")
    reconcile()
    row = get_run("inv-flagship")
    assert row.top_suspect_mmsi == 367653160
    assert row.top_score == pytest.approx(0.6691)
    assert row.slick_area_km2 == pytest.approx(59.624)


def test_a_stage_that_was_not_real_is_counted_as_not_real(runs_root):
    """A run whose drift stages were mocked must not be indexed as 5/5."""
    seal_run(runs_root, "inv-rehearsal", stages=[
        {"stage": "detect", "status": "ok", "source": "real"},
        {"stage": "characterise", "status": "ok", "source": "real"},
        {"stage": "drift_hindcast", "status": "mock", "source": "synthetic"},
        {"stage": "drift_forecast", "status": "mock", "source": "synthetic"},
        {"stage": "attribution", "status": "mock", "source": "synthetic"},
    ])
    reconcile()
    row = get_run("inv-rehearsal")
    assert (row.stages_real, row.stages_mock, row.stages_total) == (2, 3, 5)


# --------------------------------------------------------------------------
# only verified evidence is adopted
# --------------------------------------------------------------------------

def test_a_run_whose_artefacts_changed_is_not_registered(runs_root, capsys):
    """Registering a tampered run would put it in the index wearing exactly the
    same clothes as a sound one."""
    run_dir = seal_run(runs_root, "inv-tampered")
    (run_dir / "slick.geojson").write_text('{"type":"FeatureCollection","features":[]}',
                                           encoding="utf-8")

    reconcile()

    assert get_run("inv-tampered") is None
    out = capsys.readouterr().out
    assert "NOT registered" in out and "changed: slick.geojson" in out


def test_an_operator_can_override_but_the_output_says_so(runs_root, capsys):
    run_dir = seal_run(runs_root, "inv-tampered")
    (run_dir / "slick.geojson").write_text('{"type":"FeatureCollection","features":[]}',
                                           encoding="utf-8")

    reconcile("--allow-unverified")

    assert get_run("inv-tampered") is not None
    assert "registered UNVERIFIED" in capsys.readouterr().out


def test_an_unfinished_run_is_not_registered(runs_root):
    """No manifest means the run never sealed. A row for it would advertise a
    result that does not exist."""
    (runs_root / "inv-cancelled").mkdir(parents=True)
    (runs_root / "inv-cancelled" / "status.json").write_text("{}", encoding="utf-8")
    reconcile()
    assert get_run("inv-cancelled") is None


# --------------------------------------------------------------------------
# it is safe to run repeatedly
# --------------------------------------------------------------------------

def test_reconciling_twice_changes_nothing(runs_root, capsys):
    seal_run(runs_root, "inv-flagship")
    reconcile()
    first = get_run("inv-flagship").started_utc

    capsys.readouterr()
    reconcile()
    out = capsys.readouterr().out

    assert "registered 0" in out
    assert get_run("inv-flagship").started_utc == first


def test_an_api_row_is_not_rewritten_by_a_reconcile(runs_root):
    """The API records a run's REAL start time; a manifest is written at the
    end. Overwriting an observed row with a derivation would replace a fact
    with an approximation of it."""
    from backend.models.db import Run, SessionLocal, init_db

    seal_run(runs_root, "inv-api-run")
    observed = datetime(2026, 9, 7, 7, 0, 0, tzinfo=timezone.utc)
    init_db()
    with SessionLocal() as db:
        db.add(Run(id="inv-api-run", status="complete", started_utc=observed,
                   registry_source="api"))
        db.commit()

    reconcile()

    row = get_run("inv-api-run")
    assert row.started_utc.replace(tzinfo=timezone.utc) == observed
    assert row.registry_source == "api", \
        "a reconcile relabelled a row the API had observed"


def test_dry_run_writes_nothing(runs_root, capsys):
    seal_run(runs_root, "inv-flagship")
    reconcile("--dry-run")
    assert get_run("inv-flagship") is None
    assert "would add 1" in capsys.readouterr().out


# --------------------------------------------------------------------------
# E2E-11: the invariant the defect violated
# --------------------------------------------------------------------------

def test_every_sealed_run_on_disk_has_a_registry_row(runs_root):
    """Master plan E2E-11, in miniature. This is the check whose absence let
    the flagship sit unindexed for a day: disk and registry must agree, and
    where they cannot, the difference must be nameable."""
    for rid in ("inv-a", "inv-b", "inv-c"):
        seal_run(runs_root, rid)
    (runs_root / "inv-unfinished").mkdir()          # no manifest: not sealed

    reconcile()

    from backend.models.db import Run, SessionLocal
    sealed = {p.parent.name for p in runs_root.glob("*/manifest.json")}
    with SessionLocal() as db:
        indexed = {r.id for r in db.query(Run).all()}
    assert sealed - indexed == set(), f"sealed but unindexed: {sealed - indexed}"
    assert "inv-unfinished" not in indexed


# --------------------------------------------------------------------------
# the second half of the same defect: the vessel index
# --------------------------------------------------------------------------

def test_a_reconciled_run_has_its_vessels_indexed(runs_root):
    """The API indexes a run's ranked vessels at seal time. A reconciled run
    never sealed through the API, so its rank-1 MMSI was absent from
    /api/vessels and from search -- the flagship's suspect was unreachable
    even after the run itself was registered."""
    seal_run(runs_root, "inv-flagship")
    reconcile()

    from backend.models.db import SessionLocal, VesselAppearance
    with SessionLocal() as db:
        rows = db.query(VesselAppearance).filter(
            VesselAppearance.mmsi == 367653160).all()
    assert [(r.run_id, r.rank) for r in rows] == [("inv-flagship", 1)]
