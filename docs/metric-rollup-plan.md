# Metric Rollup Enhancement Plan

Status: **Draft** — 2026-08-14
Related: Google Health API v4 sync ([app/services/google_health.py](../app/services/google_health.py), [app/services/scheduler.py](../app/services/scheduler.py))

## Goal

Support **hybrid granularity** for Google Health metrics:

- **Historical data** (older than a configurable cutoff) → stored as a **single daily rollup** row per day.
- **Recent data** (within the cutoff window) → stored as **granular raw** records.

This keeps long-term history compact while preserving fine detail for the recent
data users actually drill into. Going forward, daily incremental syncs (which fall
entirely within the cutoff) are always granular.

---

## Background / API facts (verified 2026-08-14)

### Which types support `dailyRollUp`
From [developers.google.com/health/data-types](https://developers.google.com/health/data-types) —
the "Operations" column lists `dailyRollup` support:

| Internal name        | API type (kebab)         | dailyRollup? | Window cap | Rollup value field (confirmed) | Probe pts |
|----------------------|--------------------------|--------------|-----------|--------------------------------|-----------|
| steps                | steps                    | ✅           | 90 days   | `steps.countSum` (str int64) ✅ | 7 |
| distance             | distance                 | ✅           | 90 days   | `distance.millimetersSum` (str int64) ✅ | 7 |
| heart_rate           | heart-rate               | ✅           | **14 days** | `heartRate.{beatsPerMinuteAvg (float), beatsPerMinuteMin, beatsPerMinuteMax}` ✅ (3/day) | 7 |
| move_minutes         | active-minutes           | ✅           | **14 days** | `activeMinutes.activeMinutesRollupByActivityLevel[].{activityLevel, activeMinutesSum}` ✅ (list by level) | 3 |
| calories             | total-calories           | ✅           | **14 days** | `totalCalories.kcalSum` (float) ✅ | 7 |
| weight               | weight                   | ✅           | 90 days   | `weight.weightGramsAvg` (grams float → ÷1000=kg) ✅ | 2 |
| body_fat_percentage  | body-fat                 | ✅           | 90 days   | `bodyFat.bodyFatPercentageAvg` (float) ✅ | 1 |
| heart_minutes        | active-zone-minutes      | ✅           | 90 days   | ⚠️ unconfirmed (0 pts in window — likely `activeZoneMinutes...Sum`) | 0 |
| blood_glucose        | blood-glucose            | ✅           | 90 days   | ⚠️ unconfirmed (0 pts — no data in window) | 0 |
| body_temperature     | core-body-temperature    | ✅           | 90 days   | ⚠️ unconfirmed (0 pts — no data in window) | 0 |
| sleep                | sleep                    | ❌ (list only) | —       | n/a — keep raw sessions        | — |
| oxygen_saturation    | daily-oxygen-saturation  | ❌ (already a daily summary type) | — | n/a — already 1/day, keep as-is | — |
| height               | height                   | ❌ (list only) | —       | n/a — keep raw                 | — |

> ✅ = confirmed via live probe 2026-08-14 (all alongside top-level
> `civilStartTime.date` / `civilEndTime.date`). ⚠️ = no data in the probe window, so
> the field name is inferred from the `*RollupValue` reference and MUST be confirmed
> against real data before enabling that metric.
>
> Notes:
> - **weight / body_fat** rollups are **averages** (`*Avg`), not sums — makes sense
>   for point-in-time vitals. weight is grams (÷1000 for kg), matching raw handling.
> - **heart_minutes / blood_glucose / body_temperature** had no data in the user's
>   last 7 days; treat their rollup field names as unverified until data exists.

### Window caps
`dailyRollUp` enforces max query ranges: **14 days** for `heart-rate`,
`active-minutes`, `total-calories`, `calories-in-heart-rate-zone`; **90 days** for
everything else. `fetch_daily_rollup` already chunks large windows accordingly.

### Rollup point shape (confirmed for steps)
```json
{
  "civilStartTime": {"date": {"year": 2026, "month": 8, "day": 13}, "time": {}},
  "civilEndTime":   {"date": {"year": 2026, "month": 8, "day": 14}, "time": {}},
  "steps": {"countSum": "10063"}
}
```
Value fields are int64-as-string; the civil date is at the **top level** of the
point (`civilStartTime.date`), not inside the union payload. Handled by
`HealthSyncScheduler._extract_daily_rollup_point`.

---

## Config model

Per-user sync settings row (`sync_settings_{user_id}` in `AppSettings`) gains:

```json
{
  "sync_days_back": 180,
  "last_google_sync": "...",
  "rollup": {
    "default_cutoff_days": 7,
    "metrics": {
      "steps":        { "enabled": true,  "cutoff_days": 7 },
      "distance":     { "enabled": true,  "cutoff_days": 7 },
      "heart_rate":   { "enabled": false, "cutoff_days": 7 },
      "move_minutes": { "enabled": true,  "cutoff_days": 7 }
    }
  }
}
```

- `default_cutoff_days` — the "set them all to one value" control.
- `metrics.<name>.cutoff_days` — per-metric override (the "edit outliers").
- `metrics.<name>.enabled` — whether that metric rolls up at all.
- `cutoff_days: 0` → roll up everything (always daily; current steps behavior).

---

## Sync algorithm (per metric, inside `sync_health_data`)

```
if metric not in rollup.metrics or not enabled:
    raw_fetch(start, end)                       # unchanged
else:
    cutoff = now - metric.cutoff_days (or default)
    if start < cutoff:
        daily_rollup_fetch(start, min(cutoff, end))   # historical, 1 row/day
    if end > cutoff:
        raw_fetch(max(cutoff, start), end)            # recent, granular
```

- Both windows may run for a metric on the initial bulk sync.
- Incremental syncs (start within cutoff) → only the raw branch executes.
- The boundary day is fetched **raw**; a prior daily row for that day (midnight
  timestamp) won't collide with raw interval timestamps under the
  `(user_id, metric_type, recorded_at, source)` unique key. Acceptable minor
  overlap; the daily row is superseded by raw rows going forward.

### Implementation notes
- `_extract_v4_point` already branches to `_extract_daily_rollup_point` for types
  in `DAILY_DATA_TYPES`. This needs to become **dynamic**: a metric is "daily" only
  for the historical window, raw for the recent window. So the two windows must be
  extracted with the appropriate extractor, not keyed off a global set.
  → Refactor: pass a `granularity` flag (or call the right extractor per window).
- Extend `_extract_daily_rollup_point.rollup_value_field` map as each metric's
  rollup field is verified.

---

## UI: Metric Rollup settings screen

New admin screen (or extend the existing Schedule / metric-definitions page):

1. **Global default**: a single number input → sets `default_cutoff_days` and
   applies it to all rollup-enabled metrics ("set them all to one value").
2. **Per-metric table**: one row per rollup-capable metric:
   - checkbox: `enabled`
   - number input: `cutoff_days` (defaults to the global value; editable per row)
   - a "reset to default" affordance per row.

Metrics without dailyRollup support (sleep, oxygen_saturation, height) are shown
as "always raw / already daily" and are not editable.

---

## Heart-rate discussion (probe resolved — decision: A, B, or C)

**Probed 2026-08-14** — Google's `heart-rate` `dailyRollUp` returns **three** values
per day, not a single average:
```json
"heartRate": {"beatsPerMinuteAvg": 95.16, "beatsPerMinuteMax": 128, "beatsPerMinuteMin": 69}
```

Options:
- **A. Use Google's daily avg/min/max** — now attractive: 3 useful values/day for free.
  Loses resting-HR and the intra-day curve, but daily min is a rough resting proxy.
  Storage: 3 rows/day (avg/min/max as separate metric rows, since `health_metrics`
  stores one `value` per row). Caveat: 14-day rollup window cap.
- **B. Aggregate raw heart_rate ourselves** (avg/min/max/**resting**) — adds true
  resting-HR (Google's rollup doesn't provide it), but more compute + we'd reimplement
  what Google already gives. Only worth it if resting-HR matters.
- **C. Keep heart_rate raw always** — only ~70-556 rows/day in practice; cheap enough
  that rollup may be unnecessary.

→ **DECISION (2026-08-14): Option A2 — aggregate-on-ingest.** Store heart_rate as
**3 daily aggregate rows** (`Heart Rate (Avg)` / `(Min)` / `(Max)`), computed
**whether the data came from a raw fetch or a rollup fetch**, so every day carries
the same 3 metric values regardless of which sync path produced it.

Rationale: guarantees the 3 values exist even on days that were synced raw (recent
window / incremental syncs), keeping the metric set consistent across all history.

#### A2 mechanics
- **Raw window**: fetch individual BPM samples → group by civil date → compute
  avg/min/max per day → write 3 rows/day (timestamped UTC midnight).
- **Rollup window**: `fetch_daily_rollup` already returns avg/min/max per day →
  write the same 3 rows/day directly. No client-side compute needed.
- Both paths converge on the same 3 metric names + midnight timestamp → the
  `(user_id, metric_type, recorded_at, source)` unique key dedupes correctly if a
  day is ever re-synced via the other path.
- **Do we also keep raw samples?** Default: NO — once A2 is on, heart_rate is always
  stored as the 3 daily aggregates (raw samples are fetched transiently for the
  recent window, aggregated, and discarded). Keeping raw samples too would be a
  separate opt-in (`store_raw: true`) if ever needed for intra-day charts.

⚠️ Note: A2 derives avg/min/max from the samples present in the fetched window. If a
day is only partially covered by the window, the aggregate reflects the samples seen
(late-arriving samples on a re-sync would update via the dedup key only if the
computed value changed — otherwise the existing row is kept).

### Rollup multi-value handling (general)
Several rollups yield more than one value/day. Store each as its own row:
- heart_rate → `Heart Rate (Avg/Min/Max)` (3 rows, via A2 on both paths)
- move_minutes → one row per activity level, or a single summed row (decide)
Single-value rollups (steps, distance) → 1 row/day as today.

---

## Filter bugs — RESOLVED 2026-08-14

Both filter bugs are now fixed in [app/services/google_health.py](../app/services/google_health.py)
(`_build_filter`, `fetch_health_data`, `_point_in_window`):

1. **sleep (session type)** — the API rejects *every* server-side filter
   (`INVALID_DATA_POINT_FILTER_DATA_TYPE_MEMBER` for any field, `..._RESTRICTION`
   for no-prefix). Only an **unfiltered** list call succeeds. Fix: `_build_filter`
   returns `None` for session types; `fetch_health_data` omits the `filter` param
   and trims results **client-side** via `_point_in_window` (matches on the
   camelCase union payload's `interval.startTime`). Verified: 6 sleep points in a
   7-day window.
2. **oxygen_saturation (`daily-oxygen-saturation`, a daily type)** — filter must use
   the **civil-date** field with a **date (not timestamp)** format:
   `daily_oxygen_saturation.date >= "2026-08-07" AND ... < "2026-08-14"`. Fix:
   `_build_filter` emits this for daily types. Verified: 7 points in 7 days.

Note: `total-calories` is also classified as a daily type and now uses the same
civil-date filter — confirm its field name before relying on it (it's rollup-only,
not synced via list today).

---

## Rollout steps

1. ✅ Fix steps daily-rollup extraction (`countSum`, `civilStartTime.date`).
2. ✅ Fix sleep + oxygen_saturation filter bugs (see "Filter bugs — RESOLVED").
3. ✅ Probe rollup value field names — confirmed: steps, distance, heart_rate,
   move_minutes, calories, weight, body_fat_percentage. ⚠️ Unconfirmed (no data in
   probe window): heart_minutes, blood_glucose, body_temperature — see [TODO.md](TODO.md).
4. ✅ Heart_rate approach decided: **Option A2 (aggregate-on-ingest)**.
5. ✅ Per-window granularity in `sync_health_data` (IMPLEMENTED 2026-08-14):
   - `DEFAULT_ROLLUP_CONFIG` + `_rollup_config` (merges `sync_settings_{user_id}.rollup`).
   - `_metric_rollup_cutoff` per-metric (enabled, cutoff_days).
   - Per-metric window split: historical `[start, cutoff]` → `fetch_daily_rollup`,
     recent `[cutoff, end]` → `fetch_health_data`; `cutoff_days=0` → all-rollup.
   - `_extract_rows(data_type, point, granularity)` dispatcher returns a list of
     `(metric_label, value, recorded_at)`; `_extract_daily_rollup_rows` handles the
     verified rollup fields (steps/distance/calories sum, weight/body_fat avg,
     heart_rate avg/min/max, move_minutes summed by level).
   - `_aggregate_daily` implements heart_rate A2 for raw windows (group samples by
     day → avg/min/max at UTC midnight).
   - `sync_health_data(..., settings_data=)`; `_run_sync` passes the loaded settings.
   - **Verified live**: 14-day sync → steps/distance 1 row/day (Jul 31–Aug 6) + raw
     (Aug 7–14); heart_rate 3 rows/day across all 15 days.
6. ✅ Calories as daily-only metric (IMPLEMENTED 2026-08-14): added to
   `SYNC_DATA_TYPES` + `UNIT_MAP` (kcal). `DAILY_ONLY_DATA_TYPES = {"calories"}` in
   scheduler.py forces it to always use `fetch_daily_rollup` (no raw window) since
   Google has no raw list endpoint for total-calories.
7. ✅ Config **persistence — GLOBAL (admin), no per-user override** (IMPLEMENTED
   2026-08-14): stored in `AppSettings` under key `sync_rollup_config`
   (`ROLLUP_CONFIG_KEY`). `get_rollup_config()` loads + merges over
   `DEFAULT_ROLLUP_CONFIG`; `sync_health_data` calls it directly (the `settings_data`
   param is now ignored/back-compat). Endpoints in admin.py:
   `GET /admin/settings/rollup` → `{config, defaults}`; `PUT /admin/settings/rollup`
   (validates cutoff >= 0). Both admin-only.
8. ✅ Settings **UI** (IMPLEMENTED 2026-08-14): [app/frontend/src/app/admin/sync-config/page.tsx](../app/frontend/src/app/admin/sync-config/page.tsx)
   — global "default cutoff (days)" input that applies to all metrics, plus a
   per-metric table (enabled checkbox + cutoff override + reset-to-default). Added
   "Sync Config" to the admin nav in [Sidebar.tsx](../app/frontend/src/components/Sidebar.tsx).
9. ❌ **WON'T DO** — Backfill/cleanup helper. Not needed pre-launch; a wipe + fresh
   re-sync already produces the correct hybrid shape. Revisit only if preserving
   existing granular history (without re-fetching) ever becomes necessary.

10. ✅ move_minutes / heart_minutes per-level & per-zone breakdown (IMPLEMENTED
    2026-08-14):
    - move_minutes → one row per activity level: `Active Minutes (Light|Moderate|Vigorous)`
      on both raw (`activeMinutesByActivityLevel[]`) and rollup
      (`activeMinutesRollupByActivityLevel[]`) paths.
    - heart_minutes → one row per zone: `Heart Minutes (Fat Burn|Cardio|Peak)` on both
      raw (`activeZoneMinutes.{heartRateZone,activeZoneMinutes}`) and rollup
      (`activeZoneMinutesRollupByHeartRateZone[]`) paths. ⚠️ heart_minutes rollup field
      name is UNVERIFIED (no zone-minute data to probe) — confirm when data exists.
