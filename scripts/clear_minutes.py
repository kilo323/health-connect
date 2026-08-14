"""Delete move_minutes / heart_minutes rows so a re-sync shows the new breakdown."""
import os
import sqlite3

DB = os.path.join(os.path.dirname(__file__), "..", "data", "health_tracker.db")
c = sqlite3.connect(DB)
cur = c.cursor()
cur.execute(
    "DELETE FROM health_metrics WHERE metric_type LIKE '%minute%' OR metric_type LIKE '%Minutes%'"
)
c.commit()
print("deleted minute rows:", cur.rowcount)
