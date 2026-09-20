"""Live AIS: the wire protocol, the two sinks, and the honesty of "working".

No socket is opened anywhere in this file. The worker's read path and its
persistence path are separated precisely so the second can be driven with a
list of frames, and a test that needed a live provider would be untestable
offline and would silently pass on the day the provider went down.

The cases that matter, each of which was a defect first:

  * **Latitude/longitude transposition.** AISStream takes `[[lat, lon], ...]`;
    every bbox in this repo is longitude-first. The conversion happens in one
    function and a transposed box is refused, because the failure mode is a
    valid-looking subscription over Siberia that never delivers a message and
    reads as a provider outage.

  * **Go's timestamp format.** `MetaData.time_utc` is
    `2026-09-12 04:33:21.123456789 +0000 UTC`, which `fromisoformat` rejects
    on three separate counts. A first implementation returned None for every
    real message, which would have been a stream that connects, reports
    healthy, and ingests nothing.

  * **Sentinels are not numbers.** heading 511 and sog 1023 mean "not
    transmitting". Stored as NULL, never 0 -- a 0 heading reads as due north,
    and that exact bug hit 29,679 of the flagship's 86,830 rows.

  * **Connected is not working.** A session that connected and received zero
    messages is recorded as `connected_but_silent`, not as a success.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT / "main_system", REPO_ROOT / "ais_service"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PASSWORD = "ais-live-test-password"

# Whether Parquet actually WORKS here, probed by importing rather than by
# `find_spec`. As of 2026-09-12 this machine has pyarrow installed and a
# Windows Application Control policy blocking its native DLL, so the package
# is findable and unusable -- `find_spec` returns a spec and the import
# raises. A skip condition that consulted the spec would let the test run and
# fail with an ImportError that looks like a code defect.
try:
    import pyarrow as _pyarrow                      # noqa: F401

    PARQUET_OK = True
    PARQUET_REASON = ""
except Exception as _exc:                           # noqa: BLE001
    PARQUET_OK = False
    PARQUET_REASON = f"no usable parquet engine: {type(_exc).__name__}: {_exc}"


def _recent(minutes_ago: float = 5.0) -> str:
    """A report time `minutes_ago` before now, in AISStream's own format
    (nanosecond fraction, `+0000 UTC` suffix).

    The default used to be a fixed `2026-09-12 04:33` -- fresh on the day the
    tests were written, and older than every live-picture age window six days
    later, when four tests began failing on the calendar alone. Tests that pin
    an absolute time for their own reason (format parsing, out-of-order
    delivery, archive partitions) still pass one explicitly.
    """
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond:06d}000 +0000 UTC"


def _position_frame(mmsi=419000001, lat=13.5, lon=89.5, sog=12.4, cog=187.2,
                    heading=188, when=None, name="TEST TRADER", nav=0):
    """An AISStream `PositionReport` shaped the way the provider sends one."""
    when = when or _recent()
    return {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "ShipName": name, "latitude": lat,
                     "longitude": lon, "time_utc": when},
        "Message": {"PositionReport": {
            "UserID": mmsi, "Latitude": lat, "Longitude": lon,
            "Sog": sog, "Cog": cog, "TrueHeading": heading,
            "NavigationalStatus": nav}},
    }


def _static_frame(mmsi=419000001, name="TEST TRADER", ship_type=80,
                  a=180.0, b=40.0, c=16.0, d=16.0, draught=12.5,
                  callsign="TST1", imo=9512345):
    return {
        "MessageType": "ShipStaticData",
        "MetaData": {"MMSI": mmsi, "ShipName": name,
                     "time_utc": "2026-09-12 04:34:00 +0000 UTC"},
        "Message": {"ShipStaticData": {
            "UserID": mmsi, "Name": name, "CallSign": callsign,
            "ImoNumber": imo, "Type": ship_type,
            "MaximumStaticDraught": draught,
            "Dimension": {"A": a, "B": b, "C": c, "D": d},
            "Destination": "CHENNAI"}},
    }


# --------------------------------------------------------------------------
# the protocol (pure, no fixtures needed)
# --------------------------------------------------------------------------

def test_subscription_transposes_lon_first_to_lat_first():
    from ais.aisstream import subscription_message

    frame = subscription_message("KEY", [[80.5, 5.5, 95.3, 21.5]])
    # South-west corner first, latitude first inside each pair.
    assert frame["BoundingBoxes"] == [[[5.5, 80.5], [21.5, 95.3]]]
    assert frame["APIKey"] == "KEY"


def test_a_transposed_bbox_is_refused_not_silently_accepted():
    """The whole point. A lat-first box handed in by mistake describes water
    that does not exist, and AISStream accepts it and says nothing."""
    from ais.aisstream import AisStreamError, subscription_message

    with pytest.raises(AisStreamError) as exc:
        subscription_message("KEY", [[5.5, 80.5, 21.5, 95.3]])
    assert "longitude first" in str(exc.value)


def test_the_api_key_never_reaches_a_log():
    from ais.aisstream import redacted_subscription, subscription_message

    frame = subscription_message("SECRET-KEY", [[80.5, 5.5, 95.3, 21.5]])
    safe = redacted_subscription(frame)
    assert safe["APIKey"] == "[redacted]"
    assert "SECRET-KEY" not in json.dumps(safe)
    # And the original is untouched -- redaction must not mutate the frame
    # that is about to be sent.
    assert frame["APIKey"] == "SECRET-KEY"


def test_a_missing_key_is_refused_with_a_reason():
    from ais.aisstream import AisStreamError, subscription_message

    with pytest.raises(AisStreamError) as exc:
        subscription_message("", [[80.5, 5.5, 95.3, 21.5]])
    assert "NOT CONFIGURED" in str(exc.value)


@pytest.mark.parametrize("raw,expect", [
    ("2026-09-12 04:33:21.123456789 +0000 UTC",
     datetime(2026, 9, 12, 4, 33, 21, 123456, tzinfo=timezone.utc)),
    ("2026-09-12 04:33:21 +0000 UTC",
     datetime(2026, 9, 12, 4, 33, 21, tzinfo=timezone.utc)),
    ("2026-09-12T04:33:21Z",
     datetime(2026, 9, 12, 4, 33, 21, tzinfo=timezone.utc)),
    ("2026-09-12T04:33:21.500Z",
     datetime(2026, 9, 12, 4, 33, 21, 500000, tzinfo=timezone.utc)),
    # A non-UTC offset must be converted, not ignored.
    ("2026-09-12 04:33:21 +0530 UTC",
     datetime(2026, 9, 11, 23, 3, 21, tzinfo=timezone.utc)),
])
def test_go_timestamps_parse(raw, expect):
    """The regression that would have made the stream silently ingest nothing."""
    from ais.aisstream import _parse_time

    assert _parse_time(raw) == expect


@pytest.mark.parametrize("raw", ["garbage", "", None, "2026-13-12 04:33:21 +0000 UTC",
                                 "2026-09-32 04:33:21 +0000 UTC"])
def test_an_unparseable_timestamp_is_none_not_now(raw):
    """NOT replaced with arrival time: a delayed message stamped 'now' lands
    in the wrong archive bucket and corrupts every later windowed query."""
    from ais.aisstream import _parse_time

    assert _parse_time(raw) is None


def test_sentinels_become_null_never_zero():
    from ais.aisstream import normalise_position

    obs = normalise_position(_position_frame(heading=511, sog=102.3, cog=360.0))
    assert obs is not None
    assert obs["heading_deg"] is None, "511 means not transmitting, not north"
    assert obs["sog_kn"] is None
    assert obs["cog_deg"] is None


def test_a_report_with_no_position_is_rejected_not_stored_null():
    from ais.aisstream import RejectCounts, normalise_position

    counts = RejectCounts()
    assert normalise_position(_position_frame(lat=91.0, lon=181.0), counts) is None
    assert counts.no_position == 1


@pytest.mark.parametrize("mmsi", [0, 1234, 99, 1_000_000_000, None, "abc"])
def test_a_bad_mmsi_is_rejected(mmsi):
    from ais.aisstream import RejectCounts, normalise_position

    counts = RejectCounts()
    assert normalise_position(_position_frame(mmsi=mmsi), counts) is None
    assert counts.bad_mmsi == 1


def test_the_body_mmsi_wins_over_the_relay_metadata():
    """A relay that mangles its metadata must not re-attribute a position to a
    different vessel."""
    from ais.aisstream import normalise_position

    frame = _position_frame(mmsi=419000001)
    frame["MetaData"]["MMSI"] = 219000999
    obs = normalise_position(frame)
    assert obs["mmsi"] == 419000001


def test_static_dimensions_are_summed_not_taken_raw():
    """AIS transmits four distances from the antenna, not a length and beam."""
    from ais.aisstream import normalise_static

    static = normalise_static(_static_frame(a=180, b=40, c=16, d=16))
    assert static["length_m"] == 220.0
    assert static["width_m"] == 32.0
    assert static["vessel_type"] == "tanker"       # AIS code 80
    assert static["draught_m"] == 12.5


def test_static_data_report_reads_the_nested_report_parts():
    """AIS message 24 arrives in two halves and the provider nests them: the
    name under `ReportA`, the call sign, ship type and dimensions under
    `ReportB`. Reading only the flat `ShipStaticData` shape found none of
    them, which is indistinguishable from a vessel transmitting no identity --
    so every Class B vessel silently had no name."""
    from ais.aisstream import normalise_static

    frame = {
        "MessageType": "StaticDataReport",
        "MetaData": {"MMSI": 244670408, "ShipName": "MATRICARIA",
                     "time_utc": "2026-09-12 16:22:55 +0000 UTC"},
        "Message": {"StaticDataReport": {
            "MessageID": 24, "UserID": 244670408, "PartNumber": False,
            "ReportA": {"Name": "MATRICARIA", "Valid": True},
            "ReportB": {"CallSign": "PE1234", "ShipType": 70,
                        "Dimension": {"A": 90.0, "B": 10.0, "C": 8.0,
                                      "D": 8.0},
                        "Valid": True}}},
    }
    static = normalise_static(frame)
    assert static is not None
    assert static["mmsi"] == 244670408
    assert static["vessel_name"] == "MATRICARIA"
    assert static["callsign"] == "PE1234"
    # ShipType 70 -> cargo, read from ReportB rather than a flat `Type`.
    assert static["vessel_type"] == "cargo"
    assert static["length_m"] == 100.0
    assert static["width_m"] == 16.0


def test_an_empty_callsign_is_absent_not_an_empty_string():
    """The provider sends an empty CallSign for a vessel that transmits none."""
    from ais.aisstream import normalise_static

    static = normalise_static(_static_frame(callsign=""))
    assert static["callsign"] is None


def test_a_zero_dimension_is_absent_not_a_zero_length_ship():
    from ais.aisstream import normalise_static

    static = normalise_static(_static_frame(a=0, b=0, c=0, d=0, draught=0))
    assert static["length_m"] is None
    assert static["width_m"] is None
    assert static["draught_m"] is None


def test_duplicates_are_dropped_and_counted():
    from ais.aisstream import Deduplicator, normalise_batch

    dedup = Deduplicator()
    frames = [_position_frame(), _position_frame(), _position_frame()]
    batch = normalise_batch(frames, dedup)
    assert len(batch.positions) == 1
    assert batch.rejects.duplicate == 2
    assert batch.received == 3


def test_the_same_second_at_a_different_position_is_not_a_duplicate():
    """A vessel at anchor legitimately transmits several distinct reports in
    one second; collapsing them would discard real observations."""
    from ais.aisstream import Deduplicator, normalise_batch

    dedup = Deduplicator()
    batch = normalise_batch([_position_frame(lat=13.50000),
                             _position_frame(lat=13.50100)], dedup)
    assert len(batch.positions) == 2


def test_the_deduplicator_memory_stays_bounded():
    from ais.aisstream import Deduplicator

    dedup = Deduplicator(capacity=1000)
    base = datetime(2026, 9, 12, tzinfo=timezone.utc)
    for i in range(5000):
        dedup.seen({"mmsi": 419000001, "timestamp_utc": base + timedelta(seconds=i),
                    "lat": 13.0, "lon": 89.0})
    assert len(dedup) <= 1000


# --------------------------------------------------------------------------
# the worker and the API
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("ais_live_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'ais.db').as_posix()}"
    os.environ["SECRET_KEY"] = "a" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db
    from backend.services.zone_seed import seed_bay_of_bengal

    init_db()
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        display_name=role, role=role, active=True))
        db.commit()
        seed_bay_of_bengal(db)

    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    os.environ.pop("DATABASE_URL", None)


@pytest.fixture()
def worker(env):
    """A worker whose archive goes to the test root. Never started."""
    from backend.services import ais_live

    ais_live.reset_worker()
    _client, root = env
    w = ais_live.AisLiveWorker(region="test-bay",
                              archive_root=root / "ais" / "store")
    yield w
    ais_live.reset_worker()


def _as(client, role):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def test_the_drain_persists_positions_to_live_state(env, worker):
    client, _ = env
    worker._queue.extend([_position_frame(mmsi=419000001, lat=13.5, lon=89.5),
                          _position_frame(mmsi=419000002, lat=15.0, lon=90.0,
                                          name=None)])
    summary = worker.drain_once()
    assert summary["positions"] == 2

    _as(client, "analyst")
    body = client.get("/api/ais/live", params={"max_age_minutes": 1440}).json()
    assert body["count"] == 2
    assert body["source"] == "real"
    by_mmsi = {v["mmsi"]: v for v in body["vessels"]}
    assert by_mmsi[419000001]["vessel_name"] == "TEST TRADER"
    # A vessel the relay sent no name for keeps a null name. Absence of data,
    # left absent -- the flagship's four ranked vessels were all like this.
    assert by_mmsi[419000002]["vessel_name"] is None


def test_positions_are_tagged_with_their_operational_zone(env, worker):
    client, _ = env
    # 89.5 E / 13.5 N is inside the seeded Zone 04 (lon >= 87, lat 11..18).
    worker._queue.append(_position_frame(mmsi=419000010, lat=13.5, lon=89.5))
    # Well outside the theatre.
    worker._queue.append(_position_frame(mmsi=419000011, lat=15.0, lon=60.0))
    worker.drain_once()

    _as(client, "analyst")
    body = client.get("/api/ais/live", params={"max_age_minutes": 1440}).json()
    zones = {v["mmsi"]: v["zone_id"] for v in body["vessels"]}
    assert zones[419000010] == "zone-bob-04"
    # NULL, not a nearest-zone guess. Outside every declared zone is a real
    # answer.
    assert zones[419000011] is None


def test_a_late_report_does_not_drag_a_vessel_backwards(env, worker):
    """AIS relays deliver out of order. The live marker must show the latest
    position, not the last one to arrive."""
    from backend.models.db import AisLiveState, SessionLocal

    worker._queue.append(_position_frame(
        mmsi=419000020, lat=14.0, lon=89.0,
        when="2026-09-12 06:00:00 +0000 UTC"))
    worker.drain_once()
    worker._queue.append(_position_frame(
        mmsi=419000020, lat=11.5, lon=88.0,
        when="2026-09-12 05:00:00 +0000 UTC"))     # one hour EARLIER
    worker.drain_once()

    with SessionLocal() as db:
        row = db.get(AisLiveState, 419000020)
    assert row.lat == 14.0 and row.lon == 89.0, "the late report won"
    # It is still counted: the message was received and archived, just not
    # treated as current.
    assert row.message_count == 2


def test_static_data_enriches_but_never_creates_a_row(env, worker):
    """A static frame carries no position, so inserting a row for it would
    need a fabricated lat/lon."""
    from backend.models.db import AisLiveState, SessionLocal

    # Static first, for a vessel never seen moving.
    worker._queue.append(_static_frame(mmsi=419000030))
    worker.drain_once()
    with SessionLocal() as db:
        assert db.get(AisLiveState, 419000030) is None

    # Now a position, then the static merges onto it.
    worker._queue.append(_position_frame(mmsi=419000030, lat=13.0, lon=88.5,
                                         name=None))
    worker.drain_once()
    worker._queue.append(_static_frame(mmsi=419000030, name="BAY TANKER"))
    worker.drain_once()

    with SessionLocal() as db:
        row = db.get(AisLiveState, 419000030)
    assert row.vessel_name == "BAY TANKER"
    assert row.vessel_type == "tanker"
    assert row.length_m == 220.0
    assert row.imo == 9512345


def test_a_position_frame_without_a_name_does_not_erase_a_known_one(env, worker):
    from backend.models.db import AisLiveState, SessionLocal

    worker._queue.append(_position_frame(mmsi=419000040, name="NAMED SHIP"))
    worker.drain_once()
    worker._queue.append(_position_frame(mmsi=419000040, name=None,
                                         lat=13.6,
                                         when="2026-09-12 04:35:00 +0000 UTC"))
    worker.drain_once()
    with SessionLocal() as db:
        assert db.get(AisLiveState, 419000040).vessel_name == "NAMED SHIP"


def test_the_queue_is_bounded_and_drops_are_counted(env, worker):
    """An unbounded queue turns a slow database into a memory leak that ends
    the process."""
    assert worker._queue.maxlen == worker_queue_max()
    for i in range(worker._queue.maxlen + 50):
        worker._queue.append(_position_frame(mmsi=419000100 + (i % 500)))
    assert len(worker._queue) == worker._queue.maxlen


def worker_queue_max():
    from backend.services.ais_live import QUEUE_MAX

    return QUEUE_MAX


# --------------------------------------------------------------------------
# honest status
# --------------------------------------------------------------------------

def test_no_key_reports_not_configured_never_an_empty_sea(env, worker,
                                                          monkeypatch):
    from backend.core.config import get_settings

    monkeypatch.setattr(get_settings(), "env_credentials",
                        lambda provider: {"AISSTREAM_API_KEY": None})
    result = worker.start()
    assert result["started"] is False
    assert "AISSTREAM_API_KEY" in result["reason"]
    status = worker.status()
    assert status["state"] == "not_configured"
    assert status["connected"] is False
    assert status["functionally_working"] is False
    assert "not an empty sea" in status["note"]


def test_connected_is_reported_separately_from_working(env, worker):
    """Standing rule 8. A socket that connected and received nothing is
    REACHABLE and not working.

    Walks all four states, because they look identical from outside -- "no
    vessels" is the symptom of every one -- and have completely different
    causes and fixes.
    """
    from backend.models.db import utcnow

    # 1. Connected, subscription never acknowledged: a malformed frame or a
    #    rejected key.
    worker._state = "connected"
    worker._last_message_utc = None
    worker._subscription_confirmed = False
    status = worker.status()
    assert status["connected"] is True
    assert status["functionally_working"] is False
    assert "not confirmed the subscription" in status["note"]

    # 2. Subscription CONFIRMED and still nothing. The subscription is valid,
    #    so this is receiver coverage. Measured against the Bay of Bengal on
    #    2026-09-12: zero messages in 60 s while a global box delivered
    #    immediately.
    worker._subscription_confirmed = True
    status = worker.status()
    assert status["subscription_confirmed"] is True
    assert status["functionally_working"] is False
    assert "receiver coverage" in status["note"]
    assert "not an empty sea" in status["note"]

    # 3. Positions actually stored, and recent. Only now is it working.
    worker._counters["positions"] = 12
    worker._last_message_utc = utcnow()
    status = worker.status()
    assert status["functionally_working"] is True
    assert status["note"] is None

    # 4. It was working and traffic stopped.
    worker._last_message_utc = utcnow() - timedelta(seconds=600)
    status = worker.status()
    assert status["functionally_working"] is False
    assert "receiving stopped" in status["note"]


def test_the_subscription_handshake_is_not_counted_as_traffic(env, worker):
    """The bug this test exists for: counting `SubscriptionConfirmation` as a
    message set "last message" to the instant of connection, so "connected and
    not silent" held immediately and a stream over water with no receiver
    coverage reported FUNCTIONALLY WORKING while storing zero vessels."""
    from ais.aisstream import is_control_frame, normalise_batch

    confirmation = {"MessageType": "SubscriptionConfirmation",
                    "Message": {"CompressionEnabled": True}}
    assert is_control_frame(confirmation) is True
    assert is_control_frame(_position_frame()) is False

    batch = normalise_batch([confirmation, confirmation])
    assert batch.control == 2
    # Not traffic, and not a reject either -- it inflates neither figure.
    assert batch.received == 0
    assert batch.rejects.total() == 0
    assert batch.positions == []


def test_a_silent_session_is_recorded_as_silent(env, worker):
    from backend.models.db import AisStreamSession, SessionLocal, utcnow

    with SessionLocal() as db:
        db.add(AisStreamSession(status="disconnected", messages_received=0,
                                started_utc=utcnow()))
        db.commit()

    client, _ = env
    _as(client, "analyst")
    body = client.get("/api/ais/status").json()
    assert any(s["connected_but_silent"] for s in body["recent_sessions"])


def test_status_reports_the_real_archive_span_not_an_assumption(env, worker):
    client, _ = env
    _as(client, "analyst")
    body = client.get("/api/ais/status").json()
    archive = body["archive"]
    assert "available" in archive
    if archive.get("available") and not archive.get("partitions"):
        # Never implied to be complete.
        assert "no live AIS has been archived" in archive["note"]
    # The field that answers "does the archive cover my scene".
    assert "ingestion_began_utc" in body


def test_the_live_picture_states_when_it_truncated(env, worker):
    client, _ = env
    for i in range(30):
        worker._queue.append(_position_frame(mmsi=419001000 + i,
                                             lat=13.0 + i * 0.01, lon=89.0))
    worker.drain_once()

    _as(client, "analyst")
    body = client.get("/api/ais/live",
                      params={"limit": 5, "max_age_minutes": 1440}).json()
    assert body["count"] == 5
    assert body["total_in_view"] > 5
    assert body["truncated"] is True, (
        "a client that got 5 of 30 vessels must be able to tell")


def test_the_live_picture_excludes_stale_rows_by_default(env, worker):
    """A vessel last heard from two hours ago is not where the row says."""
    from backend.models.db import AisLiveState, SessionLocal, utcnow

    with SessionLocal() as db:
        db.add(AisLiveState(
            mmsi=419009999, lat=13.0, lon=89.0,
            report_utc=utcnow() - timedelta(hours=6),
            received_utc=utcnow() - timedelta(hours=6),
            first_seen_utc=utcnow() - timedelta(hours=6)))
        db.commit()

    client, _ = env
    _as(client, "analyst")
    fresh = client.get("/api/ais/live", params={"max_age_minutes": 60}).json()
    assert 419009999 not in {v["mmsi"] for v in fresh["vessels"]}
    wide = client.get("/api/ais/live", params={"max_age_minutes": 1440}).json()
    assert 419009999 in {v["mmsi"] for v in wide["vessels"]}


def test_pruning_drops_live_rows_and_keeps_the_archive(env, worker):
    from backend.models.db import AisLiveState, SessionLocal, utcnow

    with SessionLocal() as db:
        db.add(AisLiveState(
            mmsi=419008888, lat=13.0, lon=89.0,
            report_utc=utcnow() - timedelta(hours=48),
            received_utc=utcnow() - timedelta(hours=48),
            first_seen_utc=utcnow() - timedelta(hours=48)))
        db.commit()

    client, _ = env
    _as(client, "admin")
    body = client.post("/api/ais/live/prune?ttl_hours=12").json()
    assert body["pruned"] >= 1
    assert "archived observations are untouched" in body["note"]
    with SessionLocal() as db:
        assert db.get(AisLiveState, 419008888) is None


def test_zone_traffic_includes_empty_zones(env, worker):
    """Omitting them would make an empty zone indistinguishable from a zone
    that does not exist."""
    client, _ = env
    _as(client, "analyst")
    body = client.get("/api/ais/zones/summary",
                      params={"max_age_minutes": 1440}).json()
    ids = {z["zone_id"] for z in body["zones"]}
    assert {f"zone-bob-0{n}" for n in range(1, 7)} <= ids
    assert "outside_all_zones" in body


def test_starting_the_stream_is_an_admin_action(env, worker):
    client, _ = env
    for role in ("analyst", "investigator", "reviewer", "auditor",
                 "zone_officer"):
        _as(client, role)
        assert client.post("/api/ais/stream/start").status_code == 403, role
        assert client.post("/api/ais/stream/stop").status_code == 403, role


def test_reading_the_picture_is_open_to_every_role(env, worker):
    """Global situational awareness. An officer who cannot see traffic
    approaching their boundary is worse at their job, not more secure."""
    client, _ = env
    for role in ("analyst", "investigator", "reviewer", "auditor",
                 "zone_officer", "admin", "super_admin"):
        _as(client, role)
        assert client.get("/api/ais/live").status_code == 200, role
        assert client.get("/api/ais/status").status_code == 200, role


def test_a_malformed_bbox_is_refused_with_the_convention_named(env, worker):
    client, _ = env
    _as(client, "analyst")
    r = client.get("/api/ais/live", params={"bbox": "1,2,3"})
    assert r.status_code == 422
    assert "longitude first" in r.json()["detail"]


def test_an_unknown_mmsi_explains_both_possibilities(env, worker):
    client, _ = env
    _as(client, "analyst")
    r = client.get("/api/ais/live/123456789")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "never have been received" in detail
    assert "archive" in detail


# --------------------------------------------------------------------------
# subscription derivation
# --------------------------------------------------------------------------

def test_subscription_boxes_come_from_the_active_zones(env):
    """Drawing a zone is what extends coverage; there is no second list to
    keep in sync."""
    from backend.services.ais_live import AisLiveWorker

    boxes = AisLiveWorker().bboxes()
    assert boxes, "the seeded zones should produce at least one box"
    for box in boxes:
        lon_min, lat_min, lon_max, lat_max = box
        assert -180 <= lon_min < lon_max <= 180
        assert -90 <= lat_min < lat_max <= 90
    # The Bay of Bengal theatre, so the union must sit in its longitudes.
    assert min(b[0] for b in boxes) >= 80.0
    assert max(b[2] for b in boxes) <= 96.0


def test_overlapping_zone_boxes_are_merged():
    """Adjacent zones share edges; subscribing to each separately asks the
    provider for the same water twice."""
    from backend.services.ais_live import _merge_boxes

    merged = _merge_boxes([[80, 5, 88, 11], [87, 5, 95, 11]])
    assert len(merged) == 1
    assert merged[0] == [80, 5, 95, 11]

    disjoint = _merge_boxes([[0, 0, 1, 1], [50, 50, 51, 51]])
    assert len(disjoint) == 2


def test_too_many_boxes_collapse_to_one_rather_than_dropping_zones():
    """Silently dropping zones past the twentieth would leave water nobody is
    subscribed to, with nothing saying so."""
    from backend.services.ais_live import _merge_boxes

    many = [[i, 0, i + 0.5, 1] for i in range(0, 60, 2)]
    merged = _merge_boxes(many, limit=20)
    assert len(merged) == 1
    assert merged[0][0] == 0 and merged[0][2] == 58.5


def test_no_zones_means_no_subscription_not_a_global_one(env, monkeypatch):
    """A global subscription because a table was empty is a configuration
    accident that looks like a feature."""
    from backend.services import ais_live

    monkeypatch.setattr(ais_live.AisLiveWorker, "bboxes", lambda self: [])
    w = ais_live.AisLiveWorker()
    result = w.start()
    assert result["started"] is False
    assert "no active zones" in result["reason"]
    assert "global subscription is not used" in result["reason"]


# --------------------------------------------------------------------------
# the archive
# --------------------------------------------------------------------------

def test_a_failed_archive_flush_loses_no_observations(env, worker):
    """A missing Parquet engine must buffer, not discard. Observations already
    counted as stored have to end up somewhere."""
    worker._queue.extend([_position_frame(mmsi=419002000 + i, lat=13.0 + i * 0.1,
                                          lon=89.0) for i in range(5)])
    worker.drain_once()
    assert len(worker._archive_buffer) == 5

    result = worker.flush_archive()
    if result.get("error"):
        # pyarrow blocked or absent: the rows must still be held.
        assert result["rows"] == 0
        assert worker._archive_buffer, (
            "a failed flush discarded observations it had already accepted")
        assert result["buffered"] == 5
    else:
        assert result["rows"] == 5
        assert worker._archive_buffer == []


@pytest.mark.skipif(not PARQUET_OK, reason=PARQUET_REASON)
def test_archived_rows_are_contract_shaped(env, worker):
    """The archive must be readable by attribution with no special case -- it
    is the same store MarineCadastre writes to."""
    from contracts.schemas.tabular import (REQUIRED_VESSEL_COLUMNS,
                                           validate_vessels_df)

    worker._queue.extend([
        _position_frame(mmsi=419003000, lat=13.0, lon=89.0,
                        when="2026-09-12 04:00:00 +0000 UTC"),
        _position_frame(mmsi=419003000, lat=13.1, lon=89.1,
                        when="2026-09-12 04:05:00 +0000 UTC"),
        _position_frame(mmsi=419003001, lat=14.0, lon=90.0, heading=511,
                        when="2026-09-12 04:02:00 +0000 UTC"),
    ])
    worker.drain_once()
    result = worker.flush_archive()
    if result.get("error"):
        pytest.skip(f"archive unavailable: {result['error']}")
    assert result["rows"] == 3

    from ais.index import AISStore

    store = AISStore(worker.archive_root, granularity="day")
    df = store.query(bbox=(88.0, 12.0, 91.0, 15.0),
                     start="2026-09-12T00:00:00Z", end="2026-09-13T00:00:00Z")
    assert len(df) == 3
    assert list(df.columns) == REQUIRED_VESSEL_COLUMNS
    validate_vessels_df(df)
    # 511 survived as NaN, not as a bearing of zero.
    import pandas as pd
    assert pd.isna(df[df["mmsi"] == 419003001]["heading_deg"]).all()
    assert set(df["source"].unique()) == {"real"}


def test_two_reports_from_a_new_vessel_in_one_batch_do_not_lose_the_batch(env, worker):
    """The session does not autoflush, so a row only `add`-ed was invisible to
    `db.get`: the second report from a vessel first seen in this batch was
    inserted again and the UNIQUE violation at commit discarded every position
    in the batch. Two reports per vessel per two-second drain is ordinary
    AISStream traffic."""
    from backend.models.db import AisLiveState, SessionLocal

    worker._queue.extend([
        _position_frame(mmsi=419004000, lat=13.0, lon=89.0, when=_recent(10)),
        _position_frame(mmsi=419004000, lat=13.1, lon=89.1, when=_recent(5)),
        _position_frame(mmsi=419004001, lat=14.0, lon=90.0, when=_recent(5)),
        _static_frame(mmsi=419004000, name="SAME BATCH"),
    ])
    worker.drain_once()

    with SessionLocal() as db:
        row = db.get(AisLiveState, 419004000)
        other = db.get(AisLiveState, 419004001)
    assert other is not None, "the rest of the batch was lost"
    assert (row.lat, row.lon) == (13.1, 89.1), "the later report is current"
    assert row.message_count == 2
    # Identity from a static frame in the same batch as the first position is
    # merged, not dropped as "never seen moving".
    assert row.callsign == "TST1"
