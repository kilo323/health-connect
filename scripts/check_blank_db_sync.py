"""What does a sync produce against a TRULY blank database?

Creates an empty DB, runs init_db, then feeds a real Google API response shape
through the normalizer to show what metric_type/definition_id the sync would
actually store. This is the fresh-install path, which is otherwise untested.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BLANK = Path(tempfile.gettempdir()) / "health_tracker_blank.db"

for suffix in ("", "-wal", "-shm"):
    p = Path(str(BLANK) + suffix)
    if p.exists():
        p.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{BLANK.as_posix()}"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.database import Base, engine, async_session_factory  # noqa: E402
from app.models.health_data import MetricDefinition  # noqa: E402
from app.services.metric_normalizer import metric_normalizer  # noqa: E402
from app.services.scheduler import (  # noqa: E402
    SYNC_DATA_TYPES, get_rollup_config, _metric_rollup_cutoff,
    HealthSyncScheduler, UNIT_MAP,
)

# A representative Google raw point per data type (the shape _extract_v4_point
# and the minutes helpers expect).
RAW_POINTS = {
    "steps": {"startTime": "2026-09-25T10:00:00Z", "count": 120},
    "distance": {"startTime": "2026-09-25T10:00:00Z",
                 "distanceMeters": 800.0},
    "heart_rate": {"startTime": "2026-09-25T10:00:00Z", "count": 72},
    "sleep": {"startTime": "2026-09-25T01:00:00Z", "endTime": "2026-09-25T08:00:00Z"},
    "move_minutes": {"activeMinutes": {
        "interval": {"startTime": "2026-09-25T10:00:00Z"},
        "activeMinutesByActivityLevel": [
            {"activityLevel": "LIGHT", "activeMinutes": 10},
            {"activityLevel": "MODERATE", "activeMinutes": 15},
        ],
    }},
    "heart_minutes": {"activeZoneMinutes": {
        "interval": {"startTime": "2026-09-25T10:00:00Z"},
        "heartRateZone": "HEART_RATE_ZONE_2",
        "activeZoneMinutes": 8,
    }},
    "calories": {"startTime": "2026-09-25T10:00:00Z", "consumedCalories": 90.0},
}


async def main():
    print(f"blank db: {BLANK}\n")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("schema created\n")

    async with async_session_factory() as db:
        n = (await db.execute(__import__("sqlalchemy").func.count(MetricDefinition.id))).scalar()
        print(f"metric_definitions after init_db: {n}")
        await metric_normalizer.load(db)

    cfg = await get_rollup_config()
    print(f"\nrollup config on a blank DB (no sync_rollup_config row):")
    for dt in SYNC_DATA_TYPES:
        if dt in RAW_POINTS or dt in ("heart_minutes",):
            enabled, cutoff = _metric_rollup_cutoff(dt, cfg)
            print(f"  {dt:20} enabled={str(enabled):5} cutoff_days={cutoff}")

    print(f"\nWhat the sync would store (granularity='raw'):")
    async with async_session_factory() as db:
        print(f"  {'data_type':<16} {'label in':<22} -> {'metric_type':<22} "
              f"{'def_id':<7} unit")
        for dt, point in RAW_POINTS.items():
            for label, value, ts in HealthSyncScheduler._extract_rows(dt, point, "raw"):
                unit = UNIT_MAP.get(dt, "unknown")
                norm = await metric_normalizer.normalize(db, label, unit)
                did = norm.definition.id if norm.definition else None
                name = norm.canonical_name if norm.definition else label
                flag = "  <-- UNLINKED" if did is None else ""
                print(f"  {dt:<16} {label:<22} -> {name:<22} "
                      f"{str(did):<7} {unit}{flag}")

    print("\nDaily rollup labels (used by compaction and the rollup path):")
    async with async_session_factory() as db:
        for label in ("Heart Rate (Avg)", "Heart Rate (Min)", "Heart Rate (Max)",
                      "Active Minutes (Light)"):
            norm = await metric_normalizer.normalize(db, label, "minutes")
            did = norm.definition.id if norm.definition else None
            name = norm.canonical_name if norm.definition else label
            flag = "  <-- UNLINKED" if did is None else ""
            print(f"  {label:<24} -> {name:<24} def_id={did}{flag}")


if __name__ == "__main__":
    asyncio.run(main())
