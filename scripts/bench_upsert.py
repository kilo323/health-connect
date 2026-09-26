"""Micro-benchmark the new upsert write path (INSERT ... ON CONFLICT DO UPDATE).

Run: .venv\\Scripts\\python.exe scripts\\bench_upsert.py [N]
"""
import asyncio
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
REAL_DB = os.path.join(ROOT, "data", "health_tracker.db")
COPY_DB = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_bench2.db")
for suffix in ("", "-wal", "-shm"):
    src = REAL_DB + suffix
    if os.path.exists(src):
        shutil.copy2(src, COPY_DB + suffix)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + COPY_DB.replace(os.sep, "/")

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from app.services.scheduler import _upsert_metric, BATCH_COMMIT_ROWS
from app.services.metric_normalizer import metric_normalizer
from app.database import async_session_factory
from app.models.health_data import HealthMetric


async def main():
    base = datetime(2021, 1, 1, tzinfo=timezone.utc)
    async with async_session_factory() as db:
        await metric_normalizer.load(db)
        t0 = time.perf_counter()
        uncommitted = 0
        for i in range(N):
            await _upsert_metric(
                db, user_id=1, metric_type="BENCH HR", value=70.0 + i % 20, unit="bpm",
                recorded_at=base + timedelta(seconds=i), source="bench",
                definition_id=None, granularity="raw")
            uncommitted += 1
            if uncommitted >= BATCH_COMMIT_ROWS:
                await db.commit()
                uncommitted = 0
        if uncommitted:
            await db.commit()
        el = time.perf_counter() - t0
    print(f"{N} upserts in {el:.2f}s -> {el/N*1000:.3f} ms/row ({N/el:.0f} rows/s)")

    # Re-run the same rows: exercises the ON CONFLICT DO UPDATE path.
    async with async_session_factory() as db:
        await metric_normalizer.load(db)
        t0 = time.perf_counter()
        for i in range(N):
            await _upsert_metric(
                db, user_id=1, metric_type="BENCH HR", value=99.0, unit="bpm",
                recorded_at=base + timedelta(seconds=i), source="bench",
                definition_id=None, granularity="raw")
        await db.commit()
        el2 = time.perf_counter() - t0
    print(f"{N} conflict-updates in {el2:.2f}s -> {el2/N*1000:.3f} ms/row ({N/el2:.0f} rows/s)")

    from sqlalchemy import func as sfunc
    async with async_session_factory() as db:
        n = (await db.execute(sfunc.count(HealthMetric.id))).scalar()
    print("total rows now in copy:", n)


if __name__ == "__main__":
    asyncio.run(main())
