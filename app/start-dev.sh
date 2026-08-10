#!/bin/bash
set -e

# Register any mounted corporate CA certs with the system trust store
if [ -d /usr/local/share/ca-certificates ] && ls /usr/local/share/ca-certificates/*.crt &>/dev/null; then
    update-ca-certificates 2>/dev/null || true
fi

echo "🔧 Starting development servers..."
echo "   Backend:  http://localhost:8000  (uvicorn --reload)"
echo "   Frontend: http://localhost:3000  (Next.js dev server)"
echo ""

# Start Next.js dev server in the background
cd /app/frontend
npm run dev &

# Start uvicorn with hot-reload in the foreground
# Only watch Python source dirs — NOT the whole /app (which includes frontend/node_modules)
cd /
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
    --reload-dir /app/routers \
    --reload-dir /app/services \
    --reload-dir /app/models \
    --reload-dir /app/schemas \
    --reload-dir /app/main.py \
    --reload-dir /app/config.py \
    --reload-dir /app/database.py
