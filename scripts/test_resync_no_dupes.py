"""Verify that re-syncing an already-populated database does NOT duplicate rows.

The hazard: _upsert_metric conflicts on (user_id, metric_type, recorded_at,
source). If a database was filled while metric_definitions was empty, the rows
are stored under raw Google labels; once definitions exist the sync computes the
canonical name, which no longer matches, so the upsert INSERTs instead of
updating and the table doubles.

This runs a real sync against the given database and compares row counts before
and after.
"""
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "health_tracker.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + DB.replace("\\", "/")
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from app.services.scheduler import SYNC_DATA_TYPES, sync_health_data, _write_sync_cursors


def snapshot():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    total = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    per_type = dict(con.execute(
        "SELECT metric_type, COUNT(*) FROM health_metrics "
        "WHERE source='google_health_connect' GROUP BY metric_type"))
    unlinked = con.execute(
        "SELECT COUNT(*) FROM health_metrics "
        "WHERE source='google_health_connect' AND definition_id IS NULL").fetchone()[0]
    con.close()
    return total, per_type, unlinked


async def main() -> int:
    print(f"database: {DB}\n")
    before, per_type_before, unlinked_before = snapshot()
    print(f"before: {before} rows, {len(per_type_before)} metric types, "
          f"{unlinked_before} unlinked")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT value FROM app_settings WHERE key='sync_settings_1'").fetchone()
    con.close()
    settings = json.loads(row[0]) if row else {}
    days_back = int(settings.get("sync_days_back", 7))
    cursors = {k: datetime.fromisoformat(v)
               for k, v in (settings.get("type_cursors") or {}).items()}

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    print(f"syncing {start.date()} -> {now.date()} "
          f"({len(cursors)}/{len(SYNC_DATA_TYPES)} types already have cursors)\n")

    t0 = time.perf_counter()
    order: list[str] = []

    async def on_done(dt: str):
        order.append(dt)
        cursors[dt] = now
        await _write_sync_cursors(1, cursors, overall=min(cursors.values(), default=start))
        print(f"  [{time.perf_counter()-t0:6.1f}s] {dt}", flush=True)

    outcome = await sync_health_data(1, start, now, type_cursors=cursors,
                                    on_type_complete=on_done)
    print(f"\nsync finished in {time.perf_counter()-t0:.1f}s, "
          f"{len(outcome.completed)}/{len(SYNC_DATA_TYPES)} types, "
          f"rows written={outcome.saved}")

    after, per_type_after, unlinked_after = snapshot()
    print(f"after:  {after} rows, {len(per_type_after)} metric types, "
          f"{unlinked_after} unlinked")

    growth = after - before
    pct = 100.0 * growth / before if before else 0.0
    print(f"\ngrowth: {growth} rows ({pct:+.3f}%)")

    # A healthy incremental sync adds only genuinely new samples (today's
    # heart rate alone runs ~1,300/hour), and must not double any series.
    print("\nlargest per-type growth:")
    for mt in sorted(per_type_after,
                     key=lambda k: -(per_type_after[k] - per_type_before.get(k, 0)))[:8]:
        d = per_type_after[mt] - per_type_before.get(mt, 0)
        print(f"  {mt:<26} {per_type_before.get(mt, 0):>7} -> "
              f"{per_type_after[mt]:>7}  ({d:+})")

    verdict = "PASS" if growth < before * 0.02 else "FAIL - rows inflated"
    print(f"\nRESULT: {verdict} (a doubling would be +100%)")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
