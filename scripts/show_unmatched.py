"""Show the metric-library 'unmatched' queue: google rows with no definition."""
import os
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print(f"db: {p}\n=== unmatched google rows (definition_id IS NULL) ===")
rows = list(con.execute(
    "SELECT metric_type, COUNT(*) c, MIN(recorded_at), MAX(recorded_at), "
    "MIN(unit), MIN(granularity) "
    "FROM health_metrics WHERE source='google_health_connect' "
    "AND definition_id IS NULL GROUP BY metric_type ORDER BY c DESC"))
if not rows:
    print("  (none - every google row is linked to a definition)")
for r in rows:
    print(f"  {r[0]:<24} rows={r[1]:>4}  gran={r[5]:<6} unit={r[4]:<8} "
          f"{str(r[2])[:16]} .. {str(r[3])[:16]}")
con.close()
