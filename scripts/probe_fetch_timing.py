"""Diagnostic: time a single 30-day raw fetch per data type, with page timing.

Shows which types dominate a backfill's wall time (API pagination vs DB writes).
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
TYPES = ["steps", "heart_rate", "sleep", "distance", "move_minutes", "oxygen_saturation"]


async def timed_fetch(svc, data_type, start, end):
    api_type = svc.DATA_TYPE_MAP.get(data_type, data_type)
    token = await svc._get_valid_access_token(USER_ID)
    url = f"{svc.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints"
    filt = svc._build_filter(api_type, start, end)
    page_token = None
    page = 0
    total = 0
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as c:
        while True:
            params = {"pageSize": 1000}
            if filt:
                params["filter"] = filt
            if page_token:
                params["pageToken"] = page_token
            pt0 = time.perf_counter()
            r = await svc._request_with_retry(
                c, "GET", url,
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params=params)
            el = time.perf_counter() - pt0
            if r.status_code != 200:
                return None, page, total, time.perf_counter() - t0, f"ERR {r.status_code}"
            body = r.json()
            batch = body.get("dataPoints", []) or []
            total += len(batch)
            page += 1
            print(f"      page {page:>3}: {len(batch):>5} pts in {el:.2f}s (running total {total})", flush=True)
            page_token = body.get("nextPageToken") or None
            if not page_token:
                break
    return True, page, total, time.perf_counter() - t0, ""


async def main():
    svc = GoogleHealthService()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    for dt in TYPES:
        print(f"\n=== {dt} (30d raw) ===", flush=True)
        ok, page, total, el, err = await timed_fetch(svc, dt, start, now)
        if not ok:
            print(f"  {dt}: {err}")
            continue
        print(f"  -> {dt}: {total} points over {page} pages in {el:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
