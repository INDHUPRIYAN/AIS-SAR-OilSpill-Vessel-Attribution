"""One search box across runs, incidents, investigations, vessels and scenes.

PROMPT 19. The command palette is only as trustworthy as this endpoint, and a
search box is unusually good at lying quietly: it answers every query with a
plausible list, and a wrong list looks exactly like a right one.

What these defend:

* **no fuzzy matching.** An MMSI is nine digits. If `367653160` is typed and
  the archive holds `367653161`, the honest answer is "nothing", not the
  neighbour -- a near-miss on an identifier is a different vessel, and a
  palette that offers it invites an analyst to open the wrong dossier;
* **the tier order is the stated one.** The endpoint publishes "exact, then
  prefix, then substring" in its own response; if the sort drifted from the
  sentence, the sentence would become a lie the UI repeats;
* **a vessel with no name shows its MMSI**, never "Unknown Vessel". Absence of
  identity in MarineCadastre is a fact about the source, and a placeholder
  reads like a record;
* **every row can say where it came from.** `kind` + `id` + a `context` built
  from real columns, so no row is a summary somebody invented;
* **the endpoint requires a session**, like every other `/api` route.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture(scope="module")
def client(sign_in_helper, tmp_path_factory):
    """A client over a temp repo layout: temp DB, temp DATA_ROOT, and a scene
    catalogue where the app expects to find one (`<data_root>/../main_system/
    config/scene_catalog.json`), so the scene branch is exercised against a
    file this test owns rather than the repo's real catalogue."""
    fake_repo = tmp_path_factory.mktemp("searchrepo")
    data_root = fake_repo / "data"
    (data_root / "scenes" / "S1A_TESTCACHE").mkdir(parents=True)
    (fake_repo / "main_system" / "config").mkdir(parents=True)
    (fake_repo / "main_system" / "config" / "scene_catalog.json").write_text(
        json.dumps({"scenes": [
            {"id": "chennai-2017-01-29", "label": "Chennai / Ennore, India",
             "provenance": "sentinel1_real", "time_basis": "measured"},
            {"id": "emed-oil-00000", "label": "Eastern Mediterranean (held-out)",
             "provenance": "corpus_heldout", "time_basis": "assigned"},
        ]}), encoding="utf-8")
    (data_root / "scenes" / "S1A_TESTCACHE" / "scene_meta.json").write_text(
        json.dumps({"scene_id": "S1A_IW_GRDH_TESTCACHE_0001",
                    "source": "sentinel1", "acquired_utc": "2023-01-08T00:10:08Z"}),
        encoding="utf-8")

    os.environ["DATABASE_URL"] = f"sqlite:///{(fake_repo / 's.db').as_posix()}"
    os.environ["DATA_ROOT"] = str(data_root)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.main import app

    with TestClient(app, base_url="https://testserver") as c:
        sign_in_helper(c)
        _seed()
        yield c
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("DATA_ROOT", None)


def _seed() -> None:
    from backend.models.db import (Incident, Investigation, Run, SessionLocal,
                                   Vessel)

    t0 = datetime(2023, 1, 8, 0, 10, 8, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(Investigation(id="inv-gulf-flagship", name="Gulf flagship",
                             scene_id="S1A_IW_GRDH_1SDV_20230108T001008_E5A1_COG"))
        db.add(Investigation(id="inv-baltic", name="Baltic sweep",
                             scene_id="S1A_BALTIC_0001"))
        db.add(Run(id="inv-gulf-flagship-20230108-2day",
                   investigation_id="inv-gulf-flagship",
                   scene_id="S1A_IW_GRDH_1SDV_20230108T001008_E5A1_COG",
                   status="complete", started_utc=t0, stages_total=5,
                   stages_real=5))
        db.add(Run(id="inv-baltic-20260101-aaaa", investigation_id="inv-baltic",
                   scene_id="S1A_BALTIC_0001", status="failed",
                   started_utc=t0 - timedelta(days=1), stages_total=5,
                   stages_real=2))
        db.add(Incident(id="INC-2023-001", title="Gulf of Mexico slick",
                        status="open", region="Gulf of Mexico"))
        # The four ranked flagship vessels carry no static identity in
        # MarineCadastre. One named vessel alongside them, so the "label falls
        # back to the MMSI" behaviour is distinguishable from "no names at all".
        db.add(Vessel(mmsi=367653160, name=None, source="real",
                      vessel_type="cargo"))
        db.add(Vessel(mmsi=367653161, name="ATLANTIC TRADER", source="real"))
        db.commit()


def _search(client, q, **params):
    r = client.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------
# it finds the things it says it finds
# --------------------------------------------------------------------------

def test_a_run_id_finds_that_run(client):
    body = _search(client, "inv-gulf-flagship-20230108-2day")
    runs = [h for h in body["results"] if h["kind"] == "run"]
    assert [h["id"] for h in runs] == ["inv-gulf-flagship-20230108-2day"]
    assert runs[0]["route"].endswith("run=inv-gulf-flagship-20230108-2day")


def test_every_kind_is_reachable_from_one_box(client):
    """The palette's whole claim is that one box reaches everything."""
    found = {
        "run": _search(client, "inv-gulf-flagship-20230108-2day"),
        "incident": _search(client, "INC-2023-001"),
        "investigation": _search(client, "Baltic sweep"),
        "vessel": _search(client, "367653160"),
        "scene": _search(client, "chennai-2017-01-29"),
    }
    for kind, body in found.items():
        assert any(h["kind"] == kind for h in body["results"]), \
            f"nothing of kind {kind} came back: {body['results']}"


def test_the_scene_cache_on_disk_is_searchable_too(client):
    """Not just the curated catalogue -- a scene that was fetched and cached
    is a scene the analyst can be looking for."""
    body = _search(client, "TESTCACHE")
    ids = [h["id"] for h in body["results"] if h["kind"] == "scene"]
    assert "S1A_IW_GRDH_TESTCACHE_0001" in ids


def test_a_substring_of_a_scene_id_finds_the_run(client):
    body = _search(client, "20230108T001008", kinds="run")
    assert [h["id"] for h in body["results"]] == ["inv-gulf-flagship-20230108-2day"]


# --------------------------------------------------------------------------
# no fuzzy matching
# --------------------------------------------------------------------------

def test_a_near_miss_on_an_mmsi_is_not_a_result(client):
    """367653162 exists in neither row. Both seeded MMSIs are one digit away,
    which is exactly the case a Levenshtein matcher would 'helpfully' answer."""
    body = _search(client, "367653162", kinds="vessel")
    assert body["results"] == []
    assert body["count"] == 0


def test_a_typo_in_a_word_returns_nothing_rather_than_the_nearest_thing(client):
    assert _search(client, "Balitc")["results"] == []


def test_the_endpoint_states_its_own_matching_rule(client):
    """The UI renders this sentence. If the sort ever drifts from it, the
    palette repeats a claim the endpoint no longer honours -- so the sentence
    is asserted here beside the behaviour it describes."""
    body = _search(client, "gulf")
    assert "No fuzzy matching" in body["matching"]
    assert "Exact ids rank first" in body["matching"]


# --------------------------------------------------------------------------
# the stated ranking
# --------------------------------------------------------------------------

def test_an_exact_id_outranks_a_prefix(client):
    """`inv-baltic` is an exact investigation id and a prefix of the run id,
    so one query exercises the boundary the response sentence promises."""
    body = _search(client, "inv-baltic")
    tiers = {(h["kind"], h["id"]): h["tier"] for h in body["results"]}
    assert tiers[("investigation", "inv-baltic")] == 0
    assert tiers[("run", "inv-baltic-20260101-aaaa")] == 1
    # and the exact match is first in the list, not merely tagged
    assert body["results"][0]["id"] == "inv-baltic"


def test_a_substring_match_ranks_below_a_prefix_match(client):
    """`baltic` prefixes nothing and is a substring of both ids -- while
    `inv-baltic` prefixes the run id. The two queries must land in different
    tiers or the tier field is decorative."""
    prefixed = _search(client, "inv-baltic", kinds="run")["results"][0]
    substring = _search(client, "baltic", kinds="run")["results"][0]
    assert prefixed["tier"] == 1
    assert substring["tier"] == 2


def test_matching_is_case_insensitive(client):
    assert _search(client, "GULF OF MEXICO")["results"], \
        "an incident title typed in caps found nothing"


def test_results_are_stable_between_identical_queries(client):
    """The palette must not reshuffle under the cursor between keystrokes."""
    first = [(h["kind"], h["id"]) for h in _search(client, "inv")["results"]]
    second = [(h["kind"], h["id"]) for h in _search(client, "inv")["results"]]
    assert first == second


# --------------------------------------------------------------------------
# rows that can say where they came from
# --------------------------------------------------------------------------

def test_a_vessel_with_no_name_shows_its_mmsi_and_says_so(client):
    """MarineCadastre carried no static identity for the flagship's four
    ranked MMSIs. 'Unknown Vessel' would read like a record; the number is
    the only thing the source actually supplied."""
    row = next(h for h in _search(client, "367653160", kinds="vessel")["results"]
               if h["id"] == "367653160")
    assert row["label"] == "367653160"
    assert "no name in the source data" in row["context"]


def test_a_named_vessel_shows_the_name_the_source_gave(client):
    row = next(h for h in _search(client, "367653161", kinds="vessel")["results"]
               if h["id"] == "367653161")
    assert row["label"] == "ATLANTIC TRADER"
    assert "no name in the source data" not in row["context"]


def test_a_run_row_carries_its_real_stage_count(client):
    """The context line is built from columns, not composed prose: a run that
    ran 2 of 5 stages for real must not be summarised as 'complete'."""
    row = next(h for h in _search(client, "inv-baltic-20260101-aaaa")["results"]
               if h["kind"] == "run")
    assert "2/5 real" in row["context"]
    assert "failed" in row["context"]


def test_every_row_carries_kind_id_and_a_route(client):
    for h in _search(client, "inv")["results"]:
        assert h["kind"] and h["id"] and h["route"], h


def test_a_scene_row_carries_its_provenance_class(client):
    """A held-out corpus scene and a real Sentinel-1 acquisition are not
    interchangeable, and the palette row is where that first shows."""
    rows = {h["id"]: h for h in _search(client, "-", kinds="scene")["results"]}
    assert "sentinel1_real" in rows["chennai-2017-01-29"]["context"]
    assert "corpus_heldout" in rows["emed-oil-00000"]["context"]


# --------------------------------------------------------------------------
# the parameters
# --------------------------------------------------------------------------

def test_kinds_narrows_the_search(client):
    body = _search(client, "inv", kinds="run")
    assert {h["kind"] for h in body["results"]} == {"run"}


def test_an_unknown_kind_is_ignored_rather_than_silently_matching_everything(client):
    """`kinds=vessle` is a typo. Answering it with every kind would look like
    the filter worked."""
    assert _search(client, "inv", kinds="vessle")["results"] == []


def test_limit_is_honoured_and_count_matches_the_page(client):
    body = _search(client, "inv", limit=1)
    assert len(body["results"]) == 1
    assert body["count"] == 1


def test_an_empty_query_is_rejected_rather_than_listing_everything(client):
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_search_requires_a_session(client):
    """Same guard as every other /api route: the vessel index is not public."""
    cookies = dict(client.cookies)
    try:
        client.cookies.clear()
        assert client.get("/api/search", params={"q": "inv"}).status_code == 401
    finally:
        # Module-scoped client: without this every later test in the file
        # would assert against a 401 it did not intend.
        client.cookies.update(cookies)
    assert client.get("/api/search", params={"q": "inv"}).status_code == 200
