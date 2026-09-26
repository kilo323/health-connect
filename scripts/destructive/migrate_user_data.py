"""One-off migration: reassign all data from user_id=1 (admin) to user_id=2 (james).

Creates a timestamped backup of the SQLite database before making changes.
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("data/health_tracker.db")
OLD_USER = 1
NEW_USER = 2


def main() -> None:
    backup = DB_PATH.with_suffix(f".{datetime.now():%Y%m%d_%H%M%S}.db.bak")
    shutil.copy(DB_PATH, backup)
    print(f"Backup created: {backup}")

    db = sqlite3.connect(DB_PATH)

    # Find every table that has a user_id column
    tables = [
        r[0]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if "user_id" in [c[1] for c in db.execute(f"PRAGMA table_info({r[0]})")]
    ]
    print(f"Tables with user_id: {tables}")

    for table in tables:
        cur = db.execute(
            f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (NEW_USER, OLD_USER)
        )
        print(f"{table}: {cur.rowcount} row(s) migrated")

    db.commit()

    # Verify
    for table in tables:
        rows = db.execute(
            f"SELECT user_id, COUNT(*) c FROM {table} GROUP BY user_id"
        ).fetchall()
        print(f"{table}: {rows}")

    db.close()


if __name__ == "__main__":
    main()
