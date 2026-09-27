"""Verify fix 1 (covering index) end to end: init_db() -> get_unmatched_metrics.

Runs the real init_db() against a copy of the database, confirms the new index
exists and is chosen by the planner, then times the actual
MetricNormalizer.get_unmatched_metrics() the metric definitions page calls.

Run: .venv\\Scripts\\python.exe -u scripts\\verify_unmatched_index.py
"""
import asyncio
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

SRC = os.path.join(ROOT, "data", "health_tracker.db")
TMP = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_idxverify.db")

for suffix in ("", "-wal", "-shm"):
    p = TMP + suffix
    if os.path.exists(p):
        os.remove(p)
shutil.copy2(SRC, TMP)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + TMP.replace("\\", "/")

import app.models  # noqa: F401,E402
from app.database import async_session_factory, init_db  # noqa: E402


async def main() -> int:
    print("running the real init_db() against the copy ...", flush=True)
    t0 = time.perf_counter()
    await init_db()
    print(f"  init_db() took {time.perf_counter() - t0:.2f}s\n")

    con = sqlite3.connect(f"file:{TMP}?mode=ro", uri=True)
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='ix_health_metrics_unmatched'").fetchone()
    print(f"index exists: {bool(row)}")
    if not row:
        return 1
    size = os.path.getsize(TMP) / 1024 / 1024
    print(f"db size: {size:.1f} MB")
    plan = [r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT metric_type, COUNT(*) FROM health_metrics "
        "WHERE definition_id IS NULL GROUP BY metric_type")]
    print("plan:")
    for p in plan:
        print(f"    {p}")
    uses = any("ix_health_metrics_unmatched" in p for p in plan)
    con.close()
    print(f"planner uses the new index: {uses}\n")
    if not uses:
        return 1

    from app.services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False
    for i in (1, 2):
        async with async_session_factory() as db:
            t0 = time.perf_counter()
            unmatched = await metric_normalizer.get_unmatched_metrics(db)
            el = time.perf_counter() - t0
        label = "cold" if i == 1 else "warm"
        print(f"get_unmatched_metrics ({len(unmatched)} types) {label}: {el:.2f}s")

    print("\ntheir container measured 29.0s for this call before the index")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
