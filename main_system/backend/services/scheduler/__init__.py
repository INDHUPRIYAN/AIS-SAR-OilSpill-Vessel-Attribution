"""Scheduler / AOI watcher -- STAGE 0 of the pipeline (design doc v2 §4, §27·8).

    STAGE 0  TRIGGER
      Scheduler detects a new S1 pass over a registered AOI
      (or an analyst submits a manual investigation: bbox + time window)

§27·8 calls this "small effort, large narrative payoff", and that is exactly
right: it is the difference between a tool an analyst drives and a system that
watches a coastline and tells them. Everything downstream already exists; this
only has to notice a new scene and press the button.

The three things that make it safe to leave running unattended:

* **It never replays.** Every AOI's watch state (last poll, last scene seen)
  is persisted. A restart resumes; it does not re-open investigations for
  scenes it already handled. On the *first* ever poll a `lookback_hours` bound
  applies, so enabling an AOI does not open an investigation for the entire
  Sentinel-1 archive.
* **It never halts.** A provider outage marks the AOI degraded with the error
  class and moves to the next one (§11: "no single dependency can halt a
  run"). The next tick retries.
* **It never lies about AIS.** An AOI whose `ais_region` is null is one where
  no public bulk AIS exists (§8). That fact is carried onto the investigation
  it opens, so Stage 6's synthetic fallback is a declared property of the AOI
  rather than a silent substitution discovered at the end of a demo.

Configuration is ``main_system/config/aois.yaml`` (§21). Nothing here hardcodes
an AOI.
"""

from .aoi import AOI, AOIConfigError, load_aois
from .watcher import AOIWatcher, WatchTick

__all__ = ["AOI", "AOIConfigError", "load_aois", "AOIWatcher", "WatchTick"]
