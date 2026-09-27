"""DESTRUCTIVE: clear health_metrics and reset sync cursors for a full backfill.

Deletes every row in health_metrics and strips the sync cursors so the next sync
re-fetches the full window. Nothing here is recoverable from the database — only
from Google, and only for days inside the `sync_days_back` floor.

Requires --yes, and prints a preview of what it would do otherwise.

Usage:
    python scripts/destructive/reset_sync_state.py            # preview
    python scripts/destructive/reset_sync_state.py --yes      # do it
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "health_tracker.db"


def main() -> None:
    apply = "--yes" in sys.argv

    c = sqlite3.connect(DB)
    cur = c.cursor()

    total = cur.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    settings = cur.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'sync_settings_%'"
    ).fetchall()

    print(f"database: {DB}")
    print(f"would DELETE all {total} row(s) from health_metrics")
    for key, value in settings:
        d = json.loads(value)
        print(f"  {key}: would clear type_cursors "
              f"({len(d.get('type_cursors') or {})} type(s)) and last_google_sync; "
              f"sync_days_back={d.get('sync_days_back')}")

    if not apply:
        print("\nPREVIEW ONLY - nothing was changed. Re-run with --yes to apply.")
        c.close()
        return

    cur.execute("DELETE FROM health_metrics")
    print(f"\ndeleted {cur.rowcount} health_metrics row(s)")

    for key, value in settings:
        d = json.loads(value)
        # Clear BOTH: type_cursors is the per-data-type resume state added
        # 2026-09-26, and clearing only last_google_sync would leave every type
        # thinking it is already caught up.
        d.pop("last_google_sync", None)
        d.pop("type_cursors", None)
        cur.execute("UPDATE app_settings SET value=? WHERE key=?",
                    (json.dumps(d), key))
        print(f"{key}: cursors cleared; sync_days_back={d.get('sync_days_back')}")

    c.commit()
    c.close()
    print("done")


if __name__ == "__main__":
    main()
