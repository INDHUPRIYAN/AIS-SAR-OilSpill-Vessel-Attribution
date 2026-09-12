"""AISStream.io live AIS: the wire protocol, normalisation and de-duplication.

This module is deliberately **pure**. It parses and validates AISStream frames
and hands back plain dicts; it opens no database, writes no file and owns no
connection. The connection lifecycle and persistence live in
``main_system/backend/services/ais_live.py``. Splitting them is what makes the
protocol testable without a network or a server.

THE LATITUDE-FIRST TRAP
-----------------------
AISStream's subscription message takes bounding boxes as
``[[lat, lon], [lat, lon]]`` -- **latitude first**. Every bbox in this
repository is ``[lon_min, lat_min, lon_max, lat_max]`` -- **longitude first**,
frozen by contract. The two conventions are transposed, and the failure is
silent: a Bay of Bengal box (80..95 E, 5..22 N) handed over in the wrong order
becomes (80..95 N, 5..22 E), which is a valid-looking box over Siberia that
simply never produces a message. Nothing errors; the stream just stays quiet,
and it reads as "the provider is down".

So the conversion happens in exactly one place, :func:`subscription_message`,
which takes a lon-first bbox because that is this repo's convention, and emits
lat-first pairs because that is AISStream's. Do not pass a bbox to the socket
from anywhere else.

WHAT IS AND IS NOT VALIDATED
----------------------------
AIS is a noisy radio protocol relayed by volunteers. The sentinel values are
part of the spec, not corruption, and each has a meaning that is destroyed by
coercing it to a number:

    heading 511      "not transmitting heading". Stored as NaN -- a 0 would
                     read as due north, which is a fabricated bearing. This
                     exact bug hit 29,679 of the flagship's 86,830 rows.
    sog 1023 (102.3) "speed not available". NaN.
    cog 3600 (360.0) "course not available". NaN.
    lat 91 / lon 181 "position not available". The row is REJECTED, not
                     stored with a null position: an AIS observation without a
                     position is not an observation.

A message that fails validation is counted and dropped with a named reason, so
the ingest worker can report "4.1% rejected: 3.8% no position, 0.3% bad MMSI"
rather than a silent discrepancy between messages received and rows stored.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

WS_URL = "wss://stream.aisstream.io/v0/stream"

# AIS sentinel values. See the module docstring.
HEADING_UNAVAILABLE = 511
SOG_UNAVAILABLE_TENTHS = 1023
COG_UNAVAILABLE_TENTHS = 3600
LAT_UNAVAILABLE = 91.0
LON_UNAVAILABLE = 181.0

MMSI_MIN, MMSI_MAX = 100_000_000, 999_999_999

# Contract bound: `validate_vessels_df` rejects sog_kn outside [0, 60], so a
# faster-than-plausible report cannot be stored as-is. 60 kn is already well
# past any merchant vessel; anything above it is a decoding error, not a boat.
SOG_MAX_KN = 60.0

# Message types we consume. `PositionReport` (types 1/2/3) and
# `StandardClassBPositionReport` (type 18) carry positions;
# `ShipStaticData` (type 5) carries identity and dimensions, which arrive on a
# much slower cycle and are merged into the vessel record rather than archived
# as observations.
POSITION_TYPES = ("PositionReport", "StandardClassBPositionReport",
                  "ExtendedClassBPositionReport")
STATIC_TYPES = ("ShipStaticData", "StaticDataReport")
SUBSCRIBED_TYPES = list(POSITION_TYPES) + list(STATIC_TYPES)

# AIS ship-type code bands (ITU-R M.1371 table 53), mapped onto the frozen
# `vessel_type` vocabulary. Only the bands the contract has a word for are
# named; everything else is "other" rather than a guess.
_TYPE_BANDS = (
    (30, 30, "fishing"),
    (31, 32, "tug"),
    (52, 52, "tug"),
    (60, 69, "passenger"),
    (70, 79, "cargo"),
    (80, 89, "tanker"),
)


class AisStreamError(RuntimeError):
    """The provider said something we cannot act on."""


# --------------------------------------------------------------------------
# subscription
# --------------------------------------------------------------------------

def subscription_message(api_key: str,
                         bboxes: Iterable[Iterable[float]],
                         mmsis: Optional[Iterable[int]] = None,
                         message_types: Optional[Iterable[str]] = None) -> dict:
    """The frame that opens a subscription.

    `bboxes` are **longitude-first** ``[lon_min, lat_min, lon_max, lat_max]``,
    this repo's frozen convention. They are transposed to AISStream's
    latitude-first ``[[lat, lon], [lat, lon]]`` here and nowhere else. See the
    module docstring for why that matters.
    """
    if not api_key:
        raise AisStreamError(
            "AISSTREAM_API_KEY is not set. Live AIS needs a key from "
            "aisstream.io; without one the stream is NOT CONFIGURED, which is "
            "reported as such rather than silently producing no vessels.")

    boxes: list[list[list[float]]] = []
    for bbox in bboxes:
        lon_min, lat_min, lon_max, lat_max = (float(v) for v in bbox)
        if not (-180.0 <= lon_min <= 180.0 and -180.0 <= lon_max <= 180.0):
            raise AisStreamError(
                f"longitude out of range in bbox {bbox}; bboxes here are "
                f"[lon_min, lat_min, lon_max, lat_max] -- longitude first")
        if not (-90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0):
            raise AisStreamError(
                f"latitude out of range in bbox {bbox}; bboxes here are "
                f"[lon_min, lat_min, lon_max, lat_max] -- longitude first. A "
                f"latitude above 90 usually means the pair was swapped.")
        # Transposed HERE. South-west corner first, then north-east.
        boxes.append([[lat_min, lon_min], [lat_max, lon_max]])

    if not boxes:
        raise AisStreamError(
            "at least one bounding box is required; AISStream rejects an "
            "unbounded subscription")

    frame: dict[str, Any] = {
        "APIKey": api_key,
        "BoundingBoxes": boxes,
        "FilterMessageTypes": list(message_types or SUBSCRIBED_TYPES),
    }
    if mmsis:
        # AISStream expects MMSI as strings here.
        frame["FiltersShipMMSI"] = [str(int(m)) for m in mmsis]
    return frame


def redacted_subscription(frame: dict) -> dict:
    """The subscription frame with the key removed, for logging.

    The frame is the single most useful thing to log when a stream produces no
    vessels, and it carries the API key. Logging it whole would put a live
    credential in the log file the monitoring page displays.
    """
    out = dict(frame)
    if "APIKey" in out:
        out["APIKey"] = "[redacted]"
    return out


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

def vessel_type_from_code(code: Any) -> str:
    """AIS ship-type code -> the frozen `vessel_type` vocabulary."""
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "other"
    for low, high, name in _TYPE_BANDS:
        if low <= value <= high:
            return name
    return "other"


def _as_float(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) or math.isinf(out) else out


# AISStream's `MetaData.time_utc` is a Go `time.Time` rendered with `String()`,
# not ISO-8601:
#
#     2026-09-12 04:33:21.123456789 +0000 UTC
#
# Three things in that defeat `datetime.fromisoformat` on Python 3.10:
# a space instead of `T`, NINE fractional digits where it accepts at most six,
# and an offset written `+0000` where it requires `+00:00`. A hand-rolled
# chain of string surgery got two of the three and still returned None for
# every real message -- which would have shown up as a live stream that
# connects, reports healthy, and ingests nothing, blaming the provider.
#
# So the components are extracted explicitly. Anything the pattern does not
# match returns None and is counted as `no_timestamp`.
_TIME_RE = re.compile(
    r"^\s*(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})"
    r"[T ](?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"\s*(?P<tz>Z|z|[+-]\d{2}:?\d{2})?"
    r"(?:\s+(?:UTC|utc|GMT))?\s*$")


def _parse_time(value: Any) -> Optional[datetime]:
    """AISStream's `MetaData.time_utc` -> an aware UTC datetime, or None.

    An unparseable timestamp returns None rather than being replaced with
    "now". An observation stamped with its arrival time lands in the wrong
    archive bucket and silently corrupts every later time-windowed query --
    which is the one thing the archive exists to answer.
    """
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo
                else value.replace(tzinfo=timezone.utc))
    if not isinstance(value, str):
        return None

    match = _TIME_RE.match(value)
    if match is None:
        return None

    parts = match.groupdict()
    # Truncate rather than round: a nanosecond timestamp carries more
    # precision than a microsecond datetime, and rounding up could move an
    # observation across a second boundary and so across a dedup key.
    frac = (parts["frac"] or "")[:6].ljust(6, "0")

    offset = timezone.utc
    tz = parts["tz"]
    if tz and tz not in ("Z", "z"):
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        try:
            minutes = int(digits[:2]) * 60 + int(digits[2:4])
        except ValueError:
            return None
        offset = timezone(sign * timedelta(minutes=minutes))

    try:
        parsed = datetime(int(parts["y"]), int(parts["mo"]), int(parts["d"]),
                          int(parts["h"]), int(parts["mi"]), int(parts["s"]),
                          int(frac), tzinfo=offset)
    except ValueError:
        # A real date that does not exist (month 13, day 32, leap-second 60).
        return None
    return parsed.astimezone(timezone.utc)


@dataclass
class RejectCounts:
    """Why messages were dropped. Reported, never silently absorbed."""

    unparseable: int = 0
    unknown_type: int = 0
    bad_mmsi: int = 0
    no_position: int = 0
    bad_position: int = 0
    no_timestamp: int = 0
    duplicate: int = 0

    def total(self) -> int:
        return (self.unparseable + self.unknown_type + self.bad_mmsi
                + self.no_position + self.bad_position + self.no_timestamp
                + self.duplicate)

    def as_dict(self) -> dict:
        return {"unparseable": self.unparseable,
                "unknown_type": self.unknown_type,
                "bad_mmsi": self.bad_mmsi,
                "no_position": self.no_position,
                "bad_position": self.bad_position,
                "no_timestamp": self.no_timestamp,
                "duplicate": self.duplicate,
                "total": self.total()}


def _payload(frame: dict, message_type: str) -> dict:
    """The inner message body, whichever way the provider nested it."""
    message = frame.get("Message")
    if isinstance(message, dict):
        inner = message.get(message_type)
        if isinstance(inner, dict):
            return inner
        # Some relays put the fields directly under `Message`.
        if message and not any(isinstance(v, dict) for v in message.values()):
            return message
    return {}


def normalise_position(frame: dict,
                       rejects: Optional[RejectCounts] = None) -> Optional[dict]:
    """One AISStream position frame -> a normalised observation, or None.

    Returns a dict whose keys are deliberately close to the frozen
    `VESSEL_COLUMNS` names, so `ais.contract.to_contract` can project a batch
    with no field renaming in between.
    """
    counts = rejects if rejects is not None else RejectCounts()
    if not isinstance(frame, dict):
        counts.unparseable += 1
        return None

    message_type = frame.get("MessageType")
    if message_type not in POSITION_TYPES:
        counts.unknown_type += 1
        return None

    meta = frame.get("MetaData") if isinstance(frame.get("MetaData"), dict) else {}
    body = _payload(frame, message_type)

    # MMSI: `UserID` in the body is authoritative, `MetaData.MMSI` is the
    # relay's copy. Preferring the body means a relay that mangles its metadata
    # cannot silently re-attribute a position to a different vessel.
    raw_mmsi = body.get("UserID", meta.get("MMSI", meta.get("mmsi")))
    try:
        mmsi = int(raw_mmsi)
    except (TypeError, ValueError):
        counts.bad_mmsi += 1
        return None
    if not MMSI_MIN <= mmsi <= MMSI_MAX:
        counts.bad_mmsi += 1
        return None

    lat = _as_float(body.get("Latitude", meta.get("latitude")))
    lon = _as_float(body.get("Longitude", meta.get("longitude")))
    if lat is None or lon is None:
        counts.no_position += 1
        return None
    # The spec's "position not available" sentinels. Rejected outright: an AIS
    # observation with no position is not an observation.
    if abs(lat - LAT_UNAVAILABLE) < 1e-6 or abs(lon - LON_UNAVAILABLE) < 1e-6:
        counts.no_position += 1
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        counts.bad_position += 1
        return None

    report_utc = _parse_time(meta.get("time_utc") or meta.get("TimeUtc"))
    if report_utc is None:
        # NOT replaced with arrival time. A delayed message stamped "now"
        # lands in the wrong archive bucket and corrupts any later
        # time-windowed query.
        counts.no_timestamp += 1
        return None

    sog = _as_float(body.get("Sog"))
    if sog is not None and (sog >= SOG_UNAVAILABLE_TENTHS / 10.0
                            or sog < 0 or sog > SOG_MAX_KN):
        sog = None
    cog = _as_float(body.get("Cog"))
    if cog is not None and (cog >= COG_UNAVAILABLE_TENTHS / 10.0 or cog < 0):
        cog = None
    heading = _as_float(body.get("TrueHeading"))
    if heading is not None and (heading >= HEADING_UNAVAILABLE
                                or heading < 0 or heading > 360):
        # 511 means "not transmitting". NaN, never 0 -- see the module
        # docstring.
        heading = None

    nav = body.get("NavigationalStatus")
    try:
        nav_status = int(nav) if nav is not None else None
    except (TypeError, ValueError):
        nav_status = None

    return {
        "mmsi": mmsi,
        "timestamp_utc": report_utc,
        "lat": lat,
        "lon": lon,
        "sog_kn": sog,
        "cog_deg": cog,
        "heading_deg": heading,
        "nav_status": nav_status,
        "message_type": message_type,
        # The relay's name field, when it sends one. Absent is left absent --
        # the flagship's four ranked vessels all carried `vessel_name: null`
        # and that is the honest record.
        "vessel_name": (str(meta.get("ShipName")).strip() or None
                        if meta.get("ShipName") is not None else None),
        "source": "real",
        "provider": "AISStream",
    }


def normalise_static(frame: dict,
                     rejects: Optional[RejectCounts] = None) -> Optional[dict]:
    """A `ShipStaticData` frame -> identity and dimensions, or None.

    Static data arrives on a six-minute cycle against a two-to-ten-second
    position cycle, so it is merged into the vessel record rather than stored
    per observation. Dimensions come as four distances from the AIS antenna,
    not as a length and a beam -- summing them is the whole conversion.
    """
    counts = rejects if rejects is not None else RejectCounts()
    if not isinstance(frame, dict):
        counts.unparseable += 1
        return None
    message_type = frame.get("MessageType")
    if message_type not in STATIC_TYPES:
        counts.unknown_type += 1
        return None

    meta = frame.get("MetaData") if isinstance(frame.get("MetaData"), dict) else {}
    body = _payload(frame, message_type)

    raw_mmsi = body.get("UserID", meta.get("MMSI", meta.get("mmsi")))
    try:
        mmsi = int(raw_mmsi)
    except (TypeError, ValueError):
        counts.bad_mmsi += 1
        return None
    if not MMSI_MIN <= mmsi <= MMSI_MAX:
        counts.bad_mmsi += 1
        return None

    dim = body.get("Dimension") if isinstance(body.get("Dimension"), dict) else {}
    bow = _as_float(dim.get("A"))
    stern = _as_float(dim.get("B"))
    port = _as_float(dim.get("C"))
    starboard = _as_float(dim.get("D"))
    length = (bow + stern) if (bow is not None and stern is not None) else None
    width = (port + starboard) if (port is not None
                                   and starboard is not None) else None
    # A zero dimension means "not transmitted", not "a boat of length zero".
    length = length if length and length > 0 else None
    width = width if width and width > 0 else None

    draught = _as_float(body.get("MaximumStaticDraught"))
    draught = draught if draught and draught > 0 else None

    name = body.get("Name", meta.get("ShipName"))
    callsign = body.get("CallSign")
    imo = body.get("ImoNumber")
    try:
        imo = int(imo) if imo else None
    except (TypeError, ValueError):
        imo = None

    return {
        "mmsi": mmsi,
        "vessel_name": (str(name).strip() or None) if name is not None else None,
        "callsign": (str(callsign).strip() or None) if callsign is not None else None,
        "imo": imo,
        "vessel_type": vessel_type_from_code(body.get("Type")),
        "ais_ship_type": (int(body["Type"])
                          if str(body.get("Type", "")).lstrip("-").isdigit()
                          else None),
        "length_m": length,
        "width_m": width,
        "draught_m": draught,
        "destination": (str(body.get("Destination")).strip() or None
                        if body.get("Destination") is not None else None),
        "message_type": message_type,
        "provider": "AISStream",
    }


# --------------------------------------------------------------------------
# de-duplication
# --------------------------------------------------------------------------

class Deduplicator:
    """Rejects a position report already seen, with a bounded memory footprint.

    AIS positions arrive more than once: the same transmission reaches several
    receivers and the relay forwards each copy. Storing all of them inflates
    row counts, biases any per-vessel report-rate statistic, and makes an "AIS
    gap" score meaningless.

    The key is ``(mmsi, timestamp to the second, lat/lon to 5 decimals)``.
    Deliberately NOT ``(mmsi, timestamp)`` alone: a vessel at anchor legitimately
    transmits several distinct reports within the same second, and collapsing
    those would discard real observations.

    Memory is bounded by `capacity` with FIFO eviction rather than an LRU,
    because the access pattern is a moving time window -- the oldest key is
    almost always the least useful, and FIFO needs no per-hit bookkeeping. A
    duplicate arriving after its key was evicted is stored twice; that is the
    accepted cost of a fixed memory ceiling on an unbounded stream, and the
    window is sized so it only happens across a gap longer than `capacity`
    messages.
    """

    __slots__ = ("capacity", "_seen", "_order")

    def __init__(self, capacity: int = 200_000):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._seen: set[tuple] = set()
        self._order: list[tuple] = []

    @staticmethod
    def key(observation: dict) -> tuple:
        ts = observation["timestamp_utc"]
        return (int(observation["mmsi"]),
                int(ts.timestamp()),
                round(float(observation["lat"]), 5),
                round(float(observation["lon"]), 5))

    def seen(self, observation: dict) -> bool:
        """True if this exact report has already been accepted."""
        k = self.key(observation)
        if k in self._seen:
            return True
        self._seen.add(k)
        self._order.append(k)
        if len(self._order) > self.capacity:
            # Evict a block rather than one key per insert: popping from the
            # front of a list is O(n), so doing it every message would make
            # ingest quadratic at capacity.
            drop = self._order[: self.capacity // 10]
            self._order = self._order[self.capacity // 10:]
            self._seen.difference_update(drop)
        return False

    def __len__(self) -> int:
        return len(self._seen)


@dataclass
class StreamBatch:
    """What one drain of the ingest queue produced."""

    positions: list[dict] = field(default_factory=list)
    statics: list[dict] = field(default_factory=list)
    rejects: RejectCounts = field(default_factory=RejectCounts)
    received: int = 0

    def summary(self) -> dict:
        return {"received": self.received,
                "positions": len(self.positions),
                "statics": len(self.statics),
                "rejected": self.rejects.as_dict()}


def normalise_batch(frames: Iterable[dict],
                    dedup: Optional[Deduplicator] = None) -> StreamBatch:
    """Normalise, validate and de-duplicate a batch of raw frames.

    The whole read path in one function so the ingest worker does not have to
    reimplement the ordering, and so a test can drive it with a list of frames
    and no socket.
    """
    batch = StreamBatch()
    for frame in frames:
        batch.received += 1
        message_type = frame.get("MessageType") if isinstance(frame, dict) else None
        if message_type in STATIC_TYPES:
            static = normalise_static(frame, batch.rejects)
            if static is not None:
                batch.statics.append(static)
            continue
        observation = normalise_position(frame, batch.rejects)
        if observation is None:
            continue
        if dedup is not None and dedup.seen(observation):
            batch.rejects.duplicate += 1
            continue
        batch.positions.append(observation)
    return batch
