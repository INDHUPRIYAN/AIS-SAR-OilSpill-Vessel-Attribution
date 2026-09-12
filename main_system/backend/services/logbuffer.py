"""In-process structured log capture, for the System Logs page.

WHY THIS EXISTS AT ALL
----------------------
The System Logs page needs records to show. Before this there was no log
store: the application wrote diagnostics with `print()` and the `logging`
calls in the service modules went to whatever handler the host happened to
configure, which in the dev server is stdout and in a container is the
container log. Neither is queryable from a browser, so a "System Logs" page
would have had to either shell out to the host or show nothing.

WHAT IT IS, AND WHAT IT IS NOT
------------------------------
A **bounded in-memory ring buffer** attached to the root logger. That is a
deliberate choice with real limits, all of which the API states rather than
implies:

  * **It is lost on restart.** This is a live operational view, not an audit
    trail. The audit trail is `audit_log`, it is hash-chained, and it is the
    thing that survives. Presenting an in-memory buffer as an audit record
    would be the more dangerous mistake, so the two are kept visibly separate.

  * **It is bounded.** The oldest record is evicted at `capacity`. The API
    reports `dropped` so a reader can tell a quiet hour from a buffer that
    overflowed.

  * **It captures `logging`, not `print`.** A handler cannot see a bare
    `print()`. The remaining prints in the boot path were converted to
    `logging` calls for exactly this reason; `captures` in the status payload
    names the limitation so nobody reads an empty buffer as a quiet system.

A deployment that needs durable logs points a real handler at a file or a log
service; this buffer is additive and does not replace that.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional

# ~1 MB of records at typical size. Large enough to hold a full pipeline run's
# diagnostics (a run logs on the order of a hundred lines), small enough to be
# irrelevant to process memory.
DEFAULT_CAPACITY = 5000

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# The page offers these four; WARNING is spelled WARN in the spec's wording and
# both are accepted on the query so a UI written from either reads the same.
LEVEL_ALIASES = {"WARN": "WARNING", "WARNING": "WARNING",
                 "INFO": "INFO", "ERROR": "ERROR",
                 "CRITICAL": "CRITICAL", "FATAL": "CRITICAL",
                 "DEBUG": "DEBUG"}


class RingBufferHandler(logging.Handler):
    """Keeps the last `capacity` records, with a lock and a drop counter."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        super().__init__()
        self.capacity = capacity
        self._records: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._dropped = 0
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Formatted HERE, in the emitting thread, because `record.args` is
            # not guaranteed to still be valid later -- a mutable object logged
            # with %s can be mutated before a deferred format runs, so the
            # stored line would not be the line that was logged.
            message = record.getMessage()
            exc_text = None
            if record.exc_info:
                exc_text = self.format(record).split("\n", 1)[-1]

            entry = {
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                # UTC, like every other timestamp in this system. `record`
                # carries epoch seconds, which is timezone-free.
                "utc": datetime.fromtimestamp(record.created,
                                              tz=timezone.utc),
                "module": record.module,
                "func": record.funcName,
                "line": record.lineno,
                "thread": record.threadName,
                "process": record.process,
                "exception": exc_text,
                # Extra fields a caller attached with `extra=`. This is how a
                # record gets tied to a run, job or incident, which is what
                # makes the page filterable by those.
                "run_id": getattr(record, "run_id", None),
                "job_id": getattr(record, "job_id", None),
                "incident_id": getattr(record, "incident_id", None),
                "zone_id": getattr(record, "zone_id", None),
                "actor": getattr(record, "actor", None),
                "provider": getattr(record, "provider", None),
            }
            with self._lock:
                if len(self._records) == self.capacity:
                    self._dropped += 1
                self._seq += 1
                entry["seq"] = self._seq
                self._records.append(entry)
        except Exception:                          # noqa: BLE001
            # A logging handler that raises breaks the caller it was logging
            # for. Never propagate.
            self.handleError(record)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._records)

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def clear(self) -> int:
        with self._lock:
            count = len(self._records)
            self._records.clear()
            self._dropped = 0
            return count


_handler: Optional[RingBufferHandler] = None
_installed_utc: Optional[datetime] = None
_lock = threading.Lock()


def install(capacity: int = DEFAULT_CAPACITY,
            level: int = logging.INFO) -> RingBufferHandler:
    """Attach the buffer to the root logger. Idempotent.

    Attaches to the ROOT logger rather than to named ones, so a module that
    starts logging later is captured without anyone remembering to register
    it. The level is INFO: DEBUG from third-party libraries (urllib3,
    matplotlib, botocore) would fill the buffer with noise and evict the
    records an operator came to read.
    """
    global _handler, _installed_utc
    with _lock:
        if _handler is not None:
            return _handler

        root = logging.getLogger()
        # Detach any buffer left by a PREVIOUS import of this module before
        # attaching a new one.
        #
        # The module-level `_handler` guard is not sufficient. The root logger
        # is not in `sys.modules`, so a process that re-imports `backend.*`
        # -- which every test module here does, deliberately, to pick up a
        # fresh DATABASE_URL -- gets a fresh `_handler = None` while the old
        # handler is STILL attached to the root logger. Without this sweep the
        # handlers accumulate one per re-import, each holding up to `capacity`
        # records, and every log call fans out to all of them.
        # Matched by class NAME, not with `isinstance`. After a
        # `sys.modules` purge the re-imported `RingBufferHandler` is a
        # DIFFERENT class object from the one the stale handlers were built
        # from, so `isinstance(existing, RingBufferHandler)` is False for
        # exactly the handlers this sweep exists to remove -- the fix would
        # have looked right and done nothing.
        for existing in list(root.handlers):
            if type(existing).__name__ == "RingBufferHandler":
                root.removeHandler(existing)

        handler = RingBufferHandler(capacity)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
        # Only raise the root level if it is currently higher, so a deployment
        # that deliberately set DEBUG keeps it.
        if root.level == logging.NOTSET or root.level > level:
            root.setLevel(level)
        # Third-party loggers that are pure noise at INFO. Named explicitly
        # rather than filtered by prefix, so a new dependency's useful INFO
        # records are not silently discarded.
        for noisy in ("urllib3", "botocore", "boto3", "matplotlib",
                      "PIL", "asyncio", "websockets.client",
                      "websockets.protocol"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        _handler = handler
        _installed_utc = datetime.now(timezone.utc)
        return handler


def handler() -> Optional[RingBufferHandler]:
    return _handler


def installed_utc() -> Optional[datetime]:
    return _installed_utc


def query(level: Optional[str] = None,
          logger: Optional[str] = None,
          contains: Optional[str] = None,
          run_id: Optional[str] = None,
          job_id: Optional[str] = None,
          incident_id: Optional[str] = None,
          since: Optional[datetime] = None,
          until: Optional[datetime] = None,
          limit: int = 200,
          offset: int = 0) -> dict:
    """Filtered, newest-first slice of the buffer.

    `level` is a MINIMUM, not an exact match: asking for WARNING returns
    warnings, errors and criticals. An exact-match filter would hide a
    CRITICAL from somebody filtering for "problems", which is the opposite of
    what the filter is for.
    """
    buf = _handler
    if buf is None:
        return {
            "entries": [], "total": 0, "offset": offset, "limit": limit,
            "capturing": False,
            "note": ("the log buffer is not installed in this process, so no "
                     "records are being captured. This is not an empty log."),
        }

    rows = buf.snapshot()

    min_level = None
    if level:
        canonical = LEVEL_ALIASES.get(level.strip().upper())
        if canonical is None:
            raise ValueError(
                f"level must be one of {sorted(set(LEVEL_ALIASES))}")
        min_level = logging.getLevelName(canonical)
        if not isinstance(min_level, int):
            min_level = logging.INFO

    def keep(entry: dict) -> bool:
        if min_level is not None:
            value = logging.getLevelName(entry["level"])
            if not isinstance(value, int) or value < min_level:
                return False
        if logger and logger.lower() not in (entry["logger"] or "").lower():
            return False
        if contains and contains.lower() not in (entry["message"] or "").lower():
            return False
        if run_id and entry.get("run_id") != run_id:
            return False
        if job_id and entry.get("job_id") != job_id:
            return False
        if incident_id and entry.get("incident_id") != incident_id:
            return False
        stamp = entry["utc"]
        if since and stamp < since:
            return False
        if until and stamp > until:
            return False
        return True

    matched = [r for r in rows if keep(r)]
    matched.reverse()                              # newest first
    page = matched[offset:offset + limit]

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["level"]] = counts.get(row["level"], 0) + 1

    return {
        "entries": page,
        "total": len(matched),
        "offset": offset,
        "limit": limit,
        "buffered": len(rows),
        "capacity": buf.capacity,
        # Reported so a reader can tell a quiet hour from an overflowed
        # buffer. Without it, "12 records" is ambiguous.
        "dropped": buf.dropped,
        "by_level": counts,
        "capturing": True,
        "capturing_since_utc": _installed_utc,
        "loggers": sorted({r["logger"] for r in rows}),
        "levels": list(LEVELS),
        # The limits, stated with the data. See the module docstring.
        "note": ("live in-memory buffer: lost on restart, bounded at "
                 f"{buf.capacity} records, and it captures `logging` calls "
                 "only -- not bare `print()` output. The durable record of "
                 "who did what is the hash-chained audit trail at /api/audit, "
                 "which is a different thing and survives a restart."),
    }
