"""Verify the sync fix on a throwaway copy of the DB.

Wipes health_metrics, then runs sync_health_data exactly the way _run_sync does
(per-type cursors + a cursor callback) and reports coverage per data type.

Usage: .venv\\Scripts\\python.exe scripts\\verify_sync_fix.py [DAYS]
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

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
REAL_DB = os.path.join(ROOT, "data", "health_tracker.db")
COPY_DB = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_verify.db")

for suffix in ("", "-wal", "-shm"):
    # Remove the target first: a stale -wal left over from a previous run would be
    # replayed against the freshly copied database and corrupt/hang the run.
    tgt = COPY_DB + suffix
    if os.path.exists(tgt):
        os.remove(tgt)
    src = REAL_DB + suffix
    if os.path.exists(src):
        shutil.copy2(src, tgt)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + COPY_DB.replace(os.sep, "/")

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

import app.services.scheduler as sched
from app.services.scheduler import SYNC_DATA_TYPES, sync_health_data, _write_sync_cursors

USER_ID = 1

# Enable the heart-rate rollup on the copy: raw heart rate is ~13k samples/day,
# so history comes from dailyRollUp and only the recent window keeps raw samples.
con = sqlite3.connect(COPY_DB)
con.execute("DELETE FROM health_metrics")
row = con.execute("SELECT value FROM app_settings WHERE key='sync_rollup_config'").fetchone()
cfg = json.loads(row[0])
cfg["metrics"]["heart_rate"] = {"enabled": True, "cutoff_days": 2}
con.execute("UPDATE app_settings SET value=? WHERE key='sync_rollup_config'", (json.dumps(cfg),))
con.execute("UPDATE app_settings SET value=? WHERE key='sync_settings_1'",
            (json.dumps({"sync_days_back": DAYS}),))
con.commit()
con.close()
print(f"wiped health_metrics on copy; heart_rate rollup enabled (cutoff 2d); window={DAYS}d\n")

timings: dict[str, float] = {}


def instrument():
    for name in ("fetch_health_data", "fetch_daily_rollup"):
        orig = getattr(sched.GoogleHealthService, name)

        def make(orig=orig, name=name):
            async def timed(self, user_id, data_type="steps", **kw):
                t0 = time.perf_counter()
                try:
                    return await orig(self, user_id, data_type=data_type, **kw)
                finally:
                    timings[data_type] = timings.get(data_type, 0.0) + time.perf_counter() - t0
            return timed

        setattr(sched.GoogleHealthService, name, make())


async def main():
    instrument()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=DAYS)
    type_cursors: dict[str, datetime] = {}
    order: list[str] = []
    t0 = time.perf_counter()

    async def on_done(dt: str):
        type_cursors[dt] = now
        order.append(dt)
        print(f"  [{time.perf_counter()-t0:6.1f}s] cursor set for {dt}", flush=True)

    outcome = await sync_health_data(
        USER_ID, start, now,
        type_cursors=type_cursors,
        on_type_complete=on_done,
    )
    total = time.perf_counter() - t0
    await _write_sync_cursors(USER_ID, type_cursors, overall=min(type_cursors.values(), default=start))

    print(f"\nsaved={outcome.saved} failed_from={outcome.failed_from} "
          f"completed={len(outcome.completed)}/{len(SYNC_DATA_TYPES)} in {total:.1f}s")
    print(f"api time: " + ", ".join(f"{k}={v:.0f}s" for k, v in timings.items()))
    missing = [t for t in SYNC_DATA_TYPES if t not in outcome.completed]
    print(f"types that did NOT complete: {missing or 'none'}")

    con = sqlite3.connect(f"file:{COPY_DB}?mode=ro", uri=True)
    print("\n=== coverage (google_health_connect) ===")
    for r in con.execute("""
        SELECT COALESCE(m.name, h.metric_type), COUNT(*),
               COUNT(DISTINCT substr(h.recorded_at,1,10)),
               MIN(h.recorded_at), MAX(h.recorded_at), h.granularity
        FROM health_metrics h LEFT JOIN metric_definitions m ON m.id = h.definition_id
        WHERE h.source='google_health_connect'
        GROUP BY h.definition_id, h.metric_type ORDER BY 2 DESC
    """):
        print(f"  {str(r[0])[:30]:<30} rows={r[1]:>7} days={r[2]:>3} gran={r[5]:<5} "
              f"{str(r[3])[:16]} .. {str(r[4])[:16]}")

    print("\n=== persisted cursors ===")
    v = con.execute("SELECT value FROM app_settings WHERE key='sync_settings_1'").fetchone()[0]
    d = json.loads(v)
    print(f"  last_google_sync = {d.get('last_google_sync')}")
    print(f"  type_cursors: {json.dumps(d.get('type_cursors'), indent=2)[:600]}")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
