"""Verify hybrid sync output: per-metric counts + granularity by date."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
DB = os.path.join(os.path.dirname(__file__), "..", "data", "health_tracker.db")

c = sqlite3.connect(DB)
cur = c.cursor()

print("=== counts by metric_type ===")
cur.execute("SELECT metric_type, COUNT(*) FROM health_metrics GROUP BY metric_type ORDER BY 2 DESC")
for t, n in cur.fetchall():
    print(f"  {t:24} {n}")

print("\n=== rows per day for steps (expect 1/day) ===")
cur.execute("SELECT DATE(recorded_at), COUNT(*), SUM(value) FROM health_metrics WHERE metric_type='steps' GROUP BY 1 ORDER BY 1")
for d, n, s in cur.fetchall():
    print(f"  {d}  rows={n}  total={s}")

print("\n=== heart_rate variants per day ===")
cur.execute("""SELECT DATE(recorded_at), metric_type, COUNT(*), ROUND(AVG(value),1)
               FROM health_metrics WHERE metric_type LIKE '%eart%ate%'
               GROUP BY 1,2 ORDER BY 1,2""")
for d, t, n, a in cur.fetchall():
    print(f"  {d}  {t:20} rows={n} avg={a}")

print("\n=== distance rows per day (rollup=1/day older, raw=many recent) ===")
cur.execute("SELECT DATE(recorded_at), COUNT(*) FROM health_metrics WHERE metric_type='distance' GROUP BY 1 ORDER BY 1")
for d, n in cur.fetchall():
    print(f"  {d}  rows={n}")
