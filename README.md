# Health Connect

A self-hosted personal health dashboard. It pulls activity and vital metrics from the
**Google Health API** (Fitbit / Pixel Watch), ingests **medical documents** (PDFs and
images, synced from Nextcloud or uploaded directly) and extracts lab/body-composition
results with an LLM, then normalizes everything against a canonical set of metric
definitions so data from different sources can be charted and compared with proper
units and reference ranges.

> Personal / family project. The Google Health scopes it uses are *restricted*, but
> because it stays well under 100 users it does not require Google OAuth verification
> or an annual CASA assessment.

## Features

- **Google Health sync** — 17 data types: steps, heart rate, sleep, weight, distance,
  calories, blood glucose, body temperature, oxygen saturation (daily + per-minute raw),
  body-fat %, height, active-zone minutes, active minutes, heart-rate variability (HRV),
  VO2 max, and workout sessions (duration, calories, distance, avg heart rate).
  Incremental sync on a schedule, a full-window **backfill**, and optional webhook push
  notifications.
- **Data retention & compaction** — raw intraday samples are rolled up into an hourly
  tier, then daily aggregates, then pruned past a configurable retention window, so
  `health_metrics` stays bounded while intraday charts and long-term history both
  keep working. See [Data retention & compaction](#data-retention--compaction).
- **Document analysis** — an LLM extracts structured metrics (lab work, DEXA, etc.)
  from PDFs/images; results go through a review queue before being saved.
- **Metric definitions & normalization** — synced and extracted metrics are matched to
  canonical `metric_definitions` (exact / alias / fuzzy), with unit conversion and
  reference ranges. Definitions live in the database and are managed in the admin UI
  (manual CRUD plus an LLM proposal workflow for unmatched metrics).
- **Dashboards & reports** — daily charts with per-metric min–max bands, an intraday
  activity panel, a global measurement-system toggle (switch everything to metric or
  imperial) with per-metric overrides on top (e.g. view weight in lb while it is stored in
  kg), and a health-data browser for raw points.
- **Nextcloud integration** — browse, create folders and pull documents from a
  Nextcloud folder.
- **Multi-user** — JWT auth, per-user Google/Nextcloud connections, and an admin area
  for users, LLM config, Google OAuth, metric definitions, the sync schedule, rollup
  config and system settings.

## Tech stack

| Layer     | Technology |
|-----------|------------|
| Backend   | FastAPI 0.115, SQLAlchemy 2.0 (async), SQLite (`aiosqlite`, WAL), APScheduler 3.10 |
| Auth      | JWT (`python-jose`), bcrypt (`passlib`) |
| Frontend  | Next.js 16 (App Router), React 19, TypeScript 7, Tailwind CSS 4, Recharts 3, Zustand 5 |
| Docs/LLM  | OpenAI-compatible chat endpoint, `pdfplumber` / PyMuPDF / Pillow |
| Deploy    | Docker (multi-stage: static export + nginx, or dev container with live reload) |

> There is no automated test suite. Verification lives in the repo as runnable
> scripts — see [Utility scripts](#utility-scripts).

## Project layout

```
app/
  main.py            FastAPI app + lifespan (init DB, seed admin / LLM / Google OAuth config, start scheduler)
  config.py          Settings (pydantic-settings, reads env / .env)
  database.py        Engine, WAL setup, session factory, init_db (schema + idempotent migrations/cleanup)
  models/            SQLAlchemy models (users, health data, app settings)
  schemas/           Pydantic request/response schemas
  routers/           auth, health, admin, users, webhooks
  services/          scheduler (Google sync), google_health (API client),
                     metric_normalizer, metric_registry (aggregation/cadence),
                     compaction, llm, nextcloud, encryption
  frontend/          Next.js app (src/app pages, components, hooks, lib, store)
  nginx.conf         nginx template (rendered via envsubst in start.sh)
  start.sh           production container entrypoint (nginx + uvicorn)
  start-dev.sh       dev container entrypoint (next dev + uvicorn --reload)
  requirements.txt
scripts/             Standalone, manually invoked tooling (see below)
  destructive/       Data-modifying scripts: preview by default, require --yes
docs/                Design docs and TODO (see below)
data/
  llm_prompt.md                LLM extraction prompt template (edited from the admin UI)
  health_tracker.db            SQLite database (gitignored, created on first run)
register_google_health_subscriber.py   One-time webhook subscriber registration
start.ps1 / stop.ps1           Local dev service control
docker-compose.yml             Production container
docker-compose.dev.yml         Dev container with live reload
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

### Before the first sync

`init_db()` creates the schema but seeds **no** `metric_definitions` rows. Without
definitions the normalizer cannot link anything, so every synced row lands with
`definition_id = NULL` and a raw Google label as its `metric_type` — the dashboard,
unit conversion and reports all expect the curated names.

Seed the definitions before the first sync, otherwise the backfill has to be redone:

```powershell
.\.venv\Scripts\python.exe .\scripts\seed_metric_definitions.py [source.db] [target.db]
```

It copies definitions out of a source database, is idempotent (existing definitions
are skipped), and opens the source read-only. Target defaults to
`data/health_tracker.db`. After seeding, use **Admin → Metric Definitions** to review
and extend them.

## Configuration

Settings come from environment variables or a `.env` file in the repo root
(`pydantic-settings`, no prefix). Docker maps these in `docker-compose.yml` /
`docker-compose.dev.yml`.

Not every variable is a `Settings` field in `app/config.py` — the **Where** column says
who actually reads each one, which matters when you're checking why an env var appears
to be ignored.

| Variable | Default | Where | Purpose |
|----------|---------|-------|---------|
| `ADMIN_USER` | `admin@domain.com` in `config.py`; `admin` in Compose and `.env.example` | `config.py` | Admin username, created on startup if absent |
| `ADMIN_PASSWORD` | `changeme` | `config.py` | Admin password, **re-applied on every startup** |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/health_tracker.db` | `config.py` | SQLAlchemy connection string |
| `FERNET_KEY` | — | `config.py` → `services/encryption.py` | **Required** to store Google/Nextcloud secrets. Must be a valid Fernet key |
| `LLM_URL` | — | `config.py` | OpenAI-compatible base URL (e.g. `https://your-provider.com/v1`) |
| `LLM_API_TOKEN` | — | `config.py` | API token for the LLM provider |
| `LLM_MODEL` | — | `config.py` | Model name |
| `LLM_SSL_VERIFY` | `true` | `config.py` | Set to `false` to skip SSL certificate verification on LLM API calls (e.g. behind a corporate TLS-inspecting proxy) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | `config.py` | Optional first-run seed for the `google_oauth_config` app setting. Applied **only if** the stored config is missing, empty, or lacks a client id/secret — admin UI edits always win |
| `PUBLIC_URL` | — | `config.py` | Optional explicit public-origin override. Normally unneeded — origins are derived from the request's forwarded host. Set only if API and frontend are on different hosts |
| `CORS_EXTRA_ORIGINS` | — | `config.py` | Comma-separated extra allowed CORS origins |
| `METRIC_LLM_URL` / `METRIC_LLM_API_TOKEN` / `METRIC_LLM_MODEL` | fall back to `LLM_*` | `routers/admin.py` | Optional separate LLM used only by the metric-definition proposal workflow. Resolved from env → `.env` → admin UI config |
| `BACKEND_PORT` | `8000` | `main.py` (CORS), `next.config.ts` (dev rewrite), Compose | Backend listen port |
| `FRONTEND_PORT` | `3000` | `main.py` (CORS), `next.config.ts` (dev rewrite), Compose | Frontend listen port (nginx prod / Next dev) |
| `FORWARDED_ALLOW_IPS` | `*` | uvicorn CLI in `app/start.sh` | Proxy IPs whose `X-Forwarded-*` headers uvicorn trusts. Controls `request.base_url` scheme/host used for the OAuth `redirect_uri` |
| `JWT_SECRET_KEY` | `your-secret-key-change-in-production` | `routers/auth.py`, `routers/users.py` | JWT signing key. **Set this in any non-local deployment** |
| `WEBHOOK_SECRET` | — | `config.py` → `routers/webhooks.py` | Enables `POST /api/webhooks/google-health` (Bearer token). Unset = webhooks disabled |
| `GOOGLE_CLOUD_PROJECT_NUMBER` | — | `config.py` | Project **number** for webhook subscriber management |

`FERNET_KEY` must be a real Fernet key — a hex string (e.g. `openssl rand -hex 32`)
is **rejected** with "not a valid Fernet key". Generate one with:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

> `METRIC_LIBRARY_PROTECT` is still accepted by `config.py` and both Compose files but
> is **inert** — nothing reads it any more, since there is no library file to protect.
> See [Metric definitions](#metric-definitions--normalization).

### Deploying behind a reverse proxy

The app listens on `0.0.0.0` and honors `X-Forwarded-Proto`/`Host` (`uvicorn --proxy-headers`).
For a networked / proxied deployment:

1. Register the **public** callback URL in Google Cloud. The admin Google OAuth page
   pre-fills the Redirect URI from the origin you're browsing (e.g.
   `https://health.example.com/api/health/google-health/callback`) — just save it.
2. That's it for most setups. The post-OAuth redirect and the callback URL are both
   derived from the incoming request's forwarded scheme/host, so they work with zero
   extra config. Set `PUBLIC_URL` only as an explicit override if the API and frontend
   are reached on different hosts.
3. If front and back ends are served from the same origin (typical behind one proxy), CORS is a
   non-issue. Only add `CORS_EXTRA_ORIGINS` if they're split across origins.
4. Set `FORWARDED_ALLOW_IPS` to your proxy's IP if you don't want to trust all proxies (`*`).
5. Set `JWT_SECRET_KEY` — it otherwise falls back to a well-known default.

Intra-container `localhost`/`127.0.0.1` (nginx→uvicorn, Next→uvicorn) is internal and unaffected.

See [docs/reverse-proxy.md](docs/reverse-proxy.md) for sample nginx / Traefik / Caddy configs.

### Google Health (per-user) setup

Google OAuth client credentials are stored in the database (`google_oauth_config` in
`app_settings`) and managed through the admin UI. Environment variables are only a
first-run convenience seed:

1. Create an OAuth 2.0 *Web* client in Google Cloud, enable the Google Health API, and
   add an authorized redirect URI of
   `{backend}/api/health/google-health/callback`.
2. In the app, go to **Admin → Google OAuth** and enter the client ID/secret. (Alternatively
   set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` before the first start; they seed the
   row only when it is missing or incomplete.)
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
  restarts the `AsyncIOScheduler` job. The nightly compaction job (03:17 UTC) is
  registered with the same scheduler, so it only runs while the schedule is enabled.

Common cron values:

| Cadence                    | Expression      |
|----------------------------|-----------------|
| Daily at 2 AM UTC          | `0 2 * * *`    |
| Every 6 hours              | `0 */6 * * *`  |
| Weekly on Sunday at 3 AM   | `0 3 * * 0`    |
| Every weekday at midnight   | `0 0 * * 1-5`  |

You can trigger an immediate sync via **Admin → Sync Now** (`POST /api/admin/sync/now`).

#### 2. Configure per-user sync settings

Each user controls how far back their *first* sync reaches via
`sync_days_back` (default 7, range 1–365). Set it before the first scheduled
run via **Settings → Sync Settings** or `PUT /api/health/sync/settings`
with `{"sync_days_back": N}`.

Subsequent runs only fetch data since the last successful sync (with a
1-day overlap to catch late-arriving points). Per-data-type cursors and the
watermark are stored under the `sync_settings_{id}` app setting
(`last_google_sync` + `type_cursors`).

`sync_days_back` acts as a **floor** on every run, not just the first: the window is
always at least that wide.

Re-reading a wider window later:

- **Backfill All Data** (`POST /api/admin/sync/backfill`, on **Admin → Schedule**)
  clears `last_google_sync` and `type_cursors` for every user and immediately re-runs
  sync, so every user re-fetches their full `sync_days_back` window.
- **Sync Now** does *not* clear cursors — it only resumes from where sync left off.
  Use it after changing settings, not to go back in time.
- To reach further back than `sync_days_back`, raise it in **Settings → Sync Settings**
  first, then backfill.

Idempotency is enforced by the `(user_id, metric_type, recorded_at, source)` unique
index (`uq_health_metric_point`), so re-syncing refreshes a point instead of duplicating it.

`POST /api/admin/sync/reset` clears a stale in-progress flag if a sync task dies.

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

   This registers `subscriptionCreatePolicy: AUTOMATIC` for 12 of the 13 synced
   data types (everything except `calories`, which is daily-only), so notifications
   start flowing for any consenting user
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
**Settings → Nextcloud**, then picks (or creates) a folder. Documents in that folder
can be listed and pulled into the analysis pipeline. Requires a valid `FERNET_KEY`.

## Metric definitions & normalization

Canonical definitions live in the **`metric_definitions`** table — name, category,
unit, data type, description, aliases, reference ranges, unit conversions, plus
`aggregation` and `cadence`. They are the join target for `health_metrics.definition_id`,
so unit conversion, reference ranges and dashboard/report rollups all key off them.

There is **no `data/metric_library.json`** and no startup library apply step. Nothing
in `app/` loads a JSON library; definitions are managed through the database and admin UI.

### Seeding and lifecycle

- **Fresh installs**: `init_db()` creates the schema and seeds nothing. Run
  `scripts/seed_metric_definitions.py` (see [Before the first sync](#before-the-first-sync)).
- **Existing rows**: startup backfills `aggregation`/`cadence` on any definition row
  that predates those columns, from `app/services/metric_registry.py`.
- **Unlinked rows**: `POST /api/admin/metric-definitions/normalize` re-runs
  retroactive normalization over stored metrics; `POST /api/admin/metric-definitions/map-unmatched`
  (`{"metric_type": ..., "definition_id": ...}`) adds an unmatched raw label as an
  **alias** on an existing definition, so later syncs resolve to it.

### Aggregation & cadence

`app/services/metric_registry.py` is the single source of truth for how a metric
collapses over time:

- `aggregation` — how granular rows become one daily value:
  `sum` (steps, distance, calories, minutes, sleep), `avg`, `avg_minmax`
  (heart rate: day = avg, plus min/max band), `latest` (weight, labs, SpO2).
- `cadence` — which view the metric belongs in: `intraday` (hourly/minute detail is
  the point), `daily` (day-over-day is the only honest view), `event` (sparse
  point-in-time tests — charted as points, never an interpolated line).

Both are admin-editable columns on `metric_definitions`; the registry's `DEFAULTS` and
the `infer_*` fallbacks apply to metrics with no definition row, so manual entries
still behave. The read path, API and UI consult the registry rather than matching
keywords — see [docs/metric-granularity-strategy.md](docs/metric-granularity-strategy.md).

Heart rate is an `avg_minmax` parent whose parts are stored as **separate
`metric_types`** (`Heart Rate (Average)/(Minimum)/(Maximum)`), since one row holds one
value. The read path merges them back into one continuous series with a min/max band.

### The LLM proposal workflow

**Admin → Metric Definitions** covers the whole loop:

1. **Unmatched Metrics** — synced/extracted metric types with no definition. Map each
   to an existing definition, or create one by hand.
2. **LLM-Proposed Definitions** — `POST /api/admin/metric-definitions/propose` asks the
   metric-library LLM (which falls back to the document LLM) to draft definitions for
   unmatched metrics. Review each, then merge (`merge-proposal`) or
   **Accept All** (`accept-all-proposals`).

Definitions are editable in place (name, unit, aliases, ranges, conversions,
aggregation, cadence) and deletable, via `/api/admin/metric-definitions` CRUD endpoints.
The extraction prompt itself is editable at **Admin → LLM Config → Analysis Prompt
Template** (`/api/admin/settings/llm-prompt`, with reset-to-default). Saves write
straight to `data/llm_prompt.md` on disk.

## Data retention & compaction

Raw intraday samples accumulate fast (~35k heart-rate rows/day). Compaction
(`app/services/compaction.py`) bounds the table in three stages, newest data first,
one day per transaction:

1. **Hourly tier** — every closed day with raw rows is aggregated into `metric_hourly`
   (24 rows/metric/day). Raw rows are **kept**, so intraday charts stay exact.
2. **Early daily close** — metrics whose daily series uses a *different* `metric_type`
   than the raw series (heart rate → its Average/Minimum/Maximum companions) get their
   daily rows written while raw still exists.
3. **Daily + prune** — past `raw_retention_days`, daily rows are written (sum / avg /
   min / max per the registry's aggregation rule) and only then are raw rows deleted,
   after every daily row is read back as a safety gate.

Defaults: `raw_retention_days = 90`, `hourly_retention_days = 730` (0 = keep forever).
Both are editable at **Admin → Schedule → Data Retention & Rollups**
(`GET`/`PUT /api/admin/sync/compaction`) and stored in the `sync_raw_retention` app setting.

Raw rows are only ever pruned for metrics whose rollup entry has `enabled: true` and
only after that day's daily rows are written and verified. `POST /api/admin/sync/compact`
runs it on demand — pass `{"dry_run": true}` to **Preview Compaction** (reports exactly
what would be written and deleted, writes nothing), and use **Compact Now** to apply.
The nightly job only exists while the scheduler is enabled, so a manual-sync setup
(no schedule) has to trigger compaction from here.

Hourly rows are pruned only past `hourly_retention_days` and only for days that already
have an authoritative daily row. `GET /api/health/metrics/{type}/series` falls back
raw → hourly → daily, so intraday charts keep working (at hourly resolution) after raw
is gone.

Daily rollup eligibility per metric (which types use Google's `dailyRollUp` and how far
back) is configured at **Admin → Sync Config** (`/api/admin/settings/rollup`) and
documented in [docs/metric-rollup-plan.md](docs/metric-rollup-plan.md).

## Admin area

| Page | Purpose | Endpoints |
|------|---------|-----------|
| **Dashboard** | At-a-glance status cards (users, LLM, schedule, DB) | — |
| **Users** | Create/edit/deactivate users, change roles | `/api/users` |
| **LLM Config** | Document LLM, metric-library LLM, model list, analysis prompt template | `/api/admin/settings/llm`, `/metric-llm`, `/llm/models`, `/llm-prompt` |
| **Google OAuth** | Client id/secret and the derived redirect URI | `/api/admin/settings/google-oauth` |
| **Metric Definitions** | CRUD, unmatched queue, LLM proposals | `/api/admin/metric-definitions` |
| **Schedule** | Enable/cron, Sync Now, Backfill All Data, data retention windows, compaction | `/api/admin/settings/schedule`, `/sync/now`, `/sync/backfill`, `/sync/compaction`, `/sync/compact` |
| **Sync Config** | Per-metric daily-rollup config | `/api/admin/settings/rollup` |
| **System Settings** | Export the `app_settings` table; import an `app_settings.json` | `/api/admin/settings/app_settings_export`, `/api/admin/settings/import` |

The export/import round-trip moves configuration between instances (including encrypted
OAuth tokens, so `FERNET_KEY` must match on both sides). **The exported file contains
live secrets — never commit it.**

## Utility scripts

All are standalone and manually invoked from the repo root with the project venv;
none are imported by the app, Docker or CI. `scripts/` currently holds a wide set of
diagnostics, so run `Get-ChildItem scripts` before assuming a name below is the only
one.

**Setup / data**

| Script | Purpose |
|--------|---------|
| `scripts/seed_metric_definitions.py` | Copy `metric_definitions` from a source DB into a fresh one (required before the first sync) |
| `scripts/relink_metric_types.py` | Relink rows in a DB synced without definitions |
| `scripts/migrate_raw_heart_rate.py` | Re-type raw Google heart-rate samples to the canonical name |
| `scripts/export_app_settings.py` / `scripts/restore_app_settings.py` | Export/restore the `app_settings` table (**contains secrets**) |
| `scripts/apply_index_live.py`, `scripts/bench_*.py`, `scripts/audit_db_state.py` | Index creation, benchmarks, DB state audits |
| `scripts/list_metrics.py`, `scripts/query_metrics.py`, `scripts/list_tables.py`, `scripts/count_rows.py` | Inspection helpers |
| `scripts/inspect_definitions.py`, `scripts/inspect_units.py`, `scripts/show_unmatched.py`, `scripts/check_linkage.py` | Definition/linkage diagnostics |
| `scripts/make_blank_db.py`, `scripts/check_blank_db_sync.py` | Blank-DB harness for replaying a fresh-install sync |
| `scripts/check_window_split.py`, `scripts/check_hr_window_aging.py`, `scripts/final_integrity_check.py` | Retention-window and integrity checks |

**Verification** (the stand-in for a test suite)

| Script | Purpose |
|--------|---------|
| `scripts/test_prune_and_hourly.py` | Prune preserves every daily value; hourly tier behaves |
| `scripts/test_compaction.py`, `scripts/test_compaction_api.py` | Compaction stages and endpoints |
| `scripts/test_seed_definitions.py`, `scripts/test_import_app_settings.py` | Seeding and settings import |
| `scripts/test_hybrid_sync.py`, `scripts/test_resync_no_dupes.py`, `scripts/test_suggestion_gate.py` | Hybrid raw/rollup sync, re-sync idempotency, proposal gating |
| `scripts/verify_api_normalization.py`, `scripts/verify_hybrid.py`, `scripts/verify_sync_fix.py` | End-to-end verification against a database |
| `scripts/verify_propose_suggestions.py`, `scripts/verify_unmatched_index.py` | Proposal gating and the unmatched-metrics index |

**Diagnostics**

| Script | Purpose |
|--------|---------|
| `scripts/diag_sync.py`, `scripts/diag_sync_gaps.py` | Sync state and per-day gaps (accepts a database path) |
| `scripts/show_cursors.py`, `scripts/check_minutes.py`, `scripts/check_oauth_config.py` | Cursor / minute-volume / OAuth config checks |
| `scripts/probe_*.py`, `scripts/poll_coverage.py`, `scripts/count_raw_volume.py` | Google API shape and coverage probes |
| `scripts/google_health_boilerplate.py` | Standalone Google Health API experiment (uses `httpx`) |
| `scripts/analyze_metrics.py`, `scripts/repro_bad_suggestion.py`, `scripts/time_propose_http.py` | Analysis and proposal-flow profiling |

**Destructive** (`scripts/destructive/`) — preview by default, require `--yes` to
modify data, and resolve the database independently of the current directory:

| Script | Purpose |
|--------|---------|
| `scripts/destructive/clear_minutes.py` | Drop stored active/heart minute rows |
| `scripts/destructive/reset_sync_state.py` | Clear sync cursors/watermarks |
| `scripts/destructive/migrate_user_data.py` | Migrate a user's data between accounts |

**Root**

| Script | Purpose |
|--------|---------|
| `register_google_health_subscriber.py` | One-time webhook subscriber registration (needs `google-auth` + a service account) |
| `start.ps1` / `stop.ps1` | Start/stop local dev services |

## Docs

| Doc | Contents |
|-----|----------|
| [docs/metric-granularity-strategy.md](docs/metric-granularity-strategy.md) | The raw → hourly → daily design, the `metric_hourly` table, and why hourly is a separate table |
| [docs/google-sync-backfill.md](docs/google-sync-backfill.md) | Diagnosing and fixing a sync that advanced cursors without saving data |
| [docs/metric-rollup-plan.md](docs/metric-rollup-plan.md) | Per-metric `dailyRollUp` support, confirmed vs. unverified field names |
| [docs/reverse-proxy.md](docs/reverse-proxy.md) | Sample nginx / Traefik / Caddy configs |
| [docs/TODO.md](docs/TODO.md) | Completed work and the remaining open questions |

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
- **Admin password:** `ADMIN_PASSWORD` re-applies on every startup, so rotating it
  invalidates the current admin password everywhere the old one was used.
- **Secrets on disk:** `app_settings.json`, `data/*.db` and `.env` are gitignored and
  hold live secrets (OAuth tokens, `client_secret`, LLM api key). Do not commit them.
- **SQLite:** one writer at a time. The engine runs in WAL mode with a 30s busy timeout
  and `NullPool`, because a Docker bind mount (especially on Windows) will otherwise
  produce "database is locked" errors.
- **Migrations:** there is no Alembic environment in the tree (despite `alembic` being
  pinned in `requirements.txt`). `init_db()` applies additive `ALTER TABLE`s plus
  idempotent data cleanup on every startup instead.
