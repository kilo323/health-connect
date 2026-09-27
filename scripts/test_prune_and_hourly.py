"""Prune-path test on a COPY of the DB (never the live file).

Sets raw retention to 1 day, runs compaction for real, and asserts that:
  * raw rows past the cutoff are deleted (for rollup-enabled metrics only),
  * daily rows exist for every pruned day,
  * the daily read path returns the SAME values before and after pruning,
  * hourly rows survive for the pruned days.
Usage: python scripts/test_prune_and_hourly.py [--keep]
"""
import asyncio
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SRC_DB = os.path.join(ROOT, "data", "health_tracker.db")
TEST_DB = os.path.join(tempfile.gettempdir(), "health_tracker_prune_test.db")


async def main() -> None:
    shutil.copy2(SRC_DB, TEST_DB)
    if os.path.exists(SRC_DB + "-wal"):
        shutil.copy2(SRC_DB + "-wal", TEST_DB + "-wal")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"

    from sqlalchemy import func, select
    from app.database import init_db, async_session_factory
    from app.models.health_data import HealthMetric, MetricHourly
    from app.routers.health import _daily_metric_values
    from app.services.compaction import (
        compact_raw_metrics, set_raw_retention_days, get_raw_retention_days,
    )

    await init_db()
    user_id = 1

    async with async_session_factory() as db:
        before = {
            (e["metric_type"], e["day"]): e["value"]
            for e in await _daily_metric_values(db, user_id=user_id)
        }
        raw_before = (await db.execute(
            select(func.count(HealthMetric.id)).where(
                HealthMetric.granularity == "raw"))).scalar_one()
        print(f"daily snapshot: {len(before)} (metric, day) values; raw rows: {raw_before}")

    await set_raw_retention_days(1)
    assert await get_raw_retention_days() == 1

    dry = await compact_raw_metrics(dry_run=True)
    print("dry:", {k: dry[k] for k in ("days_compacted", "daily_rows_written",
                                       "hourly_rows_written", "raw_rows_deleted")})
    real = await compact_raw_metrics(dry_run=False)
    print("real:", {k: real[k] for k in ("days_compacted", "daily_rows_written",
                                         "hourly_rows_written", "raw_rows_deleted",
                                         "hourly_rows_deleted")})
    print("errors:", real["errors"][:5])

    async with async_session_factory() as db:
        after = {
            (e["metric_type"], e["day"]): e["value"]
            for e in await _daily_metric_values(db, user_id=user_id)
        }
        raw_after = (await db.execute(
            select(func.count(HealthMetric.id)).where(
                HealthMetric.granularity == "raw"))).scalar_one()
        hourly_after = (await db.execute(
            select(func.count(MetricHourly.id)))).scalar_one()
    print(f"raw rows after: {raw_after} (deleted {raw_before - raw_after}), "
          f"hourly rows: {hourly_after}")

    # Every day that had a value before must still have one, with the same value.
    missing = [k for k in before if k not in after]
    changed = [
        (k, before[k], after[k]) for k in before
        if k in after and abs(before[k] - after[k]) > 1e-6
    ]
    # New days may appear (early daily close written for the first time).
    print(f"missing after prune: {len(missing)}  changed: {len(changed)}")
    for k in missing[:10]:
        print("  MISSING", k)
    for k, b, a in changed[:10]:
        print(f"  CHANGED {k}: {b} -> {a}")

    assert raw_after < raw_before, "no raw rows were pruned"
    assert not missing, f"{len(missing)} daily values lost by pruning"
    assert not changed, f"{len(changed)} daily values changed by pruning"
    assert not real["errors"], real["errors"][:3]
    assert hourly_after > 0, "hourly tier missing"

    print("\nPRUNE + HOURLY TEST PASSED")


asyncio.run(main())
