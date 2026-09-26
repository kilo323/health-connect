"""Inspect metric_definitions inside a backup database."""
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else (
    "data/health_tracker.db.bak-prehrmigrate.20260926-124848")
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
rows = list(con.execute("SELECT id, name, aliases FROM metric_definitions ORDER BY id"))
print(f"metric_definitions in {p}: {len(rows)}\n")
for r in rows:
    aliases = (r[2] or "")[:90]
    print(f"  {r[0]:>2} {r[1]:<26} {aliases}")
con.close()
