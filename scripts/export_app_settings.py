"""Export the app_settings table from the SQLite database to a JSON file."""
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "health_tracker.db")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app_settings.json")


def main():
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, key, value, description FROM app_settings ORDER BY key"
        ).fetchall()
    finally:
        conn.close()

    data = [dict(r) for r in rows]

    export = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "table": "app_settings",
        "row_count": len(data),
        "rows": data,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False, default=str)

    print(f"Wrote {len(data)} rows from app_settings to {OUTPUT_PATH}")
    print(json.dumps(export, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
