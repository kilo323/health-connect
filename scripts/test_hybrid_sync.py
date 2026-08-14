"""Exercise the hybrid rollup sync directly (14 days, default 7-day cutoff).

Run from repo root: .venv\\Scripts\\python.exe scripts\\test_hybrid_sync.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.scheduler import sync_health_data


async def main():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=14)
    print(f"sync window: {start} -> {end}  (cutoff=7d -> rollup [start..cutoff], raw [cutoff..end])")
    # Empty settings_data -> DEFAULT_ROLLUP_CONFIG (steps/distance/calories/heart_rate/move_minutes enabled, 7d cutoff)
    saved = await sync_health_data(1, start, end, settings_data={})
    print(f"\nTOTAL saved: {saved}")


if __name__ == "__main__":
    asyncio.run(main())
