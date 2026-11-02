"""Rebuild authoritative daily rows from Google's own dailyRollUp values.

Google's rollups are its deduplicated, account-timezone totals — the same
numbers the Google Health app shows. The read path prefers ``granularity='daily'``
rows, so writing them makes day totals exact without touching the raw rows that
back the intraday charts (unless ``delete_raw`` is set).

Shared by the admin endpoint (Admin -> Sync Configuration -> Rollup Repair) and
``scripts/destructive/rebuild_daily_steps.py``. Recent days self-heal via the
sync's closed-day rollup refresh (see ``sync_health_data``); this covers history
that predates the fix (or a restore).

Preview by default: ``apply=False`` computes the per-day plan and writes nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from ..models.health_data import HealthMetric
from .google_health import GoogleHealthService
from .metric_normalizer import metric_normalizer
from .scheduler import UNIT_MAP, HealthSyncScheduler, _upsert_metric
from .timezones import get_user_timezone, local_day_bounds_utc

# Metrics with a Google dailyRollUp this repair can write. ``interval`` marks
# the accumulators that multi-device double-counting inflated (see
# app/services/interval_merge.py).
REPAIRABLE_METRICS: dict[str, dict] = {
    "steps": {"label": "Steps", "interval": True},
    "distance": {"label": "Distance", "interval": True},
    "move_minutes": {"label": "Active Minutes", "interval": True},
    "heart_minutes": {"label": "Heart Minutes", "interval": True},
    "heart_rate": {"label": "Heart Rate (Avg/Min/Max)", "interval": False},
    "calories": {"label": "Calories", "interval": False},
    "weight": {"label": "Weight", "interval": False},
    "body_fat_percentage": {"label": "Body Fat %", "interval": False},
}


async def rebuild_daily_rollups(
    db,
    *,
    user_id: int,
    data_types: list[str],
    days: int,
    delete_raw: bool = False,
    apply: bool = True,
) -> dict:
    """Fetch Google rollups for the last `days` days and write daily rows.

    Returns a summary dict with the per-day plan ``rows`` (day, metric_type,
    rollup value, current value, delta) and write counts. ``apply=False`` is a
    dry run: identical output, nothing written.

    Daily rows are stamped at UTC midnight of the civil date (the marker
    convention shared with Google rollups and compaction). Raw rows of each
    rebuilt LOCAL day are only deleted when ``delete_raw`` is set.
    """
    unknown = [m for m in data_types if m not in REPAIRABLE_METRICS]
    if unknown:
        raise ValueError(f"unsupported metric(s): {', '.join(unknown)}")

    tz_name = await get_user_timezone(db, user_id)
    await metric_normalizer.load(db)
    google = GoogleHealthService()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    rows_out: list[dict] = []
    written = 0
    raw_deleted = 0

    for data_type in data_types:
        unit = UNIT_MAP.get(data_type, "unknown")
        points = await google.fetch_daily_rollup(
            user_id=user_id, data_type=data_type,
            start_time=start, end_time=end)
        for point in points:
            for label, value, recorded_at in (
                    HealthSyncScheduler._extract_daily_rollup_rows(data_type, point)):
                norm = await metric_normalizer.normalize(db, label, unit)
                metric_type = norm.canonical_name if norm.definition else label
                definition_id = norm.definition.id if norm.definition else None
                day = recorded_at.date().isoformat()

                # Current display value for the day: the daily marker row wins,
                # else the raw sum of the day's local-day window.
                marker = await db.execute(
                    select(HealthMetric.value).where(
                        HealthMetric.user_id == user_id,
                        HealthMetric.metric_type == metric_type,
                        HealthMetric.granularity == "daily",
                        HealthMetric.recorded_at == recorded_at,
                    ))
                daily_val = marker.scalar_one_or_none()
                day_start, day_end = local_day_bounds_utc(day, tz_name)
                raw_vals = (await db.execute(
                    select(HealthMetric.value).where(
                        HealthMetric.user_id == user_id,
                        HealthMetric.metric_type == metric_type,
                        HealthMetric.granularity != "daily",
                        HealthMetric.recorded_at >= day_start,
                        HealthMetric.recorded_at < day_end,
                    ))).scalars().all()
                current = (float(daily_val) if daily_val is not None
                           else (sum(float(v) for v in raw_vals) if raw_vals else None))

                rows_out.append({
                    "day": day,
                    "metric_type": metric_type,
                    "rollup": float(value),
                    "current": current,
                    "delta": (None if current is None
                              else round(float(value) - current, 3)),
                })

                if not apply:
                    continue
                await _upsert_metric(
                    db, user_id=user_id, metric_type=metric_type,
                    value=value, unit=unit, recorded_at=recorded_at,
                    source="google_health_connect",
                    definition_id=definition_id, granularity="daily")
                written += 1
                if delete_raw:
                    result = await db.execute(
                        delete(HealthMetric).where(
                            HealthMetric.user_id == user_id,
                            HealthMetric.metric_type == metric_type,
                            HealthMetric.source == "google_health_connect",
                            HealthMetric.granularity == "raw",
                            HealthMetric.recorded_at >= day_start,
                            HealthMetric.recorded_at < day_end,
                        ))
                    raw_deleted += result.rowcount or 0

    if apply:
        await db.commit()

    return {
        "user_id": user_id,
        "timezone": tz_name,
        "days": days,
        "applied": apply,
        "delete_raw": delete_raw,
        "rows_written": written,
        "raw_rows_deleted": raw_deleted,
        "rows": rows_out,
    }
