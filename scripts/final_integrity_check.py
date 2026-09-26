"""Final integrity check on the live database."""
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("db:", p)
checks = [
    ("metric_definitions",
     "SELECT COUNT(*) FROM metric_definitions"),
    ("google rows",
     "SELECT COUNT(*) FROM health_metrics WHERE source='google_health_connect'"),
    ("unlinked google rows",
     "SELECT COUNT(*) FROM health_metrics "
     "WHERE source='google_health_connect' AND definition_id IS NULL"),
    ("duplicate logical points",
     "SELECT COUNT(*) FROM (SELECT user_id, metric_type, recorded_at, source, "
     "COUNT(*) c FROM health_metrics GROUP BY 1,2,3,4 HAVING c > 1)"),
    ("distinct google metric types",
     "SELECT COUNT(DISTINCT metric_type) FROM health_metrics "
     "WHERE source='google_health_connect'"),
]
for label, sql in checks:
    print(f"  {label:<30} {con.execute(sql).fetchone()[0]}")
con.close()
