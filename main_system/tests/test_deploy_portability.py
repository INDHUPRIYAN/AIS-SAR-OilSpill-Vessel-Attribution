"""A deployment must serve runs that were recorded on a different machine.

Sealed artefacts record absolute paths from the host that produced them (a
Windows laptop), and they are content-hashed, so they cannot be rewritten for
a Linux server. `host_path` re-anchors them; `mount_spa` lets one container
serve UI + API on one origin. Both are exercised here without the real data.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import paths
from backend.core.spa import frontend_dist, mount_spa


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake host: repo at tmp/repo, DATA_ROOT mounted somewhere else."""
    repo = tmp_path / "repo"
    data = tmp_path / "mnt" / "data"
    (data / "scenes" / "S1A_GULF").mkdir(parents=True)
    (data / "scenes" / "S1A_GULF" / "scene_sigma0_db.tif").write_bytes(b"x")
    (repo / "contracts" / "mocks").mkdir(parents=True)
    (repo / "contracts" / "mocks" / "scene_meta.json").write_text("{}")
    monkeypatch.setattr(paths, "REPO_ROOT", repo)
    monkeypatch.setattr(paths, "get_settings",
                        lambda: SimpleNamespace(data_root=data))
    return SimpleNamespace(repo=repo, data=data)


def test_windows_data_path_is_reanchored_on_data_root(host):
    recorded = ("C:\\Users\\Indhu Priyan\\Documents\\GitHub\\AIS-SAR-OilSpill-"
                "Vessel-Attribution\\data\\scenes\\S1A_GULF\\scene_sigma0_db.tif")
    assert paths.host_path(recorded) == (
        host.data / "scenes" / "S1A_GULF" / "scene_sigma0_db.tif")


def test_foreign_repo_path_is_reanchored_on_repo_root(host):
    recorded = "/home/someone/checkout/contracts/mocks/scene_meta.json"
    assert paths.host_path(recorded) == (
        host.repo / "contracts" / "mocks" / "scene_meta.json")


def test_existing_path_is_returned_untouched(host):
    real = host.data / "scenes" / "S1A_GULF" / "scene_sigma0_db.tif"
    assert paths.host_path(str(real)) == real


def test_relative_path_is_left_to_the_caller(host):
    # "../data/..." is resolved by each caller against its own anchor.
    assert paths.host_path("../data/scenes/x.tif") == Path("../data/scenes/x.tif")


def test_unresolvable_path_comes_back_as_recorded(host):
    # The caller's own "missing" message must still name what was recorded.
    recorded = "D:\\elsewhere\\data\\scenes\\gone.tif"
    assert paths.host_path(recorded) == Path(recorded)


@pytest.fixture
def spa_client(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    assert frontend_dist(str(dist)) == dist
    mount_spa(app, dist)
    return TestClient(app)


def test_spa_serves_index_assets_and_deep_links(spa_client):
    assert spa_client.get("/").text == "<html>app</html>"
    assert spa_client.get("/assets/app.js").text == "console.log(1)"
    deep = spa_client.get("/investigation?run=inv-gulf-flagship-20230108-2day")
    assert deep.status_code == 200 and deep.text == "<html>app</html>"


def test_spa_never_swallows_backend_routes(spa_client):
    assert spa_client.get("/api/ping").json() == {"ok": True}
    # An unknown API route must stay a 404, not become index.html with a 200.
    assert spa_client.get("/api/no-such-route").status_code == 404
    assert spa_client.get("/ws/engines/x").status_code == 404


def test_unbuilt_dist_is_ignored(tmp_path):
    assert frontend_dist(None) is None
    assert frontend_dist(str(tmp_path)) is None  # no index.html
