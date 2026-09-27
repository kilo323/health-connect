"""Apply and verify the index fix against the user's LIVE database + container.

1. Time GET /admin/metric-definitions/unmatched on their container (baseline).
2. Create the covering index on data/health_tracker.db (the exact statement
   init_db() will run at startup).
3. Re-time the endpoint -- nothing is cached, so this is an honest A/B.

Run: .venv\\Scripts\\python.exe -u scripts\\apply_index_live.py
"""
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.path.join(ROOT, "data", "health_tracker.db")
BASE = os.environ.get("HEALTH_API", "http://127.0.0.1:8003/api")

INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_health_metrics_unmatched "
    "ON health_metrics (definition_id, metric_type, recorded_at, source_document)"
)


def req(path, method="GET", body=None, token=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read(), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400], time.perf_counter() - t0
    except Exception as e:  # noqa: BLE001
        return -1, str(e).encode(), time.perf_counter() - t0


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    st, raw, _ = req("/auth/login", "POST",
                     {"username": os.environ.get("ADMIN_USER") or "james",
                      "password": os.environ.get("ADMIN_PASSWORD")}, timeout=30)
    if st != 200:
        print(f"login failed: {st} {raw[:200]}")
        return 1
    token = json.loads(raw)["token"]["access_token"]

    print("baseline (no index):", flush=True)
    st, raw, t1 = req("/admin/metric-definitions/unmatched", token=token)
    n = len(json.loads(raw)) if st == 200 else -1
    print(f"  GET unmatched -> {st}  {t1:.1f}s  ({n} types)\n")

    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    t0 = time.perf_counter()
    con.execute(INDEX_DDL)
    con.commit()
    print(f"CREATE INDEX ix_health_metrics_unmatched: {time.perf_counter() - t0:.2f}s")
    print("query plan:",
          [r[3] for r in con.execute(
              "EXPLAIN QUERY PLAN SELECT metric_type, COUNT(*) FROM health_metrics "
              "WHERE definition_id IS NULL GROUP BY metric_type")])
    con.close()

    print("\nwith index:", flush=True)
    st, raw, t2 = req("/admin/metric-definitions/unmatched", token=token)
    n = len(json.loads(raw)) if st == 200 else -1
    print(f"  GET unmatched -> {st}  {t2:.1f}s  ({n} types)")
    if t2 > 0:
        print(f"\n  speedup: {t1 / t2:.1f}x  ({t1:.1f}s -> {t2:.1f}s)")
    return 0 if st == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
