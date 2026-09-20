"""The SAR Image Database must describe scenes honestly and never invent
the metadata an upload lacks.

Pinned here:

  * a Sentinel-1 product identifier makes a scene MEASURED; a corpus chip is
    REFERENCE with an ASSIGNED time and place, and says so; the smoke-test
    raster is SYNTHETIC. "real" is never claimed for something nobody checked.
  * an upload without its metadata answers ``metadata_required`` and lists
    what is missing -- it does not fill anything in;
  * a georeferenced raster's own footprint outranks a typed bbox;
  * a completed upload is a contract-clean scene_meta.json (the frozen
    SceneMeta contract forbids extra keys), so the EXISTING pipeline can run it;
  * a picture is refused for analysis rather than run through a detector
    trained on calibrated backscatter.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))
sys.path.insert(0, str(REPO_ROOT))

rasterio = pytest.importorskip("rasterio")

from backend.api import sar_database as S  # noqa: E402


# --------------------------------------------------------------- provenance --


def test_product_identifier_is_parsed_not_guessed():
    f = S.product_facts("S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1_COG")
    assert f["platform"] == "Sentinel-1A"
    assert f["product_type"] == "GRDH"
    assert f["absolute_orbit"] == 46685
    assert f["sensing_start_utc"] == "2023-01-08T00:10:08Z"
    # a corpus label is not a product identifier: nothing is stated
    assert S.product_facts("S1A_IW_GRDH_GOM") == {}
    assert S.product_facts(None) == {}


@pytest.mark.parametrize("scene_id, raster, entry, label, basis", [
    ("S1A_IW_GRDH_1SDV_20230108T001008_20230108T001033_046685_059887_E5A1", "data/scenes/x/s.tif", None, "REAL", "measured"),
    ("S1A_IW_GRDH_GOM", "data/raw/trujillo/part3/Images/Oil/00072.tif", None, "REFERENCE", "assigned"),
    ("S1A_IW_GRDH_BALI", "data/scenes/b.tif", {"provenance": "corpus_train", "time_basis": "assigned"}, "REFERENCE", "assigned"),
    ("MOCK", "contracts/mocks/scene_sigma0_db.tif", {"provenance": "mock"}, "SYNTHETIC", "synthetic"),
    ("SOMETHING_ELSE", "data/scenes/u.tif", None, "UNVERIFIED", "assigned"),
])
def test_classification_never_claims_more_than_is_known(scene_id, raster, entry, label, basis):
    meta = {"scene_id": scene_id, "file_path": raster, "source": "cached"}
    c = S.classify(meta, Path("data/scenes/x/scene_meta.json"), entry, None)
    assert (c["label"], c["geo_basis"]) == (label, basis)


def test_the_store_lists_real_scenes_with_metadata_and_basis():
    rows = S.all_rows()
    if not rows:
        pytest.skip("no scenes on this host")
    ids = [r["scene_id"] for r in rows]
    assert len(ids) == len(set(ids)), "one row per scene"
    for r in rows:
        assert r["label"] in {"REAL", "REFERENCE", "SYNTHETIC", "UPLOADED", "UNVERIFIED"}
        assert r["geo_basis_note"], "every row states the basis of its position"
        if r["bbox"]:
            w, s, e, n = r["bbox"]
            assert -180 <= w < e <= 180 and -90 <= s < n <= 90
            assert r["center"] == [round((w + e) / 2, 5), round((s + n) / 2, 5)]
        if r["label"] == "REAL":
            assert r["geo_basis"] == "measured" and r["platform"]


# ------------------------------------------------------------------ uploads --


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(S, "REPO_ROOT", tmp_path)
    return tmp_path


def _tif(path: Path, georef: bool, db: bool = True) -> Path:
    from rasterio.transform import from_bounds
    data = (np.random.default_rng(0).normal(-18, 4, (64, 64)) if db
            else np.random.default_rng(0).uniform(0, 255, (64, 64))).astype("float32")
    kw = dict(driver="GTiff", width=64, height=64, count=1, dtype="float32")
    if georef:
        kw.update(crs="EPSG:4326", transform=from_bounds(-91.0, 27.5, -90.5, 28.0, 64, 64))
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **kw) as dst:
        dst.write(data, 1)
    return path


def _state(up_dir: Path, facts: dict, metadata: dict, basis: dict) -> dict:
    return {"upload_id": up_dir.name, "kind": "raster", "raster_name": "raster.tif", "caveats": [],
            "raster_facts": facts, "metadata": metadata, "metadata_basis": basis}


def test_a_raster_without_metadata_is_not_completed_for_the_user(store):
    up = store / "uploads" / "up-0000000001"
    _tif(up / "raster.tif", georef=False)
    facts = S._inspect_raster(up / "raster.tif")
    assert facts["georeferenced_raster"] is False and "bbox" not in facts
    out = S._write_scene(up, _state(up, facts, {"scene_id": "USER_SCENE"}, {}))
    assert out["status"] == "metadata_required"
    assert set(out["missing"]) == {"acquired_utc", "bbox", "polarisation"}
    assert not (up / "scene_meta.json").exists(), "no scene is written from guesses"


def test_the_rasters_own_footprint_is_read_and_a_clean_contract_is_written(store):
    from contracts.schemas.scene import SceneMeta
    up = store / "uploads" / "up-0000000002"
    _tif(up / "raster.tif", georef=True)
    facts = S._inspect_raster(up / "raster.tif")
    assert facts["georeferenced_raster"] is True
    assert facts["bbox"] == pytest.approx([-91.0, 27.5, -90.5, 28.0], abs=1e-4)
    assert facts["looks_like_db"] is True
    md = {"scene_id": "USER_SCENE", "bbox": facts["bbox"], "acquired_utc": "2023-01-08T00:10:08Z", "polarisation": "VV"}
    out = S._write_scene(up, _state(up, facts, md, {"bbox": "raster"}))
    assert out["status"] == "ready" and out["missing"] == []
    meta = json.loads((up / "scene_meta.json").read_text())
    SceneMeta.model_validate(meta)                       # extra keys would raise
    assert meta["provider_used"] == "UserUpload"
    assert meta["source"] != "real", "an unverified upload never badges as real"
    assert (store / meta["file_path"]).exists()


def test_an_ungeoreferenced_raster_is_placed_only_where_the_user_said(store):
    up = store / "uploads" / "up-0000000003"
    _tif(up / "raster.tif", georef=False)
    facts = S._inspect_raster(up / "raster.tif")
    md = {"scene_id": "USER_SCENE", "bbox": [54.0, 25.0, 54.5, 25.5], "acquired_utc": "2020-01-01T00:00:00Z", "polarisation": "VH"}
    out = S._write_scene(up, _state(up, facts, md, {"bbox": "user_supplied"}))
    assert out["status"] == "ready"
    meta = json.loads((up / "scene_meta.json").read_text())
    with rasterio.open(store / meta["file_path"]) as src:
        assert str(src.crs) == "EPSG:4326"
        assert list(src.bounds) == pytest.approx([54.0, 25.0, 54.5, 25.5])
        assert "user-supplied" in src.tags()["OCEANTRACE_GEOREF"]
    assert (up / "raster.tif").exists(), "the original upload is kept"


def test_a_raster_that_is_not_in_db_says_so(store):
    up = store / "uploads" / "up-0000000004"
    _tif(up / "raster.tif", georef=True, db=False)
    assert S._inspect_raster(up / "raster.tif")["looks_like_db"] is False


@pytest.mark.parametrize("text", ["1,2,3", "10,5,5,10", "-200,0,10,10", "a,b,c,d"])
def test_bad_footprints_are_rejected(text):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        S._parse_bbox(text)
    assert e.value.status_code == 422


def test_times_are_normalised_to_utc_and_the_future_is_refused():
    from fastapi import HTTPException
    assert S._parse_time("2023-01-08T05:40:08+05:30") == "2023-01-08T00:10:08Z"
    with pytest.raises(HTTPException):
        S._parse_time("2999-01-01T00:00:00Z")
    with pytest.raises(HTTPException):
        S._parse_time("yesterday")


# --------------------------------------------------------------------- HTTP --


PASSWORD = "sar-db-test-password"


@pytest.fixture(scope="module")
def http(tmp_path_factory):
    """The app on a throwaway database (the suite refuses the live one)."""
    import os
    root = tmp_path_factory.mktemp("sar_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'sar.db').as_posix()}"
    os.environ["SECRET_KEY"] = "s" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.api import sar_database as fresh
    from backend.core import security
    from backend.main import app
    from backend.models.db import SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        for role in ("auditor", "analyst"):
            db.add(User(email=f"sar-{role}@example.invalid", password_hash=security.hash_password(PASSWORD),
                        role=role, active=True))
        db.commit()
    fresh.UPLOADS_DIR = root / "uploads"
    fresh.REPO_ROOT = root
    with TestClient(app, base_url="https://testserver") as client:
        yield client
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATA_ROOT", None)


def test_upload_needs_a_role_and_reading_does_not(tmp_path, http):
    TEST_ADMIN_PASSWORD = PASSWORD
    tif = _tif(tmp_path / "in.tif", georef=True).read_bytes()

    if True:
        c = http
        c.cookies.clear()
        assert c.get("/api/sar/scenes").status_code == 401, "the database is not public"
        c.post("/api/auth/login", json={"email": "sar-auditor@example.invalid", "password": TEST_ADMIN_PASSWORD})
        assert c.get("/api/sar/scenes").status_code == 200
        r = c.post("/api/sar/upload", files={"raster": ("in.tif", io.BytesIO(tif), "image/tiff")})
        assert r.status_code == 403, "an auditor reads; an auditor does not add scenes"

        c.cookies.clear()
        c.post("/api/auth/login", json={"email": "sar-analyst@example.invalid", "password": TEST_ADMIN_PASSWORD})
        r = c.post("/api/sar/upload", files={"raster": ("in.tif", io.BytesIO(tif), "image/tiff")})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "metadata_required"
        assert body["metadata_basis"]["bbox"] == "raster"
        assert set(body["missing"]) == {"acquired_utc", "polarisation"}

        # a typed bbox cannot override the raster's own georeference
        done = c.post(f"/api/sar/upload/{body['upload_id']}/metadata",
                      json={"acquired_utc": "2023-01-08T00:10:08Z", "polarisation": "vv", "bbox": "0,0,1,1"}).json()
        assert done["status"] == "ready"
        assert done["metadata"]["bbox"] == pytest.approx([-91.0, 27.5, -90.5, 28.0], abs=1e-4)
        assert done["metadata"]["polarisation"] == "VV"

        pic = c.post("/api/sar/upload", files={"raster": ("look.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")})
        assert pic.status_code == 201 and pic.json()["status"] == "unsupported"
        assert c.post("/api/sar/upload", files={"raster": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")}).status_code == 415
