# TODO

## ~~Metric granularity: daily vs. hourly strategy~~ — DONE (2026-09-27)

Implemented [metric-granularity-strategy.md](metric-granularity-strategy.md):

1. `metric_definitions.aggregation` (`sum|avg|avg_minmax|latest`) + `cadence`
   (`intraday|daily|event`), seeded on startup from
   `app/services/metric_registry.py` (the fallback for metrics with no
   definition row). Read path, API and UI all consult it instead of keyword
   matching.
2. `_daily_metric_values()` rewritten as SQL (was: load every row into Python —
   ~250k+ and growing). Aggregation follows the metric; `avg_minmax` metrics get
   a min/max band and merge their companion series (Heart Rate
   (Average)/(Minimum)/(Maximum)), so heart rate is ONE continuous series with
   no hole at the 2026-09-19 raw boundary.
3. New `metric_hourly` table (hourly tier) + compaction stages: hourly for every
   closed day (raw kept), early daily close for heart-rate companions, prune at
   raw retention unchanged. Hourly retention default 730d, editable in
   Admin → Schedule. Verified: `scripts/test_prune_and_hourly.py`.
4. `/health/metrics/{type}/series` falls back raw → hourly → daily, so intraday
   charts keep working past raw retention; `/health/reports/overview` gained
   `granularity=auto|daily|hourly` (auto = hourly for ≤7d); intraday-types folds
   companions into the parent and includes hourly-only metrics.
5. UI: dashboard gained an Activity category + intraday panel and a min–max line
   on cards; reports chart picks band/point/area styles by `aggregation` and
   handles hourly labels.

## ~~Metric daily aggregation + rollup/raw overlap cleanup~~ — DONE (2026-08-19)

Dashboard/reports showed only the latest raw interval row per day (e.g. "2 steps")
instead of the day's total. Fixed in three parts:

1. `health_metrics.granularity` column (`raw` | `daily`) + idempotency
   migration in `app/database.py`: collapses legacy duplicate rows, tags UTC-midnight
   Google rollup rows as `daily`, deletes granular rows superseded by a rollup,
   and creates the unique index `uq_health_metric_point (user_id, metric_type,
   recorded_at, source)` that `sync_health_data` relied on.
2. Read path (`app/routers/health.py`): `_daily_metric_values()` produces one value
   per (metric, day) — daily rollup wins, else SUM for accumulative metrics
   (steps/distance/calories/minutes/sleep), else latest sample. Used by
   `/health/metrics` and `/health/reports/overview` (dashboard + reports pages).
3. Ingest (`app/services/scheduler.py`): `_upsert_metric()` writes each point with a
   single native `INSERT ... ON CONFLICT DO UPDATE` against
   `uq_health_metric_point`, so re-syncing refreshes a point instead of duplicating
   it; a `daily` row deletes that day's superseded `raw` rows immediately at sync
   time (deduped per slice). Rows share a transaction and are committed in
   batches — see `docs/google-sync-backfill.md`.

## ~~Seed Google Credentials from environment variables when present~~ — DONE (2026-08-14)

Implemented in `app/main.py` lifespan: on startup, if `GOOGLE_CLIENT_ID` +
`GOOGLE_CLIENT_SECRET` env vars are set and the `google_oauth_config` AppSettings row
is missing / empty / lacks client_id or client_secret, it seeds the config from env
(preserving any existing `redirect_uri`). New fields in `app/config.py`:
`google_client_id`, `google_client_secret`.

## Metric rollup — unverified vitals (need real data to confirm field names)

The following metrics support `dailyRollUp` per Google's docs, but the user had
**no data** in the probe window (2026-08-14, last 7 days), so their rollup value
field names are **inferred but unconfirmed**. Before enabling rollup for each,
probe against real data and confirm the field, then add it to
`HealthSyncScheduler._extract_daily_rollup_point` (and the rollup registry).

- [ ] **heart_minutes** (`active-zone-minutes`) — rollup per-zone breakdown implemented
  as `Heart Minutes (Fat Burn|Cardio|Peak)` reading `activeZoneMinutesRollupByHeartRateZone[].{heartRateZone,activeZoneMinutesSum}`.
  **UNVERIFIED** — user has no active-zone-minute data (0 pts over 90d raw+rollup, 2026-08-14).
  Confirm the rollup field name once real data exists; raw path uses
  `activeZoneMinutes.{heartRateZone,activeZoneMinutes}`. 90-day cap.
- [ ] **blood_glucose** (`blood-glucose`) — confirm rollup field. 90-day cap.
- [ ] **body_temperature** (`core-body-temperature`) — confirm rollup field. 90-day cap.

Probe helper: `scripts/probe_rollup_remaining.py` (edit `PROBE` list) or
`scripts/probe_rollup_shapes.py`. See `docs/metric-rollup-plan.md` for the
confirmed-field table.
