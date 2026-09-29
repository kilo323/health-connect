"""Ad-hoc check: unit_conversions seeding in init_db().

Runs against a COPY of the database so nothing touches live data.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="unit_seed_")
DB_PATH = os.path.join(TMP, "health_tracker.db")
shutil.copy2(os.environ.get("UNIT_TEST_SOURCE_DB", "data/health_tracker.db"), DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH.replace(os.sep, '/')}"

sys.path.insert(0, os.getcwd())

from sqlalchemy import text  # noqa: E402

from app.database import async_session_factory, engine, init_db  # noqa: E402


def snapshot():
    conn = __import__("sqlite3").connect(DB_PATH)
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT name, unit, unit_conversions FROM metric_definitions")
    }
    conn.close()
    return rows


async def main() -> None:
    before = snapshot()
    print("--- BEFORE (only rows the seeder may touch)")
    for name in ("Body Temperature", "Distance", "Weight", "Sleep", "Calories", "Body Fat Percentage"):
        unit, conv = before[name]
        print(f"  {name:24s} {unit!r:10s} {conv}")

    await init_db()
    after_first = snapshot()

    print("\n--- AFTER first init_db()")
    for name in ("Body Temperature", "Distance", "Weight", "Sleep", "Calories", "Body Fat Percentage"):
        unit, conv = after_first[name]
        marker = "  <-- changed" if conv != before[name][1] else ""
        print(f"  {name:24s} {unit!r:10s} {conv}{marker}")

    # Idempotence: a second startup must not touch anything.
    await init_db()
    after_second = snapshot()
    drift = {n for n in after_first if after_first[n] != after_second[n]}
    print(f"\nsecond init_db() drift: {sorted(drift) or 'none'}")

    # The values it wrote must be the exact physical constants.
    checks = {
        ("Body Temperature", "°C"): 1.0 / 1.8,
        ("Distance", "meters"): 1000.0,
    }
    for (name, unit), expected in checks.items():
        got = json.loads(after_first[name][1]).get(unit)
        ok = got is not None and abs(got - expected) < 1e-9
        print(f"  {'OK ' if ok else 'BAD'} {name}.{unit} = {got!r} (expected {expected})")
        assert ok

    # Round-trip through the hook's own direction: value * factor. The stored
    # miles factor is pre-existing and rounded, so compare loosely.
    dist_km = 2.406
    miles = json.loads(after_first["Distance"][1])["miles"]
    exact = dist_km / 1.609344
    print(f"\n  {dist_km} km -> {dist_km * miles:.4f} miles (exact {exact:.4f})")
    assert abs(dist_km * miles - exact) < 1e-3

    await engine.dispose()
    shutil.rmtree(TMP, ignore_errors=True)
    print("\nOK")


asyncio.run(main())
