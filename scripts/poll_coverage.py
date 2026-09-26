"""Poll a SQLite DB's google_health_connect coverage (used to watch a sync run)."""
import os
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ.get("TEMP", "."), "health_tracker_repro.db")
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
q = """
SELECT m.name, COUNT(*), COUNT(DISTINCT substr(h.recorded_at,1,10)),
       MIN(h.recorded_at), MAX(h.recorded_at)
FROM health_metrics h JOIN metric_definitions m ON m.id = h.definition_id
WHERE h.source = 'google_health_connect'
GROUP BY m.id ORDER BY 2 DESC
"""
total = 0
for r in con.execute(q):
    total += r[1]
    print(f"  {str(r[0])[:28]:<28} rows={r[1]:>6} days={r[2]:>3}  {str(r[3])[:16]} .. {str(r[4])[:16]}")
print(f"  {'TOTAL':<28} rows={total:>6}")
con.close()
