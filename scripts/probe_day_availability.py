"""Diagnostic: does Google actually have data for old days?

Instead of paginating a 30-day window, issue ONE tiny request per data type per
probe day (pageSize=5). This answers "is the data missing in Google, or are we
dropping it on the way in?" cheaply.

Run: .venv\\Scripts\\python.exe scripts\\probe_day_availability.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

import httpx

from app.services.google_health import GoogleHealthService
from app.services.scheduler import SYNC_DATA_TYPES

USER_ID = 1

# One recent day (known to exist) plus several older days inside the 30-day floor.
PROBE_DAYS = ["2026-09-25", "2026-09-20", "2026-09-10", "2026-09-01", "2026-08-28"]


async def probe_day(svc, data_type, day):
    api_type = svc.DATA_TYPE_MAP.get(data_type, data_type)
    start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    end = start + timedelta(days=1)
    token = await svc._get_valid_access_token(USER_ID)
    url = f"{svc.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints"
    filt = svc._build_filter(api_type, start, end)
    params = {"pageSize": 5}
    if filt:
        params["filter"] = filt
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await svc._request_with_retry(
            client, "GET", url,
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            params=params)
    if r.status_code != 200:
        return f"ERR {r.status_code} {svc._error_message(r)[:90]}"
    n = len(r.json().get("dataPoints", []))
    total = r.json().get("totalRows") or r.json().get("totalCount")
    return f"n={n}" + (f" total={total}" if total is not None else "")


async def probe_rollup_day(svc, data_type, day):
    api_type = svc.DATA_TYPE_MAP.get(data_type, data_type)
    start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    end = start + timedelta(days=1)
    token = await svc._get_valid_access_token(USER_ID)
    url = f"{svc.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints:dailyRollUp"
    body = {"range": {"start": {"date": {"year": start.year, "month": start.month, "day": start.day}},
                      "end": {"date": {"year": end.year, "month": end.month, "day": end.day}}},
            "windowSizeDays": 1}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await svc._request_with_retry(
            client, "POST", url,
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body)
    if r.status_code != 200:
        return f"ERR {r.status_code} {svc._error_message(r)[:90]}"
    return f"n={len(r.json().get('rollupDataPoints', []))}"


async def main():
    svc = GoogleHealthService()
    print(f"{'data_type':<22} " + " ".join(f"{d[5:]:>12}" for d in PROBE_DAYS))
    print("-" * 100)
    for dt in SYNC_DATA_TYPES:
        cells = []
        for day in PROBE_DAYS:
            try:
                cells.append(await probe_day(svc, dt, day))
            except Exception as ex:
                cells.append(f"EXC {type(ex).__name__}")
        print(f"{dt:<22} " + " ".join(f"{c:>12}" for c in cells), flush=True)

    print("\n=== dailyRollUp availability ===")
    print(f"{'data_type':<22} " + " ".join(f"{d[5:]:>12}" for d in PROBE_DAYS))
    print("-" * 100)
    for dt in SYNC_DATA_TYPES:
        cells = []
        for day in PROBE_DAYS:
            try:
                cells.append(await probe_rollup_day(svc, dt, day))
            except Exception as ex:
                cells.append(f"EXC {type(ex).__name__}")
        print(f"{dt:<22} " + " ".join(f"{c:>12}" for c in cells), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
