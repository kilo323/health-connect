"""Per-day raw vs daily coverage for a metric, to check the rollup window split."""
import sqlite3
import sys
from collections import defaultdict

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
metric = sys.argv[2] if len(sys.argv) > 2 else "heart_rate"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)

lo, hi = con.execute(
    "SELECT MIN(recorded_at), MAX(recorded_at) FROM health_metrics "
    "WHERE source='google_health_connect'").fetchone()
print(f"overall google range: {lo} .. {hi}\n")
print(f"=== {metric}: per-day raw vs daily ===")

per = defaultdict(lambda: defaultdict(int))
for gran, ts in con.execute("""
    SELECT granularity, recorded_at FROM health_metrics
    WHERE source='google_health_connect' AND metric_type = ?
""", (metric,)):
    per[ts[:10]][gran] += 1

for day in sorted(per):
    r = per[day].get("raw", 0)
    d = per[day].get("daily", 0)
    tag = "raw only" if r and not d else ("daily only" if d and not r else "BOTH")
    print(f"  {day}  raw={r:>7}  daily={d:>3}   {tag}")

print("\n=== granularity totals by data_type ===")
for gran, n, days in con.execute("""
    SELECT granularity, COUNT(*), COUNT(DISTINCT substr(recorded_at,1,10))
    FROM health_metrics WHERE source='google_health_connect'
    GROUP BY granularity
"""):
    print(f"  {gran:<8} rows={n:>7}  days={days}")
con.close()
