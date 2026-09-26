"""Measure the true raw point volume per data type over N days.

Pages through the raw endpoint counting points only (no DB writes) so we can
size the backfill and decide cutoffs from real numbers.
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dotenv import load_dotenv

load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

import httpx

from app.services.google_health import GoogleHealthService

USER_ID = 1
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
TYPES = sys.argv[2].split(",") if len(sys.argv) > 2 else ["heart_rate"]


async def count(svc, data_type, start, end):
    api_type = svc.DATA_TYPE_MAP.get(data_type, data_type)
    token = await svc._get_valid_access_token(USER_ID)
    url = f"{svc.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints"
    filt = svc._build_filter(api_type, start, end)
    pt = None
    page = 0
    total = 0
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as c:
        while True:
            params = {"pageSize": 1000}
            if filt:
                params["filter"] = filt
            if pt:
                params["pageToken"] = pt
            r = await svc._request_with_retry(
                c, "GET", url,
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params=params)
            if r.status_code != 200:
                return total, page, time.perf_counter() - t0, f"ERR {r.status_code}"
            b = r.json()
            total += len(b.get("dataPoints", []) or [])
            page += 1
            pt = b.get("nextPageToken") or None
            if not pt:
                break
    return total, page, time.perf_counter() - t0, ""


async def main():
    svc = GoogleHealthService()
    now = datetime.now(timezone.utc)
    for dt in TYPES:
        start = now - timedelta(days=DAYS)
        total, page, el, err = await count(svc, dt, start, now)
        status = f" {err}" if err else ""
        print(f"{dt:>20} {DAYS}d: {total:>9} points, {page:>5} pages, {el:>7.1f}s "
              f"({el/max(page,1):.2f}s/page){status}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
