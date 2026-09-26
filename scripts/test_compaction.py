"""Test compaction on a throwaway copy: prove no data is lost.

Runs with a tiny retention window so it actually has work to do, then checks that
every day which lost its raw rows gained a matching daily row with the right
value (recomputed independently from a pre-run snapshot of the raw data).

Run: .venv\\Scripts\\python.exe scripts\\test_compaction.py [RETENTION_DAYS]
"""
import asyncio
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

RETENTION = int(sys.argv[1]) if len(sys.argv) > 1 else 3
REAL_DB = os.path.join(ROOT, "data", "health_tracker.db")
COPY_DB = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_compact_test.db")

for suffix in ("", "-wal", "-shm"):
    tgt = COPY_DB + suffix
    if os.path.exists(tgt):
        os.remove(tgt)
    src = REAL_DB + suffix
    if os.path.exists(src):
        shutil.copy2(src, tgt)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + COPY_DB.replace(os.sep, "/")

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from app.services.compaction import (
    compact_raw_metrics, set_raw_retention_days, get_raw_retention_days,
)


def snapshot():
    """Independently recompute expected daily values from the raw rows."""
    con = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True)
    per_day = defaultdict(list)
    for mt, ts, v in con.execute("""
        SELECT metric_type, recorded_at, value FROM health_metrics
        WHERE source='google_health_connect' AND granularity='raw'
          AND metric_type IN ('Heart Rate','Light Active Minutes',
                              'Moderate Active Minutes','Vigorous Active Minutes',
                              'Fat Burn Heart Minutes')
    """):
        per_day[(mt, ts[:10])].append(float(v))
    con.close()
    return per_day


async def main():
    per_day_before = snapshot()
    print(f"retention set to {RETENTION} days")
    print(f"raw groups before: {len(per_day_before)}")

    con = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True)
    rows_before = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    con.close()

    await set_raw_retention_days(RETENTION)
    assert await get_raw_retention_days() == RETENTION

    print("\n=== dry run ===")
    dry = await compact_raw_metrics(dry_run=True)
    print(json.dumps({k: v for k, v in dry.items() if k != "days_kept_no_daily"},
                     indent=2, default=str)[:900])
    if dry.get("days_kept_no_daily"):
        print(f"  days_kept_no_daily: {len(dry['days_kept_no_daily'])}")
        for d in dry["days_kept_no_daily"][:5]:
            print("   ", d)

    con = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True)
    rows_after_dry = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    con.close()
    print(f"\nrows before dry run: {rows_before}")
    print(f"rows after  dry run: {rows_after_dry}  "
          f"({'unchanged - OK' if rows_before == rows_after_dry else 'CHANGED - BUG'})")

    print("\n=== real run ===")
    real = await compact_raw_metrics(dry_run=False)
    print(json.dumps({k: v for k, v in real.items() if k != "days_kept_no_daily"},
                     indent=2, default=str)[:900])
    if real.get("days_kept_no_daily"):
        print(f"  days_kept_no_daily: {len(real['days_kept_no_daily'])}")
        for d in real["days_kept_no_daily"][:5]:
            print("   ", d)
    if real.get("errors"):
        print("  ERRORS:", real["errors"][:5])

    # ── verification ────────────────────────────────────────────────────────
    con = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True)
    print("\n=== verification ===")
    problems = []
    checked = 0
    for (mt, day), values in sorted(per_day_before.items()):
        row = con.execute("""
            SELECT COUNT(*) FROM health_metrics
            WHERE source='google_health_connect' AND granularity='raw'
              AND metric_type=? AND substr(recorded_at,1,10)=?
        """, (mt, day)).fetchone()[0]
        if row > 0:
            continue  # still raw: either inside retention or deliberately kept
        checked += 1
        if mt == "Heart Rate":
            expect = {
                "Average Heart Rate": sum(values) / len(values),
                "Minimum Heart Rate": float(min(values)),
                "Maximum Heart Rate": float(max(values)),
            }
        else:
            expect = {mt: float(sum(values))}
        for name, want in expect.items():
            got = con.execute("""
                SELECT value FROM health_metrics
                WHERE source='google_health_connect' AND granularity='daily'
                  AND metric_type=? AND substr(recorded_at,1,10)=?
            """, (name, day)).fetchone()
            if got is None:
                problems.append(f"{name} {day}: MISSING daily row (raw was deleted!)")
            elif abs(float(got[0]) - want) > 1e-6:
                problems.append(
                    f"{name} {day}: value {got[0]} != expected {want}")
    print(f"compacted groups checked: {checked}")
    print(f"problems: {len(problems)}")
    for p in problems[:10]:
        print("  !", p)

    print("\n=== final counts ===")
    for r in con.execute("""
        SELECT metric_type, granularity, COUNT(*), COUNT(DISTINCT substr(recorded_at,1,10))
        FROM health_metrics WHERE source='google_health_connect'
          AND (metric_type LIKE '%Heart Rate' OR metric_type LIKE '%Active Minutes'
               OR metric_type LIKE '%Heart Minutes%')
        GROUP BY metric_type, granularity ORDER BY 3 DESC
    """):
        print(f"  {r[0]:<24} gran={r[1]:<6} rows={r[2]:>7} days={r[3]:>3}")
    con.close()
    return 1 if problems or real.get("errors") else 0


def _non_raw_count():
    """Rows the compaction is not allowed to touch (non-google or non-raw)."""
    con = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True)
    n = con.execute("""
        SELECT COUNT(*) FROM health_metrics
        WHERE NOT (source='google_health_connect' AND granularity='raw')
    """).fetchone()[0]
    con.close()
    return n


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
