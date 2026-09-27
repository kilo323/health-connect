# Google Health sync backfill (2026-09-26)

## Symptom

Only `Daily Steps` had 30 days of history. Every other metric had 1–2 days:

| metric | rows | days covered |
|---|---|---|
| Daily Steps | 12,631 | 30 |
| Distance | 331 | 2 |
| Light Active Minutes | 224 | 2 |
| Average/Max/Min Heart Rate | 2 each | 2 |
| Calories | 1 | 1 |
| Oxygen Saturation | 1 | 1 |
| Sleep Duration | 1 | 1 |

Google actually had the data the whole time. `scripts/probe_day_availability.py`
queries one day per data type with `pageSize=5` and showed distance at 285–335
points on 08-28, 09-01, 09-10 and 09-20, and heart rate on every probed day.

## Root cause

Three defects compounded. `scripts/count_raw_volume.py` and
`scripts/probe_fetch_timing.py` produced the numbers below.

### 1. `heart_rate` used the raw endpoint and ate the whole run

`sync_rollup_config` had `heart_rate.enabled = false`, so the sync window went to
the raw `dataPoints` endpoint. Measured over 30 days: **390,957 points across 391
pages, 219 s of pagination alone** (`steps`, for comparison, is 14,986 points in
19 s). Because `fetch_health_data` accumulates every page before returning, those
391 pages were fetched before a single row was written.

`heart_rate` is second in `SYNC_DATA_TYPES` (right after `steps`), so the run
reached it immediately and never got to `sleep`, `weight`, `distance`,
`move_minutes` or `calories`. This is exactly the observed state: the 09-26 run
wrote 12,252 steps rows between 00:29 and 00:35 and then stopped.

### 2. No partial progress was persisted

`last_google_sync` was written only after *every* data type finished
(`scheduler.py`, end of the per-user block). A run that died part way through
recorded nothing, so the next run recomputed the same window and died in the same
place. The tail metrics starved permanently. The database showed the fingerprint
of this: `sync_settings_1 = {"sync_days_back": 30}` with **no** `last_google_sync`
key at all.

The trigger is a fire-and-forget `asyncio.create_task(scheduler._run_sync())`
in `app/routers/admin.py`, so a dev-server reload or restart killed the run with
no trace.

### 3. One COMMIT per row, with fsync on every commit

`_upsert_metric` did SELECT + INSERT + COMMIT per row under the default rollback
journal (`journal_mode=delete`, `synchronous=FULL`). Measured 6.5 ms/row versus
0.5 ms/row when batched. In the real run it was worse (~29 ms/row; 12,252 steps
rows took six minutes), which is what made interruption likely in the first place.

## Fixes

- **`app/database.py`** — WAL + `synchronous=NORMAL` on every connection.
- **`app/services/scheduler.py`**
  - `_upsert_metric` is now one native `INSERT ... ON CONFLICT DO UPDATE`
    against `uq_health_metric_point`. No SELECT, no SAVEPOINT, and it can never
    raise `IntegrityError`, so rows share a transaction.
  - Rows are committed in batches (`BATCH_COMMIT_ROWS = 500`) and windows are
    streamed in slices (`POINT_SLICE = 5000`) so a ~1M-point window does not
    materialise every row in memory and cannot lose more than one slice of work.
  - `metric_normalizer.normalize()` is memoised per `(label, unit)`. It falls back
    to a `SequenceMatcher` fuzzy match for labels that are not exact/alias hits,
    which cost seconds per 70k-row window.
  - **Per-data-type cursors.** `sync_health_data` takes `type_cursors` and an
    `on_type_complete` callback; each type resumes from its own cursor and its
    cursor is persisted the moment it finishes. `last_google_sync` is kept as the
    minimum of the per-type cursors for backward compatibility.
  - `_aggregate_daily` was removed. Raw heart-rate samples are stored individually
    as `granularity='raw'`; older days still come from the daily rollup, so daily
    avg/min/max history is unchanged.
- **`app/routers/admin.py`** — `/sync/backfill` clears `type_cursors` as well as
  `last_google_sync`.

Measured after the fix: 6.5 ms/row → 1.1 ms/row, and a 30-day backfill completes
all 13 data types instead of dying after the first.

## Follow-up: app startup hung once the table grew

Storing raw heart-rate samples took `health_metrics` from 13k to 270k rows, and the
app then hung on startup — `init_db()` never returned. A pre-existing O(n²) bug in
its idempotent cleanup became fatal at that size:

```sql
DELETE FROM health_metrics
WHERE granularity = 'raw' AND source = 'google_health_connect'
  AND EXISTS (SELECT 1 FROM health_metrics d
              WHERE d.user_id = health_metrics.user_id
                AND d.metric_type = health_metrics.metric_type
                AND d.granularity = 'daily'
                AND date(d.recorded_at) = date(health_metrics.recorded_at))
```

`EXPLAIN QUERY PLAN` showed the inner lookup resolving through
`uq_health_metric_point`, which covers `(user_id, metric_type, recorded_at, source)`
— so the search matched on `(user_id, metric_type)` alone. For `heart_rate` that is
~246k rows, re-filtered by `date()` for every candidate raw row.

The fix is the new index `ix_health_metrics_day_lookup (user_id, metric_type,
granularity, recorded_at)`, created **before** that delete so the inner lookup
matches on `granularity='daily'` and hits a handful of rows. Measured on 270,474
rows: **hangs indefinitely → 0.24 s**; all of `init_db`'s cleanup is now 1.12 s.

Rewriting the SQL to a `substr(recorded_at, 1, 10)` semi-join or
`WITH ... AS MATERIALIZED` made no difference (0.21–0.24 s) — SQLite flattened both
back into the same correlated join. The index is what matters.

The same non-sargable `func.date(HealthMetric.recorded_at) == day` pattern was in
the sync-time supersede delete in `scheduler.py`; that now uses a half-open
`recorded_at >= day_start AND recorded_at < day_end` range.

Run `scripts/bench_startup_cleanup.py` to re-check these timings.

## Heart-rate tuning

`heart_rate` is by far the most expensive type (~24k raw samples/day). It is
configured in **Admin → Rollup config** (`sync_rollup_config`):

- `cutoff_days: 7` (**current**) — days older than 7 come from `dailyRollUp`
  (1 row/day, avg/min/max, 3 requests per 30 days); the last 7 days keep raw
  intraday samples. Backfill: ~13 min, ~246k heart-rate rows, 65 MB DB.
- `cutoff_days: 0` — roll up everything. No intraday detail, near-instant.
- `enabled: false` — raw samples for the whole window. Works now that writes are
  batched, but a 30-day backfill is ~391k rows / ~65 MB and takes ~15 min.

Raw heart-rate samples normalise to metric_type `heart_rate`, which matches no
metric definition or alias, so those rows land with `definition_id = NULL` and
appear in the admin metric library's *unmatched* list. Map them there if you want
them attached to a curated definition. Do **not** map them to `Average Heart Rate`:
the daily-rollup supersede rule deletes a day's `raw` rows once a `daily` row
exists for the same metric_type, which would delete the intraday samples.

## Rolling raw data up as it ages

Cutoff behaviour, measured with `scripts/check_hr_window_aging.py` (30-day
`sync_days_back`, `heart_rate.cutoff_days: 7`):

| scenario | dailyRollUp window | raw window |
|---|---|---|
| fresh install / cleared cursors | 23 days | 7 days |
| steady state, last run 6h ago | — | 1.25 days |
| steady state, daily schedule | — | 2 days |
| one week later | — | 1.25 days |
| app offline 10 days, then ran | 4 days | 7 days |

Two consequences drove the design below:

1. **Steady state re-pulls only ~1–2 days, not 7.** The cursor always runs ahead
   of the cutoff, so `type_start < cutoff` is never true and the daily-rollup
   window never opens.
2. **Nothing ever compacts data that already aged out.** The cutoff only selects
   which endpoint is used at fetch time. A day that falls behind the cursor is
   never fetched again, so its raw rows stay raw and its daily avg/min/max is
   never written.

### Compaction

`app/services/compaction.py` closes both gaps by deriving the daily aggregate
from the raw rows already in the database — no API call, and no dependence on
Google still retaining the data. It then deletes the raw rows it summarised.
Since 2026-09-27 it runs in three stages (see
[metric-granularity-strategy.md](metric-granularity-strategy.md)):

1. **hourly** — every closed day with raw rows is aggregated into the
   `metric_hourly` table (24 rows/metric/day) and raw is kept, so intraday
   charts survive past raw retention.
2. **early daily close** — heart rate's derived series (Heart Rate
   (Average)/(Minimum)/(Maximum)) is written for every closed day while raw
   still exists; safe because companions are different metric_types than the raw
   `Heart Rate` series the supersede rules target. This keeps day-over-day HR
   history continuous instead of stopping at the last backfill.
3. **daily + prune** — past `raw_retention_days`, daily rows are written (with an
   "existing daily row wins" guard for Google boundary-day rollups) and only
   then are raw rows deleted, after the read-back safety gate.

- `raw_retention_days` (`app_settings` key `sync_raw_retention`, **default 90**,
  `0` = keep raw forever) sets how long raw samples are kept.
- `hourly_retention_days` (same key, **default 730**, `0` = keep forever) prunes
  hourly rows only for days that already have a daily row.
- Nightly at 03:17 UTC via APScheduler (`health_compaction_job`).
- Manual: `POST /api/admin/sync/compact` with `{"dry_run": true}` to preview.
  This is the only way to run it when the scheduler is disabled, because
  `scheduler.start()` returns early without a `schedule_configs` row.
- UI: Admin → Schedule → Data Retention & Rollups.

Safety rules, in order of importance:

1. **Only rollup-enabled metrics are touched** (`sync_rollup_config` entries with
   `enabled: true`). A blanket "prune all raw rows" would delete all
   step/distance history, because those have rollup disabled and so have no
   daily rows to fall back on. `heart_minutes` is currently disabled, so
   `Fat Burn Heart Minutes` / `Heart Minutes (Cardio)` / `(Peak)` are not
   compacted — enable its rollup to bring them into scope.
2. **Raw rows are deleted only after every daily row for that day is written and
   read back out of the database.** If an aggregate cannot be produced or
   verified, the raw rows are kept and the day is reported in
   `days_kept_no_daily`. Losing a day outright is unrecoverable once it falls
   outside the `sync_days_back` floor.
3. One day is one transaction; `dry_run` reports without writing.

Verified with `scripts/test_compaction.py`: at a 3-day retention, 14 groups were
compacted into 24 daily rows and 141,696 raw rows deleted, with **0 discrepancies**
against daily values recomputed independently from a pre-run snapshot. All new
daily rows came out linked to their metric definitions (`scripts/check_linkage.py`).

### Raw heart rate is now its own series

The raw label `heart_rate` matched no definition name or alias, so ~246k rows sat
with `definition_id = NULL`. `scripts/migrate_raw_heart_rate.py` created a `Heart
Rate` definition (id 14, alias `heart_rate`) and renamed the existing rows.

**Order matters:** the upsert conflict target is
`(user_id, metric_type, recorded_at, source)`. The moment `heart_rate` became an
alias, `normalize()` would return the canonical name `Heart Rate` and every
subsequent sync would INSERT a new row instead of updating the old one —
silently doubling ~30k rows/day. The rows are renamed first, then the alias goes
live. The migration verifies no duplicate logical points afterwards.

### Intraday charts

`/health/metrics` and `/health/reports/overview` both go through
`_daily_metric_values()`, which collapses each metric to one value per day
(`app/routers/health.py:46`) — the last sample of the day for a snapshot metric.
Raw samples were therefore invisible. Added:

- `GET /health/metrics/intraday-types` — metric types that still have raw rows.
  Must stay declared **above** `/health/metrics/{metric_type}`; FastAPI matches
  in declaration order, so a literal path after a path parameter is unreachable.
- `GET /health/metrics/{metric_type}/series` — buckets the range into at most
  `max_points` slices and returns min/max/avg per slice, because a day of heart
  rate is ~30k rows.
- `app/frontend/src/components/IntradayChart.tsx` — min/max band plus average
  line, with a metric picker and date.

`health_metrics.recorded_at` is stored by SQLite as a naive
`'YYYY-MM-DD HH:MM:SS.ffffff'` string, so values read back have no `tzinfo`.
A browser sends tz-aware ISO strings, and subtracting an aware `start` from a
naive `recorded_at` raises `TypeError`. `_as_naive_utc()` normalises both sides.


| script | purpose |
|---|---|
| `scripts/probe_day_availability.py` | one cheap request per data type per day — is the data missing in Google or in our DB? |
| `scripts/count_raw_volume.py` | total raw points/pages for a window |
| `scripts/diag_sync_gaps.py` | per-metric and per-day coverage gaps in the DB |
| `scripts/show_cursors.py` | persisted per-type sync cursors |
| `scripts/count_rows.py` | raw row counts by metric and granularity |
| `scripts/backfill_google_sync.py` | `--wipe` then backfill the real DB, resumable |
| `scripts/verify_sync_fix.py` | same flow against a throwaway copy of the DB |
| `scripts/bench_upsert.py` | micro-benchmark the write path |
| `scripts/bench_startup_cleanup.py` | time the idempotent `init_db()` cleanup statements |
| `scripts/check_hr_window_aging.py` | which windows sync requests for heart rate, over time |
| `scripts/migrate_raw_heart_rate.py` | one-time: raw `heart_rate` rows → `Heart Rate` definition |
| `scripts/test_compaction.py` | compaction correctness: dry run, real run, value re-derivation |
| `scripts/test_compaction_api.py` | end-to-end test of the compaction + series endpoints |
| `scripts/check_linkage.py` | verify compacted daily rows are linked to metric definitions |
