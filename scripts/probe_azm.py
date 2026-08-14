"""Probe active-zone-minutes (heart_minutes) rollup shape over a wide window."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.google_health import GoogleHealthService


async def main():
    svc = GoogleHealthService()
    for days in (90, 30):
        print(f"\n=== heart_minutes dailyRollUp (last {days}d) ===")
        try:
            rollups = await svc.fetch_daily_rollup(user_id=1, data_type="heart_minutes", days_back=days)
            print(f"count={len(rollups)}")
            if rollups:
                print(json.dumps(rollups[0], indent=2))
                break
        except Exception as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())
