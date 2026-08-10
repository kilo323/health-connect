#!/bin/bash
set -e

# Register any mounted corporate CA certs with the system trust store
if [ -d /usr/local/share/ca-certificates ] && ls /usr/local/share/ca-certificates/*.crt &>/dev/null; then
    update-ca-certificates 2>/dev/null || true
fi

# Start nginx (serves frontend static files + proxies API)
nginx

# Start backend in production mode
# cd / so uvicorn can resolve 'app' as a package at /app
cd /
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

