"""Inspect the raw unit strings stored per metric_type, as repr/codepoints.

The console mangled body_temperature's unit, so check what is actually stored.
"""
import sqlite3
import sys
from collections import Counter

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("db:", p, "\n=== stored unit per metric_type ===")
for mt, unit, n in con.execute(
        "SELECT metric_type, unit, COUNT(*) FROM health_metrics "
        "WHERE source='google_health_connect' GROUP BY metric_type ORDER BY 3 DESC"):
    cps = " ".join(f"U+{ord(ch):04X}" for ch in (unit or ""))
    print(f"  {mt:<26} {n:>7}  unit={unit!r:<12} {cps}")
con.close()
