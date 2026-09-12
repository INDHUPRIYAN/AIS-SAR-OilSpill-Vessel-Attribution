"""System Logs and the worker monitor.

Two classes of assertion here.

**The log buffer must be real.** Before this there was no log store at all --
diagnostics went to stdout via `print()` — so a System Logs page could only
have shown nothing. The tests check that records actually arrive, that a level
filter is a MINIMUM rather than an exact match (so filtering for "problems"
cannot hide a CRITICAL), and that the response states its own limits: it is
lost on restart, it is bounded, and it cannot see `print()`.

**The worker monitor must not invent a fleet.** This deployment is one
process. A monitor that showed a worker grid, a GPU utilisation bar and a
heartbeat column would be describing infrastructure that does not exist. So:
disabled workers are listed as disabled with the setting that would enable
them, GPU says it was not measured, and processing-time percentiles are absent
rather than zero when nothing has finished.

The security assertion is the one that would matter most if it broke: the
ephemeral admin token must never reach the buffer, because /api/logs is
readable by every authenticated role.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))

PASSWORD = "ops-suite-password"


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("ops_root")
    os.environ["DATA_ROOT"] = str(root)
    os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'ops.db').as_posix()}"
    os.environ["SECRET_KEY"] = "o" * 64
    os.environ.pop("OT_ADMIN_EMAIL", None)
    os.environ.pop("OT_ADMIN_PASSWORD", None)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]

    from fastapi.testclient import TestClient

    from backend.core import security
    from backend.main import app
    from backend.models.db import ROLES, SessionLocal, User, init_db

    init_db()
    with SessionLocal() as db:
        for role in ROLES:
            db.add(User(email=f"{role}@example.invalid",
                        password_hash=security.hash_password(PASSWORD),
                        display_name=role, role=role, active=True))
        db.commit()

    with TestClient(app, base_url="https://testserver") as client:
        yield client, root

    os.environ.pop("DATABASE_URL", None)


def _as(client, role):
    client.cookies.clear()
    r = client.post("/api/auth/login",
                    json={"email": f"{role}@example.invalid",
                          "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


# --------------------------------------------------------------------------
# the buffer
# --------------------------------------------------------------------------

def test_the_buffer_is_installed_and_capturing(env):
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/logs").json()
    assert body["capturing"] is True, (
        "nothing is being captured, so the System Logs page would show an "
        "empty buffer that looks like a quiet system")
    assert body["capturing_since_utc"]
    assert body["capacity"] > 0


def test_boot_diagnostics_are_in_the_buffer(env):
    """The reason the boot `print()`s were converted. A buffer attached after
    startup misses exactly the records worth reading when a deployment comes
    up wrong."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/logs", params={"limit": 500}).json()
    loggers = set(body["loggers"])
    assert any(name.startswith("oceantrace") for name in loggers), \
        f"no oceantrace logger appears in the buffer; loggers={sorted(loggers)}"


def test_a_logged_record_appears(env):
    client, _ = env
    logging.getLogger("oceantrace.test").warning(
        "ops-suite marker: a slick was not detected")

    _as(client, "auditor")
    body = client.get("/api/logs", params={"q": "ops-suite marker"}).json()
    assert body["total"] >= 1
    entry = body["entries"][0]
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "oceantrace.test"
    assert "slick was not detected" in entry["message"]
    assert entry["utc"], "a record with no timestamp is not a log record"


def test_extra_fields_make_records_filterable_by_run(env):
    """This is what lets the page filter by incident, job and run rather than
    only by substring."""
    client, _ = env
    logging.getLogger("oceantrace.pipeline").error(
        "stage failed", extra={"run_id": "run-ops-1", "job_id": "job-ops-1"})

    _as(client, "auditor")
    body = client.get("/api/logs", params={"run_id": "run-ops-1"}).json()
    assert body["total"] == 1
    assert body["entries"][0]["job_id"] == "job-ops-1"

    other = client.get("/api/logs", params={"run_id": "run-does-not-exist"}).json()
    assert other["total"] == 0


def test_the_level_filter_is_a_minimum_not_an_exact_match(env):
    """Filtering for WARN must not hide a CRITICAL. The opposite behaviour is
    the whole reason an operator uses the filter."""
    client, _ = env
    log = logging.getLogger("oceantrace.levels")
    log.info("ops-levels info line")
    log.warning("ops-levels warning line")
    log.error("ops-levels error line")
    log.critical("ops-levels critical line")

    _as(client, "auditor")
    warn = client.get("/api/logs",
                      params={"level": "WARN", "q": "ops-levels",
                              "limit": 100}).json()
    levels = {e["level"] for e in warn["entries"]}
    assert "WARNING" in levels
    assert "ERROR" in levels
    assert "CRITICAL" in levels
    assert "INFO" not in levels

    info = client.get("/api/logs",
                      params={"level": "INFO", "q": "ops-levels",
                              "limit": 100}).json()
    assert info["total"] > warn["total"]


def test_both_warn_and_warning_are_accepted(env):
    """The spec writes WARN, python writes WARNING. A UI built from either
    must not get a 422."""
    client, _ = env
    _as(client, "auditor")
    for spelling in ("WARN", "WARNING", "warn", "critical", "FATAL"):
        r = client.get("/api/logs", params={"level": spelling})
        assert r.status_code == 200, f"{spelling}: {r.text}"


def test_an_unknown_level_is_refused_with_the_valid_set(env):
    client, _ = env
    _as(client, "auditor")
    r = client.get("/api/logs", params={"level": "SPICY"})
    assert r.status_code == 422
    assert "level must be one of" in r.json()["detail"]


def test_the_response_states_its_own_limits(env):
    """A live buffer presented as an audit trail is the more dangerous
    mistake, so the two are kept visibly separate."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/logs").json()
    note = body["note"]
    assert "lost on restart" in note
    assert "print()" in note
    assert "/api/audit" in note
    # And the fields that let a reader tell a quiet hour from an overflow.
    assert "dropped" in body and "buffered" in body


def test_the_buffer_is_bounded_and_counts_what_it_dropped(env):
    from backend.services.logbuffer import RingBufferHandler

    handler = RingBufferHandler(capacity=50)
    log = logging.getLogger("ops.bounded")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        for i in range(200):
            log.info("line %d", i)
    finally:
        log.removeHandler(handler)

    assert len(handler.snapshot()) == 50
    assert handler.dropped == 150
    # The newest survive, not the oldest.
    assert "line 199" in handler.snapshot()[-1]["message"]


def test_a_mutated_argument_cannot_rewrite_a_stored_record(env):
    """Records are formatted in the emitting thread on purpose: `record.args`
    can be mutated before a deferred format runs, so a stored line would not
    be the line that was logged."""
    from backend.services.logbuffer import RingBufferHandler

    handler = RingBufferHandler(capacity=10)
    log = logging.getLogger("ops.mutation")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        payload = {"stage": "detect"}
        log.info("state=%s", payload)
        payload["stage"] = "REWRITTEN"
    finally:
        log.removeHandler(handler)

    assert "detect" in handler.snapshot()[0]["message"]
    assert "REWRITTEN" not in handler.snapshot()[0]["message"]


def test_a_handler_error_never_propagates_to_the_caller(env):
    """A logging handler that raises breaks the code it was logging for.

    `propagate = False` matters: pytest attaches its own capture handler to
    the root logger and DELIBERATELY re-raises formatting errors so they are
    not hidden during a test run. Without isolation this would exercise
    pytest's handler rather than ours, and fail for a reason that has nothing
    to do with the buffer.
    """
    from backend.services.logbuffer import RingBufferHandler

    handler = RingBufferHandler(capacity=5)

    class Exploding:
        def __str__(self):
            raise RuntimeError("boom")

    log = logging.getLogger("ops.explode.isolated")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        # Must not raise, and must not stop the buffer working afterwards.
        log.info("value=%s", Exploding())
        log.info("still alive")
    finally:
        log.removeHandler(handler)
        log.propagate = True

    messages = [e["message"] for e in handler.snapshot()]
    assert "still alive" in messages, (
        "the handler stopped accepting records after one bad one")


def test_reinstalling_does_not_leak_handlers_onto_the_root_logger(env):
    """Every test module here purges `sys.modules` to pick up a fresh
    DATABASE_URL, which re-imports this module and resets its `_handler`
    guard -- while the previous handler is still attached to the root logger,
    because the root logger is not in `sys.modules`.

    Without the sweep in `install()` the handlers accumulate one per
    re-import, each holding up to `capacity` records, and every log call fans
    out to all of them.

    The sweep matches by class NAME rather than `isinstance`, because after a
    purge the re-imported class is a different object and `isinstance` is
    False for exactly the handlers being swept -- a fix that looks right and
    does nothing.
    """
    import logging as stdlib_logging

    def buffers():
        return [h for h in stdlib_logging.getLogger().handlers
                if type(h).__name__ == "RingBufferHandler"]

    from backend.services import logbuffer

    before = len(buffers())
    assert before == 1, f"expected one buffer on the root logger, found {before}"

    # Simulate what the next test module does.
    for name in [m for m in list(sys.modules) if m.startswith("backend.services.logbuffer")]:
        del sys.modules[name]
    from backend.services import logbuffer as reimported

    reimported.install()
    assert len(buffers()) == 1, (
        f"re-importing leaked handlers: {len(buffers())} attached to root")


# --------------------------------------------------------------------------
# the credential
# --------------------------------------------------------------------------

def test_the_admin_token_never_reaches_the_log_buffer(env):
    """/api/logs is readable by EVERY authenticated role, so a credential in
    the buffer is a credential published to every signed-in user. The boot
    banner stays on `print()` for exactly this reason."""
    client, root = env
    from backend.core.config import get_settings

    token = get_settings().admin_token
    assert token, "no admin token to check"

    _as(client, "auditor")
    body = client.get("/api/logs", params={"limit": 1000}).json()
    blob = " ".join(f"{e['message']} {e.get('exception') or ''}"
                    for e in body["entries"])
    assert token not in blob, (
        "the ephemeral admin token reached the log buffer, which /api/logs "
        "publishes to every authenticated role")


# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------

def test_the_worker_monitor_does_not_invent_a_fleet(env):
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/workers").json()
    assert body["model"] == "single-process"
    assert "not a worker fleet" in body["model_note"]
    names = {w["name"] for w in body["workers"]}
    assert {"provider-health-sweep", "aoi-scheduler",
            "ais-live-ingest"} <= names


def test_a_disabled_worker_is_listed_as_disabled_not_omitted(env):
    """A deliberately-off scheduler must not be indistinguishable from a
    crashed one."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/workers").json()
    scheduler = next(w for w in body["workers"] if w["name"] == "aoi-scheduler")
    if not scheduler["enabled"]:
        assert scheduler["disabled_reason"]
        assert "SCHEDULER_ENABLED" in scheduler["disabled_reason"]


def test_gpu_is_reported_as_not_measured_never_as_zero(env):
    """A '0%' reading would be a measurement nobody took."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/workers").json()
    assert body["gpu"]["measured"] is False
    assert "nobody took" in body["gpu"]["note"]
    assert "utilisation" not in body["gpu"]
    assert "percent" not in body["gpu"]


def test_processing_times_are_absent_not_zero_when_nothing_finished(env):
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/workers").json()
    jobs = body["jobs"]
    if not jobs["finished_sampled"]:
        assert jobs["duration_seconds"] is None
        assert "no job has finished" in jobs["duration_note"]


def test_the_ais_worker_row_separates_connected_from_working(env):
    """The monitor is where a green row for a stream ingesting nothing would
    do the most damage."""
    client, _ = env
    _as(client, "auditor")
    body = client.get("/api/workers").json()
    ais = next(w for w in body["workers"] if w["name"] == "ais-live-ingest")
    assert "functionally_working" in ais
    assert "state" in ais
    # Both keys present and independent: `alive` is about the thread,
    # `functionally_working` is about the data.
    assert "alive" in ais


def test_host_metrics_say_who_measured_them(env):
    client, _ = env
    _as(client, "auditor")
    host = client.get("/api/workers").json()["host"]
    assert host["hostname"]
    assert host["pid"] > 0
    if host.get("measured_by") is None:
        # Absent, not zero.
        assert "NOT measured" in host["note"]
        assert "cpu_percent_host" not in host
    else:
        assert host["measured_by"] == "psutil"
        assert "memory_total_mb" in host


def test_reading_logs_and_workers_is_open_to_every_role(env):
    """Operational diagnostics. None of it names a suspect, and an analyst
    debugging a failed run needs it."""
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor",
                 "zone_officer", "admin", "super_admin"):
        _as(client, role)
        assert client.get("/api/logs").status_code == 200, role
        assert client.get("/api/workers").status_code == 200, role


def test_clearing_the_buffer_is_admin_only_and_audited(env):
    client, _ = env
    for role in ("investigator", "analyst", "reviewer", "auditor",
                 "zone_officer"):
        _as(client, role)
        assert client.post("/api/logs/clear").status_code == 403, role

    logging.getLogger("oceantrace.test").info("about to be cleared")
    _as(client, "admin")
    body = client.post("/api/logs/clear").json()
    assert body["cleared"] >= 1
    assert "hash-chained" in body["note"]

    # The audit trail is a different store and recorded the clearing.
    _as(client, "auditor")
    entries = client.get("/api/audit", params={"limit": 200}).json()["items"]
    assert any(e["resource"] == "logs" for e in entries)
    assert client.get("/api/audit/verify").json()["ok"] is True
