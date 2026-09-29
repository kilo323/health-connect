# AGENTS.md

Working agreement for this repo. `README.md` is the user-facing guide (setup,
config, features); this file is for agents and contributors working in the code.

# Development

This application can be deployed on hardware as services or as a docker container.
When developing, always start services locally on the development machine and not
through docker. The only exceptions to this rule are:

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

**Helper Scripts**

- There are issues with multi-line and escaping when developing on Windows. Write scripts to
  execute when doing commands of this nature.
- Save all non-application used scripts in `scripts`. Save the ones that modify data in
  `scripts/destructive`.

## Commands

```powershell
# Services
.\start.ps1                  # both (opens two terminal windows)
.\start.ps1 -Service backend
.\start.ps1 -Service frontend
.\stop.ps1

# Frontend checks (from app\frontend) — there is no lint or test script
npx tsc --noEmit
npm run build

# Data setup (run BEFORE the first sync on a fresh database)
.\.venv\Scripts\python.exe .\scripts\seed_metric_definitions.py

# Verification (the stand-in for a test suite; see scripts/test_*.py, scripts/verify_*.py)
.\.venv\Scripts\python.exe .\scripts\test_prune_and_hourly.py
```

## Repository layout

```
app/
  main.py          FastAPI app + lifespan: init_db, seed admin/LLM/Google OAuth
                   config, start the scheduler when the schedule is enabled
  config.py        Settings (pydantic-settings). Note: NOT every env var is a field —
                   see "Configuration" below before assuming `settings.<x>` exists
  database.py      Engine (WAL, NullPool, 30s busy timeout), init_db: create_all plus
                   idempotent ALTER TABLEs, indexes and data cleanup
  models/          users, health data (health_metrics, metric_hourly, metric_definitions,
                   documents, pending analyses), app settings
  routers/         auth (/api/auth), health (/api/health), admin (/api/admin),
                   users (/api/users), webhooks (/api/webhooks)
  schemas/         Pydantic request/response models
  services/        scheduler (sync ingest), google_health (API client),
                   metric_normalizer, metric_registry, compaction, llm, nextcloud,
                   encryption
  frontend/        Next.js App Router app under src/app, plus components/hooks/lib/store
scripts/           Standalone tooling (see "Scripts boundary")
docs/              Design docs + TODO
data/              SQLite DB (gitignored) + llm_prompt.md
```

## Conventions and traps

These are the things that are easy to get wrong and expensive to debug.

### Metric semantics live in the registry, not in handlers

`app/services/metric_registry.py` decides how a metric collapses over time:

- `aggregation` — `sum` | `avg` | `avg_minmax` | `latest`: how granular rows become one
  daily value.
- `cadence` — `intraday` | `daily` | `event`: which view the metric belongs in.

Both are columns on `metric_definitions` (admin-editable); the registry's `DEFAULTS` and
`infer_aggregation`/`infer_cadence` are the fallback for metrics with no definition row.
When adding a metric, add it to `DEFAULTS` and let the read path, API and UI consult the
registry — **do not add keyword matching** (`if "steps" in metric_type`) in a router or
component. It was deliberately removed when the granularity strategy shipped.

### `health_metrics` storage rules

- The unique key is `(user_id, metric_type, recorded_at, source)`, enforced by the
  `uq_health_metric_point` index. `_upsert_metric` writes with a single native
  `INSERT ... ON CONFLICT DO UPDATE`, so re-syncing refreshes a point instead of duplicating.
- `granularity` is `raw` or `daily`. A `daily` row supersedes that day's `raw` rows **of the
  same `metric_type` only**. That is why heart rate's daily parts are separate
  `metric_types` (`Heart Rate (Average)/(Minimum)/(Maximum)`) — one row holds one value.
- Hourly rows live in the separate `metric_hourly` table by design: the unique key above
  would collide with raw rows at exact hour boundaries, and the daily-supersede rules would
  delete them. Do not merge the tables.
- **Renaming a `metric_type` orphans its rows.** With `definition_id` NULL and the old
  name gone, later syncs INSERT instead of UPDATE and silently double-write. Use
  `scripts/relink_metric_types.py` (or `scripts/migrate_raw_heart_rate.py` for the raw
  heart-rate case).

### FastAPI route ordering

FastAPI matches routes in **declaration order**, so a literal path declared after a
path-parameter route is unreachable. `GET /health/metrics/intraday-types` must stay above
`GET /health/metrics/{metric_type}`. Check this when adding endpoints.

### SQLite specifics

- SQLAlchemy's SQLite `DATETIME` has no offset, so `recorded_at` round-trips as a **naive**
  `YYYY-MM-DD HH:MM:SS.ffffff` string even when written with tz-aware datetimes. Do not mix
  DB-read timestamps with tz-aware ones in a single comparison — normalize first (see
  `_parse_dt` in `app/routers/health.py`).
- The engine is in WAL mode with `synchronous=NORMAL` and `NullPool` on purpose: Google sync
  writes tens of thousands of rows per backfill, and idle pooled connections on a Docker bind
  mount (especially Windows) cause "database is locked".
- There is no Alembic environment in the tree. `init_db()` applies additive `ALTER TABLE`s
  and idempotent data cleanup on every startup; follow that pattern instead of adding a
  migration tool.

### Compaction

`app/services/compaction.py` is the only thing that deletes raw rows. Preserve its safety
rules: one day per transaction, `dry_run=True` writes nothing, and raw is deleted only after
the day's daily rows have been written **and read back** — and only for metrics whose
`sync_rollup_config` entry has `enabled: true`.

### Scheduler

`services.scheduler.scheduler` is a module-level singleton. `start()`/`update_schedule()`
are safe to call repeatedly (`replace_existing=True`). The nightly compaction job
(03:17 UTC) is registered on the same scheduler, so it only runs while the schedule is enabled.
A 5-field crontab string must go through `CronTrigger.from_crontab()`, not the `CronTrigger`
kwargs.

### Configuration

Not every environment variable is a `Settings` field in `app/config.py`. These are read
directly from the environment or `.env` by their consumers:

| Variable | Read by |
|---|---|
| `METRIC_LLM_*` | `app/routers/admin.py` (env → `.env` → admin UI config) |
| `BACKEND_PORT` / `FRONTEND_PORT` | `app/main.py` (CORS), `next.config.ts` (dev rewrite), Compose |
| `FORWARDED_ALLOW_IPS` | uvicorn CLI in `app/start.sh` |
| `JWT_SECRET_KEY` | `app/routers/auth.py`, `app/routers/users.py` |
| `FERNET_KEY` | `app/services/encryption.py` |

`METRIC_LIBRARY_PROTECT` is **inert** — there is no metric library file anymore, so nothing
reads the flag. Don't add behaviour on top of it.

`FERNET_KEY` must be a valid Fernet key (32 url-safe base64 bytes). Generate with
`Fernet.generate_key()`; `openssl rand -hex 32` is rejected.

### Frontend

- Production builds are a **static export** (`output: "export"`, `distDir: "dist"` in
  `next.config.ts`); only dev mode gets the `/api/*` rewrite and the webpack watcher config.
  Anything that breaks static export (server components fetching at request time, dynamic
  routes without `generateStaticParams`) breaks the production image.
- `app/frontend/AGENTS.md` is generated and re-added by `next dev`; commit it with your work
  rather than removing it from a diff.
- Clicking a metric opens its detail chart in a **modal overlay** (fixed backdrop; close via
  X, Escape, backdrop click, or toggle) — never inline below the metrics grid. Keep the
  dashboard and reports consistent on this.
- Per-metric unit conversion goes through `useUnitConversion` + the `user_unit_preferences`
  table; store canonical units (kg, meters) and convert for display.

### Scripts boundary

`scripts/` is standalone tooling invoked by hand. The application, Docker/Compose and CI do
**not** import or execute it — if `app/` needs something from `scripts/`, move it into
`app/services/`.

- `scripts/destructive/` holds the data-modifying scripts (`clear_minutes.py`,
  `reset_sync_state.py`, `migrate_user_data.py`). They preview by default, require `--yes`
  to modify data, and resolve the database independently of the current directory.
- Never commit one-off or machine-specific scripts (hardcoded user names, live DB paths,
  dated local backups). Already-tracked ones need `git rm --cached`.

### Secrets

Never commit `app_settings.json`, `.env`, `data/*.db`, or anything under `certs/`. All are
gitignored. `app_settings.json` is an export of the `app_settings` table and contains live
Google OAuth refresh/access tokens, the OAuth `client_secret`, and the LLM api key.

## Verification expectations

- Frontend: `npx tsc --noEmit` and `npm run build` must pass from `app/frontend`.
- Backend has no test runner. Pick the matching script under `scripts/` (e.g.
  `test_prune_and_hourly.py` for storage/compaction changes,
  `verify_api_normalization.py` for normalizer changes) and run it against a **copy** of the
  database. Use `.\.venv\Scripts\python.exe` to run app modules for ad-hoc checks.
- The database is ~100 MB of real data. Copy before experimenting; never point a destructive
  script at `data/health_tracker.db` without asking.
