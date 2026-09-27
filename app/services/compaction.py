"""Compact raw Google Health samples into hourly and daily rows, then prune raw.

Why this exists
---------------
Rollup-enabled metrics (heart_rate, move_minutes, steps, ...) sync intraday raw
samples for a recent window and daily rollups for older days. In steady state
the cursor always runs ahead of the rollup cutoff, so the daily-rollup window
never opens and a day's raw samples are written once and then never touched
again. That means:

  * daily avg/min/max history stops at whatever the last backfill produced, and
  * raw rows accumulate forever (~35k heart-rate samples/day).

`compact_raw_metrics` closes both gaps in three stages, from newest to oldest
data (see docs/metric-granularity-strategy.md):

  1. HOURLY tier — every closed day with raw rows is aggregated into
     ``metric_hourly`` (24 rows/metric/day instead of tens of thousands). Raw
     rows are KEPT, so intraday charts stay exact inside the retention window
     and keep working (at hourly resolution) after raw is pruned.
  2. EARLY DAILY CLOSE — metrics whose derived daily series uses a *different*
     metric_type than the raw series (heart_rate -> Heart Rate
     (Average)/(Minimum)/(Maximum)) get their daily rows written while raw still
     exists. Safe because a daily row only supersedes same-metric_type raw rows,
     and companions are separate metric_types: day-over-day history never holes
     out while raw ages toward retention.
  3. DAILY + PRUNE — past ``raw_retention_days``, daily rows are written (sum /
     avg / min / max per the rule) and only then are raw rows deleted, after
     every daily row is read back as a safety gate.

Hourly rows are pruned only past ``hourly_retention_days`` (0 = keep forever)
and only for days that already have an authoritative daily row.

Safety rules (in order of importance)
-------------------------------------
1. Raw rows are only ever deleted for metrics whose ``sync_rollup_config``
   entry has ``enabled: true`` AND only after that day's daily rows are written
   and verified. A blanket "prune all raw rows" would delete history that has
   no daily fallback.
2. Stage 1/2 never delete anything, so they run for every rule here regardless
   of rollup config.
3. One day is one transaction, so a failure rolls back cleanly and the next run
   retries it.
4. ``dry_run=True`` reports exactly what would be written and deleted, and
   writes nothing.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..database import async_session_factory
from ..models.health_data import HealthMetric, MetricHourly
from ..models.settings import AppSettings

logger = logging.getLogger(__name__)

# AppSettings key holding the retention windows (JSON).
RAW_RETENTION_KEY = "sync_raw_retention"
RAW_RETENTION_DESCRIPTION = (
    "Days of non-rolled-up (raw) Google data to keep before compacting it into a "
    "daily rollup and deleting the raw rows. Applies to rollup-enabled metrics "
    "only (heart_rate, move_minutes, steps, distance). 0 = keep raw forever."
)
DEFAULT_RAW_RETENTION_DAYS = 90
DEFAULT_HOURLY_RETENTION_DAYS = 730  # 0 = keep hourly rows forever

# How each data type's raw samples collapse into derived rows.
#
# `source` is the metric_type the raw rows are stored under; `daily` lists
# (label, kind) pairs where the label is what `_extract_daily_rollup_rows` emits
# for that aggregate (so it normalises to the same canonical name a Google
# dailyRollUp sync would have written) and `kind` is how to compute it.
#
# "self" in the label position means "reuse the source metric_type" -- used where
# raw rows already arrive split per level/zone, so the day's rows simply add up.
#
# Optional keys:
#   early_daily -- write the derived DAILY rows for every closed day even while
#                  raw still exists (safe only when the derived labels differ
#                  from the raw metric_type, i.e. heart_rate).
#   self_pattern-- LIKE prefix used to find raw metric_types belonging to this
#                  data type when `source` is None (e.g. "Active Minutes").
COMPACTION_RULES: dict[str, dict] = {
    "heart_rate": {
        "source": "Heart Rate",
        "early_daily": True,
        "daily": [
            ("Heart Rate (Avg)", "avg"),
            ("Heart Rate (Min)", "min"),
            ("Heart Rate (Max)", "max"),
        ],
    },
    # move_minutes / heart_minutes arrive per activity level / heart-rate zone,
    # and each level is its own metric_type whose daily value is the sum of
    # that day.
    "move_minutes": {
        "source": None,
        "self_pattern": "Active Minutes%",
        "daily": [("self", "sum")],
    },
    "heart_minutes": {
        "source": None,
        "self_pattern": "Heart Minutes%",
        "daily": [("self", "sum")],
    },
    # Steps/distance already get daily rows from Google's dailyRollUp, so they
    # are hourly+prune only (hourly from raw; daily rows only when pruning).
    "steps": {
        "source": "Steps",
        "daily": [("self", "sum")],
    },
    "distance": {
        "source": "Distance",
        "daily": [("self", "sum")],
    },
}

# Unit used when the raw rows' own unit is unavailable (resolved labels only).
_KIND_UNIT = {"avg": "bpm", "min": "bpm", "max": "bpm", "sum": "minutes"}


async def _read_retention() -> dict:
    import json

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == RAW_RETENTION_KEY))
            row = result.scalar_one_or_none()
            if row:
                return json.loads(row.value)
    except Exception as e:
        logger.warning(f"Could not read {RAW_RETENTION_KEY}, using defaults: {e}")
    return {}


async def get_raw_retention_days() -> int:
    """Days of raw data to keep. 0 means keep raw rows forever (no pruning)."""
    value = await _read_retention()
    try:
        return max(0, int(value.get("raw_retention_days", DEFAULT_RAW_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_RAW_RETENTION_DAYS


async def get_hourly_retention_days() -> int:
    """Days of hourly rows to keep. 0 means keep them forever."""
    value = await _read_retention()
    try:
        return max(0, int(value.get(
            "hourly_retention_days", DEFAULT_HOURLY_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_HOURLY_RETENTION_DAYS


async def _write_retention(**days: int) -> dict:
    """Persist retention fields, preserving the ones not passed in."""
    import json

    async with async_session_factory() as db:
        result = await db.execute(
            select(AppSettings).where(AppSettings.key == RAW_RETENTION_KEY))
        row = result.scalar_one_or_none()
        value = json.loads(row.value) if row else {}
        for field, val in days.items():
            value[field] = val
        payload = json.dumps(value)
        if row:
            row.value = payload
        else:
            db.add(AppSettings(key=RAW_RETENTION_KEY, value=payload,
                               description=RAW_RETENTION_DESCRIPTION))
        await db.commit()
    return value


async def set_raw_retention_days(days: int) -> int:
    """Persist the raw-retention window. Negative values clamp to 0."""
    days = max(0, int(days))
    await _write_retention(raw_retention_days=days)
    logger.info(f"raw_retention_days set to {days}")
    return days


async def set_hourly_retention_days(days: int) -> int:
    """Persist the hourly-retention window. Negative values clamp to 0."""
    days = max(0, int(days))
    await _write_retention(hourly_retention_days=days)
    logger.info(f"hourly_retention_days set to {days}")
    return days


def _aggregate(values: list[float], kind: str) -> float | None:
    if not values:
        return None
    if kind == "sum":
        return float(sum(values))
    if kind == "avg":
        return sum(values) / len(values)
    if kind == "min":
        return float(min(values))
    if kind == "max":
        return float(max(values))
    return None


async def _rollup_enabled_data_types() -> list[str]:
    """Data types whose rollup is enabled, i.e. safe to compact."""
    from .scheduler import ROLLUP_CAPABLE, _metric_rollup_cutoff, get_rollup_config

    cfg = await get_rollup_config()
    enabled = []
    for data_type in ROLLUP_CAPABLE:
        is_enabled, _cutoff = _metric_rollup_cutoff(data_type, cfg)
        if is_enabled and data_type in COMPACTION_RULES:
            enabled.append(data_type)
    return enabled


async def compact_raw_metrics(dry_run: bool = False) -> dict:
    """Build hourly rows, close daily rows, then prune raw past retention.

    Returns a summary: which (user, metric, day) groups were compacted, how many
    hourly/daily rows were written, how many raw/hourly rows were deleted, and
    which days were deliberately KEPT because no daily row could be produced.
    """
    from .metric_normalizer import metric_normalizer
    from .scheduler import _upsert_metric

    retention_days = await get_raw_retention_days()
    hourly_retention_days = await get_hourly_retention_days()
    summary = {
        "dry_run": dry_run,
        "retention_days": retention_days,
        "hourly_retention_days": hourly_retention_days,
        "cutoff": None,
        "data_types": [],
        "days_compacted": 0,
        "days_hourly": 0,
        "daily_rows_written": 0,
        "hourly_rows_written": 0,
        "raw_rows_deleted": 0,
        "hourly_rows_deleted": 0,
        "days_kept_no_daily": [],
        "errors": [],
    }

    # recorded_at is stored naive-UTC in SQLite, so compare against naive bounds.
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now_utc - timedelta(days=retention_days) if retention_days > 0 else None
    today = datetime(now_utc.year, now_utc.month, now_utc.day)  # days < today are closed
    summary["cutoff"] = cutoff.isoformat() if cutoff else None

    enabled = set(await _rollup_enabled_data_types())
    # Hourly/early-close stages never delete, so they run for every rule even
    # when rollup (and therefore pruning) is disabled for that metric.
    candidates = sorted(set(COMPACTION_RULES) | enabled)
    summary["data_types"] = sorted(enabled)
    if not candidates:
        summary["skipped"] = "no compaction rules; nothing to do"
        return summary

    async with async_session_factory() as db:
        await metric_normalizer.load(db)

        for data_type in candidates:
            rule = COMPACTION_RULES[data_type]
            daily_specs = rule["daily"]
            can_prune = data_type in enabled

            # Resolve each rule label to (canonical_name, kind, definition_id)
            # once, so derived rows are linked to the metric library like
            # synced ones.
            resolved: list[tuple[str, str, int | None]] = []
            for label, kind in daily_specs:
                if label == "self":
                    continue  # resolved per metric_type below
                norm = await metric_normalizer.normalize(db, label, _KIND_UNIT[kind])
                resolved.append((
                    norm.canonical_name if norm.definition else label,
                    kind,
                    norm.definition.id if norm.definition else None,
                ))
            uses_self = any(label == "self" for label, _ in daily_specs)

            # Which raw metric_types belong to this data type.
            if rule["source"]:
                source_types = [rule["source"]]
            else:
                # Per-level/zone series: find them by the rule's prefix rather
                # than "every metric_type with a daily row" — the latter would
                # also match Steps/Distance and write their totals with the
                # wrong unit.
                pattern = rule.get("self_pattern")
                source_types = [r[0] for r in (await db.execute(
                    select(HealthMetric.metric_type).where(
                        HealthMetric.source == "google_health_connect",
                        HealthMetric.granularity == "raw",
                        HealthMetric.metric_type.like(pattern),
                    ).distinct())).all()]
                if not source_types:
                    continue
                if not uses_self and not resolved:
                    continue

            # Resolve "self" labels once per source metric_type, not per day.
            self_specs: dict[str, list[tuple[str, str, int | None]]] = {}
            if uses_self:
                for st in source_types:
                    unit_row = (await db.execute(
                        select(HealthMetric.unit).where(
                            HealthMetric.granularity == "raw",
                            HealthMetric.metric_type == st,
                        ).limit(1))).scalar_one_or_none()
                    norm = await metric_normalizer.normalize(db, st, unit_row or "")
                    self_specs[st] = [(
                        norm.canonical_name if norm.definition else st,
                        "sum",
                        norm.definition.id if norm.definition else None,
                    )]

            # Every closed day (day < today) that still has raw rows. Cutoff is
            # applied per group so hourly/early-close run inside retention.
            targets = (await db.execute(
                select(
                    HealthMetric.user_id,
                    HealthMetric.metric_type,
                    func.date(HealthMetric.recorded_at).label("day"),
                    func.count(HealthMetric.id),
                )
                .where(
                    HealthMetric.source == "google_health_connect",
                    HealthMetric.granularity == "raw",
                    HealthMetric.metric_type.in_(source_types),
                    func.date(HealthMetric.recorded_at) < today.date().isoformat(),
                )
                .group_by(HealthMetric.user_id, HealthMetric.metric_type,
                          func.date(HealthMetric.recorded_at))
            )).all()

            for user_id, metric_type, day_str, n_rows in targets:
                if not day_str:
                    continue
                try:
                    day = datetime.fromisoformat(day_str)
                    midnight = datetime(day.year, day.month, day.day)
                    day_end = midnight + timedelta(days=1)
                    past_cutoff = cutoff is not None and day_end <= cutoff

                    rows = (await db.execute(
                        select(HealthMetric.recorded_at, HealthMetric.value,
                               HealthMetric.unit)
                        .where(
                            HealthMetric.source == "google_health_connect",
                            HealthMetric.granularity == "raw",
                            HealthMetric.user_id == user_id,
                            HealthMetric.metric_type == metric_type,
                            HealthMetric.recorded_at >= midnight,
                            HealthMetric.recorded_at < day_end,
                        ))).all()
                    if not rows:
                        continue
                    raw_unit = rows[0][2] or ""

                    # ── Stage 1: hourly tier (never deletes) ────────────────
                    # Grouped per (label, hour); idempotent upsert.
                    label_specs: list[tuple[str, str, int | None]] = (
                        self_specs.get(metric_type, []) if uses_self else resolved
                    )

                    hourly_written = 0
                    for label, kind, def_id in label_specs:
                        by_hour: dict[datetime, list[float]] = {}
                        for ts, value, _unit in rows:
                            if not ts:
                                continue
                            hour = ts.replace(minute=0, second=0, microsecond=0)
                            by_hour.setdefault(hour, []).append(float(value))
                        for hour, vals in by_hour.items():
                            agg = _aggregate(vals, kind)
                            if agg is None:
                                continue
                            hourly_written += 1
                            if dry_run:
                                continue
                            stmt = sqlite_insert(MetricHourly).values(
                                user_id=user_id,
                                metric_type=label,
                                value=agg,
                                unit=raw_unit or _KIND_UNIT.get(kind, ""),
                                recorded_at=hour,
                                source="google_health_connect",
                                definition_id=def_id,
                            )
                            stmt = stmt.on_conflict_do_update(
                                index_elements=[
                                    MetricHourly.user_id,
                                    MetricHourly.metric_type,
                                    MetricHourly.recorded_at,
                                    MetricHourly.source,
                                ],
                                set_={"value": agg, "definition_id": def_id},
                            )
                            await db.execute(stmt)
                    summary["hourly_rows_written"] += hourly_written
                    if hourly_written:
                        summary["days_hourly"] += 1

                    # ── Stage 2/3: daily rows (+ prune when past cutoff) ────
                    should_close_daily = past_cutoff and can_prune
                    early_close = (
                        not should_close_daily
                        and rule.get("early_daily") and bool(label_specs)
                    )
                    if not (should_close_daily or early_close):
                        if not dry_run and hourly_written:
                            await db.commit()
                        continue

                    written: list[str] = []
                    for label, kind, def_id in label_specs:
                        value = _aggregate([float(v) for _ts, v, _u in rows], kind)
                        if value is None:
                            continue
                        if dry_run:
                            written.append(label)
                            continue
                        # Keep an existing authoritative daily row (Google's own
                        # rollup for a boundary day) rather than overwriting it
                        # with a recomputed sum.
                        if should_close_daily:
                            existing = (await db.execute(
                                select(func.count(HealthMetric.id)).where(
                                    HealthMetric.source == "google_health_connect",
                                    HealthMetric.granularity == "daily",
                                    HealthMetric.user_id == user_id,
                                    HealthMetric.metric_type == label,
                                    HealthMetric.recorded_at >= midnight,
                                    HealthMetric.recorded_at < day_end,
                                ))).scalar_one()
                            if existing:
                                written.append(label)
                                continue
                        await _upsert_metric(
                            db, user_id=user_id, metric_type=label,
                            value=value,
                            unit=raw_unit or _KIND_UNIT.get(kind, ""),
                            recorded_at=midnight,
                            source="google_health_connect",
                            definition_id=def_id, granularity="daily")
                        written.append(label)

                    if not written:
                        summary["days_kept_no_daily"].append({
                            "user_id": user_id, "metric_type": metric_type,
                            "day": day_str, "raw_rows": n_rows,
                            "reason": "no aggregate could be produced",
                        })
                        if not dry_run and hourly_written:
                            await db.rollback()
                        continue

                    if should_close_daily and not dry_run:
                        # SAFETY GATE: delete only after every daily row is
                        # readable back out. If any is missing, keep the raw rows.
                        missing = []
                        for name in written:
                            exists = (await db.execute(
                                select(func.count(HealthMetric.id)).where(
                                    HealthMetric.source == "google_health_connect",
                                    HealthMetric.granularity == "daily",
                                    HealthMetric.user_id == user_id,
                                    HealthMetric.metric_type == name,
                                    HealthMetric.recorded_at == midnight,
                                ))).scalar_one()
                            if not exists:
                                missing.append(name)
                        if missing:
                            summary["days_kept_no_daily"].append({
                                "user_id": user_id, "metric_type": metric_type,
                                "day": day_str, "raw_rows": n_rows,
                                "reason": f"daily row missing for {missing}",
                            })
                            await db.rollback()
                            continue
                        await db.execute(
                            delete(HealthMetric).where(
                                HealthMetric.source == "google_health_connect",
                                HealthMetric.granularity == "raw",
                                HealthMetric.user_id == user_id,
                                HealthMetric.metric_type == metric_type,
                                HealthMetric.recorded_at >= midnight,
                                HealthMetric.recorded_at < day_end,
                            ))
                        summary["raw_rows_deleted"] += n_rows
                    elif should_close_daily:
                        summary["raw_rows_deleted"] += n_rows

                    if not dry_run:
                        await db.commit()
                    summary["days_compacted"] += 1
                    summary["daily_rows_written"] += len(written)
                except Exception as e:
                    await db.rollback()
                    summary["errors"].append(
                        f"user {user_id} {metric_type} {day_str}: "
                        f"{type(e).__name__}: {e}")
                    logger.warning(
                        f"Compaction failed for user {user_id} {metric_type} {day_str}",
                        exc_info=True)

        # ── Hourly pruning: past hourly retention, and only for days that
        # already have an authoritative daily row (otherwise the hourly rows are
        # the only sub-daily history left). ──────────────────────────────────
        if hourly_retention_days > 0:
            h_cutoff = now_utc - timedelta(days=hourly_retention_days)
            try:
                stale = (await db.execute(
                    select(func.count(MetricHourly.id)).where(
                        MetricHourly.recorded_at < h_cutoff,
                        ~select(HealthMetric.id).where(
                            HealthMetric.user_id == MetricHourly.user_id,
                            HealthMetric.metric_type == MetricHourly.metric_type,
                            HealthMetric.granularity == "daily",
                            func.date(HealthMetric.recorded_at)
                            == func.date(MetricHourly.recorded_at),
                        ).exists(),
                    ))).scalar_one()
                if stale and not dry_run:
                    await db.execute(
                        delete(MetricHourly).where(
                            MetricHourly.recorded_at < h_cutoff,
                            ~select(HealthMetric.id).where(
                                HealthMetric.user_id == MetricHourly.user_id,
                                HealthMetric.metric_type == MetricHourly.metric_type,
                                HealthMetric.granularity == "daily",
                                func.date(HealthMetric.recorded_at)
                                == func.date(MetricHourly.recorded_at),
                            ).exists(),
                        ))
                    await db.commit()
                summary["hourly_rows_deleted"] = stale
            except Exception as e:
                await db.rollback()
                summary["errors"].append(f"hourly prune: {type(e).__name__}: {e}")
                logger.warning("Hourly pruning failed", exc_info=True)

    logger.info(
        f"Compaction {'(dry run) ' if dry_run else ''}raw_retention={retention_days}d "
        f"hourly_retention={hourly_retention_days}d: "
        f"{summary['days_compacted']} day group(s), "
        f"{summary['hourly_rows_written']} hourly row(s), "
        f"{summary['daily_rows_written']} daily row(s), "
        f"{summary['raw_rows_deleted']} raw row(s) deleted, "
        f"{len(summary['days_kept_no_daily'])} kept, "
        f"{len(summary['errors'])} error(s)")
    return summary
