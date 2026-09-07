"""Report v2: composition from artefacts, review lifecycle, annex, CSV.

PROMPT 16. The four things these defend:

* **artefacts only.** Every value in the body came from a file the pipeline
  sealed. A stage that did not run produces no section -- and the omission is
  named, because a silently missing section reads as "nothing to report" rather
  than "that stage did not run".
* **the annex is what /verify checks.** Hashes are copied from the manifest,
  not recomputed, so the provenance table and the verifier can never disagree.
  An annex that reassures while being wrong is the worst possible outcome.
* **published is immutable.** Revising a published report creates version n+1
  as a draft. A document that can change after sign-off is not signed off.
* **neutral language.** The templated summary contains no word that assigns
  responsibility. The system ranks; it does not accuse.

Composition tests run against the frozen flagship when it is present and skip
cleanly when it is not, so a clean checkout stays green.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

FLAGSHIP_POINTER = REPO_ROOT / "dev_evidence" / "P14" / "flagship.json"

# Words a report must never use about a ranked vessel. The system produces an
# explainable ordering, not a finding of fault.
FORBIDDEN = ("culprit", "guilty", "perpetrator", "offender", "responsible for",
             "at fault", "convicted", "polluter")


def _flagship_dir():
    if not FLAGSHIP_POINTER.exists():
        return None
    run_id = json.loads(FLAGSHIP_POINTER.read_text(encoding="utf-8"))["run_id"]
    run_dir = REPO_ROOT / "data" / "runs" / run_id
    return run_dir if (run_dir / "manifest.json").exists() else None


@pytest.fixture(scope="module")
def flagship():
    run_dir = _flagship_dir()
    if run_dir is None:
        pytest.skip("flagship run not in this checkout")
    return run_dir


@pytest.fixture(scope="module")
def body(flagship):
    from backend.services.report_compose import compose

    return compose(flagship)


@pytest.fixture(scope="module")
def client(sign_in_helper, tmp_path_factory):
    root = tmp_path_factory.mktemp("reports")
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'r.db').as_posix()}"
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        yield c
    os.environ.pop("DATABASE_URL", None)


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------

def test_an_unsealed_run_cannot_be_reported_on(tmp_path):
    """No manifest means no fixed content; composing anyway would produce a
    document that changes under the reader."""
    from backend.services.report_compose import compose

    (tmp_path / "status.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="not sealed"):
        compose(tmp_path)


def test_the_body_names_what_did_not_render(body):
    """A missing section must be stated, not left as an absence."""
    assert "omitted" in body
    kinds = {s["kind"] for s in body["sections"]}
    for name in body["omitted"]:
        assert name not in kinds


def test_every_expected_section_composed_for_a_complete_run(body):
    kinds = [s["kind"] for s in body["sections"]]
    for expected in ("executive_summary", "detection", "characterisation",
                     "environment", "origin", "ais", "attribution",
                     "limitations", "provenance_annex"):
        assert expected in kinds, f"{expected} missing from {kinds}"


def test_the_summary_never_assigns_responsibility(body):
    summary = next(s for s in body["sections"] if s["kind"] == "executive_summary")
    prose = " ".join(summary["paragraphs"]).lower()
    for word in FORBIDDEN:
        assert word not in prose, f"the summary says '{word}'"
    # And it says what a rank is not.
    assert "not a determination of responsibility" in prose


def test_the_composers_own_prose_avoids_culpability_language(body):
    """Section notes and captions, not only the summary.

    Scoped to text the composer WRITES. The limitations section is quoted
    verbatim from `docs/LIMITATIONS.md`, which legitimately uses "culprit" to
    discuss benchmark ground truth ("real confirmed-culprit corpora do not
    exist"). Censoring a source document to satisfy a lint would make the
    report less accurate, not more careful -- the rule is about what this
    system says of a ranked vessel, not about what a cited document may
    discuss.
    """
    authored = [s for s in body["sections"] if s["kind"] != "limitations"]
    text = json.dumps(authored).lower()
    for word in ("culprit", "guilty", "perpetrator", "convicted", "at fault"):
        assert word not in text, f"the composer's own prose contains '{word}'"


def test_age_is_labelled_low_confidence(body):
    section = next(s for s in body["sections"] if s["kind"] == "characterisation")
    assert section["age_label"] == "LOW confidence"
    assert "low confidence" in section["age_caveat"].lower()
    assert "does not date the discharge" in section["age_caveat"]


def test_the_origin_section_carries_its_derivation_method(body):
    section = next(s for s in body["sections"] if s["kind"] == "origin")
    assert section["method"] in ("cloud_convergence", "age_estimate", "midpoint")
    assert section["uncertainty_km"] is not None
    if section["method"] == "cloud_convergence":
        assert section["method_caveat"] is None
    else:
        assert "localises no" in (section["method_caveat"] or "") or \
               "carries no information" in (section["method_caveat"] or "")


def test_the_environment_section_names_real_providers(body):
    section = next(s for s in body["sections"] if s["kind"] == "environment")
    providers = json.dumps(section).lower()
    assert "mock" not in providers and "synthetic" not in providers
    assert section["currents"]["provider"]
    assert section["wind"]["provider"]


def test_the_ais_section_badges_its_source(body):
    section = next(s for s in body["sections"] if s["kind"] == "ais")
    assert section["source_badge"] in ("REAL", "SYNTHETIC")
    # The rejection ledger travels with the claim, so "we used real AIS" is
    # falsifiable rather than asserted.
    assert section["considered"], "no AIS candidate ledger in the report"


def test_limitations_come_from_the_file_not_the_composer(body):
    section = next(s for s in body["sections"] if s["kind"] == "limitations")
    assert section["source"] == "docs/LIMITATIONS.md"
    assert section["sections"], "no limitation sections parsed"
    headings = {s["heading"] for s in section["sections"]}
    assert "Data availability" in headings


def test_the_limitations_file_no_longer_claims_all_ais_is_synthetic():
    """The flagship used real MarineCadastre archives. A stale limitation is a
    false statement about what the system cannot do."""
    text = (REPO_ROOT / "docs" / "LIMITATIONS.md").read_text(encoding="utf-8")
    assert "All AIS in this repository is\n   synthetic" not in text
    assert "All AIS in this repository is synthetic" not in text
    assert "MarineCadastre" in text


# --------------------------------------------------------------------------
# the provenance annex
# --------------------------------------------------------------------------

def test_annex_hashes_are_exactly_what_verify_checks(body, flagship):
    from backend.services.pipeline import provenance

    annex = next(s for s in body["sections"] if s["kind"] == "provenance_annex")
    manifest = json.loads((flagship / "manifest.json").read_text(encoding="utf-8"))

    assert annex["artefact_digest"] == manifest["artefact_digest"]
    assert annex["artefacts"] == manifest["artefacts"], \
        "the annex table differs from the sealed manifest"

    report = provenance.verify_run(flagship)
    assert report["ok"], f"the run does not verify: {report.get('problems')}"
    assert report["checked"] == len(annex["artefacts"])


def test_the_annex_records_code_and_model_identity(body):
    annex = next(s for s in body["sections"] if s["kind"] == "provenance_annex")
    assert annex["code_git_sha"]
    kinds = {m["kind"]: m for m in annex["models"]}
    assert kinds["segment"]["name"] == "unet-r34-fullcorpus-e48"
    assert kinds["screen"]["name"].startswith("yolo11n")
    for model in kinds.values():
        assert len(model["sha256"]) == 64


def test_the_annex_lists_each_source_and_where_it_came_from(body):
    annex = next(s for s in body["sections"] if s["kind"] == "provenance_annex")
    layers = {s["layer"]: s for s in annex["sources"]}
    assert {"scene", "currents", "wind"} <= set(layers)
    assert layers["currents"]["provider"]


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------

def test_the_full_lifecycle_and_publish_immutability(client):
    run_dir = _flagship_dir()
    if run_dir is None:
        pytest.skip("flagship run not in this checkout")

    from backend.models.db import Run, SessionLocal

    with SessionLocal() as db:
        if db.get(Run, run_dir.name) is None:
            db.add(Run(id=run_dir.name, status="complete"))
            db.commit()

    created = client.post("/api/reports", json={"run_id": run_dir.name})
    assert created.status_code == 201, created.text
    report = created.json()
    assert report["version"] == 1 and report["status"] == "draft"
    assert report["immutable"] is False
    report_id = report["id"]

    # A second compose for the same run is refused: versions come from /revise.
    assert client.post("/api/reports", json={"run_id": run_dir.name}).status_code == 409

    # Cannot publish a draft without review.
    assert client.post(f"/api/reports/{report_id}/publish", json={}).status_code == 409

    assert client.post(f"/api/reports/{report_id}/submit",
                       json={"note": "ready"}).status_code == 200
    published = client.post(f"/api/reports/{report_id}/publish",
                            json={"note": "approved"})
    assert published.status_code == 200
    assert published.json()["status"] == "published"

    fetched = client.get(f"/api/reports/{report_id}").json()
    assert fetched["immutable"] is True
    assert fetched["digest_matches_run"] is True

    # Publishing again is refused rather than silently re-stamping.
    assert client.post(f"/api/reports/{report_id}/publish", json={}).status_code == 409
    # Submitting a published report is refused too.
    assert client.post(f"/api/reports/{report_id}/submit", json={}).status_code == 409

    revised = client.post(f"/api/reports/{report_id}/revise")
    assert revised.status_code == 201
    assert revised.json()["version"] == 2
    assert revised.json()["status"] == "draft"

    # The published version is untouched.
    still = client.get(f"/api/reports/{report_id}").json()
    assert still["status"] == "published" and still["version"] == 1


def test_a_report_for_an_unknown_run_is_404(client):
    assert client.post("/api/reports", json={"run_id": "no-such-run"}).status_code == 404


def test_a_report_for_an_unsealed_run_is_409(client):
    from backend.models.db import Run, SessionLocal

    with SessionLocal() as db:
        db.add(Run(id="run-unsealed", status="running"))
        db.commit()
    r = client.post("/api/reports", json={"run_id": "run-unsealed"})
    assert r.status_code == 409
    assert "not sealed" in r.json()["detail"]


# --------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------

GOLDEN_COLUMNS = [
    "run_id", "scene_id", "rank", "mmsi", "vessel_name", "vessel_type",
    "total_score", "source", "proximity", "temporal", "trajectory",
    "behaviour", "ais_gap", "vessel_prior", "closest_approach_km",
    "time_in_origin_window_min", "ais_gap_minutes", "course_delta_deg",
    "min_sog_kn", "track_points_in_cloud", "reason",
]


def test_csv_columns_match_the_golden_header(body):
    """A silently reordered column is how a spreadsheet attributes one
    vessel's evidence to another."""
    from backend.services.report_compose import flatten_suspects_csv

    rows = flatten_suspects_csv(body)
    assert rows[0] == GOLDEN_COLUMNS


def test_csv_has_one_row_per_ranked_vessel(body):
    from backend.services.report_compose import flatten_suspects_csv

    attribution = next(s for s in body["sections"] if s["kind"] == "attribution")
    rows = flatten_suspects_csv(body)
    assert len(rows) == len(attribution["suspects"]) + 1
    for row in rows[1:]:
        assert len(row) == len(GOLDEN_COLUMNS)


def test_csv_export_over_http(client):
    run_dir = _flagship_dir()
    if run_dir is None:
        pytest.skip("flagship run not in this checkout")

    listing = client.get("/api/reports", params={"run": run_dir.name}).json()
    if not listing:
        pytest.skip("no report composed in this session")
    report_id = listing[0]["id"]

    r = client.get(f"/api/reports/{report_id}/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.splitlines()[0] == ",".join(GOLDEN_COLUMNS)
