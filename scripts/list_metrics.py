"""Print all distinct metric types from health_metrics for seed planning."""
import sqlite3

conn = sqlite3.connect("data/health_tracker.db")
rows = conn.execute("""
    SELECT metric_type, unit, COUNT(*) as cnt
    FROM health_metrics
    GROUP BY metric_type, unit
    ORDER BY cnt DESC, metric_type
""").fetchall()
for r in rows:
    unit = r[1] or "-"
    print(f"{r[2]:3d}x  {r[0]:50s}  [{unit}]")
print(f"\nTotal distinct: {len(rows)}")
conn.close()
