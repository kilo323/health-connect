"""Clear health_metrics and remove last_google_sync so the next sync does a full backfill."""
import json
import os
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "..", "data", "health_tracker.db")
c = sqlite3.connect(DB)
cur = c.cursor()

cur.execute("DELETE FROM health_metrics")
print("metrics deleted:", cur.rowcount)

cur.execute("SELECT key, value FROM app_settings WHERE key LIKE 'sync_settings_%'")
for key, value in cur.fetchall():
    d = json.loads(value)
    if "last_google_sync" in d:
        d.pop("last_google_sync", None)
        cur.execute("UPDATE app_settings SET value=? WHERE key=?", (json.dumps(d), key))
        print(f"{key}: removed last_google_sync; sync_days_back={d.get('sync_days_back')}")
    else:
        print(f"{key}: no last_google_sync (sync_days_back={d.get('sync_days_back')})")

c.commit()
print("done")
