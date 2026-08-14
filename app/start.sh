#!/bin/bash
set -e

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"

# Render nginx config with the configured ports (listen + upstream backend port)
envsubst '${BACKEND_PORT} ${FRONTEND_PORT}' < /app/nginx.conf > /etc/nginx/sites-enabled/health-connect

# Start nginx (serves frontend static files + proxies API)
nginx

# Start backend in production mode
# cd / so uvicorn can resolve 'app' as a package at /app
cd /
# --proxy-headers: honor X-Forwarded-Proto/Host from the reverse proxy so
# request.base_url reflects the public scheme/host (needed for OAuth redirect_uri).
exec uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"

