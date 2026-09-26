"""Show the persisted per-type sync cursors for a user."""
import json
import sqlite3
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "data/health_tracker.db"
uid = sys.argv[2] if len(sys.argv) > 2 else "1"
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
row = con.execute("SELECT value FROM app_settings WHERE key=?",
                  (f"sync_settings_{uid}",)).fetchone()
if not row:
    print("no sync settings row")
else:
    d = json.loads(row[0])
    tc = d.get("type_cursors") or {}
    print("sync_days_back:", d.get("sync_days_back"))
    print("last_google_sync:", d.get("last_google_sync"))
    print(f"type_cursors ({len(tc)}):")
    for k, v in tc.items():
        print(f"  {k:24} {v}")
print("health_metrics rows:", con.execute(
    "SELECT COUNT(*) FROM health_metrics").fetchone()[0])
con.close()
