# Development
This application can be deployed on hardware as services or as a docker container.  When developing, always start services locally on the development machine and not through docker.  The only exceptions to this rule are:
- if the user asks you to start in docker
- you are working on docker specific tasks

## Dev Container (Live Reload)
To run in Docker **with live reload** (both Python hot-reload and Next.js HMR):

```powershell
# Build and start the dev container
docker compose -f docker-compose.dev.yml up --build

# Stop the dev container
docker compose -f docker-compose.dev.yml down
```

**How it works:**
- Source code is volume-mounted — edits on the host are picked up immediately.
- **Backend:** `uvicorn --reload` watches `/app` for Python file changes.
- **Frontend:** `npm run dev` runs the Next.js dev server with Hot Module Replacement (HMR).
- Next.js dev server on `:3000` proxies `/api/*` to the backend on `:8000`.
- `node_modules` and `.next` cache are stored in Docker volumes to survive container restarts.
- The database file (`data/`) is shared between dev and production containers.

**Production vs Dev:**
| | Production (`docker-compose.yml`) | Dev (`docker-compose.dev.yml`) |
|---|---|---|
| Frontend | Static export → nginx | Next.js dev server with HMR |
| Backend | `uvicorn` | `uvicorn --reload` |
| Source | Baked into image | Volume-mounted |
| Rebuild needed? | Yes, for any change | No, live reload |
