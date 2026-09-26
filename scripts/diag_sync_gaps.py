"""Diagnostic: inspect Google Health sync coverage in the DB (read-only).

Reports per-metric row counts, date range, and per-day gaps so we can tell
whether sync is missing whole days, whole metrics, or just sparse points.
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta

DB = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()

tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("TABLES:", ", ".join(tables))
print()


def cols(table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]


if "health_metrics" in tables:
    print("health_metrics columns:", cols("health_metrics"))
else:
    print("!! no health_metrics table")
    sys.exit(1)

# 1. Per-metric coverage
rows = list(cur.execute("""
    SELECT m.id, m.name, m.unit, m.data_type,
           COUNT(h.id) AS n,
           MIN(h.recorded_at) AS first_at,
           MAX(h.recorded_at) AS last_at
    FROM metric_definitions m
    LEFT JOIN health_metrics h ON h.definition_id = m.id
    GROUP BY m.id
    ORDER BY n DESC, m.name
"""))
print("\n=== PER-METRIC COVERAGE (rows / first / last) ===")
print(f"{'id':>4} {'name':<34} {'unit':<10} {'type':<10} {'rows':>6}  first .. last")
for mid, name, unit, dtype, n, first, last in rows:
    f = (first or "-")[:16]
    la = (last or "-")[:16]
    print(f"{mid:>4} {str(name)[:34]:<34} {str(unit or '')[:10]:<10} {str(dtype or '')[:10]:<10} {n:>6}  {f} .. {la}")

# 2. Orphan rows (metric_definition_id with no definition)
orphans = list(cur.execute("""
    SELECT h.definition_id, COUNT(*)
    FROM health_metrics h
    LEFT JOIN metric_definitions m ON m.id = h.definition_id
    WHERE m.id IS NULL GROUP BY 1
"""))
if orphans:
    print("\n!! ORPHAN health_metrics rows (no matching metric_definition):", orphans)

# 3. Per-day coverage for the busiest metrics -> find whole missing days
print("\n=== PER-DAY ROW COUNTS (top metrics, gaps flagged) ===")
overall_first = cur.execute(
    "SELECT MIN(recorded_at) FROM health_metrics").fetchone()[0]
overall_last = cur.execute(
    "SELECT MAX(recorded_at) FROM health_metrics").fetchone()[0]
print(f"overall range: {overall_first} .. {overall_last}")
if not overall_first:
    sys.exit(0)

start = datetime.fromisoformat(overall_first).replace(hour=0, minute=0, second=0, microsecond=0)
end = datetime.fromisoformat(overall_last).replace(hour=0, minute=0, second=0, microsecond=0)
alldays = []
d = start
while d <= end:
    alldays.append(d)
    d += timedelta(days=1)
print(f"calendar days in range: {len(alldays)} ({start.date()} .. {end.date()})")

for mid, name, _unit, _dtype, n, _f, _l in rows[:12]:
    if not n:
        continue
    per_day = defaultdict(int)
    for (ts,) in cur.execute(
            "SELECT recorded_at FROM health_metrics WHERE definition_id=?",
            (mid,)):
        try:
            dt = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        per_day[dt.replace(hour=0, minute=0, second=0, microsecond=0)] += 1
    missing = [x.date() for x in alldays if x not in per_day]
    tag = "OK" if not missing else f"MISSING {len(missing)} days"
    print(f"\n[{mid}] {name}: {n} rows over {len(per_day)} days -> {tag}")
    if missing:
        print(f"     missing: {', '.join(str(x) for x in missing)}")
    # print daily counts compactly
    line = " ".join(f"{x.date()}:{per_day.get(x, 0)}" for x in alldays)
    print(f"     {line}")

# 4. sync settings / cursor
print("\n=== SYNC SETTINGS ROWS ===")
if "app_settings" in tables:
    for k, v in cur.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'sync%' OR key LIKE '%google%'"):
        print(f"  {k} = {v}")

con.close()
