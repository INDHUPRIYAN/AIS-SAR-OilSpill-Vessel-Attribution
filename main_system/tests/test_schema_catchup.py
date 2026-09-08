"""Rows that predate an ALTER TABLE-added column.

P20 acceptance found the default runs list showing 18 of 110 runs. The
`archived` column was added to an existing database by `ALTER TABLE ... ADD
COLUMN`, which leaves every existing row NULL; the Python-side default only
applies to rows inserted afterwards; and the listing filtered
`archived IS FALSE`. So every run older than the column vanished from the UI
without anyone archiving it -- including the real Chennai run and the Bali
synthetic run the acceptance plan names.

This reproduces that history exactly: a database whose `runs` table was
created before the column existed, then opened by the current code.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "main_system"))


@pytest.fixture
def old_database(tmp_path, monkeypatch):
    """A runs table from before `archived` existed, with two sealed-looking rows."""
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.execute("create table runs (id text primary key, status text, started_utc datetime, "
                "stages_total integer, stages_real integer, stages_mock integer, stages_failed integer)")
    con.execute("insert into runs (id, status, started_utc, stages_total, stages_real, stages_mock, stages_failed) "
                "values ('inv-chennai-real2', 'complete', '2026-09-01 10:00:00', 5, 5, 0, 0)")
    con.execute("insert into runs (id, status, started_utc, stages_total, stages_real, stages_mock, stages_failed) "
                "values ('inv-final-audit', 'complete', '2026-09-02 10:00:00', 5, 5, 0, 0)")
    con.commit(); con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    (tmp_path / "data" / "runs").mkdir(parents=True)
    for name in [m for m in list(sys.modules) if m.startswith("backend")]:
        del sys.modules[name]
    return db_path


def test_opening_an_old_database_gives_archived_its_default(old_database):
    from backend.models.db import init_db
    init_db()
    con = sqlite3.connect(old_database)
    assert con.execute("select archived from runs order by id").fetchall() == [(0,), (0,)]


def test_pre_column_runs_are_listed(old_database):
    """Both halves: init_db() backfills the default, and the listing filter is
    NULL-safe on its own -- a row that somehow still carries NULL is shown."""
    from fastapi.testclient import TestClient

    from backend.main import app

    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import sign_in

    with TestClient(app, base_url="https://testserver") as client:
        sign_in(client)
        listed = {r["run_id"] for r in client.get("/api/runs").json()["items"]}
        assert {"inv-chennai-real2", "inv-final-audit"} <= listed

        # and a NULL inserted after the fact (the column is nullable in an
        # ALTER TABLE-migrated file) is still treated as never-archived
        con = sqlite3.connect(old_database)
        con.execute("insert into runs (id, status, archived) values ('inv-null-later', 'complete', NULL)")
        con.commit(); con.close()
        listed = {r["run_id"] for r in client.get("/api/runs").json()["items"]}
        assert "inv-null-later" in listed
