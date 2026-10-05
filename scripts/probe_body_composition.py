"""Probe the Google Health API v4 for body-composition data types beyond
body-fat percentage (e.g. fat mass, muscle mass, skeletal muscle mass, bone
mass, body water), to see which the live account actually exposes.

Read-only: performs GET dataPoints requests only. Writes nothing.

Run with the project venv:
    .\\.venv\\Scripts\\python.exe scripts\\probe_body_composition.py
"""
import asyncio
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from app.services.google_health import GoogleHealthService

# Broad candidate list of plausible v4 data-type slugs, spanning every
# category Google documents for Health Connect / the v4 REST surface. The 13
# the app already syncs are included as positive controls.
CANDIDATES = [
    # --- already synced (controls) ---
    "steps", "heart-rate", "sleep", "weight", "blood-glucose",
    "core-body-temperature", "distance", "total-calories",
    "daily-oxygen-saturation", "body-fat", "height",
    "active-zone-minutes", "active-minutes",
    # --- body composition ---
    "body-fat-mass", "fat-mass", "lean-body-mass", "muscle-mass",
    "skeletal-muscle-mass", "bone-mass", "body-water", "body-water-mass",
    "visceral-fat", "basal-metabolic-rate", "metabolic-rate",
    # --- activity / fitness ---
    "calories-burned", "active-calories", "basal-calories",
    "floors-climbed", "elevation-gained", "step-count", "cadence",
    "stride-length", "speed", "power", "exercise", "workout",
    "activity-recognition", "stand-hours", "wheelchair-pushes",
    # --- vitals / cardio ---
    "resting-heart-rate", "heart-rate-variability", "hrv",
    "blood-pressure", "systolic-blood-pressure", "diastolic-blood-pressure",
    "respiratory-rate", "vo2-max", "oxygen-saturation",
    "skin-temperature", "body-temperature",
    # --- sleep ---
    "sleep-stages", "sleep-analysis", "snoring",
    # --- nutrition / metabolic ---
    "nutrition", "hydration", "water", "dietary-energy",
    # --- reproductive / other ---
    "menstruation", "menstrual-cycle", "ovulation", "cervical-mucus",
    "uv-exposure", "mindful-minutes", "mindfulness",
]

WINDOW = 'sample_time.physical_time >= "2020-01-01T00:00:00Z"'


async def probe(svc: GoogleHealthService, user_id: int, api_type: str, token: str):
    """Fetch a few points with NO filter (the sample_time filter is rejected for
    some types); return (status, count, raw_first_point, error_detail)."""
    url = f"{svc.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints"
    params = {"pageSize": 5}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers, params=params)

    status = resp.status_code
    if status != 200:
        try:
            err = resp.json().get("error", {})
            msg = err.get("message", "") or resp.text[:160]
            st = err.get("status", "")
            detail = f"{st}: {msg}".strip(": ")
        except Exception:
            detail = resp.text[:160]
        return status, 0, None, detail

    data = resp.json()
    points = data.get("dataPoints", [])
    return status, len(points), (points[0] if points else None), None


async def list_catalog(svc: GoogleHealthService, token: str):
    """GET /v4/users/me/dataTypes — every data type the API exposes, with the
    app's sync status for each."""
    url = f"{svc.BASE_URL}/v4/users/me/dataTypes"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    synced = set(GoogleHealthService.DATA_TYPE_MAP.values())

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers, params={"pageSize": 1000})

    if resp.status_code != 200:
        print(f"dataTypes catalog: HTTP {resp.status_code}: {resp.text[:300]}")
        return

    data = resp.json()
    types = data.get("dataTypes", [])
    print(f"=== FULL v4 DATA TYPE CATALOG ({len(types)} types) ===\n")
    print(f"{'data type':<34} status")
    print("-" * 78)
    for t in sorted(types, key=lambda x: x.get("name", "")):
        # name is like "users/{id}/dataTypes/steps" -> last segment
        slug = t.get("name", "").rsplit("/", 1)[-1]
        mark = "SYNCED" if slug in synced else "available (not synced)"
        print(f"{slug:<34} {mark}")


async def main():
    svc = GoogleHealthService()
    user_id = 1

    try:
        # Force a refresh: a stored access token is usually expired by probe time.
        token = await svc._get_valid_access_token(user_id, force_refresh=True)
    except ValueError as e:
        print(f"Could not get access token (refresh failed, re-authorize): {e}")
        return

    # No GET dataTypes collection endpoint exists (404), so scan candidates and
    # classify by the API's own error text:
    #   "Invalid data type ID referenced in the parent data type collection"
    #       => the type does not exist in the catalog
    #   anything else / HTTP 200  => the type EXISTS
    synced = set(GoogleHealthService.DATA_TYPE_MAP.values())
    exists, missing, errored = [], [], []

    print(f"Scanning {len(CANDIDATES)} candidate data types (user {user_id})...\n")
    for api_type in CANDIDATES:
        try:
            status, count, first, detail = await probe(svc, user_id, api_type, token)
        except Exception as e:
            errored.append((api_type, str(e)))
            continue
        if status == 200:
            exists.append((api_type, count))
        elif detail and "Invalid data type ID referenced" in detail:
            missing.append(api_type)
        else:
            # Exists, but the request itself failed (filter/permission/etc.)
            exists.append((api_type, f"exists, HTTP {status}"))

    print("=== EXISTS in v4 catalog ===")
    for slug, info in exists:
        tag = "SYNCED" if slug in synced else "available (not synced)"
        print(f"  {slug:<30} [{tag}]  points={info}")

    print(f"\n=== NOT in v4 catalog ({len(missing)}) ===")
    for slug in missing:
        print(f"  {slug}")

    if errored:
        print(f"\n=== REQUEST ERRORS ({len(errored)}) ===")
        for slug, err in errored:
            print(f"  {slug}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
