"""Probe dailyRollUp response shapes for the remaining rollup-capable metrics.

Run: .venv\\Scripts\\python.exe scripts\\probe_rollup_remaining.py
"""
import asyncio
import json
import os
import sys

# Repo root on the path so `from app.services...` resolves regardless of CWD.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.google_health import GoogleHealthService

# (internal_name, days_back). Keep 14-day-capped types within their window.
# calories / heart_minutes / move_minutes are 14-day; rest are 90-day.
PROBE = [
    ("calories", 7),
    ("heart_minutes", 7),
    ("weight", 7),
    ("blood_glucose", 7),
    ("body_fat_percentage", 7),
    ("body_temperature", 7),
]


async def main():
    svc = GoogleHealthService()
    for internal, days in PROBE:
        print(f"\n=== {internal} dailyRollUp (last {days}d) ===")
        try:
            rollups = await svc.fetch_daily_rollup(user_id=1, data_type=internal, days_back=days)
            print(f"count={len(rollups)}")
            if rollups:
                print(json.dumps(rollups[0], indent=2))
            else:
                print("(empty)")
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())
