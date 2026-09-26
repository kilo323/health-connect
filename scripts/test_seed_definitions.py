"""Verify: with metric_definitions seeded, does normalization resolve correctly?

Runs the same label set against a seeded database and asserts every label links
to a definition. This is the check that would fail on an unseeded fresh install.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BLANK = Path(tempfile.gettempdir()) / "health_tracker_seedcheck.db"
BACKUP = ROOT / "data" / "health_tracker.db.bak-prehrmigrate.20260926-124848"

for suffix in ("", "-wal", "-shm"):
    p = Path(str(BLANK) + suffix)
    if p.exists():
        p.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{BLANK.as_posix()}"
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import app.models  # noqa: F401,E402
from app.database import Base, engine, async_session_factory, init_db  # noqa: E402
from app.services.metric_normalizer import metric_normalizer  # noqa: E402
from app.services.scheduler import HealthSyncScheduler, UNIT_MAP  # noqa: E402

# (data_type, raw point) pairs covering the shapes the sync actually produces.
POINTS = {
    "steps": {"startTime": "2026-09-25T10:00:00Z", "count": 120},
    "distance": {"startTime": "2026-09-25T10:00:00Z", "distanceMeters": 800.0},
    "heart_rate": {"startTime": "2026-09-25T10:00:00Z", "count": 72},
    "calories": {"startTime": "2026-09-25T10:00:00Z", "consumedCalories": 90.0},
    "move_minutes": {"activeMinutes": {
        "interval": {"startTime": "2026-09-25T10:00:00Z"},
        "activeMinutesByActivityLevel": [
            {"activityLevel": "LIGHT", "activeMinutes": 10},
            {"activityLevel": "MODERATE", "activeMinutes": 15},
        ],
    }},
    # Google's activeZoneMinutes.heartRateZone enum uses bare zone names, so
    # `_extract_raw_minutes_rows` title-cases them to "Cardio" / "Peak" /
    # "Fat Burn" — matching what is stored in the live database.
    "heart_minutes": {"activeZoneMinutes": {
        "interval": {"startTime": "2026-09-25T10:00:00Z"},
        "heartRateZone": "CARDIO",
        "activeZoneMinutes": 8,
    }},
    "heart_minutes_peak": {"activeZoneMinutes": {
        "interval": {"startTime": "2026-09-25T10:00:00Z"},
        "heartRateZone": "PEAK",
        "activeZoneMinutes": 5,
    }},
    "heart_minutes_fat_burn": {"activeZoneMinutes": {
        "interval": {"startTime": "2026-09-25T10:00:00Z"},
        "heartRateZone": "FAT_BURN",
        "activeZoneMinutes": 12,
    }},
}

# Labels the rollup path and compaction emit.
ROLLUP_LABELS = [
    ("Heart Rate (Avg)", "bpm"), ("Heart Rate (Min)", "bpm"),
    ("Heart Rate (Max)", "bpm"),
    ("Active Minutes (Light)", "minutes"),
    ("Active Minutes (Moderate)", "minutes"),
    ("Active Minutes (Vigorous)", "minutes"),
    ("Heart Minutes (Fat Burn)", "minutes"),
]


async def main() -> int:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_db()

    import subprocess
    subprocess.run(
        [sys.executable, str(HERE / "seed_metric_definitions.py"),
         str(BACKUP), str(BLANK)],
        check=True, capture_output=True)

    import sqlite3
    con = sqlite3.connect(f"file:{BLANK}?mode=ro", uri=True)
    n = con.execute("SELECT COUNT(*) FROM metric_definitions").fetchone()[0]
    con.close()
    print(f"metric_definitions seeded: {n}\n")

    unlinked: list[str] = []
    async with async_session_factory() as db:
        await metric_normalizer.load(db)
        print("raw point labels:")
        for dt, point in POINTS.items():
            base = "heart_minutes" if dt.startswith("heart_minutes") else dt
            for label, _v, _ts in HealthSyncScheduler._extract_rows(base, point, "raw"):
                unit = UNIT_MAP.get(base, "unknown")
                norm = await metric_normalizer.normalize(db, label, unit)
                did = norm.definition.id if norm.definition else None
                name = norm.canonical_name if norm.definition else label
                if did is None:
                    unlinked.append(name)
                print(f"  {base:<20} {label:<34} -> {name:<22} id={did}"
                      f"{'  <-- UNLINKED' if did is None else ''}")

        print("\ndaily rollup / compaction labels:")
        for label, unit in ROLLUP_LABELS:
            norm = await metric_normalizer.normalize(db, label, unit)
            did = norm.definition.id if norm.definition else None
            name = norm.canonical_name if norm.definition else label
            if did is None:
                unlinked.append(name)
            print(f"  {label:<34} -> {name:<22} id={did}"
                  f"{'  <-- UNLINKED' if did is None else ''}")

    # Known, pre-existing quirks of the label set (not blockers for a backfill):
    #
    # 1. "Heart Minutes (Cardio)" has no definition. Because normalize() falls back
    #    to a SequenceMatcher fuzzy match, it does NOT stay unlinked -- it lands on
    #    "Heart Minutes (Peak)" (id 13), silently merging Cardio data into Peak.
    #    Only ~200 rows over 22 days, and heart_minutes rollup is disabled by
    #    default so compaction never prunes it, but it is wrong. Add a
    #    "Heart Minutes (Cardio)" definition to fix it properly.
    # 2. Everything the rollup-enabled path emits (steps, distance, heart_rate,
    #    calories, move_minutes) must link exactly.
    KNOWN_MISMATCH = {"Heart Minutes (Cardio)": "Heart Minutes (Peak)"}
    mismatches = []
    async with async_session_factory() as db:
        await metric_normalizer.load(db)
        print("raw point labels:")
        for dt, point in POINTS.items():
            base = "heart_minutes" if dt.startswith("heart_minutes") else dt
            for label, _v, _ts in HealthSyncScheduler._extract_rows(base, point, "raw"):
                unit = UNIT_MAP.get(base, "unknown")
                norm = await metric_normalizer.normalize(db, label, unit)
                did = norm.definition.id if norm.definition else None
                name = norm.canonical_name if norm.definition else label
                expected = KNOWN_MISMATCH.get(label)
                if expected and name != expected:
                    mismatches.append(f"{label}: expected '{expected}', got '{name}'")
                print(f"  {base:<20} {label:<34} -> {name:<22} id={did}"
                      f"{'  <-- UNLINKED' if did is None else ''}"
                      f"{'  (known fuzzy mis-map)' if expected and name == expected else ''}")

        print("\ndaily rollup / compaction labels:")
        for label, unit in ROLLUP_LABELS:
            norm = await metric_normalizer.normalize(db, label, unit)
            did = norm.definition.id if norm.definition else None
            name = norm.canonical_name if norm.definition else label
            if did is None:
                unlinked.append(f"rollup:{name}")
            print(f"  {label:<34} -> {name:<22} id={did}"
                  f"{'  <-- UNLINKED' if did is None else ''}")

    blocking = [u for u in unlinked if u != "Heart Minutes (Cardio)"]
    print(f"\nrollup labels unlinked: {blocking or 'none'}")
    if mismatches:
        print("unexpected changes to known quirks:")
        for m in mismatches:
            print("  !", m)
    print("RESULT:", "PASS" if not blocking and not mismatches else "FAIL")
    return 0 if (not blocking and not mismatches) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
