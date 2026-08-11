#!/usr/bin/env python3
"""One-time setup: register a Google Health API webhook subscriber.

Registers this application's webhook endpoint with the Google Health API so it
receives push notifications when users' health data changes (replacing
high-frequency scheduler polling). Uses subscriptionCreatePolicy=AUTOMATIC, so
no per-user subscription calls are needed — notifications start flowing for any
user who has granted the matching OAuth scopes.

Prerequisites (one-time GCP setup):
  1. Google Cloud project with "Google Health API" enabled.
  2. A service account with the "Google Health API Editor" (or Admin) IAM role.
  3. Application Default Credentials pointing at that service account, e.g.:
       $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\\path\\to\\service-account.json"
  4. Your app deployed at a public HTTPS URL (TLS 1.2+) — webhooks require it.
     For local dev, use a tunnel (ngrok / cloudflared) and pass that URL.
  5. pip install google-auth  (not an app dependency — management script only)

Usage:
    python register_google_health_subscriber.py \
        --project-number 123456789012 \
        --endpoint https://your-domain.com/api/webhooks/google-health \
        --secret "Bearer <same-random-string-as-WEBHOOK_SECRET>"

Notes:
  - Use the project NUMBER, not the project ID (Google returns 400/403 otherwise).
  - Registration triggers Google's two-step verification handshake against the
    endpoint synchronously; it must already be running and reachable.
  - The --secret value must exactly match the app's WEBHOOK_SECRET env var
    (settings.webhook_secret); it is sent as the Authorization header.
  - Re-running with the same subscriberId updates nothing; use --delete first
    or patch via the API if you need to change the endpoint.
"""
import argparse
import sys

import httpx

BASE_URL = "https://health.googleapis.com"

# All data types the app syncs (see app/services/scheduler.py SYNC_DATA_TYPES).
# AUTOMATIC policy: Google tracks consenting users automatically.
SUBSCRIBER_CONFIGS = [
    {
        "dataTypes": ["steps", "distance", "active-zone-minutes", "active-minutes"],
        "subscriptionCreatePolicy": "AUTOMATIC",
    },
    {
        "dataTypes": [
            "heart-rate", "weight", "blood-glucose", "core-body-temperature",
            "body-fat", "height", "daily-oxygen-saturation",
        ],
        "subscriptionCreatePolicy": "AUTOMATIC",
    },
    {
        "dataTypes": ["sleep"],
        "subscriptionCreatePolicy": "AUTOMATIC",
    },
]


def get_access_token() -> str:
    """Mint an access token from Application Default Credentials."""
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError:
        sys.exit("ERROR: google-auth is not installed. Run: pip install google-auth")

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a Google Health API webhook subscriber")
    parser.add_argument("--project-number", required=True, help="Google Cloud project NUMBER (not the project ID)")
    parser.add_argument("--endpoint", required=True, help="Public HTTPS webhook URL, e.g. https://host/api/webhooks/google-health")
    parser.add_argument("--secret", required=True, help='Authorization header value, e.g. "Bearer <random>" (must match WEBHOOK_SECRET)')
    parser.add_argument("--subscriber-id", default="health-connect-webhook", help="Subscriber ID (4-36 chars, lowercase/digits/hyphens)")
    parser.add_argument("--delete", action="store_true", help="Delete the subscriber instead of creating it")
    args = parser.parse_args()

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    if args.delete:
        url = f"{BASE_URL}/v4/projects/{args.project_number}/subscribers/{args.subscriber_id}"
        resp = httpx.delete(url, headers=headers, timeout=30)
        if resp.status_code in (200, 204):
            print(f"Subscriber {args.subscriber_id!r} deleted.")
            return 0
        print(f"Delete failed (HTTP {resp.status_code}): {resp.text[:300]}", file=sys.stderr)
        return 1

    url = f"{BASE_URL}/v4/projects/{args.project_number}/subscribers"
    body = {
        "endpointUri": args.endpoint,
        "subscriberConfigs": SUBSCRIBER_CONFIGS,
        "endpointAuthorization": {"secret": args.secret},
    }

    print(f"Registering subscriber {args.subscriber_id!r} -> {args.endpoint}")
    print("Google will now run the two-step verification handshake against the endpoint...")
    resp = httpx.post(url, headers=headers, params={"subscriberId": args.subscriber_id}, json=body, timeout=60)

    if resp.status_code in (200, 201):
        print("Subscriber registered and verified successfully.")
        return 0

    detail = resp.text[:500]
    try:
        error = resp.json().get("error", {})
        detail = f"{error.get('status', '')}: {error.get('message', detail)}"
    except ValueError:
        pass

    if resp.status_code == 400 and "FAILED_PRECONDITION" in detail.upper():
        print("Verification handshake FAILED — check that the endpoint is publicly reachable over HTTPS,", file=sys.stderr)
        print("returns 200 for authorized verification POSTs and 401 for unauthorized ones,", file=sys.stderr)
        print("and that --secret matches the app's WEBHOOK_SECRET.", file=sys.stderr)
    print(f"Registration failed (HTTP {resp.status_code}): {detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
