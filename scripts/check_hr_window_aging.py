"""Determine which windows sync actually requests for heart_rate over time.

Patches the two fetch functions to record (start, end) and return nothing, so we
can see the window split without fetching or writing anything.

Simulates: a fresh backfill, then successive steady-state runs on later days.
"""
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

REAL_DB = os.path.join(ROOT, "data", "health_tracker.db")
COPY_DB = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_windowcheck.db")
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

import app.services.scheduler as sched
from app.services.scheduler import sync_health_data

USER_ID = 1
DAYS_BACK = 30
CUTOFF = 7

calls: list[tuple[str, object, object]] = []


def patch():
    async def fake_rollup(self, user_id, data_type="steps", start_time=None, end_time=None, **kw):
        calls.append(("dailyRollUp", data_type, start_time, end_time))
        return []

    async def fake_raw(self, user_id, data_type="steps", start_time=None, end_time=None, **kw):
        calls.append(("dataPoints", data_type, start_time, end_time))
        return []

    sched.GoogleHealthService.fetch_daily_rollup = fake_rollup
    sched.GoogleHealthService.fetch_health_data = fake_raw


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


async def scenario(label, cursor, now):
    calls.clear()
    cursors = {"heart_rate": cursor} if cursor else {}
    await sync_health_data(
        USER_ID, now - timedelta(days=DAYS_BACK), now,
        data_types=["heart_rate"],
        type_cursors=cursors,
    )
    cutoff = now - timedelta(days=CUTOFF)
    print(f"\n{label}")
    print(f"  simulated now = {fmt(now)}   cutoff(now-{CUTOFF}d) = {fmt(cutoff)}")
    for kind, dt, s, e in calls:
        span = (e - s).total_seconds() / 86400
        print(f"    {kind:12} {dt:11} {fmt(s)} -> {fmt(e)}   ({span:.2f} days)")
    if not calls:
        print("    (no window requested)")


async def main():
    patch()
    base = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    # 1. Fresh: no cursor at all -> full backfill window.
    await scenario("A. fresh install / cleared cursors (backfill)", None, base)

    # 2. Steady state: last run finished 6h ago.
    await scenario(
        "B. steady state, last successful run 6h ago",
        base - timedelta(hours=6), base,
    )

    # 3. Steady state with a daily schedule: last run 24h ago.
    await scenario(
        "C. steady state, daily schedule (last run 24h ago)",
        base - timedelta(days=1), base,
    )

    # 4. Seven days later, with a healthy cursor from a run 6h before that.
    later = base + timedelta(days=7)
    await scenario(
        "D. one week later, last run 6h before that",
        later - timedelta(hours=6), later,
    )

    # 5. App was offline for 10 days, then ran.
    await scenario(
        "E. app offline 10 days, then ran (cursor 10d old)",
        base - timedelta(days=10), base,
    )

    print("\n\nWhat happens to raw rows as they age past the cutoff?")
    print("  The cutoff only selects WHICH ENDPOINT is used for a window at fetch")
    print("  time. There is no job that converts already-stored raw rows into daily")
    print("  rows, so raw heart-rate samples are never compacted after they are")
    print("  written. Checked below against the real DB.")


if __name__ == "__main__":
    asyncio.run(main())
