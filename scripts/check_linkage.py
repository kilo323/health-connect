"""Check that compacted daily rows are linked to metric definitions.

Unlinked rows surface in the admin metric library's "unmatched" queue, so
creating a new unlinked series would be a regression.
"""
import os
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("TEMP", "."), "health_tracker_compact_test.db")
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("db:", p)
print("\n=== definition linkage by metric_type/granularity ===")
bad = 0
for r in con.execute("""
    SELECT h.metric_type, h.granularity, COUNT(*) AS n,
           SUM(CASE WHEN h.definition_id IS NULL THEN 1 ELSE 0 END) AS unlinked
    FROM health_metrics h
    WHERE h.source='google_health_connect'
      AND (h.metric_type LIKE '%Heart Rate' OR h.metric_type LIKE '%Active Minutes'
           OR h.metric_type LIKE '%Heart Minutes%')
    GROUP BY h.metric_type, h.granularity
    ORDER BY 3 DESC
"""):
    flag = "" if r[3] == 0 else "  <-- UNLINKED"
    if r[3]:
        bad += r[3]
    print(f"  {r[0]:<24} gran={r[1]:<6} rows={r[2]:>7} unlinked={r[3]}{flag}")

print(f"\ntotal unlinked rows in these series: {bad}")
print("\n=== full-table unmatched metric types (admin queue) ===")
for r in con.execute("""
    SELECT metric_type, COUNT(*) FROM health_metrics
    WHERE source='google_health_connect' AND definition_id IS NULL
    GROUP BY metric_type ORDER BY 2 DESC
"""):
    print(f"  {r[0]:<28} {r[1]}")
con.close()
