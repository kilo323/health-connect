"""Diagnose sync settings and data range."""
import os
import sqlite3
import sys

DB = os.path.join(os.path.dirname(__file__), "..", "data", "health_tracker.db")
c = sqlite3.connect(DB)
cur = c.cursor()

print("=== app_settings: sync + rollup keys ===")
cur.execute("SELECT key, substr(value,1,300) FROM app_settings WHERE key LIKE 'sync%' OR key LIKE '%rollup%' OR key LIKE 'google_health_tokens_%'")
for k, v in cur.fetchall():
    print(f"  {k} = {v}")

print("\n=== data date range per metric ===")
cur.execute("SELECT metric_type, COUNT(*), MIN(DATE(recorded_at)), MAX(DATE(recorded_at)) FROM health_metrics GROUP BY metric_type ORDER BY 2 DESC")
for t, n, mn, mx in cur.fetchall():
    print(f"  {t:22} n={n:5}  {mn} -> {mx}")

print("\n=== steps rows per day (all) ===")
cur.execute("SELECT DATE(recorded_at), COUNT(*) FROM health_metrics WHERE metric_type='steps' GROUP BY 1 ORDER BY 1")
for d, n in cur.fetchall():
    print(f"  {d} rows={n}")
