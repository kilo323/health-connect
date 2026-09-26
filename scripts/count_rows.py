"""Raw row counts for a SQLite DB, ignoring the metric-definition join."""
import os
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("db:", p)
for r in con.execute(
        "SELECT source, COUNT(*) FROM health_metrics GROUP BY source ORDER BY 2 DESC"):
    print(f"  source={r[0]!r:28} rows={r[1]}")
for r in con.execute(
        "SELECT metric_type, granularity, COUNT(*) FROM health_metrics "
        "WHERE source='google_health_connect' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20"):
    print(f"  {str(r[0])[:30]:<30} gran={str(r[1]):<6} rows={r[2]}")
print("  TOTAL:", con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0])
con.close()
