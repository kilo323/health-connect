"""One-time migration: give raw intraday heart rate its own metric definition.

Why this needs a migration rather than just adding an alias
-----------------------------------------------------------
`_upsert_metric` upserts on (user_id, metric_type, recorded_at, source). Raw
heart-rate samples are currently stored with metric_type='heart_rate' and
definition_id=NULL, because that label matches no definition name or alias.

The moment "heart_rate" becomes an alias of a real definition, `normalize()`
returns canonical name "Heart Rate", and every subsequent sync would INSERT a new
row instead of updating the existing one -- silently doubling ~30k rows/day.
So the existing rows are renamed FIRST, then the alias is published.

Run: .venv\\Scripts\\python.exe scripts\\migrate_raw_heart_rate.py [--dry-run]
"""
import json
import os
import sqlite3
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DB = os.path.join(ROOT, "data", "health_tracker.db")

DEF_NAME = "Heart Rate"
LEGACY_TYPE = "heart_rate"
DRY_RUN = "--dry-run" in sys.argv

con = sqlite3.connect(DB)
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA synchronous=NORMAL")

print(f"db: {DB}  (dry_run={DRY_RUN})\n")

existing = con.execute(
    "SELECT id FROM metric_definitions WHERE name = ?", (DEF_NAME,)).fetchone()
collide = con.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE metric_type = ?", (DEF_NAME,)
).fetchone()[0]
if collide:
    print(f"ABORT: {collide} row(s) already use metric_type '{DEF_NAME}'. "
          "Resolve that before migrating.")
    sys.exit(1)

legacy_rows = con.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE metric_type = ?", (LEGACY_TYPE,)
).fetchone()[0]
print(f"rows to migrate: {legacy_rows} (metric_type='{LEGACY_TYPE}')")

if existing:
    def_id = existing[0]
    print(f"definition '{DEF_NAME}' already exists (id={def_id}); reusing")
    action_def = None
else:
    def_id = None
    action_def = (
        "INSERT INTO metric_definitions (name, category, unit, data_type, description, aliases)"
        " VALUES (?, 'Vital Signs', 'bpm', 'float', ?, ?)",
        (DEF_NAME,
         "Individual heart-rate samples synced from Google Health Connect. "
         "Daily summaries live in Average/Minimum/Maximum Heart Rate.",
         json.dumps([LEGACY_TYPE, "heart rate", "bpm", "heart rate sample"])),
    )
    print(f"would create definition '{DEF_NAME}' (aliases include '{LEGACY_TYPE}')")

if DRY_RUN:
    print("\n-- dry run, nothing written --")
    for r in con.execute(
            "SELECT substr(recorded_at,1,10) d, COUNT(*) FROM health_metrics "
            "WHERE metric_type=? GROUP BY d ORDER BY d", (LEGACY_TYPE,)):
        print(f"  {r[0]}  {r[1]}")
    con.close()
    sys.exit(0)

con.execute("BEGIN")
if action_def:
    sql, params = action_def
    con.execute(sql, params)
    def_id = con.execute(
        "SELECT id FROM metric_definitions WHERE name = ?", (DEF_NAME,)).fetchone()[0]
    print(f"created definition id={def_id}")

# Rename the rows. No unique-index conflict: nothing else uses 'Heart Rate',
# and the (user_id, metric_type, recorded_at, source) tuples are unchanged
# because user_id/recorded_at/source are untouched and 'Heart Rate' is unused.
con.execute(
    "UPDATE health_metrics SET metric_type = ?, definition_id = ? WHERE metric_type = ?",
    (DEF_NAME, def_id, LEGACY_TYPE))
migrated = con.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE metric_type = ? AND definition_id = ?",
    (DEF_NAME, def_id)).fetchone()[0]
print(f"migrated {migrated} row(s) to '{DEF_NAME}' with definition_id={def_id}")
con.execute("COMMIT")

# The unique index must still hold; verify rather than assume.
dupes = con.execute("""
    SELECT COUNT(*) FROM (
        SELECT user_id, metric_type, recorded_at, source, COUNT(*) c
        FROM health_metrics GROUP BY 1,2,3,4 HAVING c > 1
    )
""").fetchone()[0]
print(f"duplicate logical points after migration: {dupes}")
if dupes:
    print("ABORT: duplicates appeared; investigate before syncing.")
    sys.exit(1)

now = con.execute(
    "SELECT COUNT(*) FROM health_metrics WHERE metric_type = ? AND definition_id = ?",
    (DEF_NAME, def_id)).fetchone()[0]
print(f"rows now on '{DEF_NAME}' with definition_id={def_id}: {now}")
con.close()
print("\ndone. Safe to run a sync now.")
