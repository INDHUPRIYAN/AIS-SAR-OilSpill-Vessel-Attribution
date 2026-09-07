"""The scene the UI launches must be a real one, and must describe itself.

Audit N-14: `Dashboard.jsx` hardcoded `contracts/mocks/scene_meta.json`, so a
run started from the browser was a 1-of-5-real smoke test. Nobody could see the
scene was wrong, because the scene was never shown.

Beyond replacing the hardcode, these tests pin the two provenance facts that
`GET /api/scenes/local` exists to carry:

* a scene the deployed segmenter TRAINED on is labelled as such -- confidence
  on it is memorisation, not accuracy;
* a scene whose `acquired_utc` was assigned rather than measured says so, since
  the hindcast integrates backward from that timestamp.

Both were discovered while building this endpoint: the previous demo scene
(Trujillo `Oil/00074`) is absent from the held-out test split, and the corpus
ships no acquisition metadata at all.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG = REPO_ROOT / "main_system" / "config" / "scene_catalog.json"
TEST_SPLIT = REPO_ROOT / "data" / "processed" / "trujillo" / "test" / "index.json"


@pytest.fixture(scope="module")
def client(sign_in_helper):
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    sys.path.insert(0, str(REPO_ROOT / "main_system"))
    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        yield c


@pytest.fixture(scope="module")
def catalog(client):
    r = client.get("/api/scenes/local")
    assert r.status_code == 200
    return r.json()


# --------------------------------------------------------------------------
# the endpoint
# --------------------------------------------------------------------------

def test_catalog_lists_scenes_and_declares_a_default(catalog):
    assert catalog["count"] >= 2
    assert catalog["available"] >= 1
    assert catalog["default_id"], "a default must be declared"


def test_every_entry_resolves_or_says_why_not(catalog):
    """A missing scene is surfaced, not silently dropped."""
    for s in catalog["scenes"]:
        if s["available"]:
            assert s["scene_id"], f"{s['id']} resolved but has no scene_id"
            assert s["raster_path"], f"{s['id']} resolved but has no raster"
        else:
            assert s["unavailable_reason"], f"{s['id']} unavailable with no reason"


def test_default_is_a_real_acquisition_never_the_mock(catalog):
    """The whole point of N-14: the mock must not be what a user runs."""
    default = [s for s in catalog["scenes"] if s["is_default"]]
    assert len(default) == 1, "exactly one default"
    assert default[0]["provenance"] != "mock"
    assert default[0]["available"]


def test_mock_is_offered_but_labelled(catalog):
    """Kept, because it genuinely exercises the wiring -- but never disguised."""
    mock = [s for s in catalog["scenes"] if s["provenance"] == "mock"]
    assert mock, "the smoke-test raster should remain available"
    m = mock[0]
    assert m["is_default"] is False
    assert "MOCK" in m["source"]
    assert any("no oil" in c.lower() or "1-of-5" in c for c in m["caveats"])


def test_single_scene_lookup(client, catalog):
    sid = catalog["default_id"]
    r = client.get(f"/api/scenes/local/{sid}")
    assert r.status_code == 200
    assert r.json()["id"] == sid
    assert client.get("/api/scenes/local/no-such-scene").status_code == 404


# --------------------------------------------------------------------------
# provenance honesty
# --------------------------------------------------------------------------

def test_the_default_scene_has_a_measured_acquisition_time(catalog):
    """Its acquired_utc is read from the Sentinel-1 product identifier, so the
    temporal chain the hindcast builds on is anchored to a real time."""
    default = next(s for s in catalog["scenes"] if s["is_default"])
    assert default["time_basis"] == "measured"
    assert default["provenance"] == "sentinel1_real"
    # S1 product ids embed the start time: ..._20170129T003132_...
    stamp = default["acquired_utc"].replace("-", "").replace(":", "")[:15]
    assert stamp.replace("T", "T") in default["scene_id"].replace("_", "")


def test_training_scenes_are_flagged_as_unusable_evidence(catalog):
    """The segmenter trained on Oil/00074. Quoting its confidence as accuracy
    would be the exact claim the truth rules forbid."""
    trained = [s for s in catalog["scenes"] if s["provenance"] == "corpus_train"]
    if not trained:
        pytest.skip("no training-set scenes in the catalog")
    for s in trained:
        assert "TRAINING" in s["source"].upper()
        assert any("memoris" in c.lower() or "not evidence" in c.lower()
                   for c in s["caveats"]), f"{s['id']} lacks the training caveat"


def test_assigned_timestamps_are_declared(catalog):
    """Corpus rasters carry no acquisition metadata, so their times were
    assigned. That has to reach the UI: it anchors the AIS query window."""
    for s in catalog["scenes"]:
        if s["time_basis"] == "assigned":
            assert any("ASSIGNED" in c or "assigned" in c for c in s["caveats"]), \
                f"{s['id']} has an assigned time with no caveat"


def test_heldout_claims_match_the_actual_split():
    """Cross-check the catalog's `corpus_heldout` claims against the split
    index, so a mislabelled scene cannot quietly become 'held out'."""
    if not TEST_SPLIT.exists():
        pytest.skip("processed test split not present in this checkout")

    scenes_in_test = {t["scene"] for t in
                      json.loads(TEST_SPLIT.read_text(encoding="utf-8"))["tiles"]}
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["scenes"]

    for e in entries:
        meta_path = REPO_ROOT / e["scene_meta_path"]
        if not meta_path.exists():
            continue
        raster = json.loads(meta_path.read_text(encoding="utf-8")).get("file_path", "")
        parts = Path(str(raster).replace("\\", "/")).parts
        if len(parts) < 2 or "trujillo" not in str(raster):
            continue
        corpus_id = f"{parts[-2]}/{Path(parts[-1]).stem}"

        if e["provenance"] == "corpus_heldout":
            assert corpus_id in scenes_in_test, \
                f"{e['id']} claims held-out but {corpus_id} is not in the test split"
        elif e["provenance"] == "corpus_train":
            assert corpus_id not in scenes_in_test, \
                f"{e['id']} is labelled a training scene but IS in the test split"


def test_catalog_never_labels_a_corpus_scene_as_a_sentinel1_product():
    """`sentinel1_real` means a real product with a measured time. A corpus
    raster must not borrow that label."""
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))["scenes"]
    for e in entries:
        if e["provenance"] != "sentinel1_real":
            continue
        assert e["time_basis"] == "measured", f"{e['id']}: real product needs a measured time"
        meta_path = REPO_ROOT / e["scene_meta_path"]
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert meta.get("source") == "real", f"{e['id']}: scene_meta.source is not 'real'"
