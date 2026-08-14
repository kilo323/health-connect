"""Show current google_oauth_config AppSettings state (masked)."""
import json
import os
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "..", "data", "health_tracker.db")
c = sqlite3.connect(DB)
cur = c.cursor()
cur.execute("SELECT value, description FROM app_settings WHERE key='google_oauth_config'")
row = cur.fetchone()
if not row:
    print("google_oauth_config: MISSING (will be seeded from env on next startup)")
else:
    value, desc = row
    try:
        d = json.loads(value)
        cid = d.get("client_id", "")
        csec = d.get("client_secret", "")
        print(f"client_id: {cid[:20]}... ({'set' if cid else 'EMPTY'})")
        print(f"client_secret: {'set' if csec else 'EMPTY'}")
        print(f"redirect_uri: {d.get('redirect_uri','')!r}")
        print(f"description: {desc}")
    except Exception as e:
        print(f"value present but not valid JSON: {e}")
