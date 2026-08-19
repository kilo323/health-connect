"""End-to-end verification of the reports overview fix via the live API.

Logs in with the admin credentials from .env and prints the summary cards +
recent steps time series.
"""
import json
import os
import sys
import urllib.request

BASE = "http://localhost:8000/api"

# Load .env
env = {}
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")

username = env.get("ADMIN_USER", "")
password = env.get("ADMIN_PASSWORD", "")
if not username or not password:
    print("ERROR: ADMIN_USER/ADMIN_PASSWORD not found in .env")
    sys.exit(1)

def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def get(path, token):
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

login = post("/auth/login", {"username": username, "password": password})
token = login.get("access_token") or login["token"]["access_token"]
print("logged in as", login["user"]["username"])

data = get("/health/reports/overview?days=30", token)
print("\n== summary cards ==")
for s in sorted(data["summary"], key=lambda x: x["metric_type"]):
    print(f"  {s['metric_type']:<24} latest={s['latest_value']:>10.1f} {s['unit']:<8} date={s['date']} trend={s['trend']}({s['trend_pct']})")

print("\n== steps time series (last 10 days) ==")
ts = data["time_series"].get("Steps") or data["time_series"].get("steps") or []
for p in ts[-10:]:
    print(f"  {p['date']}: {p['value']:,.0f}")
con_check = get("/health/metrics?metric_type=Steps&limit=5", token)
print("\n== /health/metrics Steps (latest 5 daily rows) ==")
for m in con_check:
    print(f"  {m['recorded_at'][:10]}: {m['value']:,.0f}")
