"""AOI registry and watcher -- STAGE 0 (design doc v2 §4, §21, §27·8).

The watcher runs unattended and can start full pipeline runs on its own, so the
tests concentrate on the two ways that goes wrong quietly:

  * it replays -- a restart, or a second poll, re-opens investigations for
    scenes it already handled, and each duplicate is a full GPU run;
  * it lies about AIS -- an AOI with no public bulk coverage opens an
    investigation that does not say so, and Stage 6's synthetic fallback is
    discovered on stage instead of declared up front (§8, Standing Rule 9).

The search chain and the run starter are injected, so nothing here touches a
network or a GPU.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml


UTC = timezone.utc
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _cfg(tmp_path, aois, defaults=None):
    path = tmp_path / "aois.yaml"
    payload = {"version": 1, "aois": aois}
    if defaults:
        payload["defaults"] = defaults
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


BASE_AOI = {"id": "test-aoi", "name": "Test AOI",
            "bbox": [10.2, 55.2, 12.6, 57.2], "ais_region": "denmark",
            "poll_minutes": 60, "lookback_hours": 24,
            "auto_run": True, "enabled": True}


# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------


def test_loads_the_shipped_registry():
    """The file that actually ships must parse and validate."""
    from backend.services.scheduler.aoi import load_aois

    aois = load_aois()
    assert aois, "config/aois.yaml registers no AOIs"
    assert {a.id for a in aois} >= {"danish-straits", "chennai-coast"}


def test_shipped_registry_declares_ais_coverage_honestly():
    """§8: no public bulk AIS exists for Indian waters. The config must say so."""
    from backend.services.scheduler.aoi import load_aois

    by_id = {a.id: a for a in load_aois()}
    assert by_id["chennai-coast"].has_real_ais is False
    assert by_id["danish-straits"].has_real_ais is True


def test_defaults_fill_in_omitted_fields(tmp_path):
    from backend.services.scheduler.aoi import load_aois

    path = _cfg(tmp_path, [{"id": "minimal", "bbox": [10.0, 55.0, 11.0, 56.0]}],
                defaults={"poll_minutes": 15, "auto_run": False})
    aoi = load_aois(path)[0]
    assert aoi.poll_minutes == 15 and aoi.auto_run is False
    assert aoi.lookback_hours == 24        # builtin default, not in the file
    assert aoi.name == "minimal"           # falls back to the id


def test_a_transposition_that_leaves_latitude_out_of_range_is_rejected(tmp_path):
    """Standing Rule 1. Transposing a mid-latitude AOI puts lat above 90."""
    from backend.services.scheduler.aoi import AOIConfigError, load_aois

    # The Chennai AOI [79.9, 12.6, 80.9, 13.6] transposed: lat 79.9 and 80.9
    # are still legal, but this is the common shape and the range check on the
    # SECOND pair catches the version that matters -- a high-longitude AOI.
    path = _cfg(tmp_path, [{**BASE_AOI, "bbox": [10.0, 55.0, 11.0, 195.0]}])
    with pytest.raises(AOIConfigError, match="transposed"):
        load_aois(path)


def test_a_transposition_that_stays_in_range_is_NOT_detectable(tmp_path):
    """Documents the limit of this validator rather than pretending it has none.

    The Danish AOI [10.2, 55.2, 12.6, 57.2] transposed is [55.2, 10.2, 57.2,
    12.6] -- every value in range, both pairs correctly ordered. It is a
    perfectly valid bbox in the Arabian Sea, and no amount of numeric checking
    can tell it from an intended one.

    What catches it in practice is the AOI's `watch` state: a transposed AOI
    searches open ocean nobody registered and its `scenes_seen` stays at zero
    while `polls` climbs, which the /api/aois listing shows plainly. That is a
    monitoring answer, not a validation one, and this test exists so nobody
    later assumes the loader already handled it.
    """
    from backend.services.scheduler.aoi import load_aois

    path = _cfg(tmp_path, [{**BASE_AOI, "bbox": [55.2, 10.2, 57.2, 12.6]}])
    aoi = load_aois(path)[0]
    assert aoi.bbox == (55.2, 10.2, 57.2, 12.6)   # loads, by design


def test_inverted_bbox_is_rejected(tmp_path):
    from backend.services.scheduler.aoi import AOIConfigError, load_aois

    path = _cfg(tmp_path, [{**BASE_AOI, "bbox": [12.6, 55.2, 10.2, 57.2]}])
    with pytest.raises(AOIConfigError, match="lon_min must be"):
        load_aois(path)


def test_duplicate_ids_are_rejected(tmp_path):
    """Two AOIs sharing an id share watch state and replay scenes forever."""
    from backend.services.scheduler.aoi import AOIConfigError, load_aois

    path = _cfg(tmp_path, [BASE_AOI, {**BASE_AOI, "name": "Other"}])
    with pytest.raises(AOIConfigError, match="duplicate"):
        load_aois(path)


def test_non_positive_poll_interval_is_rejected(tmp_path):
    from backend.services.scheduler.aoi import AOIConfigError, load_aois

    path = _cfg(tmp_path, [{**BASE_AOI, "poll_minutes": 0}])
    with pytest.raises(AOIConfigError, match="must be positive"):
        load_aois(path)


def test_missing_registry_is_an_error_not_an_empty_list(tmp_path):
    """Silently watching nothing is the worst possible outcome here."""
    from backend.services.scheduler.aoi import AOIConfigError, load_aois

    with pytest.raises(AOIConfigError, match="no AOI registry"):
        load_aois(tmp_path / "absent.yaml")


# --------------------------------------------------------------------------
# the watcher
# --------------------------------------------------------------------------


class FakeScene:
    def __init__(self, scene_id, acquired, file_path=None):
        self.scene_id = scene_id
        self.acquisition_time = acquired
        self.file_path = file_path


class FakeResult:
    def __init__(self, scenes):
        self.scenes = scenes
        self.total_count = len(scenes)


@pytest.fixture(scope="module", autouse=True)
def _isolated_data_root(tmp_path_factory):
    """Point DATA_ROOT at a temp dir BEFORE `backend` is imported.

    The engine and settings are built at module import, so without this the
    watcher's state and its test investigations would land in the real
    data/oceantrace.db -- test rows in the operator's run history.
    """
    root = tmp_path_factory.mktemp("sched_data_root")
    os.environ["DATA_ROOT"] = str(root)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    yield root
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]


@pytest.fixture()
def db(_isolated_data_root):
    """A clean watcher-state DB per test."""
    from backend.models.db import (AoiWatch, Base, Investigation, SessionLocal,
                                   engine)

    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        session.query(AoiWatch).delete()
        session.query(Investigation).delete()
        session.commit()
        yield session


def _watcher(scenes, started=None, **kw):
    from backend.services.scheduler.watcher import AOIWatcher

    calls = []

    def search(bbox, start_time, end_time, top=10):
        calls.append({"bbox": bbox, "start": start_time, "end": end_time})
        return FakeResult(scenes)

    def start_run(inv_id, aoi, scene):
        if started is not None:
            started.append(inv_id)
        return f"{inv_id}-run"

    w = AOIWatcher(search=search, start_run=start_run, **kw)
    w.calls = calls
    return w


def _aoi(**over):
    from backend.services.scheduler.aoi import AOI

    base = dict(id="test-aoi", name="Test AOI", bbox=(10.2, 55.2, 12.6, 57.2),
                ais_region="denmark", poll_minutes=60, lookback_hours=24,
                auto_run=True, enabled=True, notes="")
    base.update(over)
    return AOI(**base)


def test_a_new_scene_opens_an_investigation_and_starts_a_run(db):
    started = []
    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    w = _watcher([scene], started)

    opened = w.poll_aoi(_aoi(), db, now=NOW)

    assert len(opened) == 1
    assert opened[0]["scene_id"] == "S1A_TEST_001"
    assert opened[0]["run_id"] == f"{opened[0]['investigation_id']}-run"
    assert started == [opened[0]["investigation_id"]]


def test_the_same_scene_is_not_opened_twice(db):
    """The failure this prevents: one duplicate full pipeline run per poll."""
    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    w = _watcher([scene])

    first = w.poll_aoi(_aoi(), db, now=NOW)
    second = w.poll_aoi(_aoi(), db, now=NOW + timedelta(hours=2))

    assert len(first) == 1 and second == []


def test_the_first_poll_is_bounded_by_lookback(db):
    """Enabling an AOI must not open an investigation for the whole archive."""
    old = FakeScene("S1A_OLD", NOW - timedelta(days=30))
    fresh = FakeScene("S1A_NEW", NOW - timedelta(hours=3))
    w = _watcher([old, fresh])

    opened = w.poll_aoi(_aoi(lookback_hours=24), db, now=NOW)

    assert [o["scene_id"] for o in opened] == ["S1A_NEW"]
    # ...and the search itself asked for the bounded window, not all of time.
    assert w.calls[0]["start"] == NOW - timedelta(hours=24)


def test_state_survives_a_restart(db):
    """A fresh watcher object must resume, not replay -- the state is in the DB."""
    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    _watcher([scene]).poll_aoi(_aoi(), db, now=NOW)

    reborn = _watcher([scene])          # as if the process restarted
    assert reborn.poll_aoi(_aoi(), db, now=NOW + timedelta(hours=2)) == []


def test_a_gap_between_polls_loses_nothing(db):
    """After the first poll the window runs from the high-water mark, not lookback."""
    first = FakeScene("S1A_ONE", NOW - timedelta(hours=2))
    w = _watcher([first])
    w.poll_aoi(_aoi(lookback_hours=6), db, now=NOW)

    # The watcher was down for three days; a scene arrived two days ago.
    later = NOW + timedelta(days=3)
    missed = FakeScene("S1A_TWO", NOW + timedelta(days=1))
    w2 = _watcher([first, missed])
    opened = w2.poll_aoi(_aoi(lookback_hours=6), db, now=later)

    assert [o["scene_id"] for o in opened] == ["S1A_TWO"]
    assert w2.calls[0]["start"] == first.acquisition_time


def test_scenes_are_handled_oldest_first(db):
    """Out-of-order provider results must not park the high-water mark ahead."""
    a = FakeScene("S1A_A", NOW - timedelta(hours=5))
    b = FakeScene("S1A_B", NOW - timedelta(hours=1))
    opened = _watcher([b, a]).poll_aoi(_aoi(), db, now=NOW)
    assert [o["scene_id"] for o in opened] == ["S1A_A", "S1A_B"]


def test_a_busy_poll_is_capped_and_the_rest_come_next_tick(db):
    """A wide lookback over a busy strait must not start ten GPU runs at once."""
    scenes = [FakeScene(f"S1A_{i}", NOW - timedelta(hours=10 - i))
              for i in range(5)]
    w = _watcher(scenes, max_scenes_per_poll=2)

    first = w.poll_aoi(_aoi(), db, now=NOW)
    assert [o["scene_id"] for o in first] == ["S1A_0", "S1A_1"]

    second = w.poll_aoi(_aoi(), db, now=NOW + timedelta(hours=2))
    assert [o["scene_id"] for o in second] == ["S1A_2", "S1A_3"]


def test_auto_run_false_opens_the_investigation_but_starts_nothing(db):
    started = []
    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    opened = _watcher([scene], started).poll_aoi(_aoi(auto_run=False), db, now=NOW)

    assert len(opened) == 1 and opened[0]["run_id"] is None
    assert started == []


def test_an_aoi_without_public_ais_says_so_on_the_investigation(db):
    """§8 + Standing Rule 9: synthetic AIS is declared up front, not discovered."""
    from backend.models.db import Investigation

    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    opened = _watcher([scene]).poll_aoi(
        _aoi(ais_region=None), db, now=NOW)

    assert opened[0]["ais_provenance"] == "synthetic"
    inv = db.get(Investigation, opened[0]["investigation_id"])
    assert json.loads(inv.notes)["ais_provenance"] == "synthetic"


def test_a_provider_failure_degrades_the_aoi_and_does_not_raise(db):
    """§11: no single dependency can halt a run -- or the watcher."""
    from backend.models.db import AoiWatch
    from backend.services.scheduler.watcher import AOIWatcher

    def boom(bbox, start_time, end_time, top=10):
        raise TimeoutError("CDSE search timed out after 30s")

    opened = AOIWatcher(search=boom).poll_aoi(_aoi(), db, now=NOW)

    assert opened == []
    state = db.get(AoiWatch, "test-aoi")
    assert state.status == "DEGRADED"
    assert state.last_error_class == "TIMEOUT"
    assert state.consecutive_failures == 1


def test_recovery_clears_the_degraded_state(db):
    from backend.models.db import AoiWatch
    from backend.services.scheduler.watcher import AOIWatcher

    AOIWatcher(search=lambda **kw: (_ for _ in ()).throw(
        ConnectionError("503 service unavailable"))).poll_aoi(_aoi(), db, now=NOW)
    assert db.get(AoiWatch, "test-aoi").last_error_class == "UNAVAILABLE"

    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=1))
    _watcher([scene]).poll_aoi(_aoi(), db, now=NOW + timedelta(hours=2))

    state = db.get(AoiWatch, "test-aoi")
    assert state.status == "WORKING" and state.consecutive_failures == 0


def test_a_failed_run_start_still_leaves_the_investigation(db):
    """Losing the investigation would lose the fact that a scene arrived at all."""
    from backend.models.db import Investigation
    from backend.services.scheduler.watcher import AOIWatcher

    def boom(inv_id, aoi, scene):
        raise RuntimeError("no GPU available")

    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    opened = AOIWatcher(search=lambda **kw: FakeResult([scene]),
                        start_run=boom).poll_aoi(_aoi(), db, now=NOW)

    assert len(opened) == 1 and opened[0]["run_id"] is None
    assert "no GPU available" in opened[0]["error"]
    assert db.get(Investigation, opened[0]["investigation_id"]) is not None


def test_scenes_without_a_usable_timestamp_are_skipped(db):
    """A scene we cannot place in time cannot be de-duplicated; skip it loudly."""
    good = FakeScene("S1A_GOOD", NOW - timedelta(hours=2))
    bad = FakeScene("S1A_BAD", None)
    opened = _watcher([bad, good]).poll_aoi(_aoi(), db, now=NOW)
    assert [o["scene_id"] for o in opened] == ["S1A_GOOD"]


def test_naive_acquisition_times_are_read_as_utc(db):
    """Standing Rule 1: a naive timestamp must not be shifted by the host zone."""
    naive = FakeScene("S1A_NAIVE", (NOW - timedelta(hours=2)).replace(tzinfo=None))
    opened = _watcher([naive]).poll_aoi(_aoi(), db, now=NOW)
    assert len(opened) == 1


# --------------------------------------------------------------------------
# a full sweep
# --------------------------------------------------------------------------


def test_tick_skips_disabled_and_not_yet_due_aois(db, tmp_path):
    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    w = _watcher([scene])

    aois = [_aoi(id="a1"), _aoi(id="a2", enabled=False)]
    first = w.tick(now=NOW, aois=aois)
    assert first.polled == ["a1"] and first.disabled == ["a2"]

    # 10 minutes later a1 is not due again (poll_minutes=60).
    second = w.tick(now=NOW + timedelta(minutes=10), aois=aois)
    assert second.polled == [] and second.skipped == ["a1"]

    third = w.tick(now=NOW + timedelta(minutes=90), aois=aois)
    assert third.polled == ["a1"]


def test_tick_reports_a_broken_registry_rather_than_watching_nothing(tmp_path):
    from backend.services.scheduler.watcher import AOIWatcher

    bad = tmp_path / "aois.yaml"
    bad.write_text("aois: []\n", encoding="utf-8")
    tick = AOIWatcher(config_path=bad).tick(now=NOW)

    assert tick.polled == []
    assert tick.errors and tick.errors[0]["aoi_id"] == "*"


def test_a_dry_run_previews_without_consuming(db):
    """A preview must not eat the scenes it was only supposed to show."""
    from backend.models.db import AoiWatch

    scene = FakeScene("S1A_TEST_001", NOW - timedelta(hours=2))
    w = _watcher([scene])

    tick = w.tick(now=NOW, aois=[_aoi()], dry_run=True)
    assert [s["scene_id"] for s in tick.new_scenes] == ["S1A_TEST_001"]
    assert tick.opened == []
    assert db.get(AoiWatch, "test-aoi") is None or \
        db.get(AoiWatch, "test-aoi").last_scene_time_utc is None

    # The real poll that follows still sees it, and counts it exactly once.
    opened = w.poll_aoi(_aoi(), db, now=NOW)
    assert [o["scene_id"] for o in opened] == ["S1A_TEST_001"]
    state = db.get(AoiWatch, "test-aoi")
    assert state.scenes_seen == 1 and state.polls == 1


def test_a_dry_run_does_not_record_a_provider_failure(db):
    """A preview against a dead provider must not flip the AOI to DEGRADED."""
    from backend.models.db import AoiWatch
    from backend.services.scheduler.watcher import AOIWatcher

    def boom(bbox, start_time, end_time, top=10):
        raise TimeoutError("CDSE search timed out")

    tick = AOIWatcher(search=boom).tick(now=NOW, aois=[_aoi()], dry_run=True)

    assert tick.errors and tick.errors[0]["error_class"] == "TIMEOUT"
    state = db.get(AoiWatch, "test-aoi")
    assert state is None or state.status in (None, "UNKNOWN")
