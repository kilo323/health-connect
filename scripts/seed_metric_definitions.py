"""Seed metric_definitions into a database from another (backup) database.

Why this is needed on a fresh install
-------------------------------------
`init_db()` creates the schema but seeds NO metric definitions, and nothing in
the codebase loads `data/metric_library.json` (the file is absent; only a
`metric_library_proposed.json` and a dated `.bak` exist). Without definitions,
`MetricNormalizer.normalize()` finds no name or alias match, so every synced row
lands with `definition_id = NULL` and `metric_type` set to the raw Google label
("heart_rate", "Active Minutes (Light)", "Heart Rate (Avg)", ...) instead of the
curated names the dashboard, unit conversion, and report layer expect.

Verified: a blank database has `metric_definitions = 0` after `init_db`, and a
normalization pass over it returns 100% unlinked rows.

Run this BEFORE the first sync, otherwise the backfill has to be redone.

    python scripts/seed_metric_definitions.py [source.db] [target.db]

Default target is data/health_tracker.db. Idempotent: definitions that already
exist are skipped. The source database is opened read-only.
"""
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SOURCE = os.path.join(
    ROOT, "data", "health_tracker.db.bak-prehrmigrate.20260926-124848")
DEFAULT_TARGET = os.path.join(ROOT, "data", "health_tracker.db")

# Added after that backup was taken, by scripts/migrate_raw_heart_rate.py.
# Included here so a fresh install gets it in one step: raw Google heart-rate
# samples carry metric_type "heart_rate", which needs an alias to be linked.
# Its definition_id is NULL, so the rollup supersede rule cannot remove its rows.
EXTRA_DEFINITIONS = [
    {
        "name": "Heart Rate",
        "category": "Vital Signs",
        "unit": "bpm",
        "data_type": "float",
        "description": (
            "Individual heart-rate samples synced from Google Health Connect. "
            "Daily summaries live in Average/Minimum/Maximum Heart Rate."
        ),
        "aliases": ["heart_rate", "heart rate", "bpm", "heart rate sample"],
        "reference_ranges": "[]",
        "unit_conversions": "{}",
    },
    {
        # Google's activeZoneMinutes.heartRateZone yields bare zone names, so the
        # sync stores "Heart Minutes (Cardio)" / "(Peak)" / "(Fat Burn)". The
        # library defines Peak and Fat Burn but not Cardio, and normalize()'s
        # SequenceMatcher fallback then silently maps Cardio onto Peak. Giving it
        # an exact alias wins before the fuzzy pass ever runs.
        "name": "Heart Minutes (Cardio)",
        "category": "Activity",
        "unit": "minutes",
        "data_type": "float",
        "description": "Time spent in the cardio heart-rate zone, from Google Health Connect.",
        "aliases": ["Heart Minutes (Cardio)", "cardio heart minutes",
                    "heart minutes cardio", "cardio minutes"],
        "reference_ranges": "[]",
        "unit_conversions": "{}",
    },
]

COLUMNS = ("name", "category", "unit", "data_type", "description",
           "aliases", "reference_ranges", "unit_conversions")


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SOURCE
    target = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TARGET

    if not os.path.exists(source):
        print(f"ERROR: source database not found: {source}")
        sys.exit(1)
    if not os.path.exists(target):
        print(f"ERROR: target database not found: {target}")
        print("       Start the app once so it creates the schema, then re-run.")
        sys.exit(1)

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    rows = [dict(r) for r in src.execute(
        "SELECT name, category, unit, data_type, description, aliases, "
        "reference_ranges, unit_conversions FROM metric_definitions ORDER BY id")]
    src.close()
    print(f"source: {source}\n  {len(rows)} definition(s) found")
    for d in EXTRA_DEFINITIONS:
        print(f"  +1 built-in: {d['name']}")
    print(f"  total to consider: {len(rows) + len(EXTRA_DEFINITIONS)}")

    dst = sqlite3.connect(target)
    existing = {r[0] for r in dst.execute("SELECT name FROM metric_definitions")}
    print(f"\ntarget: {target}\n  {len(existing)} definition(s) already present")

    inserted, skipped = 0, 0
    for d in rows + EXTRA_DEFINITIONS:
        name = d["name"]
        if name in existing:
            print(f"  skip    {name} (already present)")
            skipped += 1
            continue
        if d.get("aliases") and not isinstance(d["aliases"], str):
            d["aliases"] = json.dumps(d["aliases"])
        if d.get("reference_ranges") and not isinstance(d["reference_ranges"], str):
            d["reference_ranges"] = json.dumps(d["reference_ranges"])
        if d.get("unit_conversions") and not isinstance(d["unit_conversions"], str):
            d["unit_conversions"] = json.dumps(d["unit_conversions"])
        cols = [c for c in COLUMNS if d.get(c) is not None]
        dst.execute(
            f"INSERT INTO metric_definitions ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            [d.get(c) for c in cols])
        print(f"  insert  {name}")
        inserted += 1
    dst.commit()

    total = dst.execute("SELECT COUNT(*) FROM metric_definitions").fetchone()[0]
    dst.close()
    print(f"\ninserted {inserted}, skipped {skipped}; "
          f"metric_definitions now {total}")
    if inserted:
        print("\nReady to sync: labels will now resolve to canonical metric names.")


if __name__ == "__main__":
    main()
