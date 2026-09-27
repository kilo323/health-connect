"""Measure get_unmatched_metrics' SQL cost with and without a covering index.

Runs against a copy of the database in the user's state (no metric
definitions, every row unlinked) using exactly the SQL the current code issues,
then repeats after creating the index proposed for the fix.

Run: .venv\\Scripts\\python.exe -u scripts\\bench_unmatched_index.py
"""
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "data", "health_tracker.db")
TMP = os.path.join(os.environ.get("TEMP", ROOT), "health_tracker_idxbench.db")

for suffix in ("", "-wal", "-shm"):
    p = TMP + suffix
    if os.path.exists(p):
        os.remove(p)
shutil.copy2(SRC, TMP)

con = sqlite3.connect(TMP)
con.execute("PRAGMA journal_mode=WAL")
size_mb = os.path.getsize(TMP) / 1024 / 1024
n = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
print(f"db: {n} rows, {size_mb:.1f} MB (same state as their container)\n")

GROUP_SQL = (
    "SELECT metric_type, COUNT(*) FROM health_metrics "
    "WHERE definition_id IS NULL GROUP BY metric_type "
    "ORDER BY COUNT(*) DESC"
)
LATEST_SQL = (
    "SELECT value, unit FROM health_metrics "
    "WHERE metric_type = ? AND definition_id IS NULL "
    "ORDER BY recorded_at DESC LIMIT 1"
)
DOCS_SQL = (
    "SELECT DISTINCT source_document FROM health_metrics "
    "WHERE metric_type = ? AND definition_id IS NULL "
    "AND source_document IS NOT NULL"
)

NEW_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_health_metrics_unmatched "
    "ON health_metrics (definition_id, metric_type, recorded_at, source_document)"
)


def run_suite(label):
    t0 = time.perf_counter()
    rows = con.execute(GROUP_SQL).fetchall()
    for mt, _ in rows:
        con.execute(LATEST_SQL, (mt,)).fetchone()
        con.execute(DOCS_SQL, (mt,)).fetchall()
    el = time.perf_counter() - t0
    print(f"  {label:<34} {el:6.2f}s   ({1 + 2 * len(rows)} queries, {len(rows)} types)")
    return el


LATEST_PASS_SQL = """
    SELECT metric_type, cnt, value, unit FROM (
        SELECT metric_type, value, unit,
               ROW_NUMBER() OVER (PARTITION BY metric_type ORDER BY recorded_at DESC) rn,
               COUNT(*) OVER (PARTITION BY metric_type) cnt
        FROM health_metrics WHERE definition_id IS NULL
    ) WHERE rn = 1
"""
DOCS_PASS_SQL = """
    SELECT metric_type, source_document FROM health_metrics
    WHERE definition_id IS NULL AND source_document IS NOT NULL
    GROUP BY metric_type, source_document
"""


def run_rewrite(label):
    t = time.perf_counter()
    a = con.execute(LATEST_PASS_SQL).fetchall()
    b = con.execute(DOCS_PASS_SQL).fetchall()
    el = time.perf_counter() - t
    print(f"  {label:<34} {el:6.2f}s   (2 queries, {len(a)} types, {len(b)} doc rows)")
    return el


def explain(label, sql, *args):
    plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args)]
    print(f"  {label}:")
    for p in plan:
        print(f"      {p}")


print("=== as the code runs today (27 queries) ===")
before = run_suite("full scans, no index")
before_rw = run_rewrite("2-query rewrite, no index")
sample_type = con.execute(GROUP_SQL).fetchone()[0]

print("\n=== after adding the covering index ===")
t = time.perf_counter()
con.execute(NEW_INDEX)
con.commit()
print(f"  index create: {time.perf_counter() - t:.2f}s")
idx_mb = os.path.getsize(TMP) / 1024 / 1024
print(f"  db now {idx_mb:.1f} MB (index adds {idx_mb - size_mb:.1f} MB)")
after = run_suite("27 queries, with index")
after_rw = run_rewrite("2-query rewrite, with index")

print(f"\n  index alone:            {before / after:.1f}x  ({before:.2f}s -> {after:.2f}s)")
print(f"  rewrite alone:          {before / before_rw:.1f}x  ({before:.2f}s -> {before_rw:.2f}s)")
print(f"  index + rewrite:        {before / after_rw:.1f}x  ({before:.2f}s -> {after_rw:.2f}s)")

print("\n=== query plans with the index ===")
explain("GROUP BY (count)", GROUP_SQL)
explain("latest row", LATEST_SQL, sample_type)
explain("distinct docs", DOCS_SQL, sample_type)

con.close()
sys.exit(0)
