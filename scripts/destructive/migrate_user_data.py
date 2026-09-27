"""DESTRUCTIVE (one-off, already applied): reassign user_id=1 (admin) -> user_id=2.

Kept for reference only — the migration it performed has already been run, and
user 1 is now the primary account ("james"). Re-running it against today's data
would move current rows to the wrong user.

It does create a timestamped backup before writing. Requires --yes.

Usage:
    python scripts/destructive/migrate_user_data.py            # preview
    python scripts/destructive/migrate_user_data.py --yes      # do it
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "health_tracker.db"
OLD_USER = 1
NEW_USER = 2


def main() -> None:
    apply = "--yes" in sys.argv
    db = sqlite3.connect(DB_PATH)

    tables = [
        r[0]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if "user_id" in [c[1] for c in db.execute(f"PRAGMA table_info({r[0]})")]
    ]

    print(f"database: {DB_PATH}")
    print(f"would move every row with user_id={OLD_USER} to user_id={NEW_USER}")
    for table in tables:
        n = db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (OLD_USER,)
        ).fetchone()[0]
        print(f"  {table}: {n} row(s)")

    if not apply:
        print("\nPREVIEW ONLY - nothing was changed. Re-run with --yes to apply.")
        db.close()
        return

    backup = DB_PATH.with_suffix(f".{datetime.now():%Y%m%d_%H%M%S}.db.bak")
    shutil.copy(DB_PATH, backup)
    print(f"\nBackup created: {backup}")

    for table in tables:
        cur = db.execute(
            f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (NEW_USER, OLD_USER)
        )
        print(f"{table}: {cur.rowcount} row(s) migrated")

    db.commit()

    for table in tables:
        rows = db.execute(
            f"SELECT user_id, COUNT(*) c FROM {table} GROUP BY user_id"
        ).fetchall()
        print(f"{table}: {rows}")

    db.close()


if __name__ == "__main__":
    main()
