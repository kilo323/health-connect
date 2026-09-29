"""Ad-hoc check: unit_conversions seeding in init_db().

Runs against a COPY of the database so nothing touches live data.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="unit_seed_")
DB_PATH = os.path.join(TMP, "health_tracker.db")
shutil.copy2(os.environ.get("UNIT_TEST_SOURCE_DB", "data/health_tracker.db"), DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH.replace(os.sep, '/')}"

sys.path.insert(0, os.getcwd())

from app.database import engine, init_db  # noqa: E402

DEG = "\u00b0"
# 1 canonical = factor * alternate, i.e. what the admin UI shows as
# `unit × factor = 1 canonical`.
EXPECTED = {
    ("Weight", "lb"): 1.0 / 0.45359237,
    ("Weight", "g"): 1000.0,
    ("Distance", "meters"): 1000.0,
    # Temperature needs the factor AND the offset the read path adds, so the
    # factor is seeded like any other ratio.
    ("Body Temperature", DEG + "C"): (5.0 / 9.0),
}
UNTOUCHED = {
    ("Distance", "km"): 1,
    ("Distance", "miles"): 0.621371,  # rounded on purpose — must be preserved
    ("Sleep", "hours"): 1,
    ("Sleep", "minutes"): 60,
    ("Calories", "kJ"): 4.184,
    ("Body Fat Percentage", "fraction"): 0.01,
}


def snapshot():
    conn = sqlite3.connect(DB_PATH)
    rows = {
        r[0]: json.loads(r[1]) if r[1] else {}
        for r in conn.execute("SELECT name, unit_conversions FROM metric_definitions")
    }
    conn.close()
    return rows


async def main() -> None:
    before = snapshot()

    await init_db()
    after = snapshot()

    print("--- AFTER init_db()")
    for name in ("Body Temperature", "Distance", "Weight", "Sleep", "Calories"):
        changed = "  <-- changed" if after[name] != before.get(name) else ""
        print(f"  {name:22s} {json.dumps(after[name], ensure_ascii=False)}{changed}")

    await init_db()
    drift = {n for n in after if after[n] != snapshot()[n]}
    print(f"\nsecond init_db() drift: {sorted(drift) or 'none'}")
    assert not drift, drift

    for (name, unit), expected in EXPECTED.items():
        got = after[name].get(unit)
        ok = got == expected
        print(f"  {'OK ' if ok else 'BAD'} {name}.{unit} = {got!r} (expected {expected!r})")
        assert ok, (name, unit, got, expected)

    for (name, unit), expected in UNTOUCHED.items():
        got = after[name].get(unit)
        ok = got == expected
        print(f"  {'OK ' if ok else 'BAD'} {name}.{unit} = {got!r} untouched")
        assert ok, (name, unit, got, expected)

    # The headline bug: 95.9 kg must be 211.5 lb, not 43.5.
    lb = after["Weight"]["lb"]
    weight_kg = 95.949
    print(f"\n  {weight_kg} kg -> {weight_kg * lb:.1f} lb (expect ~211.5)")
    assert abs(weight_kg * lb - 211.5) < 0.1

    # km -> miles must agree with the exact definition of a mile.
    miles = after["Distance"]["miles"]
    print(f"  2.406 km -> {2.406 * miles:.4f} miles (exact {2.406 / 1.609344:.4f})")
    assert abs(2.406 * miles - 2.406 / 1.609344) < 1e-3

    # Temperature is affine: the factor alone would give 64.7 °F, the offset
    # has to bring it to 96.7 °F.
    from app.services.unit_systems import default_factor, default_offset

    canonical, alternate = DEG + "F", DEG + "C"
    factor = after["Body Temperature"][alternate]
    offset = default_offset(canonical, alternate)
    stored_celsius = 35.949954986572266
    fahrenheit = (stored_celsius - offset) / factor
    print(
        f"  {stored_celsius:.2f} C -> {fahrenheit:.2f} F "
        f"(factor only would give {stored_celsius / factor:.1f})"
    )
    assert abs(fahrenheit - 96.7099) < 1e-3
    assert abs(factor - default_factor(canonical, alternate)) < 1e-12

    await engine.dispose()
    shutil.rmtree(TMP, ignore_errors=True)
    print("\nOK")


asyncio.run(main())
