"""The live AIS ingest worker: connection, persistence and archival.

The protocol lives in `ais_service/ais/aisstream.py` and is pure. This module
owns everything stateful: the socket, the reconnect policy, the live state
table and the flush into the day-partitioned archive.

THE TWO SINKS, AND WHY THERE ARE TWO
------------------------------------
Every accepted observation goes to both:

  **`ais_live`** -- one row per MMSI, updated in place. This is the map. It
  answers "where is everything now" in one indexed scan.

  **`AISStore`** -- day-partitioned, contract-shaped Parquet, the SAME archive
  `MarineCadastre` and `DMA` ingest into. This is the investigation record. It
  answers "which vessels were within 5 km of this origin cloud during a
  thirteen-hour window", which is the question the system exists for.

Serving the second from the first would mean keeping every historical row in a
row-per-vessel table, and then drawing one live frame scans years. Serving the
first from the second would mean opening Parquet partitions to render a map.
They are different questions with different indexes.

WHAT "WORKING" MEANS HERE
-------------------------
Standing rule 8: provider status must distinguish REACHABLE from FUNCTIONALLY
WORKING. A WebSocket that accepts a connection and then sends nothing has four
possible causes that look identical from outside -- "no vessels" is the symptom
of all of them -- so `status()` separates them by name:

  1. no API key                  -> NOT_CONFIGURED
  2. subscription not confirmed  -> malformed frame, or the key was rejected
  3. confirmed and no positions  -> the subscription is VALID and the water it
                                    names is producing nothing: receiver
                                    coverage, not a fault
  4. positions then silence      -> traffic ceased on an open connection

`functionally_working` requires connected AND at least one position STORED AND
recent traffic. All three, because the first version asked only "connected and
not silent" and the subscription handshake counted as a message -- so the flag
held from the instant of connection.

MEASURED COVERAGE LIMIT (2026-09-12)
------------------------------------
AISStream is relayed by **volunteer receivers**, and its coverage of the Bay of
Bengal is effectively nil. A subscription to the seeded theatre
(80.5..95.3 E, 5.5..21.5 N) returned ZERO messages in 60 s, while a
globally-bounded subscription on the same key delivered a firehose immediately
(Denmark, Canada, Spain, Finland, the Netherlands). The subscription mechanism
works; the receivers do not exist there.

That is case 3 above, and it is reported as receiver coverage rather than as an
empty sea -- there are ships in the Bay of Bengal and nothing is listening to
them. It is also why the archive cannot be presented as a complete record of
that water.

WITHOUT A KEY
-------------
`AISSTREAM_API_KEY` absent means the state is NOT_CONFIGURED. It does not mean
zero vessels. An empty vessel list reads as "we looked and the sea is empty",
which is the same class of lie as returning `[]` for a Sentinel-2 search.

BACKPRESSURE
------------
A busy bounding box delivers faster than SQLite commits. The socket reader does
nothing but parse and enqueue; a separate drain batches into the database. The
queue is BOUNDED and drops the OLDEST frame when full, counting every drop --
an unbounded queue turns a slow database into a memory leak that ends the
process, and dropping the newest would mean the live map freezes while the
archive keeps filling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (REPO_ROOT, REPO_ROOT / "ais_service"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ais.aisstream import (WS_URL, Deduplicator, is_control_frame,  # noqa: E402
                           normalise_batch, redacted_subscription,
                           subscription_message)

from backend.core.config import get_settings  # noqa: E402
from backend.models.db import (AisLiveState, AisStreamSession, SessionLocal,  # noqa: E402
                               Zone, record_call, utcnow)
from backend.services import zones as zsvc  # noqa: E402

log = logging.getLogger(__name__)
settings = get_settings()

# Bounded so a slow database cannot become a memory leak. At ~200 msg/s from a
# busy box this is roughly a minute of slack, which is far more than a commit
# ever needs and small enough to stay bounded in RAM.
QUEUE_MAX = 20_000

# How often the drain wakes. Short enough that the live map is current,
# long enough that each commit carries hundreds of rows instead of one.
DRAIN_SECONDS = 2.0

# How often accepted observations are appended to the Parquet archive. A
# partition rewrite is a read-merge-write of a whole day bucket, so doing it
# per message would be quadratic; five minutes keeps each flush to a few
# thousand rows.
ARCHIVE_FLUSH_SECONDS = 300.0

# Reconnect backoff. Jittered so a provider outage does not produce a
# thundering herd of synchronised reconnects from every deployment.
BACKOFF_BASE = 2.0
BACKOFF_MAX = 120.0

# A connected socket that has been silent this long is reported as silent.
# AISStream sends continuously over any populated box, so a minute of nothing
# means the subscription matches nothing.
SILENCE_WARN_SECONDS = 90.0

# Live rows older than this are dropped by `prune_live_state`. A vessel that
# stopped transmitting twelve hours ago is not "currently at" its last
# position, and leaving it on the map is a stale reading presented as current.
LIVE_TTL_HOURS = 12


class AisLiveWorker:
    """Owns one AISStream subscription and its two sinks.

    Started and stopped from the API lifespan, or from
    `POST /api/ais/stream/start`. One instance per process: a second
    subscription on the same key would double every row and AISStream rate
    limits per key anyway.
    """

    def __init__(self, bboxes: Optional[list[list[float]]] = None,
                 region: str = "bay-of-bengal",
                 archive_root: Optional[Path] = None):
        self._bboxes = bboxes
        self.region = region
        self.archive_root = (archive_root
                             or (settings.data_root / "ais" / "store"))

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop = threading.Event()

        # `deque(maxlen=)` drops from the OPPOSITE end on append, which is
        # exactly the oldest-first policy we want, and it does it without a
        # lock-and-pop dance.
        self._queue: deque = deque(maxlen=QUEUE_MAX)
        self._dropped = 0
        self._dedup = Deduplicator()
        self._archive_buffer: list[dict] = []

        self._session_id: Optional[int] = None
        self._state = "stopped"      # stopped|not_configured|connecting|connected|disconnected|failed
        self._last_message_utc: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self._reconnects = 0
        self._counters = {"received": 0, "control": 0, "positions": 0,
                          "statics": 0, "archived": 0, "dropped": 0,
                          "duplicates": 0}
        # Set when the provider acknowledges the subscription frame. Reported
        # separately from "connected" because it rules out a malformed
        # subscription or a rejected key, which is the first thing to know when
        # a connected stream delivers nothing.
        self._subscription_confirmed = False
        self._lock = threading.Lock()

    # -- configuration ----------------------------------------------------

    def bboxes(self) -> list[list[float]]:
        """Boxes to subscribe to, longitude-first.

        Derived from the ACTIVE OPERATIONAL ZONES by default, not from a
        constant. That is the point of the zone model: an operator who draws a
        new zone should start receiving traffic for it without a code change,
        and a deployment that monitors a different sea needs no edit here.

        Falls back to the jurisdiction boundary when no operational zone
        exists, and returns an empty list when there are no zones at all --
        which is reported as "nothing to subscribe to", never as a global
        subscription. Subscribing to the whole planet because a table was
        empty would be a configuration accident that looks like a feature.
        """
        if self._bboxes:
            return self._bboxes
        with SessionLocal() as db:
            rows = (db.query(Zone)
                    .filter(Zone.status == "active")
                    .filter(Zone.kind == "operational").all())
            if not rows:
                rows = (db.query(Zone)
                        .filter(Zone.status == "active")
                        .filter(Zone.kind == "jurisdiction").all())
            boxes = []
            for zone in rows:
                try:
                    boxes.append([float(v) for v in json.loads(zone.bbox_json)])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        return _merge_boxes(boxes)

    # -- lifecycle --------------------------------------------------------

    def start(self) -> dict:
        """Begin ingesting. Idempotent -- a second call is a no-op."""
        if self._thread is not None and self._thread.is_alive():
            return {"started": False, "reason": "already running",
                    "status": self.status()}

        api_key = (settings.env_credentials("AISStream")
                   .get("AISSTREAM_API_KEY") or "")
        if not api_key:
            self._state = "not_configured"
            self._last_error = (
                "AISSTREAM_API_KEY is not set. Live AIS reports NOT CONFIGURED "
                "rather than an empty vessel list, which would read as 'we "
                "looked and there is no traffic'.")
            return {"started": False, "reason": self._last_error,
                    "status": self.status()}

        boxes = self.bboxes()
        if not boxes:
            self._state = "failed"
            self._last_error = (
                "no active zones to subscribe to. Draw an operational zone "
                "first; a global subscription is not used as a fallback.")
            return {"started": False, "reason": self._last_error,
                    "status": self.status()}

        self._stop.clear()
        self._state = "connecting"
        self._thread = threading.Thread(target=self._run, args=(api_key, boxes),
                                        name="ais-live", daemon=True)
        self._thread.start()
        return {"started": True, "bboxes": boxes, "region": self.region,
                "status": self.status()}

    def stop(self, timeout: float = 10.0) -> dict:
        """Stop ingesting, flushing whatever is buffered.

        The final flush is not optional. Observations already accepted and
        counted but not yet written would otherwise vanish on shutdown, and
        the session row would claim rows it never archived.
        """
        self._stop.set()
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        flushed = self.flush_archive()
        self._state = "stopped"
        self._close_session("stopped")
        return {"stopped": True, "final_flush": flushed,
                "status": self.status()}

    def _run(self, api_key: str, boxes: list[list[float]]) -> None:
        """Thread entry: own an asyncio loop for the socket."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect_forever(api_key, boxes))
        except Exception as exc:                   # noqa: BLE001
            self._state = "failed"
            self._last_error = f"{type(exc).__name__}: {exc}"
            log.exception("[ais-live] worker died")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
                self._loop = None

    async def _connect_forever(self, api_key: str,
                               boxes: list[list[float]]) -> None:
        """Connect, read, and reconnect with jittered exponential backoff."""
        try:
            import websockets
        except ImportError:
            self._state = "failed"
            self._last_error = (
                "the `websockets` package is not installed; live AIS cannot "
                "connect. It ships with uvicorn[standard].")
            return

        attempt = 0
        drain = asyncio.create_task(self._drain_loop())
        try:
            while not self._stop.is_set():
                frame = subscription_message(api_key, boxes)
                with self._lock:
                    self._subscription_confirmed = False
                self._open_session(frame, boxes, attempt)
                try:
                    # `ping_interval` is what detects a half-open socket. A TCP
                    # connection to a relay that has stopped forwarding stays
                    # ESTABLISHED indefinitely, so without keepalives the
                    # worker would sit "connected" and silent forever.
                    async with websockets.connect(
                            WS_URL, ping_interval=20, ping_timeout=20,
                            close_timeout=5, max_size=2 ** 20) as socket:
                        await socket.send(json.dumps(frame))
                        self._state = "connected"
                        self._mark_session(status="connected")
                        attempt = 0
                        await self._read_loop(socket)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:           # noqa: BLE001 - any transport error
                    self._state = "disconnected"
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._mark_session(status="disconnected",
                                       error_class=type(exc).__name__,
                                       error_detail=str(exc)[:500])
                    self._record_provider_failure(type(exc).__name__, str(exc))

                self._close_session(self._state)
                if self._stop.is_set():
                    break

                attempt += 1
                self._reconnects += 1
                delay = min(BACKOFF_MAX, BACKOFF_BASE ** min(attempt, 7))
                # Full jitter. A fixed backoff synchronises every deployment's
                # retry against a recovering provider.
                delay = delay * (0.5 + random.random() * 0.5)
                log.warning("[ais-live] reconnect %d in %.1fs: %s",
                            attempt, delay, self._last_error)
                try:
                    await asyncio.wait_for(asyncio.shield(
                        asyncio.sleep(delay)), timeout=delay + 1)
                except asyncio.TimeoutError:
                    pass
        finally:
            drain.cancel()
            try:
                await drain
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _read_loop(self, socket) -> None:
        """Parse nothing, decide nothing: enqueue and move on.

        The reader must never block on the database. A commit that takes 300 ms
        while the socket buffers is how a busy subscription ends up dropping
        frames at the OS level, where nothing can count them.
        """
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                # No message for 30 s. Not an error -- a quiet box is quiet --
                # but the loop must return to the stop check.
                continue
            try:
                frame = json.loads(raw)
            except (TypeError, ValueError):
                with self._lock:
                    self._counters["received"] += 1
                continue

            # An error frame from the provider is not data. The usual cause is
            # a rejected key, and treating it as an unparseable message would
            # hide an authentication failure behind a rising reject count.
            if isinstance(frame, dict) and frame.get("error"):
                self._last_error = f"provider error: {frame['error']}"
                self._mark_session(error_class="PROVIDER_ERROR",
                                   error_detail=str(frame["error"])[:500])
                log.error("[ais-live] %s", self._last_error)
                continue

            # The subscription handshake. Recorded as confirmation that the
            # frame was ACCEPTED -- which is genuinely useful, because it rules
            # out a malformed subscription or a bad key -- but NOT counted as
            # traffic and NOT allowed to touch `_last_message_utc`.
            #
            # Counting it did real damage: "connected and not silent" then held
            # the instant a connection opened, so a subscription over water
            # with no receiver coverage reported FUNCTIONALLY WORKING while
            # ingesting nothing. Measured against the Bay of Bengal on
            # 2026-09-12, that was exactly the observed behaviour.
            if is_control_frame(frame):
                with self._lock:
                    self._subscription_confirmed = True
                    self._counters["control"] += 1
                log.info("[ais-live] subscription confirmed by provider")
                continue

            with self._lock:
                self._counters["received"] += 1
                if len(self._queue) == self._queue.maxlen:
                    # deque drops the oldest on append; count it so the
                    # shortfall between received and stored is explainable.
                    self._dropped += 1
                    self._counters["dropped"] = self._dropped
                self._queue.append(frame)
                self._last_message_utc = utcnow()

    async def _drain_loop(self) -> None:
        """Batch the queue into the database, and periodically to Parquet."""
        last_flush = utcnow()
        while not self._stop.is_set():
            await asyncio.sleep(DRAIN_SECONDS)
            try:
                self.drain_once()
            except Exception:                      # noqa: BLE001
                # A failed drain must never kill ingestion. The queue is
                # bounded, so the worst case is dropped frames that are
                # counted, not a dead worker.
                log.exception("[ais-live] drain failed")
            if (utcnow() - last_flush).total_seconds() >= ARCHIVE_FLUSH_SECONDS:
                try:
                    self.flush_archive()
                except Exception:                  # noqa: BLE001
                    log.exception("[ais-live] archive flush failed")
                last_flush = utcnow()

    # -- persistence ------------------------------------------------------

    def drain_once(self) -> dict:
        """Normalise and persist everything currently queued.

        Public and synchronous so a test can drive the whole persistence path
        with `worker._queue.extend(frames); worker.drain_once()` and no socket.
        """
        with self._lock:
            frames = list(self._queue)
            self._queue.clear()
        if not frames:
            return {"frames": 0}

        batch = normalise_batch(frames, self._dedup)

        with SessionLocal() as db:
            zone_cache: dict[tuple, Optional[str]] = {}
            for obs in batch.positions:
                self._upsert_live(db, obs, zone_cache)
            for static in batch.statics:
                self._merge_static(db, static)
            db.commit()

        with self._lock:
            self._counters["positions"] += len(batch.positions)
            self._counters["statics"] += len(batch.statics)
            self._counters["duplicates"] += batch.rejects.duplicate
            self._archive_buffer.extend(batch.positions)

        self._mark_session(
            messages_received=self._counters["received"],
            positions_stored=self._counters["positions"],
            statics_stored=self._counters["statics"],
            rejects_json=json.dumps(batch.rejects.as_dict()))
        return batch.summary()

    def _zone_for(self, db, lon: float, lat: float,
                  cache: dict) -> Optional[str]:
        """Zone id for a position, with a coarse cache.

        Point-in-polygon per vessel per batch is the expensive part of ingest:
        a thousand positions against forty polygons is forty thousand Shapely
        calls every two seconds. Keyed on a 0.05-degree cell (~5 km) because
        vessels in the same cell are in the same zone except within 5 km of a
        boundary -- and a live map marker's zone tag being briefly wrong near a
        line is acceptable, while an incident's routed zone is resolved exactly
        and separately in `zone_for_point`.
        """
        key = (round(lon * 20), round(lat * 20))
        if key in cache:
            return cache[key]
        zone = zsvc.zone_for_point(db, lon, lat)
        cache[key] = zone.id if zone else None
        return cache[key]

    def _upsert_live(self, db, obs: dict, zone_cache: dict) -> None:
        row = db.get(AisLiveState, obs["mmsi"])
        now = utcnow()
        report = obs["timestamp_utc"]

        if row is None:
            db.add(AisLiveState(
                mmsi=obs["mmsi"], lat=obs["lat"], lon=obs["lon"],
                sog_kn=obs["sog_kn"], cog_deg=obs["cog_deg"],
                heading_deg=obs["heading_deg"], nav_status=obs["nav_status"],
                vessel_name=obs.get("vessel_name"),
                report_utc=report, received_utc=now, first_seen_utc=now,
                message_count=1,
                zone_id=self._zone_for(db, obs["lon"], obs["lat"], zone_cache),
                source=obs.get("source", "real"),
                provider=obs.get("provider", "AISStream")))
            return

        # Out-of-order guard. A late relay must not drag the marker backwards.
        # `_naive` because SQLite hands back naive datetimes for a
        # `DateTime(timezone=True)` column, and comparing naive to aware
        # raises -- which inside a drain would abort the whole batch.
        if _naive(report) < _naive(row.report_utc):
            row.message_count = (row.message_count or 0) + 1
            row.received_utc = now
            return

        row.lat, row.lon = obs["lat"], obs["lon"]
        row.sog_kn = obs["sog_kn"]
        row.cog_deg = obs["cog_deg"]
        row.heading_deg = obs["heading_deg"]
        row.nav_status = obs["nav_status"]
        # Only overwrite a known name with another known name. A position
        # frame without `ShipName` must not erase an identity a static frame
        # established.
        if obs.get("vessel_name"):
            row.vessel_name = obs["vessel_name"]
        row.report_utc = report
        row.received_utc = now
        row.message_count = (row.message_count or 0) + 1
        row.zone_id = self._zone_for(db, obs["lon"], obs["lat"], zone_cache)

    def _merge_static(self, db, static: dict) -> None:
        """Merge identity into an existing vessel row.

        Deliberately does NOT create a row. A static frame carries no position,
        and inserting one would need a fabricated lat/lon -- so identity for a
        vessel we have never seen move is dropped until it transmits a
        position. The next static frame (six minutes) re-merges it.
        """
        row = db.get(AisLiveState, static["mmsi"])
        if row is None:
            return
        for field in ("vessel_name", "callsign", "imo", "vessel_type",
                      "ais_ship_type", "length_m", "width_m", "draught_m",
                      "destination"):
            value = static.get(field)
            # None means "not transmitted in this frame", which must not
            # overwrite a value an earlier frame did transmit.
            if value is not None:
                setattr(row, field, value)

    def flush_archive(self) -> dict:
        """Append buffered observations to the day-partitioned AISStore.

        This is the half of the design that answers the original NOT_DEPLOYED
        objection: a stream alone cannot speak to a past acquisition, but a
        stream that is archived can, and this writes into the SAME store the
        historical providers use, contract-shaped, so attribution reads it with
        no special case.
        """
        with self._lock:
            rows = self._archive_buffer
            self._archive_buffer = []
        if not rows:
            return {"rows": 0, "partitions": []}

        try:
            import pandas as pd
            from ais.contract import to_contract
            from ais.index import AISStore
        except ImportError as exc:
            # Put them back: a missing Parquet engine must lose no observations.
            with self._lock:
                self._archive_buffer = rows + self._archive_buffer
            return {"rows": 0, "error": f"archive unavailable: {exc}",
                    "buffered": len(self._archive_buffer)}

        frame = pd.DataFrame(rows)
        try:
            shaped = to_contract(frame, source="real")
            store = AISStore(self.archive_root, granularity="day")
            report = store.ingest(shaped, region=self.region)
        except Exception as exc:                   # noqa: BLE001
            with self._lock:
                self._archive_buffer = rows + self._archive_buffer
            log.exception("[ais-live] archive ingest failed")
            return {"rows": 0, "error": f"{type(exc).__name__}: {exc}",
                    "buffered": len(self._archive_buffer)}

        written = int(getattr(report, "rows_written", len(shaped)) or 0)
        with self._lock:
            self._counters["archived"] += written
        self._mark_session(archived_rows=self._counters["archived"])
        return {"rows": written, "region": self.region,
                "store": str(self.archive_root)}

    # -- session bookkeeping ----------------------------------------------

    def _open_session(self, frame: dict, boxes: list, attempt: int) -> None:
        with SessionLocal() as db:
            row = AisStreamSession(
                status="connecting",
                subscription_json=json.dumps(redacted_subscription(frame)),
                bbox_json=json.dumps(boxes), reconnect_attempt=attempt)
            db.add(row)
            db.commit()
            self._session_id = row.id

    def _mark_session(self, **fields) -> None:
        if self._session_id is None:
            return
        try:
            with SessionLocal() as db:
                row = db.get(AisStreamSession, self._session_id)
                if row is None:
                    return
                for key, value in fields.items():
                    setattr(row, key, value)
                if self._last_message_utc is not None:
                    if row.first_message_utc is None:
                        row.first_message_utc = self._last_message_utc
                    row.last_message_utc = self._last_message_utc
                db.commit()
        except Exception:                          # noqa: BLE001
            # Bookkeeping must never take down ingestion.
            log.debug("[ais-live] session update failed", exc_info=True)

    def _close_session(self, status: str) -> None:
        if self._session_id is None:
            return
        try:
            with SessionLocal() as db:
                row = db.get(AisStreamSession, self._session_id)
                if row is not None and row.ended_utc is None:
                    row.ended_utc = utcnow()
                    row.status = status
                    db.commit()
        except Exception:                          # noqa: BLE001
            log.debug("[ais-live] session close failed", exc_info=True)
        self._session_id = None

    def _record_provider_failure(self, error_class: str, detail: str) -> None:
        try:
            with SessionLocal() as db:
                record_call(db, "AISStream", "wss stream", "error",
                            error_class=error_class, error_detail=detail[:300])
        except Exception:                          # noqa: BLE001
            log.debug("[ais-live] provider health update failed", exc_info=True)

    # -- introspection ----------------------------------------------------

    def status(self) -> dict:
        """What the monitoring page and `/api/ais/status` report.

        `functionally_working` is separate from `connected` on purpose
        (standing rule 8). A socket that connected and has received nothing is
        REACHABLE and NOT working, and the usual cause is a subscription whose
        bounding box matches no water -- which is why the redacted
        subscription is reported alongside it.
        """
        with self._lock:
            counters = dict(self._counters)
            confirmed = self._subscription_confirmed
        silent_for = (None if self._last_message_utc is None
                      else (utcnow() - _aware(self._last_message_utc))
                      .total_seconds())
        connected = self._state == "connected"
        # THREE conditions, not two. An earlier version asked only "connected
        # and not silent", and because the subscription handshake counted as a
        # message that held true from the instant of connection -- so a
        # subscription over water with no receiver coverage reported
        # FUNCTIONALLY WORKING while storing zero vessels. Requiring a stored
        # position means the claim is backed by data that reached the database.
        working = bool(connected
                       and counters["positions"] > 0
                       and silent_for is not None
                       and silent_for < SILENCE_WARN_SECONDS)
        return {
            "state": self._state,
            "connected": connected,
            "subscription_confirmed": confirmed,
            "functionally_working": working,
            "provider": "AISStream",
            "region": self.region,
            "last_message_utc": self._last_message_utc,
            "silent_for_seconds": (round(silent_for, 1)
                                   if silent_for is not None else None),
            "silence_threshold_seconds": SILENCE_WARN_SECONDS,
            "reconnects": self._reconnects,
            "counters": counters,
            "queue_depth": len(self._queue),
            "queue_capacity": QUEUE_MAX,
            "archive_buffer": len(self._archive_buffer),
            "dedup_keys": len(self._dedup),
            "last_error": self._last_error,
            "archive_root": str(self.archive_root),
            # Said explicitly because it is the difference between "no traffic
            # here" and "we are not configured to look".
            "note": _status_note(self._state, connected, confirmed,
                                 counters, silent_for),
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _status_note(state: str, connected: bool, confirmed: bool,
                 counters: dict, silent_for: Optional[float]) -> Optional[str]:
    """A sentence naming what is actually wrong, when something is.

    Each branch exists because the states it separates look identical from the
    outside and have completely different causes. "No vessels" is the symptom
    of all four.
    """
    if state == "not_configured":
        return ("NOT CONFIGURED -- AISSTREAM_API_KEY is unset. This is not an "
                "empty sea.")
    if not connected:
        return None
    if not confirmed:
        # The provider never acknowledged the subscription frame, so it was
        # malformed or the key was rejected.
        return ("connected, but the provider has not confirmed the "
                "subscription -- the frame was rejected or the key is not "
                "accepted")
    if counters.get("positions", 0) == 0:
        # Confirmed and silent. The subscription is VALID and the water it
        # names is producing nothing. Measured on 2026-09-12: the Bay of
        # Bengal box returned zero messages in 60 s while a global box
        # delivered a firehose immediately, so the subscription mechanism
        # works and the coverage does not.
        #
        # AISStream is relayed by volunteer receivers. Coverage is dense off
        # north-west Europe and North America and effectively absent over the
        # northern Indian Ocean. This is NOT reported as "no traffic": there
        # are ships in the Bay of Bengal, and nothing is listening to them.
        return ("subscription CONFIRMED and zero positions received. The "
                "subscription is valid, so this is receiver coverage, not a "
                "fault: AISStream is relayed by volunteer receivers and has "
                "little or none over the northern Indian Ocean. Vessels are "
                "present in this water and unheard -- this is not an empty "
                "sea. Verify with a globally-bounded subscription, which "
                "delivers immediately.")
    if silent_for is not None and silent_for >= SILENCE_WARN_SECONDS:
        return (f"receiving stopped {silent_for:.0f}s ago after "
                f"{counters.get('positions', 0)} position(s); the connection "
                f"is open but traffic has ceased")
    return None


def _naive(value: datetime) -> datetime:
    """A comparable naive-UTC datetime.

    SQLite stores `DateTime(timezone=True)` as text and reads it back NAIVE,
    while a freshly parsed AIS timestamp is AWARE. Comparing the two raises
    `TypeError`, and inside a drain that aborts the whole batch -- so both
    sides are normalised before any comparison.
    """
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(
        tzinfo=timezone.utc)


def _merge_boxes(boxes: list[list[float]],
                 limit: int = 20) -> list[list[float]]:
    """Collapse overlapping subscription boxes, and cap how many are sent.

    Adjacent operational zones share edges, so subscribing to each one
    separately asks the provider for the same water repeatedly -- duplicate
    delivery the deduplicator then has to throw away. Above `limit` boxes the
    whole set is replaced by its single bounding box: AISStream rejects very
    large subscriptions, and receiving a superset plus filtering locally is
    honest, whereas silently dropping zones past the twentieth is not.
    """
    if not boxes:
        return []
    if len(boxes) > limit:
        return [[min(b[0] for b in boxes), min(b[1] for b in boxes),
                 max(b[2] for b in boxes), max(b[3] for b in boxes)]]

    remaining = [list(b) for b in boxes]
    merged: list[list[float]] = []
    while remaining:
        current = remaining.pop()
        changed = True
        while changed:
            changed = False
            for other in list(remaining):
                if (current[0] <= other[2] and other[0] <= current[2]
                        and current[1] <= other[3] and other[1] <= current[3]):
                    current = [min(current[0], other[0]),
                               min(current[1], other[1]),
                               max(current[2], other[2]),
                               max(current[3], other[3])]
                    remaining.remove(other)
                    changed = True
        merged.append(current)
    return merged


def prune_live_state(db, ttl_hours: int = LIVE_TTL_HOURS) -> int:
    """Drop live rows nobody has heard from in `ttl_hours`.

    A vessel that stopped transmitting half a day ago is not "currently at"
    its last known position. Leaving the row would put a stale marker on the
    live map indistinguishable from a current one -- and that stale marker is
    exactly the kind of thing an operator would later treat as evidence.

    The observations themselves are NOT deleted. They are in the archive,
    which is where history belongs.
    """
    cutoff = _naive(utcnow() - timedelta(hours=ttl_hours))
    stale = (db.query(AisLiveState)
             .filter(AisLiveState.report_utc < cutoff).all())
    for row in stale:
        db.delete(row)
    db.commit()
    return len(stale)


# One worker per process. A second subscription on the same key would double
# every row, and AISStream rate limits per key.
_worker: Optional[AisLiveWorker] = None


def get_worker() -> AisLiveWorker:
    global _worker
    if _worker is None:
        _worker = AisLiveWorker()
    return _worker


def reset_worker() -> None:
    """Drop the singleton. For tests, which need a fresh worker per module."""
    global _worker
    if _worker is not None:
        try:
            _worker.stop(timeout=2.0)
        except Exception:                          # noqa: BLE001
            pass
    _worker = None
