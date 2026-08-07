#!/bin/sh
set -e

# Start backend in background
uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# Start nginx in foreground
nginx -g 'daemon off;'
