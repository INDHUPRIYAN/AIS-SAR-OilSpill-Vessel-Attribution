"""Scene search over HTTP, and the EO refusal.

The route wraps `SceneRetrievalChain`, which was already proven from the CLI;
nothing about provider selection is reimplemented. What the tests here pin is
the honesty around it:

* `source=S2` returns **501 NOT_DEPLOYED**, not an empty result. The Sentinel-2
  adapter exists and is tested, but no optical data is wired into the pipeline
  and no accuracy has been measured for it. An empty list would read as "we
  looked and found none", which is a different and false claim.
* `attempts` distinguishes what was observed from what was inferred. The chain
  swallows a failing member's error internally, so reporting a CDSE failure we
  never received would be inventing evidence.

Network tests are marked skippable: a provider outage is not a code defect, and
a suite that fails on someone else's downtime gets ignored.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "search-test-password"
GULF_BBOX = "-91.5,27.8,-89.0,29.8"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("search_root")
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 's.db').as_posix()}"
    os.environ["SECRET_KEY"] = "s" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        db.add(User(email="analyst@example.invalid",
                    password_hash=security.hash_password(PASSWORD),
                    role="analyst", active=True))
        db.commit()

    with TestClient(app, base_url="https://testserver") as c:
        c.post("/api/auth/login", json={"email": "analyst@example.invalid",
                                        "password": PASSWORD})
        yield c

    os.environ.pop("DATABASE_URL", None)


# --------------------------------------------------------------------------
# EO is not deployed, and says so
# --------------------------------------------------------------------------

def test_sentinel2_returns_501_not_an_empty_result(client):
    r = client.get("/api/scenes/search", params={"bbox": GULF_BBOX, "source": "S2"})
    assert r.status_code == 501, "an empty S2 result would imply EO is deployed"

    body = r.json()
    assert body["status"] == "NOT_DEPLOYED"
    assert "not deployed" in body["detail"].lower()
    # The adapter is named, because "not deployed" and "does not exist" are
    # different statements and the difference is the roadmap.
    assert body["adapter"].endswith("s2_adapter.py")
    assert "scenes" not in body, "a NOT_DEPLOYED response must carry no results"


def test_the_s2_adapter_really_does_exist():
    """Guards the claim above: the response says the adapter exists, so if it
    were ever removed this message would become false."""
    assert (REPO_ROOT / "scene_service" / "satellite" / "s2_adapter.py").exists()


def test_only_known_sources_are_accepted(client):
    r = client.get("/api/scenes/search", params={"bbox": GULF_BBOX, "source": "S3"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# input validation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bbox,reason", [
    ("1,2,3", "four values"),
    ("a,b,c,d", "numeric"),
    ("-200,10,-190,20", "longitude range"),
    ("10,-100,20,-95", "latitude range"),
    ("-89,27.8,-91.5,29.8", "min before max"),
])
def test_bad_bbox_is_rejected(client, bbox, reason):
    r = client.get("/api/scenes/search", params={"bbox": bbox})
    assert r.status_code == 422, f"accepted a bbox that fails on {reason}"


def test_bbox_is_required(client):
    assert client.get("/api/scenes/search").status_code == 422


def test_search_requires_a_session(client):
    client.cookies.clear()
    assert client.get("/api/scenes/search",
                      params={"bbox": GULF_BBOX}).status_code == 401
    client.post("/api/auth/login", json={"email": "analyst@example.invalid",
                                         "password": PASSWORD})


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def test_attempts_separate_observed_from_inferred(client, monkeypatch):
    """The chain hides a failing member's error, so the route must not claim
    to have seen one."""
    from backend.api import scenes as scenes_mod

    class _Result:
        provider = "ASF"
        total_count = 1
        scenes = []

    class _Chain:
        def search_scenes(self, **_kw):
            return _Result()

    import satellite.chain as chain_mod

    monkeypatch.setattr(chain_mod, "SceneRetrievalChain", _Chain)
    body = client.get("/api/scenes/search", params={"bbox": GULF_BBOX}).json()

    by_provider = {a["provider"]: a for a in body["attempts"]}
    assert by_provider["CDSE"]["result"] == "not_reached"
    assert by_provider["CDSE"]["inferred"] is True, \
        "a CDSE failure we never received must not be reported as observed"
    assert by_provider["ASF"]["inferred"] is False
    assert "LocalCache" not in by_provider, "members after the winner are not attempted"


def test_provider_failure_is_reported_not_swallowed(client, monkeypatch):
    """An unreachable provider and an empty catalogue need different fixes, so
    they must not produce the same response."""
    import satellite.chain as chain_mod

    class _Broken:
        def search_scenes(self, **_kw):
            raise RuntimeError("token endpoint refused the credentials")

    monkeypatch.setattr(chain_mod, "SceneRetrievalChain", _Broken)
    body = client.get("/api/scenes/search", params={"bbox": GULF_BBOX}).json()

    assert body["total"] == 0 and body["scenes"] == []
    failure = body["attempts"][0]
    assert failure["ok"] is False
    assert failure["error_class"] == "RuntimeError"
    assert "credentials" in failure["detail"], "the provider's own words survive"


def test_search_hit_shape(client, monkeypatch):
    from datetime import datetime, timezone

    import satellite.chain as chain_mod

    class _Scene:
        scene_id = "S1A_IW_GRDH_1SDV_20230127T000231_x"
        platform = "Sentinel-1A"
        acquisition_time = datetime(2023, 1, 27, 0, 2, 31, tzinfo=timezone.utc)
        bbox = [-91.0, 28.0, -90.0, 29.0]
        product_type = "GRD_HD"
        polarisation = "VV+VH"
        orbit_direction = "DESCENDING"
        file_size_bytes = 1_000
        download_url = "https://example.invalid/x"
        file_path = None

    class _Result:
        provider = "CDSE"
        total_count = 1
        scenes = [_Scene()]

    class _Chain:
        def search_scenes(self, **_kw):
            return _Result()

    monkeypatch.setattr(chain_mod, "SceneRetrievalChain", _Chain)
    hit = client.get("/api/scenes/search", params={"bbox": GULF_BBOX}).json()["scenes"][0]

    assert hit["product_id"] == _Scene.scene_id
    assert hit["bbox"] == [-91.0, 28.0, -90.0, 29.0]
    assert hit["polarisation"] == "VV+VH"
    # A catalogue entry is not a file. Conflating the two is how a UI offers
    # "run this" for something nobody has downloaded.
    assert hit["cached_path"] is None


# --------------------------------------------------------------------------
# live provider (skippable)
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.getenv("OT_SKIP_NETWORK_TESTS") == "1",
                    reason="network tests disabled")
def test_live_search_returns_real_products(client):
    """One recorded live search. A provider outage is not a code defect, so
    this reports rather than fails when nothing comes back."""
    r = client.get("/api/scenes/search", params={
        "bbox": GULF_BBOX, "start": "2023-01-01T00:00:00Z",
        "end": "2023-01-31T23:59:59Z", "top": 5})
    assert r.status_code == 200
    body = r.json()

    if not body["scenes"]:
        pytest.skip(f"no provider answered: {body['attempts']}")

    assert body["provider"] in ("CDSE", "ASF", "LocalCache")
    for hit in body["scenes"]:
        assert hit["product_id"].startswith("S1")
        assert hit["acquired_utc"], "a real product carries its acquisition time"
