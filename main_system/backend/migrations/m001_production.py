"""Guardrails around the schema catch-up, for the production tables.

The repo already has an additive migration mechanism: `init_db()` runs
`create_all()` (new tables) and `_add_missing_columns()` (new columns on
existing tables). That is the right shape and this module does not replace it.

What was missing is the part that matters when the database is not a scratch
file: knowing *which* database is about to be changed, and refusing when the
answer looks wrong. Two databases exist in this tree -- `data/oceantrace.db`
with 138 investigations and 92 runs, and an orphan at
`main_system/data/oceantrace.db` with none, created when the backend was once
started from a different working directory. A migration that silently picks
the second one is not destructive, but it produces a "successful" migration of
the wrong file and a backend that then serves an empty system.

So, in order:

  1. resolve and print the database that `DATABASE_URL` actually points at;
  2. count existing rows, and refuse a database that looks unexpectedly empty
     unless `--allow-empty` says that is intended (a fresh checkout);
  3. back up the file before touching it;
  4. run the existing `init_db()`;
  5. re-count, and fail loudly if any pre-existing table lost rows.

Step 5 is the one that earns its keep: an additive migration should never
reduce a count, so comparing before and after turns "additive by intent" into
"additive by evidence".

Usage:
    python -m backend.migrations.m001_production            # migrate, guarded
    python -m backend.migrations.m001_production --check    # report only
    python -m backend.migrations.m001_production --allow-empty
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (REPO_ROOT, REPO_ROOT / "main_system"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from backend.core.config import get_settings  # noqa: E402

# Tables that existed before this migration. None of them may lose rows.
PRE_EXISTING = ("investigations", "runs", "decisions", "aoi_watch",
                "api_providers", "api_calls", "api_keys", "audit_log")

# Tables this migration is expected to add.
INTRODUCES = ("users",)


class MigrationRefused(RuntimeError):
    """The database did not look like what the migration expected."""


def resolve_database() -> Path:
    """The file `DATABASE_URL` points at, as an absolute path."""
    url = get_settings().database_url
    if not url.startswith("sqlite"):
        raise MigrationRefused(
            f"this guarded migration only handles sqlite; DATABASE_URL is {url!r}")
    return Path(url.split("///", 1)[1]).resolve()


def row_counts(db_path: Path) -> Dict[str, int]:
    import sqlite3

    if not db_path.exists():
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        names = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' order by name")]
        return {n: con.execute(f"select count(*) from {n}").fetchone()[0] for n in names}
    finally:
        con.close()


def backup(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dst = db_path.with_suffix(f".pre-m001.{stamp}.bak")
    shutil.copy2(db_path, dst)
    return dst


def migrate(allow_empty: bool = False, check_only: bool = False) -> dict:
    db_path = resolve_database()
    before = row_counts(db_path)

    report = {
        "database": str(db_path),
        "exists": db_path.exists(),
        "before": before,
        "allow_empty": allow_empty,
    }

    print(f"database : {db_path}")
    print(f"exists   : {db_path.exists()}")
    if before:
        print("row counts before:")
        for name, count in before.items():
            print(f"  {name:<18}{count:>8}")
    else:
        print("row counts before: (no tables)")

    populated = any(before.get(t, 0) for t in ("runs", "investigations"))
    if before and not populated and not allow_empty:
        raise MigrationRefused(
            f"{db_path} has tables but no runs or investigations. That usually "
            f"means DATABASE_URL points at the wrong file -- an orphan database "
            f"exists at main_system/data/oceantrace.db. Re-run with "
            f"--allow-empty if this really is a fresh system.")

    if check_only:
        report["action"] = "check-only, nothing written"
        print("\ncheck-only: no changes made")
        return report

    if db_path.exists():
        saved = backup(db_path)
        report["backup"] = str(saved)
        print(f"\nbackup   : {saved.name}")

    from backend.models.db import init_db

    init_db()

    after = row_counts(db_path)
    report["after"] = after

    lost = {t: (before[t], after.get(t, 0)) for t in PRE_EXISTING
            if t in before and after.get(t, 0) < before[t]}
    if lost:
        raise MigrationRefused(
            f"row counts DECREASED, which an additive migration must never do: "
            f"{lost}. The pre-migration backup is at {report.get('backup')}.")

    added = [t for t in INTRODUCES if t in after and t not in before]
    report["tables_added"] = added
    report["verified_no_row_loss"] = True

    print("\nrow counts after:")
    for name, count in after.items():
        delta = count - before.get(name, 0)
        mark = "  (new table)" if name not in before else (f"  ({delta:+d})" if delta else "")
        print(f"  {name:<18}{count:>8}{mark}")
    print(f"\ntables added: {added or 'none'}")
    print("verified   : no pre-existing table lost rows")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allow-empty", action="store_true",
                    help="proceed even though the database has no runs (fresh system)")
    ap.add_argument("--check", action="store_true",
                    help="report what would happen; write nothing")
    args = ap.parse_args(argv)

    try:
        migrate(allow_empty=args.allow_empty, check_only=args.check)
    except MigrationRefused as exc:
        print(f"\nREFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
