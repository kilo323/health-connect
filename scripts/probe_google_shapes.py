"""Probe Google Health API v4 to inspect response shapes and test filter field names.

Run with the project venv: .venv\\Scripts\\python.exe scripts\\probe_google_shapes.py
"""
import asyncio
import json
import sys

sys.path.insert(0, "app")

from app.services.google_health import GoogleHealthService


async def main():
    svc = GoogleHealthService()

    # 1. Steps daily rollup: dump the raw first point to see the real field names.
    print("=== steps dailyRollUp: first raw point ===")
    try:
        rollups = await svc.fetch_daily_rollup(user_id=1, data_type="steps", days_back=7)
        print(f"count={len(rollups)}")
        if rollups:
            print(json.dumps(rollups[0], indent=2)[:1500])
        else:
            print("(empty)")
    except Exception as e:
        print(f"ERROR: {e}")

    # 2. Sleep filter test
    print("\n=== sleep list filter test ===")
    for fdesc, in [("sleep.interval.start_time",)]:
        try:
            data = await svc.fetch_health_data(user_id=1, data_type="sleep", days_back=7)
            print(f"sleep OK count={len(data)}")
            if data:
                print(json.dumps(data[0], indent=2)[:800])
        except Exception as e:
            print(f"sleep ERROR: {e}")

    # 3. Oxygen saturation filter test
    print("\n=== oxygen_saturation list filter test ===")
    try:
        data = await svc.fetch_health_data(user_id=1, data_type="oxygen_saturation", days_back=7)
        print(f"oxygen OK count={len(data)}")
        if data:
            print(json.dumps(data[0], indent=2)[:800])
    except Exception as e:
        print(f"oxygen ERROR: {e}")


if __name__ == "__main__":
    asyncio.run(main())
