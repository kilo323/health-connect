"""Report the pre-flight state of the live database: metric library, rollup
config, retention, and how many synced rows actually resolved to a definition.
"""
import json
import sqlite3
import sys
from collections import Counter

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)

print("=== metric_definitions ===")
rows = list(con.execute(
    "SELECT id, name, aliases FROM metric_definitions ORDER BY id"))
print(f"count: {len(rows)}")
for r in rows:
    print(f"  {r[0]:>2} {r[1]:<26} {(r[2] or '')[:70]}")

print("\n=== definition linkage of google rows ===")
for r in con.execute("""
    SELECT COALESCE(m.name, h.metric_type) AS name, h.granularity,
           COUNT(*), SUM(CASE WHEN h.definition_id IS NULL THEN 1 ELSE 0 END) AS unlinked
    FROM health_metrics h LEFT JOIN metric_definitions m ON m.id = h.definition_id
    WHERE h.source = 'google_health_connect'
    GROUP BY h.metric_type, h.granularity ORDER BY 3 DESC
"""):
    flag = "" if r[3] == 0 else f"   <-- {r[3]} UNLINKED"
    print(f"  {str(r[0])[:34]:<34} gran={r[1]:<6} rows={r[2]:>7}{flag}")

print("\n=== app_settings relevant to sync ===")
for key, value in con.execute(
        "SELECT key, value FROM app_settings ORDER BY key"):
    if key.startswith("sync_") or key == "sync_raw_retention":
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = value
        if key == "sync_settings_1" and isinstance(parsed, dict):
            tc = parsed.get("type_cursors") or {}
            parsed = {k: v for k, v in parsed.items() if k != "type_cursors"}
            parsed["type_cursors"] = f"({len(tc)} types)"
        print(f"  {key} = {json.dumps(parsed)[:400]}")

print("\n=== pending metric proposals ===")
try:
    n = con.execute("SELECT COUNT(*) FROM pending_metrics").fetchone()[0]
    print(f"pending_metrics rows: {n}")
    for r in con.execute("SELECT * FROM pending_metrics LIMIT 20"):
        print("  ", str(r)[:200])
except sqlite3.Error as e:
    print("  (no pending_metrics table)", e)

print("\n=== pending_analyses ===")
try:
    print("rows:", con.execute("SELECT COUNT(*) FROM pending_analyses").fetchone()[0])
except sqlite3.Error as e:
    print("  ", e)

con.close()
