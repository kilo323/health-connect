"""Show minutes metric breakdown after re-sync."""
import os
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "..", "data", "health_tracker.db")
c = sqlite3.connect(DB)
cur = c.cursor()
print("=== minutes metric_type counts ===")
cur.execute(
    "SELECT metric_type, COUNT(*), MIN(DATE(recorded_at)), MAX(DATE(recorded_at)) "
    "FROM health_metrics WHERE metric_type LIKE '%Minutes%' OR metric_type LIKE '%minute%' "
    "GROUP BY metric_type ORDER BY 2 DESC"
)
for r in cur.fetchall():
    print(r)

print("\n=== sample rows ===")
cur.execute(
    "SELECT metric_type, value, unit, DATE(recorded_at) FROM health_metrics "
    "WHERE metric_type LIKE '%Minutes%' ORDER BY recorded_at DESC LIMIT 10"
)
for r in cur.fetchall():
    print(r)
