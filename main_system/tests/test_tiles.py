"""SAR tiles and viewport decimation.

PROMPT 18. The workspace could draw a slick outline over a basemap but never
the SAR itself. A polygon over OpenStreetMap is a claim; the backscatter is the
evidence, and an analyst who cannot see the pixels cannot check the outline
against them.

What these defend:

* **georeferencing.** A tile that renders in the wrong place is worse than no
  tile: it puts a slick over the wrong water. Checked against the scene's own
  corner coordinates at two zooms.
* **nodata is transparent, never black.** Black reads as very dark water, which
  is exactly what oil looks like in SAR -- the one confusion this system cannot
  afford to introduce.
* **the stretch is the training range.** What the analyst sees is what the
  segmenter saw, and the response headers say which values were applied so a
  screenshot cannot silently misrepresent contrast.
* **decimation never drops a ranked suspect.** A suspect that vanished when the
  analyst zoomed out would be a map disagreeing with the ranking beside it.

Tile tests run against the frozen flagship raster and skip cleanly without it.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

FLAGSHIP_POINTER = REPO_ROOT / "dev_evidence" / "P14" / "flagship.json"
MAX_TILE_BYTES = 256 * 1024


def _flagship_run_id():
    if not FLAGSHIP_POINTER.exists():
        return None
    return json.loads(FLAGSHIP_POINTER.read_text(encoding="utf-8"))["run_id"]


def _deg2tile(lon: float, lat: float, z: int):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi)
            / 2.0 * n)
    return x, y


@pytest.fixture(scope="module")
def client(sign_in_helper, tmp_path_factory):
    root = tmp_path_factory.mktemp("tiles")
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 't.db').as_posix()}"
    os.environ["DATA_ROOT"] = str(REPO_ROOT / "data")
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        yield c
    os.environ.pop("DATABASE_URL", None)


@pytest.fixture(scope="module")
def run_id(client):
    rid = _flagship_run_id()
    if rid is None:
        pytest.skip("no flagship pointer in this checkout")
    if client.get(f"/api/tiles/{rid}/info").status_code != 200:
        pytest.skip("the flagship raster is not on this host")
    return rid


@pytest.fixture(scope="module")
def info(client, run_id):
    return client.get(f"/api/tiles/{run_id}/info").json()


# --------------------------------------------------------------------------
# tile info
# --------------------------------------------------------------------------

def test_info_publishes_what_a_client_needs(info):
    assert info["tile_url"].endswith("/{z}/{x}/{y}.png")
    assert info["tile_size"] == 256
    assert len(info["bounds_wgs84"]) == 4
    assert info["default_stretch_db"] == [-35.0, 0.0], \
        "the default stretch must be the frozen training clip range"
    assert "the segmenter was trained on" in info["stretch_note"]
    assert "never black" in info["nodata_note"]


def test_info_for_a_run_without_a_raster_says_so(client):
    r = client.get("/api/tiles/no-such-run/info")
    assert r.status_code == 404
    assert "no calibrated raster" in r.json()["detail"]


# --------------------------------------------------------------------------
# georeferencing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("z", [10, 12])
def test_a_tile_inside_the_scene_has_pixels(client, run_id, info, z):
    """Checked at two zooms: a projection error that survives one zoom rarely
    survives two."""
    import numpy as np
    from PIL import Image

    west, south, east, north = info["bounds_wgs84"]
    x, y = _deg2tile((west + east) / 2, (south + north) / 2, z)

    r = client.get(f"/api/tiles/{run_id}/{z}/{x}/{y}.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"

    pixels = np.array(Image.open(io.BytesIO(r.content)))
    assert pixels.shape == (256, 256, 4)
    opaque = (pixels[..., 3] > 0).mean()
    assert opaque > 0.9, f"a tile at the scene centre is only {opaque:.0%} opaque"


def test_a_tile_outside_the_scene_is_transparent_not_black(client, run_id, info):
    """The distinction that matters: black is what oil looks like."""
    import numpy as np
    from PIL import Image

    west, south, east, north = info["bounds_wgs84"]
    x, y = _deg2tile(west - 3.0, (south + north) / 2, 10)

    r = client.get(f"/api/tiles/{run_id}/10/{x}/{y}.png")
    assert r.status_code == 200

    pixels = np.array(Image.open(io.BytesIO(r.content)))
    assert (pixels[..., 3] == 0).all(), \
        "a tile outside the scene has opaque pixels -- it would read as dark water"


def test_tiles_stay_within_the_size_budget(client, run_id, info):
    west, south, east, north = info["bounds_wgs84"]
    for z in (8, 10, 12, 14):
        x, y = _deg2tile((west + east) / 2, (south + north) / 2, z)
        r = client.get(f"/api/tiles/{run_id}/{z}/{x}/{y}.png")
        assert r.status_code == 200
        assert len(r.content) <= MAX_TILE_BYTES, \
            f"z{z} tile is {len(r.content)} bytes, over the 256 KB budget"


# --------------------------------------------------------------------------
# the stretch
# --------------------------------------------------------------------------

def test_the_applied_stretch_is_reported_in_the_headers(client, run_id, info):
    """A screenshot must not be able to misrepresent contrast silently."""
    west, south, east, north = info["bounds_wgs84"]
    x, y = _deg2tile((west + east) / 2, (south + north) / 2, 10)

    default = client.get(f"/api/tiles/{run_id}/10/{x}/{y}.png")
    assert default.headers["X-Stretch-Db-Min"] == "-35.0"
    assert default.headers["X-Stretch-Db-Max"] == "0.0"

    custom = client.get(f"/api/tiles/{run_id}/10/{x}/{y}.png",
                        params={"db_min": -25, "db_max": -5})
    assert custom.status_code == 200
    assert custom.headers["X-Stretch-Db-Min"] == "-25.0"
    assert custom.content != default.content, \
        "a different stretch produced identical pixels"


def test_an_inverted_stretch_is_refused(client, run_id, info):
    west, south, east, north = info["bounds_wgs84"]
    x, y = _deg2tile((west + east) / 2, (south + north) / 2, 10)
    r = client.get(f"/api/tiles/{run_id}/10/{x}/{y}.png",
                   params={"db_min": 0, "db_max": -35})
    assert r.status_code == 400


@pytest.mark.parametrize("z,x,y", [(23, 0, 0), (10, 99999, 0), (10, 0, 99999)])
def test_impossible_tile_coordinates_are_refused(client, run_id, z, x, y):
    assert client.get(f"/api/tiles/{run_id}/{z}/{x}/{y}.png").status_code == 400


def test_tiles_require_a_session(client, run_id, info, sign_in_helper):
    west, south, east, north = info["bounds_wgs84"]
    x, y = _deg2tile((west + east) / 2, (south + north) / 2, 10)
    client.cookies.clear()
    try:
        assert client.get(f"/api/tiles/{run_id}/10/{x}/{y}.png").status_code == 401
    finally:
        # The client is module-scoped. Leaving it signed out would make every
        # later test in this file assert against a 401 it did not intend --
        # which is how a suite reports a working feature as broken.
        sign_in_helper(client)


# --------------------------------------------------------------------------
# viewport decimation
# --------------------------------------------------------------------------

def test_decimation_thins_points_without_dropping_vessels(client):
    """`zoom` reduces vertices, never vessels."""
    rid = _flagship_run_id()
    if rid is None:
        pytest.skip("no flagship pointer")

    full = client.get(f"/api/runs/{rid}/vessels_geojson")
    if full.status_code != 200:
        pytest.skip("no vessels in this run")
    thin = client.get(f"/api/runs/{rid}/vessels_geojson", params={"zoom": 4})
    assert thin.status_code == 200

    full_body, thin_body = full.json(), thin.json()
    assert len(thin_body["features"]) == len(full_body["features"]), \
        "decimation dropped a vessel"
    assert thin_body["metadata"]["viewport"]["point_stride"] > 1

    thinned = sum(len(f["geometry"]["coordinates"]) for f in thin_body["features"])
    whole = sum(len(f["geometry"]["coordinates"]) for f in full_body["features"])
    assert thinned < whole, "zoom=4 did not thin anything"


def test_a_thinned_track_still_reports_the_real_distance(client):
    """The number and the picture must not disagree: distance is computed from
    every point, not from the simplified line."""
    rid = _flagship_run_id()
    if rid is None:
        pytest.skip("no flagship pointer")

    full = client.get(f"/api/runs/{rid}/vessels_geojson")
    if full.status_code != 200:
        pytest.skip("no vessels in this run")
    thin = client.get(f"/api/runs/{rid}/vessels_geojson", params={"zoom": 4}).json()
    by_mmsi = {f["properties"]["mmsi"]: f["properties"]
               for f in full.json()["features"]}

    for feature in thin["features"]:
        props = feature["properties"]
        assert props["distance_km"] == by_mmsi[props["mmsi"]]["distance_km"]
        assert props["points_total"] >= props["points_drawn"]


def test_a_ranked_suspect_survives_a_viewport_that_excludes_it(client):
    """The first casualty of viewport culling is always the thing you were
    looking for."""
    rid = _flagship_run_id()
    if rid is None:
        pytest.skip("no flagship pointer")

    full = client.get(f"/api/runs/{rid}/vessels_geojson")
    if full.status_code != 200:
        pytest.skip("no vessels in this run")
    ranked = [f["properties"]["mmsi"] for f in full.json()["features"]
              if f["properties"].get("rank")]
    if not ranked:
        pytest.skip("this run ranked nobody")

    # A box in the middle of the Atlantic: nothing in this run touches it.
    away = client.get(f"/api/runs/{rid}/vessels_geojson",
                      params={"bbox": "-30,10,-29,11"}).json()
    kept = {f["properties"]["mmsi"] for f in away["features"]}

    assert set(ranked) <= kept, "a ranked suspect was culled by the viewport"
    assert away["metadata"]["viewport"]["ranked_never_culled"] is True
    assert away["metadata"]["viewport"]["tracks_culled_by_bbox"] > 0


def test_a_bad_bbox_is_refused(client):
    rid = _flagship_run_id()
    if rid is None:
        pytest.skip("no flagship pointer")
    r = client.get(f"/api/runs/{rid}/vessels_geojson", params={"bbox": "1,2,3"})
    assert r.status_code == 422
