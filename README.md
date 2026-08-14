# Health Connect

A self-hosted personal health dashboard. It pulls activity and vital metrics from the
**Google Health API** (Fitbit / Pixel Watch), ingests **medical documents** (PDFs and
images, synced from Nextcloud or uploaded directly) and extracts lab/body-composition
results with an LLM, then normalizes everything against a canonical metric library so
data from different sources can be charted and compared with proper units and
reference ranges.

> Personal / family project. The Google Health scopes it uses are *restricted*, but
> because it stays well under 100 users it does not require Google OAuth verification
> or an annual CASA assessment.

## Features

- **Google Health sync** — steps, heart rate, sleep, weight, distance, calories,
  blood glucose, body temperature, oxygen saturation, body-fat %, height, active-zone
  minutes and active minutes. Incremental sync on a schedule, plus optional webhook
  push notifications.
- **Document analysis** — an LLM extracts structured metrics (lab work, DEXA, etc.)
  from PDFs/images; results go through a review queue before being saved.
- **Metric normalization** — extracted and synced metrics are matched to canonical
  `MetricDefinition`s (exact / alias / fuzzy), with unit conversion and reference
  ranges. Definitions are seeded automatically from `data/metric_library.json` at
  startup.
- **Dashboards & reports** — charts, sparklines, trend views, and per-metric unit
  preferences (e.g. view weight in lb while it is stored in kg).
- **Nextcloud integration** — browse and pull documents from a Nextcloud folder.
- **Multi-user** — JWT auth, per-user Google/Nextcloud connections, admin area for
  users, LLM config, Google OAuth, metric definitions and the sync schedule.

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Backend   | FastAPI, SQLAlchemy (async), SQLite (`aiosqlite`), APScheduler |
| Frontend  | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Recharts, Zustand |
| Docs/LLM  | OpenAI-compatible chat endpoint, `pdfplumber` / PyMuPDF / Pillow |
| Deploy    | Docker (multi-stage: static export + nginx, or dev container with live reload) |

## Project layout

```
app/
  main.py            FastAPI app + lifespan (init DB, seed admin/LLM config/metric library, start scheduler)
  config.py          Settings (pydantic-settings, reads env / .env)
  database.py        Engine, session factory, init_db
  models/            SQLAlchemy models (users, health data, settings)
  routers/           auth, users, health, admin, webhooks
  services/          google_health, scheduler, metric_normalizer, llm, nextcloud, encryption
  frontend/          Next.js app (src/app pages, components, hooks, lib, store)
data/
  metric_library.json          Canonical metric definitions (seeded into the DB)
  llm_prompt.md                Editable LLM extraction prompt
  health_tracker.db            SQLite database (created on first run)
```

## Getting started (local development)

Prereqs: **Python 3.11+** and **Node.js 20+**. Per `AGENTS.md`, run services locally
(not in Docker) during development unless you're specifically working on Docker.

```powershell
# 1. Python environment + backend deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r app\requirements.txt

# 2. Frontend deps
cd app\frontend
npm install
cd ..\..

# 3. Configure environment (see Configuration below)
copy .env.example .env   # then edit

# 4. Start both services (backend :8000, frontend :3000)
.\start.ps1                # both
.\start.ps1 -Service backend
.\start.ps1 -Service frontend

# Stop
.\stop.ps1
```

The Next.js dev server proxies `/api/*` to the FastAPI backend on `:8000`.
Open http://localhost:3000 and log in with the seeded admin account.

## Configuration

Settings come from environment variables or a `.env` file in the repo root
(`pydantic-settings`, no prefix). Docker also maps these in `docker-compose.yml`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ADMIN_USER` | `admin` | Admin username seeded on first startup |
| `ADMIN_PASSWORD` | `changeme` | Admin password seeded on first startup |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/health_tracker.db` | SQLAlchemy connection string |
| `FERNET_KEY` | — | Encryption key for stored secrets (OAuth tokens, Nextcloud creds) |
| `LLM_URL` | — | OpenAI-compatible base URL (e.g. `https://your-provider.com/v1`) |
| `LLM_API_TOKEN` | — | API token for the LLM provider |
| `LLM_MODEL` | — | Model name |
| `METRIC_LLM_URL` / `METRIC_LLM_API_TOKEN` / `METRIC_LLM_MODEL` | fall back to `LLM_*` | Optional separate LLM used only by `refresh_metric_library.py` |
| `LLM_SSL_VERIFY` | `true` | Set to `false` to skip SSL certificate verification on LLM API calls (e.g. behind a corporate TLS-inspecting proxy) |
| `METRIC_LIBRARY_PROTECT` | `true` | Protect hand-tuned metric definitions from overwrites (see below) |
| `GOOGLE_CLOUD_PROJECT_NUMBER` | — | Project **number** for webhook subscriber management |
| `WEBHOOK_SECRET` | — | Enables `POST /api/webhooks/google-health` (Bearer token). Unset = webhooks disabled |

### Google Health (per-user) setup

Google OAuth **client credentials are not env vars** — they are stored in the database
and configured through the admin UI:

1. Create an OAuth 2.0 *Web* client in Google Cloud, enable the Google Health API, and
   add an authorized redirect URI of
   `{backend}/api/health/google-health/callback`.
2. In the app, go to **Admin → Google OAuth** and enter the client ID/secret.
3. Each user then links their own account via **Settings → Connect Google Health**.
   The app requests these read-only scopes:
   `googlehealth.activity_and_fitness.readonly`,
   `googlehealth.health_metrics_and_measurements.readonly`,
   `googlehealth.sleep.readonly`.
4. The account must have a Google Health / Fitbit profile — sign into the Fitbit mobile
   app with the same Google account once, otherwise sync reports "not linked".

### After the initial Google sync

Once a user has connected their Google Health account and the first sync has run,
a few one-time follow-up steps are needed to keep data flowing automatically.

#### 1. Enable the sync schedule (admin)

The scheduler is **disabled** by default. The admin enables cron-triggered
incremental syncs:

- Go to **Admin → Schedule**, toggle **Enabled**, and set a cron expression
  (default `0 2 * * *` = daily at 2 AM UTC), then **Save**.
- The backend (`PUT /api/admin/settings/schedule`) stores the config and
  restarts the `AsyncIOScheduler` job.

Common cron values:

| Cadence                    | Expression      |
|----------------------------|-----------------|
| Daily at 2 AM UTC          | `0 2 * * *`    |
| Every 6 hours              | `0 */6 * * *`  |
| Weekly on Sunday at 3 AM   | `0 3 * * 0`    |
| Every weekday at midnight   | `0 0 * * 1-5`  |

You can trigger an immediate sync via **Admin → Sync Now**
(`POST /admin/sync/now`), useful after changing settings.

#### 2. Configure per-user sync settings

Each user controls how far back their *first* sync reaches via
`sync_days_back` (default 7, range 1–365). Set it before the first scheduled
run via **Settings → Sync Settings** or `PUT /api/health/sync/settings`
with `{"sync_days_back": N}`.

Subsequent runs only fetch data since the last successful sync (with a
1-day overlap to catch late-arriving points). The scheduler stores the
watermark per-user under `sync_settings_{id}.last_google_sync`.

> To backfill more history later, increase `sync_days_back` and either clear
> `last_google_sync` (via the admin DB) or trigger **Admin → Sync Now** —
> idempotency is enforced by a `(user_id, metric_type, recorded_at, source)`
> unique constraint, so duplicates are skipped.

#### 3. (Optional) Enable webhook push notifications

For a publicly deployed instance, register push notifications to avoid polling
the Google Health API on a fixed schedule. The webhook receiver lives at
`POST /api/webhooks/google-health` (see `app/routers/webhooks.py`).

1. Set `WEBHOOK_SECRET` in `.env` to a strong random string — without it,
   the endpoint returns 404 and webhooks stay disabled.
2. Set `GOOGLE_CLOUD_PROJECT_NUMBER` to your GCP project **number**
   (not the project ID — Google returns 400/403 otherwise).
3. Install `google-auth` (script-only dependency) and run the one-time
   subscriber registration:

   ```powershell
   pip install google-auth
   $env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\service-account.json"
   .\.venv\Scripts\python.exe .\register_google_health_subscriber.py `
       --project-number 123456789012 `
       --endpoint https://your-domain.com/api/webhooks/google-health `
       --secret "Bearer $WEBHOOK_SECRET"
   ```

   This registers `subscriptionCreatePolicy: AUTOMATIC` for all 13 synced
   data types, so notifications start flowing for any consenting user
   without per-user subscription calls. Registration triggers Google's
   two-step verification handshake against the endpoint, which must already
   be running, reachable over HTTPS (TLS 1.2+), and returning 200 for
   authorized verification POSTs / 401 for unauthorized ones.

For local dev, expose the backend through a tunnel (ngrok / cloudflared)
and pass that URL to `--endpoint`. The webhook handler acks with 204 and
processes notifications asynchronously; the
`google_health_uid_{healthUserId}` mapping written during the OAuth
callback (`app/routers/health.py` → `google_health_callback`) is used to
route notifications to the correct local user.

### Nextcloud (per-user) setup

Each user enters their Nextcloud server URL, username and an app password under
**Settings → Nextcloud**, then picks a folder. Documents in that folder can be listed
and pulled into the analysis pipeline.

## Metric library & normalization

`data/metric_library.json` is the source of truth for canonical metric definitions
(name, category, unit, data type, description, aliases, reference ranges, unit
conversions). It is **applied to the database automatically on startup** (idempotent
create-or-update + alias merge), and the same logic backs the admin
**"Refresh from Library"** button. Applying the library also re-links any unmatched
`health_metrics` rows.

The library already contains definitions for all 13 synced Google Health metrics
(steps, heart rate, sleep, weight, distance, calories, blood glucose, body
temperature, oxygen saturation, body-fat %, height, active-zone/active minutes).
Canonical units match what Google stores (e.g. **weight in kg**); the frontend converts
to each user's preferred display unit.

### Protected definitions

The Google Health definitions are hand-tuned (canonical units, aliases, conversions)
and marked `"protected": true` in the library so an automated regeneration can't
clobber them. Protection is controlled by `METRIC_LIBRARY_PROTECT` (default `true`):

- When **on**, protected entries are left untouched by `refresh_metric_library.py`
  (LLM merge) and by the DB apply step (aliases are still merged so new name variants
  keep linking).
- Set `METRIC_LIBRARY_PROTECT=0` (or `false`/`no`/`off`) to allow overwrites.

### `refresh_metric_library.py`

Finds health metrics with no matching definition, asks the LLM to propose new
definitions, and merges them into the library:

```powershell
.\.venv\Scripts\python.exe .\refresh_metric_library.py
```

It writes `data/metric_library_proposed.json` for review, backs up the current library,
and only applies on confirmation. New definitions take effect after the next startup
seed or a manual **Refresh from Library**.

## Utility scripts

| Script | Purpose |
|--------|---------|
| `refresh_metric_library.py` | LLM-assisted generation of missing metric definitions |
| `list_metrics.py` | Print distinct `metric_type`/`unit` pairs in `health_metrics` (seed planning) |
| `query_metrics.py` | Ad-hoc query of stored metrics |
| `google_health_boilerplate.py` | Standalone Google Health API experiment (uses `httpx`) |
| `register_google_health_subscriber.py` | One-time webhook subscriber registration (needs `google-auth` + a service account) |
| `start.ps1` / `stop.ps1` | Start/stop local dev services |

## Docker

Production (`docker-compose.yml`) bakes the frontend as a static export served by nginx
and runs the backend under `uvicorn`; the SQLite DB in `./data` is volume-mounted.

```powershell
docker compose up --build          # production
docker compose -f docker-compose.dev.yml up --build   # dev with live reload
```

The dev container volume-mounts the source and runs `uvicorn --reload` plus the Next.js
dev server (HMR), proxying `/api/*` to the backend — edits on the host are picked up
immediately. See `AGENTS.md` for the production-vs-dev comparison.

## Notes & limitations

- **Data coverage:** only Fitbit / Pixel Watch / first-party Google sources are exposed
  by the Google Health API; old Google Fit phone-sensor data does not carry over.
- **Dropped metrics:** blood pressure, BMR (→ total calories) and speed have no Google
  Health v4 equivalent and are not synced.
- **Tokens:** users who previously linked the legacy Google Fit API must re-link, as old
  `fitness.*` tokens are invalid against the v4 API.
