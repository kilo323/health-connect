#!/bin/bash
set -e

# Start nginx (serves frontend static files + proxies API)
nginx

# Start backend in production mode
# cd / so uvicorn can resolve 'app' as a package at /app
cd /
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

