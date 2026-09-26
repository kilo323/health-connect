"""Verify normalization through the live API: do the daily endpoints now return
the curated metric names the dashboard and reports depend on?
"""
import json
import os
import urllib.error
import urllib.request
from collections import Counter

BASE = os.environ.get("HEALTH_API", "http://127.0.0.1:8000/api")


def req(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def login():
    from dotenv import load_dotenv
    load_dotenv(".env")
    st, body = req("/auth/login", "POST", {
        "username": os.environ.get("ADMIN_USER"),
        "password": os.environ.get("ADMIN_PASSWORD")})
    if st != 200:
        raise SystemExit(f"login failed: {st} {body}")
    return body["token"]["access_token"]


def main():
    token = login()

    st, metrics = req("/health/metrics?limit=2000", token=token)
    names = Counter(m["metric_type"] for m in metrics or [])
    print(f"=== /health/metrics ({st}) - distinct metric_type ===")
    for n, c in names.most_common():
        print(f"  {n:<28} {c:>5}")

    raw_labels = {"steps", "distance", "heart_rate", "sleep", "calories",
                  "oxygen_saturation", "weight", "body_fat_percentage",
                  "body_temperature", "height", "blood_glucose"}
    leaked = sorted(set(names) & raw_labels)
    print(f"\nraw google labels still surfacing: {leaked or 'none'}")

    st, ov = req("/health/reports/overview", token=token)
    print(f"\n=== /health/reports/overview ({st}) ===")
    if isinstance(ov, dict):
        series = ov.get("series") or ov.get("data") or []
        if isinstance(series, list):
            snames = Counter(s.get("metric_type") for s in series)
            for n, c in snames.most_common():
                print(f"  {n:<28} {c:>4}")
            ov_leaked = sorted(set(snames) & raw_labels)
            print(f"  raw labels in overview: {ov_leaked or 'none'}")
        else:
            print("  keys:", list(ov)[:12])

    st, types = req("/health/metrics/intraday-types", token=token)
    print(f"\n=== /health/metrics/intraday-types ({st}) ===")
    for t in types or []:
        print(f"  {t['metric_type']:<28} {t['raw_row_count']:>7} rows  "
              f"{str(t.get('last_at'))[:16]}")

    st, unmatched = req("/admin/metric-definitions/unmatched", token=token)
    print(f"\n=== /admin/metric-library/unmatched ({st}) ===")
    if isinstance(unmatched, list):
        for u in unmatched:
            print("  ", json.dumps(u)[:150])
    elif isinstance(unmatched, dict):
        items = unmatched.get("unmatched") or unmatched.get("items") or []
        for u in items:
            print("  ", json.dumps(u)[:150])
        if not items:
            print("  ", json.dumps(unmatched)[:200])


if __name__ == "__main__":
    main()
