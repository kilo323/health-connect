#!/usr/bin/env python3
"""Standalone Google Health API (v4) boilerplate: steps and sleep duration.

Migration starter for moving off the legacy Google Fit REST API
(fitness.googleapis.com, com.google.* data types) onto the modern
Google Health API v4 (health.googleapis.com, unified data types).

Verified API surface (from developers.google.com/health, as of 2026-08):
  - Base URL:   https://health.googleapis.com
  - Read raw:   GET  /v4/users/me/dataTypes/{type}/dataPoints?filter=...
  - Daily sums: POST /v4/users/me/dataTypes/{type}/dataPoints:dailyRollUp
  - Filter:     AIP-160 expression, e.g.
                steps.interval.start_time >= "2026-08-01T00:00:00Z" AND
                steps.interval.start_time <  "2026-08-11T00:00:00Z"
  - Scopes:     https://www.googleapis.com/auth/googlehealth.<bundle>.readonly
                Bundles needed here: activity_and_fitness (steps), sleep

Prerequisites:
  1. Google Cloud project with "Google Health API" enabled.
  2. OAuth 2.0 web client with the googlehealth scopes added under Data Access.
  3. A user access token granted with:
       https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
       https://www.googleapis.com/auth/googlehealth.sleep.readonly
     (quickest path: OAuth 2.0 Playground with your own client credentials)
  4. The user must have a Fitbit/Pixel Watch (or other 1P source) syncing data.

Usage:
    export GOOGLE_HEALTH_ACCESS_TOKEN="ya29...."
    python google_health_boilerplate.py [--days 7]

Note: this script uses httpx in sync mode (already an app dependency) so it
runs without the app's async stack. In-app code lives in app/services/google_health.py.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE_URL = "https://health.googleapis.com"

# Data types are kebab-case in endpoint paths. Legacy mapping for reference:
#   com.google.step_count.delta  -> steps
#   com.google.sleep.segment     -> sleep
DATA_TYPE_STEPS = "steps"
DATA_TYPE_SLEEP = "sleep"


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _check_response(resp: httpx.Response, context: str) -> dict:
    """Raise a readable error, parsing the google.rpc Status shape."""
    if resp.status_code != 200:
        try:
            error = resp.json().get("error", {})
            message = error.get("message", resp.text[:300])
            status = error.get("status", "")
            detail = f"{status}: {message}" if status else message
        except ValueError:
            detail = resp.text[:300]
        raise RuntimeError(f"{context} failed (HTTP {resp.status_code}): {detail}")
    return resp.json()


def fetch_daily_steps(access_token: str, days: int) -> list[dict]:
    """Daily step totals via the dailyRollUp endpoint.

    Returns a list of {date, steps} dicts (civil days, user-local).
    dailyRollUp reconciles duplicate device streams (Pixel Watch vs Fitbit)
    server-side, so no client-side dedup is needed.
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    end_exclusive = today + timedelta(days=1)  # CivilTimeInterval end is exclusive

    url = f"{BASE_URL}/v4/users/me/dataTypes/{DATA_TYPE_STEPS}/dataPoints:dailyRollUp"
    body = {
        "range": {
            "start": {"date": {"year": start.year, "month": start.month, "day": start.day}},
            "end": {"date": {"year": end_exclusive.year, "month": end_exclusive.month, "day": end_exclusive.day}},
        },
        "windowSizeDays": 1,
    }

    results = []
    page_token = None
    while True:
        request_body = dict(body)
        if page_token:
            request_body["pageToken"] = page_token

        resp = httpx.post(url, headers=_headers(access_token), json=request_body, timeout=30)
        payload = _check_response(resp, "steps dailyRollUp")

        for point in payload.get("rollupDataPoints", []):
            date_info = point.get("civilStartTime", {}).get("date", {})
            steps_value = point.get("steps", {})
            # countSum is an int64 serialized as a JSON string
            count = int(steps_value.get("countSum", 0))
            results.append({
                "date": f"{date_info.get('year', 0):04d}-{date_info.get('month', 0):02d}-{date_info.get('day', 0):02d}",
                "steps": count,
            })

        page_token = payload.get("nextPageToken") or None
        if not page_token:
            break

    return results


def fetch_sleep_sessions(access_token: str, days: int) -> list[dict]:
    """Sleep sessions with total duration, via dataPoints.list.

    Duration is computed from the session interval (endTime - startTime).
    The sleep.summary.minutesAsleep field is also available when the session
    has been fully processed (stages status SUCCEEDED).
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    url = f"{BASE_URL}/v4/users/me/dataTypes/{DATA_TYPE_SLEEP}/dataPoints"
    # Session types filter on interval.start_time (same as interval types).
    filter_expr = (
        f'{DATA_TYPE_SLEEP}.interval.start_time >= "{start_time.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
        f'AND {DATA_TYPE_SLEEP}.interval.start_time < "{end_time.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )

    sessions = []
    page_token = None
    while True:
        params = {"filter": filter_expr}
        if page_token:
            params["pageToken"] = page_token

        resp = httpx.get(url, headers=_headers(access_token), params=params, timeout=30)
        payload = _check_response(resp, "sleep dataPoints.list")

        for point in payload.get("dataPoints", []):
            sleep = point.get("sleep", {})
            interval = sleep.get("interval", {})
            start_str = interval.get("startTime")
            end_str = interval.get("endTime")
            if not start_str or not end_str:
                continue

            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            duration_minutes = (end_dt - start_dt).total_seconds() / 60

            summary = sleep.get("summary", {})
            sessions.append({
                "start": start_str,
                "end": end_str,
                "duration_minutes": round(duration_minutes, 1),
                # Prefer the server's computed asleep time when available
                "minutes_asleep": int(summary["minutesAsleep"]) if "minutesAsleep" in summary else None,
                "type": sleep.get("type"),
            })

        page_token = payload.get("nextPageToken") or None
        if not page_token:
            break

    return sessions


def main() -> int:
    parser = argparse.ArgumentParser(description="Query steps and sleep from the Google Health API (v4)")
    parser.add_argument("--days", type=int, default=7, help="How many days back to query (default: 7)")
    args = parser.parse_args()

    access_token = os.environ.get("GOOGLE_HEALTH_ACCESS_TOKEN", "").strip()
    if not access_token:
        print("ERROR: Set GOOGLE_HEALTH_ACCESS_TOKEN to a user OAuth access token.", file=sys.stderr)
        print("Required scopes: googlehealth.activity_and_fitness.readonly, googlehealth.sleep.readonly", file=sys.stderr)
        return 1

    print(f"--- Daily steps (last {args.days} days) ---")
    try:
        daily_steps = fetch_daily_steps(access_token, args.days)
        if not daily_steps:
            print("No step data found. Ensure a Fitbit/Pixel Watch source has synced.")
        for entry in daily_steps:
            print(f"  {entry['date']}: {entry['steps']:,} steps")
    except RuntimeError as exc:
        print(f"  {exc}", file=sys.stderr)

    print(f"\n--- Sleep sessions (last {args.days} days) ---")
    try:
        sessions = fetch_sleep_sessions(access_token, args.days)
        if not sessions:
            print("No sleep data found.")
        for session in sessions:
            asleep = f", asleep {session['minutes_asleep']} min" if session["minutes_asleep"] is not None else ""
            print(f"  {session['start']} -> {session['end']}: {session['duration_minutes']:.0f} min in bed{asleep}")
    except RuntimeError as exc:
        print(f"  {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
