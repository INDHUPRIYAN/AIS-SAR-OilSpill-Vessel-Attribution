"""The AOI watcher: poll registered AOIs, open an investigation per new scene.

STAGE 0 of the flow in design doc v2 §4, and item 8 of the §27 plan.

One tick, per enabled AOI:

    due?  ──no──►  skip (its poll_minutes has not elapsed)
     │yes
     ▼
    search CDSE → ASF → LocalCache for scenes over the AOI bbox
     │                                        │
     │                                    failure
     ▼                                        ▼
    keep scenes acquired AFTER the         mark the AOI DEGRADED with the
    high-water mark (first poll: after     error class and move on. §11:
    now - lookback_hours)                  no dependency halts anything.
     │
     ▼
    per new scene: open an Investigation, and if auto_run, start the pipeline
     │
     ▼
    advance the high-water mark to the newest scene handled

Everything is driven off ``config/aois.yaml``; nothing about a particular
coastline is written here.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

# The sibling services are separate import roots, not installed packages
# (pyproject is dependencies-only by design, so `pip install -e .` adds
# nothing to sys.path). `pytest.ini` declares those roots, which is why the
# scheduler's tests passed while the deployed path raised
# `ModuleNotFoundError: No module named 'satellite'` on every poll -- the
# suite never exercised the environment uvicorn actually runs in (audit P2/C7).
#
# Same bootstrap as backend/services/pipeline/ais_index.py, which is why the
# AIS store keeps working under uvicorn while this did not.
REPO_ROOT = Path(__file__).resolve().parents[4]
for _root in ("scene_service", "ais_service", "metocean_service"):
    _path = str(REPO_ROOT / _root)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from backend.models.db import AoiWatch, Investigation, SessionLocal, utcnow  # noqa: E402
from backend.services.scheduler.aoi import AOI, AOIConfigError, load_aois  # noqa: E402

log = logging.getLogger(__name__)

#: Error classes shared with every other adapter in the system (§11).
ERROR_CLASSES = ("AUTH_FAILED", "TIMEOUT", "RATE_LIMITED", "UNAVAILABLE",
                 "BAD_RESPONSE", "NONE")


@dataclass
class WatchTick:
    """What one sweep of the watcher did. Returned so a test or the API can assert."""

    started_utc: datetime
    polled: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    disabled: List[str] = field(default_factory=list)
    new_scenes: List[Dict[str, Any]] = field(default_factory=list)
    opened: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"started_utc": self.started_utc, "polled": self.polled,
                "skipped": self.skipped, "disabled": self.disabled,
                "new_scenes": self.new_scenes, "opened": self.opened,
                "errors": self.errors}


def _classify(exc: BaseException) -> str:
    """Map an adapter exception onto the shared error taxonomy (§11)."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timed out" in text:
        return "TIMEOUT"
    if any(t in text for t in ("401", "403", "unauthorized", "forbidden",
                               "credential", "token")):
        return "AUTH_FAILED"
    if "429" in text or "rate limit" in text:
        return "RATE_LIMITED"
    if any(t in text for t in ("connection", "unreachable", "resolve", "dns",
                               "503", "502")):
        return "UNAVAILABLE"
    return "BAD_RESPONSE"


def _acquired(scene: Any) -> Optional[datetime]:
    """UTC acquisition time of a SceneMetadata, whatever shape it arrives in."""
    value = getattr(scene, "acquisition_time", None)
    if value is None and isinstance(scene, dict):
        value = scene.get("acquisition_time") or scene.get("acquired_utc")
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    # Naive means UTC here, not local -- Standing Rule 1.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _scene_id(scene: Any) -> Optional[str]:
    return getattr(scene, "scene_id", None) or (
        scene.get("scene_id") if isinstance(scene, dict) else None)


class AOIWatcher:
    """Polls registered AOIs and opens investigations for new Sentinel-1 passes.

    ``search`` and ``start_run`` are injected so the watcher can be tested
    without a network or a running pipeline, and so the search chain stays the
    scene service's business rather than being reimplemented here.
    """

    def __init__(self,
                 search: Optional[Callable[..., Any]] = None,
                 start_run: Optional[Callable[[str, AOI, Any], Optional[str]]] = None,
                 config_path=None,
                 max_scenes_per_poll: int = 3):
        self._search = search
        self._start_run = start_run
        self.config_path = config_path
        # A bound on how many investigations one tick can open. An AOI enabled
        # with a wide lookback over a busy strait can legitimately return a
        # dozen scenes; opening a dozen full pipeline runs at once would swamp
        # a single-GPU machine mid-demo. The rest are picked up next tick,
        # because the high-water mark only advances past what was handled.
        self.max_scenes_per_poll = max_scenes_per_poll
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- the search chain, resolved lazily --------------------------------

    def search_scenes(self, bbox, start_time, end_time, top: int = 10):
        if self._search is not None:
            return self._search(bbox=bbox, start_time=start_time,
                                end_time=end_time, top=top)
        # Imported here, not at module import: the scene service pulls in
        # network adapters, and the API must boot with zero credentials (§19).
        from satellite.chain import SceneRetrievalChain

        return SceneRetrievalChain().search_scenes(
            bbox=list(bbox), start_time=start_time, end_time=end_time, top=top)

    # -- one AOI ----------------------------------------------------------

    def _is_due(self, aoi: AOI, state: AoiWatch, now: datetime) -> bool:
        if state.last_polled_utc is None:
            return True
        last = state.last_polled_utc
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return now - last >= timedelta(minutes=aoi.poll_minutes)

    def _window_start(self, aoi: AOI, state: AoiWatch, now: datetime) -> datetime:
        """How far back this poll looks.

        First poll: `lookback_hours`, so enabling an AOI does not open an
        investigation for the whole archive. After that: the high-water mark,
        so nothing between two polls is missed however long the gap was.
        """
        mark = state.last_scene_time_utc
        if mark is None:
            return now - timedelta(hours=aoi.lookback_hours)
        return mark if mark.tzinfo else mark.replace(tzinfo=timezone.utc)

    def poll_aoi(self, aoi: AOI, db, now: Optional[datetime] = None,
                 tick: Optional[WatchTick] = None,
                 dry_run: bool = False) -> List[Dict[str, Any]]:
        """Search one AOI and open an investigation per new scene.

        Returns the investigations opened. Never raises: a provider failure
        marks the AOI DEGRADED and is reported through ``tick.errors``, because
        §11 is explicit that no dependency may halt anything.

        ``dry_run`` searches and reports what it *would* open, and advances no
        state at all -- not the high-water mark, and not the counters. Anything
        less would make a dry run consume the scenes it was only supposed to
        preview, or double-count them on the real poll that follows.
        """
        now = now or utcnow()
        state = db.get(AoiWatch, aoi.id)
        if state is None:
            state = AoiWatch(aoi_id=aoi.id)
            db.add(state)

        since = self._window_start(aoi, state, now)
        if not dry_run:
            state.last_polled_utc = now
            state.polls = (state.polls or 0) + 1

        try:
            result = self.search_scenes(aoi.bbox, since, now)
        except Exception as exc:
            error_class = _classify(exc)
            detail = f"{type(exc).__name__}: {exc}"[:500]
            if not dry_run:
                state.status = "DEGRADED"
                state.last_error_class = error_class
                state.last_error = detail
                state.consecutive_failures = (state.consecutive_failures or 0) + 1
            db.commit()
            log.warning("aoi %s: search failed (%s): %s", aoi.id, error_class, exc)
            if tick is not None:
                tick.errors.append({"aoi_id": aoi.id, "error_class": error_class,
                                    "detail": detail})
            return []

        scenes = list(getattr(result, "scenes", None) or [])
        # Strictly after the high-water mark: a scene already handled must not
        # reappear just because the search window is inclusive at its start.
        fresh = []
        for scene in scenes:
            acquired = _acquired(scene)
            if acquired is None or _scene_id(scene) is None:
                continue
            if acquired <= since:
                continue
            fresh.append((acquired, scene))
        fresh.sort(key=lambda pair: pair[0])

        opened: List[Dict[str, Any]] = []
        if not dry_run:
            state.status = "WORKING"
            state.last_error_class = "NONE"
            state.last_error = None
            state.consecutive_failures = 0
            state.scenes_seen = (state.scenes_seen or 0) + len(fresh)

            for acquired, scene in fresh[: self.max_scenes_per_poll]:
                record = self._open_investigation(aoi, scene, acquired, db)
                if record is not None:
                    opened.append(record)
                # Advance the mark only past scenes actually handled, so the
                # ones trimmed by max_scenes_per_poll come back next tick.
                state.last_scene_id = _scene_id(scene)
                state.last_scene_time_utc = acquired

            state.investigations_opened = (state.investigations_opened or 0) + len(opened)
        db.commit()

        if tick is not None:
            tick.new_scenes += [{"aoi_id": aoi.id, "scene_id": _scene_id(s),
                                 "acquired_utc": a} for a, s in fresh]
            tick.opened += opened
        return opened

    def _open_investigation(self, aoi: AOI, scene: Any, acquired: datetime,
                            db) -> Optional[Dict[str, Any]]:
        """Create the Investigation row for one new scene, and maybe run it."""
        scene_id = _scene_id(scene)
        inv_id = f"aoi-{aoi.id}-{acquired.strftime('%Y%m%dT%H%M%S')}"
        if db.get(Investigation, inv_id) is not None:
            # Same AOI, same acquisition: already handled. Belt and braces
            # behind the high-water mark, because a duplicate investigation is
            # a duplicate full pipeline run.
            log.debug("aoi %s: investigation %s already exists", aoi.id, inv_id)
            return None

        inv = Investigation(
            id=inv_id,
            name=f"{aoi.name} — {acquired.strftime('%Y-%m-%d %H:%M')}Z",
            scene_id=scene_id,
            scene_path=getattr(scene, "file_path", None),
            bbox=json.dumps(list(aoi.bbox)),
            # The AIS provenance of this AOI travels with the investigation, so
            # a synthetic-AIS run is declared up front rather than discovered
            # when Stage 6 falls back (§8, §23 Scenario B, Standing Rule 9).
            notes=json.dumps({
                "opened_by": "scheduler",
                "aoi_id": aoi.id,
                "ais_region": aoi.ais_region,
                "ais_provenance": "real" if aoi.has_real_ais else "synthetic",
                "acquired_utc": acquired.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }))
        db.add(inv)
        db.commit()

        record = {"investigation_id": inv_id, "aoi_id": aoi.id,
                  "scene_id": scene_id, "acquired_utc": acquired,
                  "ais_provenance": "real" if aoi.has_real_ais else "synthetic",
                  "run_id": None, "auto_run": aoi.auto_run}

        if aoi.auto_run and self._start_run is not None:
            try:
                record["run_id"] = self._start_run(inv_id, aoi, scene)
            except Exception as exc:
                # The investigation stands even if the run could not start: a
                # human can press the button. Losing the investigation would
                # lose the fact that a scene arrived at all.
                log.warning("aoi %s: could not auto-start run for %s: %s",
                            aoi.id, inv_id, exc)
                record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return record

    # -- one sweep --------------------------------------------------------

    def tick(self, now: Optional[datetime] = None,
             aois: Optional[Sequence[AOI]] = None,
             dry_run: bool = False) -> WatchTick:
        """One sweep over every registered AOI."""
        now = now or utcnow()
        result = WatchTick(started_utc=now)

        try:
            registry = list(aois) if aois is not None else load_aois(self.config_path)
        except AOIConfigError as exc:
            # A broken registry is a configuration error, not a provider
            # outage: report it as such rather than silently watching nothing.
            log.error("AOI registry unusable: %s", exc)
            result.errors.append({"aoi_id": "*", "error_class": "BAD_RESPONSE",
                                  "detail": str(exc)})
            return result

        with SessionLocal() as db:
            for aoi in registry:
                if not aoi.enabled:
                    result.disabled.append(aoi.id)
                    continue
                state = db.get(AoiWatch, aoi.id)
                if state is not None and not self._is_due(aoi, state, now):
                    result.skipped.append(aoi.id)
                    continue
                result.polled.append(aoi.id)
                self.poll_aoi(aoi, db, now=now, tick=result, dry_run=dry_run)
        return result

    # -- background loop --------------------------------------------------

    def start(self, interval_seconds: int = 300) -> None:
        """Run ``tick`` on a timer in a daemon thread.

        The loop interval is not the poll cadence: each AOI's own
        ``poll_minutes`` decides whether it is due. This just wakes up often
        enough that a 60-minute AOI is polled within a few minutes of becoming
        due, without every AOI sharing one clock.
        """
        if self._thread is not None and self._thread.is_alive():
            return

        def _loop() -> None:
            # Let the app finish starting before the first sweep.
            self._stop.wait(10)
            while not self._stop.is_set():
                try:
                    tick = self.tick()
                    if tick.opened:
                        log.info("AOI watcher opened %d investigation(s): %s",
                                 len(tick.opened),
                                 ", ".join(o["investigation_id"] for o in tick.opened))
                except Exception:
                    # A watcher crash must never take down the API.
                    log.exception("AOI watcher tick failed; retrying next interval")
                self._stop.wait(interval_seconds)

        self._stop.clear()
        self._thread = threading.Thread(target=_loop, name="aoi-watcher",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
