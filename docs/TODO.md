# TODO

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
3. Ingest (`app/services/scheduler.py`): `_upsert_metric()` updates existing points
   instead of inserting duplicates; a `daily` row now deletes that day's superseded
   `raw` rows immediately at sync time.

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
