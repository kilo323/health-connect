"""Wipe Google Health metrics and run a full backfill against the real DB.

Usage:
  .venv\\Scripts\\python.exe scripts\\backfill_google_sync.py --wipe     # wipe + backfill
  .venv\\Scripts\\python.exe scripts\\backfill_google_sync.py           # backfill only

The backfill mirrors _run_sync: per-data-type cursors persisted as each type
completes, so it can be interrupted and resumed without losing progress.
"""
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

USER_ID = 1
DB = os.path.join(ROOT, "data", "health_tracker.db")
BACKUP = DB + ".bak-prebackfill"
# Raw intraday heart rate is ~24k samples/day, so keep raw samples for a recent
# window and take older days from the cheap dailyRollUp endpoint.
HEART_RATE_CUTOFF_DAYS = 7


def wipe():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{BACKUP}.{stamp}"
    shutil.copy2(DB, dest)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(DB + suffix):
            shutil.copy2(DB + suffix, dest + suffix)
    print(f"backed up DB -> {dest}")

    con = sqlite3.connect(DB)
    before = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    con.execute("DELETE FROM health_metrics")
    con.commit()
    print(f"deleted {before} health_metrics rows")

    row = con.execute(
        "SELECT value FROM app_settings WHERE key='sync_rollup_config'").fetchone()
    cfg = json.loads(row[0]) if row else {}
    cfg.setdefault("metrics", {})["heart_rate"] = {
        "enabled": True, "cutoff_days": HEART_RATE_CUTOFF_DAYS}
    con.execute("UPDATE app_settings SET value=? WHERE key='sync_rollup_config'",
                (json.dumps(cfg),))
    row = con.execute(
        "SELECT value FROM app_settings WHERE key='sync_settings_1'").fetchone()
    settings = json.loads(row[0]) if row else {}
    settings.pop("type_cursors", None)
    settings.pop("last_google_sync", None)
    settings["sync_days_back"] = 30
    con.execute("UPDATE app_settings SET value=? WHERE key='sync_settings_1'",
                (json.dumps(settings),))
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"heart_rate rollup enabled (cutoff {HEART_RATE_CUTOFF_DAYS}d); cursors cleared")


async def backfill():
    from app.services.scheduler import (
        SYNC_DATA_TYPES, sync_health_data, _write_sync_cursors,
    )
    from app.database import async_session_factory
    from sqlalchemy import select
    from app.models.settings import AppSettings

    async with async_session_factory() as db:
        res = await db.execute(select(AppSettings).where(
            AppSettings.key == f"sync_settings_{USER_ID}"))
        row = res.scalar_one_or_none()
        settings = json.loads(row.value) if row else {}
    days_back = int(settings.get("sync_days_back", 30))
    type_cursors = {k: datetime.fromisoformat(v)
                    for k, v in (settings.get("type_cursors") or {}).items()}

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    print(f"\nbackfilling user {USER_ID}: {start.isoformat()} -> {now.isoformat()} "
          f"(sync_days_back={days_back})")
    print(f"resuming from cursors: {len(type_cursors)} type(s) already done\n")

    t0 = time.perf_counter()

    async def on_done(dt: str):
        type_cursors[dt] = now
        await _write_sync_cursors(
            USER_ID, type_cursors, overall=min(type_cursors.values(), default=start))
        print(f"  [{time.perf_counter()-t0:7.1f}s] done: {dt}", flush=True)

    outcome = await sync_health_data(
        USER_ID, start, now, type_cursors=type_cursors, on_type_complete=on_done)
    print(f"\nrows written={outcome.saved} failed_from={outcome.failed_from} "
          f"completed={len(outcome.completed)}/{len(SYNC_DATA_TYPES)} "
          f"in {time.perf_counter()-t0:.1f}s")


def report():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    print("\n=== final coverage (google_health_connect) ===")
    tot = 0
    for r in con.execute("""
        SELECT COALESCE(m.name, h.metric_type), COUNT(*),
               COUNT(DISTINCT substr(h.recorded_at,1,10)),
               MIN(h.recorded_at), MAX(h.recorded_at), h.granularity
        FROM health_metrics h LEFT JOIN metric_definitions m ON m.id=h.definition_id
        WHERE h.source='google_health_connect'
        GROUP BY h.metric_type, h.granularity ORDER BY 2 DESC
    """):
        tot += r[1]
        print(f"  {str(r[0])[:30]:<30} rows={r[1]:>7} days={r[2]:>3} gran={r[5]:<5} "
              f"{str(r[3])[:16]} .. {str(r[4])[:16]}")
    print(f"  {'TOTAL':<30} rows={tot:>7}")
    con.close()


if __name__ == "__main__":
    if "--wipe" in sys.argv:
        wipe()
    asyncio.run(backfill())
    report()
