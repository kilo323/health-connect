# Reverse Proxy Setup

The app listens on `0.0.0.0` and honors `X-Forwarded-*` headers
(`uvicorn --proxy-headers`). Put it behind any TLS-terminating reverse proxy — the
post-OAuth redirect and Google OAuth callback URL are **derived from the incoming
request's forwarded scheme/host**, so no extra env config is needed in the typical
single-origin setup.

Single-origin proxying is simplest: route both the frontend and `/api` to the
container's frontend port (nginx inside the container already proxies `/api` to the
backend). Then CORS is a non-issue.

## Environment (optional)

```dotenv
# Only needed as an explicit override if the API is reached on a different host than
# the frontend. Normally leave unset.
PUBLIC_URL=https://health.example.com
# Optional: restrict which proxy IPs are trusted for X-Forwarded-* (default *)
FORWARDED_ALLOW_IPS=10.0.0.0/8
```

The container publishes `FRONTEND_PORT` (default 3000, serves the built frontend +
proxies `/api`) and `BACKEND_PORT` (default 8000). **Point your proxy at the
frontend port only** unless you intentionally expose the API directly.

## Google OAuth redirect URI

In the admin **Google OAuth** page, the Redirect URI is pre-filled from the current
browser origin (e.g. `https://health.example.com/api/health/google-health/callback`).
Register that exact value in Google Cloud → Credentials → Authorized redirect URIs.

---

## nginx

```nginx
server {
    listen 443 ssl;
    server_name health.example.com;

    ssl_certificate     /etc/letsencrypt/live/health.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/health.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;   # container FRONTEND_PORT
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Traefik (docker labels)

Add to the `app` service in `docker-compose.yml`:

```yaml
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.health.rule=Host(`health.example.com`)"
      - "traefik.http.routers.health.entrypoints=websecure"
      - "traefik.http.routers.health.tls.certresolver=letsencrypt"
      - "traefik.http.services.health.loadbalancer.server.port=3000"
```

(Traefik forwards `X-Forwarded-*` by default for trusted entrypoints.)

## Caddy

```caddy
health.example.com {
    reverse_proxy 127.0.0.1:3000
}
```

Caddy sets `Host`, `X-Forwarded-For`, and `X-Forwarded-Proto` automatically and
provisions TLS for you.

---

## Verify

After deploying, confirm the forwarded scheme/host reach the app:

```bash
curl -s https://health.example.com/api/health/... # any authed endpoint
```

And in the browser, the admin Google OAuth page should show the Redirect URI
pre-filled with `https://health.example.com/...`.
