"""DESTRUCTIVE: delete all minutes rows (move_minutes / heart_minutes).

Used to force a re-sync so a changed per-level / per-zone breakdown shows up.
Loses those metrics permanently unless they are re-fetched from Google within
the `sync_days_back` floor.

Requires --yes, and prints a preview otherwise.

Usage:
    python scripts/destructive/clear_minutes.py            # preview
    python scripts/destructive/clear_minutes.py --yes      # do it
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "health_tracker.db"

DELETE_SQL = (
    "DELETE FROM health_metrics "
    "WHERE metric_type LIKE '%minute%' OR metric_type LIKE '%Minutes%'"
)


def main() -> None:
    apply = "--yes" in sys.argv
    c = sqlite3.connect(DB)
    cur = c.cursor()

    total = cur.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    match = cur.execute(
        "SELECT COUNT(*) FROM health_metrics "
        "WHERE metric_type LIKE '%minute%' OR metric_type LIKE '%Minutes%'"
    ).fetchone()[0]

    print(f"database: {DB}")
    print(f"would DELETE {match} of {total} health_metrics row(s) "
          f"(metric_type matching minutes)")

    if not apply:
        print("\nPREVIEW ONLY - nothing was changed. Re-run with --yes to apply.")
        c.close()
        return

    cur.execute(DELETE_SQL)
    c.commit()
    print(f"deleted {cur.rowcount} minute row(s)")
    c.close()


if __name__ == "__main__":
    main()
