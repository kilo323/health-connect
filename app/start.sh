#!/bin/bash
set -e

# Start backend with auto-reload
cd / && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &

# Start frontend dev server
cd /app/frontend && npm run dev -- --port 3000 &

# Wait for both
wait

