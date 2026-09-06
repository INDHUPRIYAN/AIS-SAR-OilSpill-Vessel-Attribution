"""Content hashes, idempotency keys and the immutability guard (design v2 §10, §12, §14).

These tests exist because every failure in this area is silent. An artefact
overwritten in place still looks like a valid run; an idempotency key that
changes with dict ordering makes every retry redo the work while claiming it
did not; a manifest sealed before the last stage wrote would hash a file that
no longer exists. None of it shows up in a demo.
"""

import json
from pathlib import Path

import pytest

from backend.services.pipeline import provenance


@pytest.fixture()
def run_dir(tmp_path):
    """A minimal completed run: two artefacts plus a sealed manifest."""
    d = tmp_path / "inv-001"
    d.mkdir()
    (d / "slick.geojson").write_text('{"type":"FeatureCollection","features":[]}',
                                     encoding="utf-8")
    (d / "suspects.json").write_text('{"suspects":[]}', encoding="utf-8")
    # status.json is working state, rewritten several times during a run --
    # it must NOT be hashed or every run fails its own verification.
    (d / "status.json").write_text('{"running":"detect"}', encoding="utf-8")
    manifest = {"run_id": "inv-001", "stages": []}
    provenance.seal(d, manifest)
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


# --------------------------------------------------------------------------
# hashing and sealing
# --------------------------------------------------------------------------


def test_seal_records_every_artefact_present(run_dir):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert {a["file"] for a in manifest["artefacts"]} == {"slick.geojson",
                                                          "suspects.json"}
    assert all(len(a["sha256"]) == 64 and a["bytes"] > 0
               for a in manifest["artefacts"])
    assert manifest["immutable"] is True


def test_working_state_is_not_hashed(run_dir):
    """status.json is rewritten mid-run by design; hashing it would fail every run."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "status.json" not in {a["file"] for a in manifest["artefacts"]}
    (run_dir / "status.json").write_text('{"running":"done"}', encoding="utf-8")
    assert provenance.verify_run(run_dir)["ok"]


def test_missing_artefacts_are_skipped_not_recorded_as_absent(run_dir):
    """§10 invariant 4: a degraded run with no AIS layer is a normal outcome."""
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "vessels.parquet" not in {a["file"] for a in manifest["artefacts"]}
    assert provenance.verify_run(run_dir)["ok"]


def test_artefact_digest_covers_the_whole_set(tmp_path):
    """Changing any one artefact must change the single digest quoted in a report."""
    def digest(payload):
        d = tmp_path / f"run-{payload}"
        d.mkdir()
        (d / "slick.geojson").write_text(payload, encoding="utf-8")
        m = {}
        provenance.seal(d, m)
        return m["artefact_digest"]

    assert digest("a") != digest("b")


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def test_verify_passes_on_an_untouched_run(run_dir):
    report = provenance.verify_run(run_dir)
    assert report["ok"] and report["checked"] == 2 and report["problems"] == []


def test_verify_catches_an_overwritten_artefact(run_dir):
    (run_dir / "slick.geojson").write_text('{"type":"FeatureCollection",'
                                           '"features":[1]}', encoding="utf-8")
    report = provenance.verify_run(run_dir)
    assert not report["ok"]
    assert any(p.startswith("changed: slick.geojson") for p in report["problems"])


def test_verify_catches_a_deleted_artefact(run_dir):
    (run_dir / "suspects.json").unlink()
    report = provenance.verify_run(run_dir)
    assert not report["ok"]
    assert "missing: suspects.json" in report["problems"]


def test_verify_catches_an_artefact_added_after_the_run(run_dir):
    """Nothing recorded changed, but the record is no longer the whole truth."""
    (run_dir / "vessels.parquet").write_bytes(b"PAR1")
    report = provenance.verify_run(run_dir)
    assert not report["ok"]
    assert any("added after the run completed" in p for p in report["problems"])


def test_verify_distinguishes_unverifiable_from_tampered(tmp_path):
    """A run predating content hashing is not a tamper signal; say which it is."""
    d = tmp_path / "old-run"
    d.mkdir()
    (d / "slick.geojson").write_text("{}", encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"run_id": "old-run",
                                                 "stages": []}), encoding="utf-8")
    report = provenance.verify_run(d)
    assert not report["ok"] and report.get("unverifiable") is True


def test_verify_reports_an_incomplete_run(tmp_path):
    d = tmp_path / "half-run"
    d.mkdir()
    report = provenance.verify_run(d)
    assert not report["ok"]
    assert "never completed" in report["problems"][0]


# --------------------------------------------------------------------------
# the immutability guard
# --------------------------------------------------------------------------


def test_a_completed_run_refuses_to_be_rerun(run_dir):
    with pytest.raises(provenance.RunImmutable, match="immutable"):
        provenance.assert_writable(run_dir)


def test_a_fresh_or_interrupted_run_dir_is_writable(tmp_path):
    provenance.assert_writable(tmp_path / "does-not-exist-yet")
    partial = tmp_path / "interrupted"
    partial.mkdir()
    (partial / "slick.geojson").write_text("{}", encoding="utf-8")
    provenance.assert_writable(partial)      # no manifest == never sealed


# --------------------------------------------------------------------------
# idempotency keys (§10 invariant 1, §5's job message)
# --------------------------------------------------------------------------


def test_same_inputs_and_params_give_the_same_key():
    a = provenance.idempotency_key("drift", {"slick": "s3://x/slick.geojson"},
                                   {"hours": 12, "windage": 0.03})
    b = provenance.idempotency_key("drift", {"slick": "s3://x/slick.geojson"},
                                   {"hours": 12, "windage": 0.03})
    assert a == b and len(a) == 64


def test_key_is_independent_of_dict_ordering():
    """A retry must not redo the work just because a dict was built differently."""
    a = provenance.idempotency_key("drift", {"slick": "a", "wind": "b"},
                                   {"hours": 12, "windage": 0.03})
    b = provenance.idempotency_key("drift", {"wind": "b", "slick": "a"},
                                   {"windage": 0.03, "hours": 12})
    assert a == b


def test_key_is_independent_of_the_path_separator():
    """A Windows run and a Linux run of the same inputs must agree."""
    assert (provenance.idempotency_key("detect", {"scene": Path("a/b/c.tif")})
            == provenance.idempotency_key("detect", {"scene": "a/b/c.tif"}))


def test_key_tolerates_float_representation_noise():
    assert (provenance.idempotency_key("drift", params={"K": 0.1 + 0.2})
            == provenance.idempotency_key("drift", params={"K": 0.3}))


def test_key_changes_when_a_parameter_changes():
    """Different windage is a different physical run and must not be skipped."""
    a = provenance.idempotency_key("drift", {"slick": "a"}, {"windage": 0.03})
    b = provenance.idempotency_key("drift", {"slick": "a"}, {"windage": 0.05})
    assert a != b


def test_key_changes_when_the_stage_changes():
    assert (provenance.idempotency_key("drift", {"x": 1})
            != provenance.idempotency_key("attribution", {"x": 1}))
