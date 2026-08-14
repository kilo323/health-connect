#!/bin/bash
set -e

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
export BACKEND_PORT FRONTEND_PORT

echo "🔧 Starting development servers..."
echo "   Backend:  http://localhost:${BACKEND_PORT}  (uvicorn --reload)"
echo "   Frontend: http://localhost:${FRONTEND_PORT}  (Next.js dev server)"
echo ""

# Start Next.js dev server in the background
cd /app/frontend
npm run dev -- --port "$FRONTEND_PORT" &

# Start uvicorn with hot-reload in the foreground
# Run from /app (not /) so the watcher doesn't scan the entire filesystem tree.
# NOTE: --reload-dir only accepts *directories* (passing files like main.py makes
# watchfiles fall back to watching all of /app). Watching /app/data is what caused
# the 100% CPU: the SQLite DB changes on every request, triggering constant reloads.
# Run from / so the mounted /app directory IS the "app" Python package (the code
# uses relative imports, so it must be loaded as a package). Watch /app (for
# top-level files like main.py, config.py, database.py) plus the Python source
# subdirs, but exclude the heavy dirs (frontend/node_modules, .next, data) that
# caused both the reload loop and the 100% CPU.
cd /
exec uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload \
    --reload-dir /app \
    --reload-dir /app/routers \
    --reload-dir /app/services \
    --reload-dir /app/models \
    --reload-dir /app/schemas \
    --reload-exclude "frontend/*" \
    --reload-exclude "data/*" \
    --reload-exclude "*/node_modules/*" \
    --reload-exclude "*/.next/*" \
    --reload-exclude "*/__pycache__/*"
