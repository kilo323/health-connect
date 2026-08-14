"""Quick analysis of health_metrics row counts and granularity per metric type."""
import sqlite3

c = sqlite3.connect('data/health_tracker.db')
cur = c.cursor()

print('=== row counts by metric_type ===')
cur.execute(
    'SELECT metric_type, COUNT(*), MIN(recorded_at), MAX(recorded_at) '
    'FROM health_metrics GROUP BY metric_type ORDER BY 2 DESC'
)
for r in cur.fetchall():
    print(f'{r[0]:22} count={r[1]:5}  min={r[2]}  max={r[3]}')

print('\n=== steps (all rows) ===')
cur.execute("SELECT value, unit, recorded_at FROM health_metrics WHERE metric_type='steps' ORDER BY recorded_at")
for r in cur.fetchall():
    print(r)

print('\n=== granularity sample: distinct dates per type ===')
cur.execute(
    "SELECT metric_type, COUNT(DISTINCT DATE(recorded_at)) AS days, COUNT(*) AS rows "
    "FROM health_metrics GROUP BY metric_type ORDER BY 3 DESC"
)
for r in cur.fetchall():
    per_day = (r[2] / r[1]) if r[1] else 0
    print(f'{r[0]:22} days={r[1]:3} rows={r[2]:6}  (~{per_day:.0f}/day)')
