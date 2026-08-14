"""Probe dailyRollUp response shapes for candidate metrics.

Run: .venv\\Scripts\\python.exe scripts\\probe_rollup_shapes.py
"""
import asyncio
import json
import sys

sys.path.insert(0, "app")

from app.services.google_health import GoogleHealthService

# (internal_name, days_back) — keep heart-rate/active-minutes within 14-day cap.
PROBE = [
    ("heart_rate", 7),
    ("distance", 7),
    ("move_minutes", 7),
    ("heart_minutes", 7),
]


async def main():
    svc = GoogleHealthService()
    for internal, days in PROBE:
        print(f"\n=== {internal} dailyRollUp (last {days}d) ===")
        try:
            rollups = await svc.fetch_daily_rollup(user_id=1, data_type=internal, days_back=days)
            print(f"count={len(rollups)}")
            if rollups:
                # Print full first point to see exact field names.
                print(json.dumps(rollups[0], indent=2))
            else:
                print("(empty)")
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())
