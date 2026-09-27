# Metric Granularity Strategy — daily vs. hourly/minute views

Status: **Implemented** — 2026-09-27
Related: [metric-rollup-plan.md](metric-rollup-plan.md), [google-sync-backfill.md](google-sync-backfill.md)

> **Implementation notes (2026-09-27)** — this plan shipped with one deliberate
> deviation: the hourly tier lives in its own **`metric_hourly`** table instead of
> a `granularity='hourly'` value on `health_metrics`. The unique key
> `(user_id, metric_type, recorded_at, source)` would otherwise collide with raw
> rows at exact hour boundaries, and the daily-supersede rules in
> `app/database.py` / `_upsert_metric` treat same-name rows as "delete the raw".
> A separate table keeps raw data untouchable by construction. Also: the
> one-off hourly backfill script (rollout step 7) turned out to be unnecessary —
> no raw rows predate the tier (history before 2026-09-19 was never stored raw).
>
> Shipped: `app/services/metric_registry.py` (aggregation/cadence + companions),
> SQL rewrite of `_daily_metric_values`, `metric_hourly` + compaction stages,
> tier fallback in `/metrics/{type}/series`, `granularity` on
> `/reports/overview`, dashboard Activity category + intraday panel, reports
> band/event charts, hourly retention in Admin → Schedule. Verified with
> `scripts/test_prune_and_hourly.py` (prune preserves every daily value) and
> endpoint smoke tests.

## 1. What the data actually looks like (measured 2026-09-27)

DB: `data/health_tracker.db`, 114 MB, 251,689 raw rows + 210 daily rows.

| metric_type | rows | granularity | range |
|---|---|---|---|
| Heart Rate | 244,053 | raw (~35k samples/day, 63–148 bpm) | 09-19 → now |
| Steps | 2,506 raw + 23 daily | raw ~360/day (minute-level) | 08-27 → now |
| Distance | 1,837 raw + 23 daily | raw ~278/day | 08-27 → now |
| Active Minutes (Light/Moderate/Vigorous) | 1,482/149/165 raw + 23/19/19 daily | raw interval samples | 08-27 → now |
| Heart Minutes (Fat Burn/Cardio/Peak) | 1,025/195/5 | raw interval samples | 08-27 → now |
| Calories | 30 | **daily only** (Google has no raw endpoint) | 08-27 → now |
| Sleep | 30 | 1 session row/day (minutes) | 08-28 → now |
| Oxygen Saturation | 29 | 1 row/day (daily API type) | 08-27 → now |
| Weight / Body Fat % | 3 each | point-in-time | 09-01 → 09-23 |
| Body Temperature | 1 | point-in-time | 09-01 |

**Two eras exist.** The backfill (≤ 09-18) produced `granularity='daily'` rows; steady
state (≥ 09-19) produces `granularity='raw'` only, because the sync cursor runs ahead
of the 7-day rollup cutoff so `fetch_daily_rollup` never re-opens (see
google-sync-backfill.md §"Rolling raw data up as it ages"). Compaction only fires for
rows older than `raw_retention_days` (default **90**), so:

- `Heart Rate (Average/Min/Max)` daily rows **stop at 09-18** — the day-over-day HR
  series has a growing hole.
- `Heart Rate` (raw) fills 09-19 → now, but collapses to the **last sample of the day**
  in reports (§3), e.g. 83 bpm at 21:06 vs. the day's true average of 88.7.
- Steps/Distance/Active/Heart minutes still work for raw days only because
  `_is_summable_metric` sums them; Calories has daily rows throughout.

## 2. Metric cadence classification

Three classes drive everything below (view, storage, aggregation).

### A. Daily-native — day-over-day is the only honest view
Aggregation = `sum` (activity accumulators) or `avg`/`latest` (point-in-time).
Hourly noise is meaningless: a step count "at 14:03" or an HbA1c "trend line" is not
information.

- **Labs / document-derived**: Hemoglobin A1c, Glucose, Insulin, LDL/HDL, Triglycerides,
  Cholesterol, hs-CRP, eGFR, Creatinine, ALT, Hemoglobin, WBC, Platelets, TSH,
  Sodium, Potassium, Vitamin D/B12, Ferritin, Blood Pressure.
- **Body composition**: Weight, Body Fat %, BMI, Height.
- **Activity accumulators**: Steps, Distance, Calories, Active Minutes
  (Light/Moderate/Vigorous), Heart Minutes (Fat Burn/Cardio/Peak).
- **Already-daily API types**: Oxygen Saturation, Calories (rollup-only).

### B. Intraday-meaningful — hourly/minute view earns its keep
What you want to know is *when*: "HR at 7am vs. during the 6pm run", "when did I hit
10k steps".

- **Heart Rate** (35k samples/day) — the canonical case.
- **Steps / Distance / Active Minutes** — second view: a daily total for trend, an
  hourly curve for "when did I move".
- **Sleep** — daily total now; stage/interval detail is intraday if Google ever
  provides stages (keep the session row either way).
- Future: blood glucose, blood pressure, workout sessions.

### C. Event / point-in-time — no trend line, latest value + history list
Labs and Body Temperature: sparse, irregular, meaningful at the date of the test.
These should never render an interpolated area chart across empty gaps.

The class is a **property of the metric**, currently not stored anywhere — it is
hard-coded as string matching (`_SUMMABLE_METRIC_KEYWORDS`) in the read path and as
`CORE_METRICS` in the dashboard.

## 3. Problems this strategy must fix

1. **`_daily_metric_values` loads every row into Python** (health.py:46) on every
   `/health/metrics` and `/health/reports/overview` call — ~244k HR rows today,
   growing ~35k/day. It will not scale to a year of data.
2. **Non-summable raw metrics degrade to "last sample of the day"** — wrong for Heart
   Rate (should be avg, with min/max).
3. **Two parallel, both-incomplete HR series**: `Heart Rate` (raw, ≥09-19) and
   `Heart Rate (Average/Min/Max)` (daily, ≤09-18). Reports can show either, never
   both halves.
4. **No middle tier.** Only `raw` (seconds) and `daily` exist. Once compaction removes
   raw, sub-daily history is gone forever — an intraday chart can never show last
   month.
5. **Cadence is implicit** (keyword matching + a hardcoded lab list), so dashboard,
   reports, and API each guess independently.

## 4. Storage strategy — three tiers

```
raw  --(age > raw_retention)-->  hourly  --(day closed > daily_retention)-->  daily
```

| tier | row shape | who writes it | retention |
|---|---|---|---|
| `raw` | every sample/interval (`granularity='raw'`) | `sync_health_data` | per-metric, 7–14 days for HR, 90 days for cheap counters (existing `sync_raw_retention`) |
| `hourly` **(new)** | 1 row/metric/hour: `avg`+`min`+`max`+`count` for class B, `sum` for class A counters | compaction, stage 1 | ~1 year (24 rows/metric/day vs. 35k) |
| `daily` | 1 row/metric/day: sum or avg/min/max (3 rows for HR) | compaction, stage 2 + Google `dailyRollUp` backfill | forever |

Design points:

- **The hourly tier is a separate `metric_hourly` table** (user_id, metric_type,
  value, unit, recorded_at hour-aligned, source, definition_id; unique on
  (user_id, metric_type, recorded_at, source)). A `granularity='hourly'` value on
  `health_metrics` was rejected: hourly timestamps would collide with raw rows at
  the same (metric, time, source) under `uq_health_metric_point`, and the
  daily-supersede rules would keep threatening raw rows. `health_metrics.granularity`
  stays `raw` | `daily`.
- **Split "write the daily aggregate" from "delete the raw rows."** Today compaction
  does both at the same 90-day cutoff, which is why recent days have no daily rows.
  Stage 2 should close **every completed day** (T-1) as soon as raw rows exist for it,
  while deletion stays at the retention cutoff. This alone heals the `Heart Rate
  (Average)` hole starting tomorrow.
- **Extend `COMPACTION_RULES`** to produce hourly rows: `heart_rate` → avg/min/max per
  hour; `steps`/`distance` → sum per hour; `move_minutes`/`heart_minutes` → sum per
  level/zone per hour. Daily rows then derive from the *hourly* rows (avg of avgs
  weighted by `count`), so no stage ever re-reads raw after it is deleted.
- **Cover the gaps in `ROLLUP_CAPABLE`/`COMPACTION_RULES`:** `heart_minutes`,
  `sleep`, `oxygen_saturation` currently fall outside compaction entirely (heart_minutes
  is in `COMPACTION_RULES` but not in `ROLLUP_CAPABLE`, so it is never eligible).
- **Volume**: HR at 35k rows/day raw vs. 24 rows/day hourly vs. 3 rows/day daily.
  The hourly tier costs ~0.2 MB/year/metric and buys back month-scale intraday charts.
- **Config** lives alongside the existing keys: extend `sync_rollup_config` with
  per-metric `hourly_retention_days`, and keep `sync_raw_retention` as the raw window.

### Metric registry becomes the source of truth
Add two columns to `metric_definitions`:

- `aggregation`: `sum | avg | avg_minmax | latest`
- `cadence`: `intraday | daily | event`

Seed them from §2 (e.g. `Heart Rate → avg_minmax/intraday`, `Steps → sum/intraday`,
`Weight → latest/daily`, `Hemoglobin A1c → latest/event`). The read path, the API and
the UI all consult this instead of keyword matching.

## 5. Read-path strategy (API)

1. **Rewrite `_daily_metric_values` as SQL** — `GROUP BY date(recorded_at)` with
   `SUM/AVG/MIN/MAX` chosen from `aggregation`. No ORM rows pulled into Python. Daily
   rows are preferred where they exist (keep the current precedence rule).
2. **Unify Heart Rate into one series.** Canonical name `Heart Rate` with
   `avg_minmax`: the daily view returns `{avg, min, max}` per day across the *whole*
   history (daily rows ≤09-18, hourly-derived daily ≥09-19); retire
   `Heart Rate (Average/Min/Max)` as separate card entries (keep the rows, alias them).
3. **`GET /health/metrics/{type}/series` gains a tier fallback**: pick the finest tier
   that covers the requested range — `raw` if the range is inside raw retention, else
   `hourly`, else `daily`. One endpoint serves both the day chart and the month chart.
   (Keep the existing `max_points` bucketing as a safety net.)
4. **`GET /health/reports/overview` gains `granularity=auto|daily|hourly`.** `auto`
   = daily for ranges > 7 days, hourly for ≤ 7 days and only for `cadence=intraday`
   metrics. Summary cards always stay daily (a card is a day-over-day artifact).
5. Fix FastAPI route ordering when adding literal paths — declaration order wins
   (`/metrics/intraday-types` must stay above `/metrics/{metric_type}`).

## 6. Dashboard & reporting changes

### Dashboard (`app/frontend/src/app/dashboard/page.tsx`)
- **Two zones** instead of one lab grid:
  - *Today* (class B, intraday): HR now + day min/max/avg, steps today vs. goal,
    active minutes — sourced from the series endpoint with `hourly` granularity.
  - *Trends* (class A daily + class C event): the existing card grid, organized by
    category.
- **Add an `Activity` category** to `CATEGORY_ORDER`/`CATEGORY_ICONS` and to
  `CORE_METRICS` defaults (Steps, Calories, Distance, Active Minutes, Sleep) — right
  now the Google-synced data is invisible unless manually added via Customize.
- **Card values follow `aggregation`**: HR card shows today's average with a min–max
  range line, never the last sample.
- **Per-card range switch**: `1d` (hourly) for intraday metrics, plus the existing
  global 30d…All for daily metrics.

### Reports (`app/frontend/src/app/reports/page.tsx`)
- Chart granularity follows §5.4 (`auto`): daily area chart for long ranges, hourly
  for ≤ 7d on intraday metrics.
- For `avg_minmax` metrics draw a min–max band with an average line (same visual
  language as `IntradayChart.tsx`) instead of a single interpolated line.
- For `event` metrics (labs) use a scatter/step chart with visible gaps — no area
  fill across months of no tests.
- Surface `Heart Rate` as one metric, not four cards (`Heart Rate`,
  `(Average)`, `(Minimum)`, `(Maximum)`).

### Health Data (`health-data/page.tsx`)
- Keep `IntradayChart`, but point it at the tiered series endpoint so the date picker
  can go back months (hourly) instead of dying at the raw-retention boundary.
- The table should default to daily rows and offer a "show hourly/raw" toggle for the
  selected day; today it only ever shows `_daily_metric_values` output.

## 7. Rollout order

1. **SQL rewrite of `_daily_metric_values`** + `avg` handling for non-summable raw
   metrics (fixes perf + the misleading HR card; no schema change).
2. **Decouple daily-aggregate writing from raw deletion in compaction** — close every
   completed day, delete only past retention. Heals the 09-19→now daily hole.
3. **Hourly tier** in compaction (`granularity='hourly'`) + per-metric retention config.
4. **`aggregation`/`cadence` columns on `metric_definitions`** + seed data; delete the
   keyword lists.
5. **Series-endpoint tier fallback** + `granularity` on `/reports/overview`.
6. **UI**: Today panel on dashboard, Activity category, chart-granularity auto rules,
   HR series unification.
7. Backfill hourly rows for the last 90 days from existing raw (one-off script under
   `scripts/`, preview-by-default like the other helpers).
