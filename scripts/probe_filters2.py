"""Find working filter field/format for sleep and daily-oxygen-saturation list calls.

Run: .venv\\Scripts\\python.exe scripts\\probe_filters2.py
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "app")

import httpx
from app.services.google_health import GoogleHealthService

BASE = "https://health.googleapis.com"

# Each candidate is (description, filter_string_built_with_start_end)
def ts(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")

def dt(d):
    return d.strftime("%Y-%m-%d")

CANDIDATES = {
    "sleep": [
        ("interval.start_time (ts)", lambda s, e: f'sleep.interval.start_time >= "{ts(s)}" AND sleep.interval.start_time < "{ts(e)}"'),
        ("no data_type prefix", lambda s, e: f'interval.start_time >= "{ts(s)}" AND interval.start_time < "{ts(e)}"'),
        ("create_time", lambda s, e: f'sleep.create_time >= "{ts(s)}" AND sleep.create_time < "{ts(e)}"'),
        ("NO FILTER", lambda s, e: None),
    ],
    "oxygen_saturation": [
        ("date (civil date fmt)", lambda s, e: f'daily_oxygen_saturation.date >= "{dt(s)}" AND daily_oxygen_saturation.date < "{dt(e)}"'),
        ("date (ts fmt)", lambda s, e: f'daily_oxygen_saturation.date >= "{ts(s)}" AND daily_oxygen_saturation.date < "{ts(e)}"'),
        ("NO FILTER", lambda s, e: None),
    ],
}


async def try_one(svc, api_type, filt):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    f = filt(start, end)
    url = f"{BASE}/v4/users/me/dataTypes/{api_type}/dataPoints"
    token = await svc._get_valid_access_token(1)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    params = {"filter": f} if f else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers, params=params)
    if r.status_code == 200:
        n = len(r.json().get("dataPoints", []))
        return f"OK ({n} pts)"
    return f"{r.status_code} {r.json().get('error', {}).get('message', '')[:90]}"


async def main():
    svc = GoogleHealthService()
    for internal, cands in CANDIDATES.items():
        api_type = svc.DATA_TYPE_MAP[internal]
        print(f"\n=== {internal} ({api_type}) ===")
        for desc, fn in cands:
            res = await try_one(svc, api_type, fn)
            print(f"  {desc:32} -> {res}")


if __name__ == "__main__":
    asyncio.run(main())
