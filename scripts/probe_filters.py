"""Test candidate filter field names for sleep and oxygen_saturation.

Run: .venv\\Scripts\\python.exe scripts\\probe_filters.py
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "app")

import httpx
from app.services.google_health import GoogleHealthService

BASE = "https://health.googleapis.com"

CANDIDATES = {
    "sleep": [
        "sleep.interval.start_time",
        "sleep.session_start_time",
        "sleep.start_time",
        "sleep.interval.civil_start_time",
    ],
    "oxygen_saturation": [
        "daily_oxygen_saturation.interval.civil_start_time",
        "daily_oxygen_saturation.civil_start_time",
        "daily_oxygen_saturation.sample_time.physical_time",
        "daily_oxygen_saturation.date",
    ],
}


async def try_filter(svc, api_type, field):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    f = f'{field} >= "{start:%Y-%m-%dT%H:%M:%SZ}" AND {field} < "{end:%Y-%m-%dT%H:%M:%SZ}"'
    url = f"{BASE}/v4/users/me/dataTypes/{api_type}/dataPoints"
    token = await svc._get_valid_access_token(1)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers, params={"filter": f})
    if r.status_code == 200:
        n = len(r.json().get("dataPoints", []))
        return f"OK ({n} pts)"
    return f"{r.status_code} {r.text[:120]}"


async def main():
    svc = GoogleHealthService()
    for internal, fields in CANDIDATES.items():
        api_type = svc.DATA_TYPE_MAP[internal]
        print(f"=== {internal} ({api_type}) ===")
        for field in fields:
            res = await try_filter(svc, api_type, field)
            print(f"  {field:55} -> {res}")


if __name__ == "__main__":
    asyncio.run(main())
