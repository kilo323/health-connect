"""Time the two problem endpoints against a RUNNING container (theirs or local).

Usage: .venv\\Scripts\\python.exe -u scripts\\time_propose_http.py [base_url]

Prints elapsed time for GET unmatched (page-load cost) and POST propose
(the reported hang), plus the response byte size.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8003/api"
PROPOSE_TIMEOUT = 420


def req(path, method="GET", body=None, token=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500], time.perf_counter() - t0
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, str(e).encode(), time.perf_counter() - t0


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env"))
    user = os.environ.get("ADMIN_USER") or "james"
    pw = os.environ.get("ADMIN_PASSWORD")
    st, raw, el = req("/auth/login", "POST",
                      {"username": user, "password": pw}, timeout=30)
    print(f"base={BASE}\nlogin -> {st} in {el:.1f}s")
    if st != 200:
        print(f"  {raw[:200]}")
        return 1
    token = json.loads(raw)["token"]["access_token"]

    # 1. Page-load cost
    st, raw, el = req("/admin/metric-definitions", token=token, timeout=300)
    print(f"\nGET  /admin/metric-definitions           -> {st}  {el:6.1f}s  {len(raw):>8} B")
    st, raw, el = req("/admin/metric-definitions/unmatched", token=token, timeout=300)
    n = len(json.loads(raw)) if st == 200 else -1
    print(f"GET  /admin/metric-definitions/unmatched -> {st}  {el:6.1f}s  {len(raw):>8} B  ({n} types)")

    # 2. The reported hang
    print("\nPOST /admin/metric-definitions/propose (timeout "
          f"{PROPOSE_TIMEOUT}s) ...", flush=True)
    t0 = time.perf_counter()
    st, raw, el = req("/admin/metric-definitions/propose", "POST", token=token,
                      timeout=PROPOSE_TIMEOUT)
    print(f"  -> status={st}  {el:.1f}s  {len(raw)} B")
    if st == 200:
        d = json.loads(raw)
        print(f"  proposals={len(d.get('proposals', []))} "
              f"unmatched_count={d.get('unmatched_count')}")
        biggest = max(((p.get("name") or "", len(json.dumps(p)))
                       for p in d.get("proposals", [])), key=lambda x: x[1])
        print(f"  largest proposal: {biggest[0]!r} ({biggest[1]} B)")
    else:
        print(f"  body: {raw[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
