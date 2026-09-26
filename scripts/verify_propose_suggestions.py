"""Call the real propose endpoint and report the merge suggestions it returns."""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("HEALTH_API", "http://127.0.0.1:8000/api")


def req(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def main():
    from dotenv import load_dotenv
    load_dotenv(".env")
    st, body = req("/auth/login", "POST", {
        "username": os.environ.get("ADMIN_USER"),
        "password": os.environ.get("ADMIN_PASSWORD")})
    if st != 200:
        raise SystemExit(f"login failed: {st} {body}")
    token = body["token"]["access_token"]

    st, res = req("/admin/metric-definitions/propose", "POST", token=token)
    print(f"POST /admin/metric-definitions/propose -> {st}")
    if st != 200:
        print("  ", res)
        return
    print(f"unmatched_count: {res.get('unmatched_count')}")
    for p in res.get("proposals") or []:
        sim = p.get("similar_definition_name")
        score = p.get("similarity_score")
        print(f"\n  {p.get('raw_metric_type')} -> {p.get('name')}")
        print(f"    category={p.get('category')!r} unit={p.get('unit')!r} "
              f"aliases={p.get('aliases')}")
        if sim:
            print(f"    !! would show 'Looks like {sim} ({round(score*100)}%)' "
                  f"and a Merge button")
        else:
            print(f"    no merge suggestion (UI hides badge + button)")

    bad = [p for p in (res.get("proposals") or [])
           if p.get("similar_definition_id")]
    print(f"\nproposals with a merge suggestion: {len(bad)}")
    print("RESULT:", "PASS" if not bad else "FAIL")


if __name__ == "__main__":
    main()
