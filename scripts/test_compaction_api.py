"""End-to-end test of the new compaction + series endpoints against a running API.

Logs in as the admin from .env, then:
  1. GET  /api/admin/sync/compaction          -> current retention
  2. PUT  /api/admin/sync/compaction          -> set a small retention
  3. POST /api/admin/sync/compact (dry_run)   -> preview, must not change row count
  4. POST /api/admin/sync/compact             -> real run
  5. GET  /api/health/metrics/Heart Rate/series -> downsampled intraday series
  6. PUT  retention back to the original value
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HEALTH_API", "http://127.0.0.1:8000/api")
DB = "data/health_tracker.db"
TEST_RETENTION = 3


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
        return e.code, e.read().decode()[:400]


def login():
    from dotenv import load_dotenv
    load_dotenv(".env")
    user = os.environ.get("ADMIN_USER")
    pw = os.environ.get("ADMIN_PASSWORD")
    for candidate in ([user, "admin@domain.com"] if user else ["admin@domain.com"]):
        st, body = req("/auth/login", "POST", {"username": candidate, "password": pw})
        if st == 200:
            print(f"logged in as {candidate}")
            # Response nests the JWT under "token".
            return (body.get("token") or {}).get("access_token")
        print(f"  login as {candidate} -> {st} {str(body)[:120]}")
    raise SystemExit("could not log in")


def row_count():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = con.execute("SELECT COUNT(*) FROM health_metrics").fetchone()[0]
    con.close()
    return n


def main():
    token = login()
    hdr = {"Authorization": f"Bearer {token}"}

    st, cur = req("/admin/sync/compaction", token=token)
    print(f"\n1. GET compaction -> {st} {cur}")
    original = cur.get("raw_retention_days")

    st, res = req("/admin/sync/compaction", "PUT",
                  {"raw_retention_days": TEST_RETENTION}, token=token)
    print(f"\n2. PUT retention={TEST_RETENTION} -> {st} {res}")

    before = row_count()
    st, dry = req("/admin/sync/compact", "POST", {"dry_run": True}, token=token)
    after_dry = row_count()
    print(f"\n3. POST compact dry_run -> {st}")
    print(f"   would compact {dry.get('days_compacted')} group(s), "
          f"write {dry.get('daily_rows_written')} daily row(s), "
          f"delete {dry.get('raw_rows_deleted')} raw row(s)")
    print(f"   rows {before} -> {after_dry} "
          f"({'unchanged - OK' if before == after_dry else 'CHANGED - BUG'})")

    st, real = req("/admin/sync/compact", "POST", {"dry_run": False}, token=token)
    print(f"\n4. POST compact -> {st}")
    for k in ("days_compacted", "daily_rows_written", "raw_rows_deleted"):
        print(f"   {k}: {real.get(k)}")
    print(f"   days_kept_no_daily: {len(real.get('days_kept_no_daily') or [])}")
    print(f"   errors: {len(real.get('errors') or [])} {str(real.get('errors'))[:200]}")

    st, series = req(
        "/health/metrics/Heart%20Rate/series?start=2026-09-25T00:00:00"
        "&end=2026-09-26T00:00:00&max_points=24", token=token)
    print(f"\n5. GET series -> {st}")
    if st == 200:
        pts = series.get("points") or []
        print(f"   raw_row_count={series.get('raw_row_count')} "
              f"bucket_seconds={series.get('bucket_seconds'):.0f} "
              f"unit={series.get('unit')} points={len(pts)}")
        for p in pts[:3]:
            print(f"   {p['t']}  min={p['min']:.0f} max={p['max']:.0f} "
                  f"avg={p['avg']:.1f} n={p['count']}")
    else:
        print(f"   {series}")

    st, res = req("/admin/sync/compaction", "PUT",
                  {"raw_retention_days": original}, token=token)
    print(f"\n6. PUT retention back to {original} -> {st} {res}")


if __name__ == "__main__":
    main()
