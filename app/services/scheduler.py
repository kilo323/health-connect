from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
import asyncio
import logging
import logging.handlers
import os
from datetime import datetime, timezone

from ..database import async_session_factory
from ..models.settings import ScheduleConfig, AppSettings
from ..models.health_data import DocumentStatus
from ..services.google_health import GoogleHealthService, NOT_LINKED_MARKER
from ..services.nextcloud import NextcloudService
from ..services.metric_normalizer import metric_normalizer

logger = logging.getLogger(__name__)

# Dedicated file logger for sync operations so we can diagnose hangs even when
# the main uvicorn logger only prints INFO/ERROR to the console.
os.makedirs("logs", exist_ok=True)
_sync_file_handler = logging.handlers.RotatingFileHandler(
    "logs/sync.log", maxBytes=2_000_000, backupCount=3
)
_sync_file_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
_sync_logger = logging.getLogger("health_connect.sync")
_sync_logger.setLevel(logging.DEBUG)
if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in _sync_logger.handlers):
    _sync_logger.addHandler(_sync_file_handler)

def _sync_log(msg: str):
    _sync_logger.info(msg)
    logger.info(msg)

# Internal data type names synced from the Google Health API v4.
# (blood_pressure, bmr, speed have no v4 equivalent and were dropped.)
SYNC_DATA_TYPES = [
    "steps", "heart_rate", "sleep", "weight", "distance",
    "blood_glucose", "body_temperature",
    "oxygen_saturation", "body_fat_percentage", "height",
    "heart_minutes", "move_minutes", "calories",
]

UNIT_MAP = {
    "steps": "count",
    "heart_rate": "bpm",
    "sleep": "minutes",
    "weight": "kg",
    "distance": "meters",
    "blood_glucose": "mg/dL",
    "body_temperature": "°C",
    "oxygen_saturation": "%",
    "body_fat_percentage": "%",
    "height": "meters",
    "heart_minutes": "minutes",
    "move_minutes": "minutes",
    "calories": "kcal",
}

# Metrics that only have a dailyRollUp endpoint in Google's API (no raw list
# endpoint) — they are ALWAYS fetched as a daily aggregate, even within the
# cutoff window. total-calories is the one such type we sync.
DAILY_ONLY_DATA_TYPES = {"calories"}

# Reverse of GoogleHealthService.DATA_TYPE_MAP (v4 kebab-case -> internal name)
API_TYPE_TO_INTERNAL = {
    "steps": "steps",
    "heart-rate": "heart_rate",
    "sleep": "sleep",
    "weight": "weight",
    "blood-glucose": "blood_glucose",
    "core-body-temperature": "body_temperature",
    "distance": "distance",
    "total-calories": "calories",
    "daily-oxygen-saturation": "oxygen_saturation",
    "body-fat": "body_fat_percentage",
    "height": "height",
    "active-zone-minutes": "heart_minutes",
    "active-minutes": "move_minutes",
}

# ── Metric rollup configuration ──────────────────────────────────────────────
# Rollup-capable metrics fetch historical data (older than a per-metric cutoff)
# as a single daily-aggregate row, and recent data (within the cutoff) as raw
# granular points. Verified rollup value fields live in
# HealthSyncScheduler._extract_daily_rollup_point. Unverified vitals
# (heart_minutes, blood_glucose, body_temperature) are excluded until confirmed
# — see docs/TODO.md.
ROLLUP_CAPABLE = {
    "steps", "distance", "calories", "heart_rate", "move_minutes",
    "weight", "body_fat_percentage",
}

# Sensible defaults. This is a GLOBAL (admin) setting with no per-user override;
# stored in AppSettings under key "sync_rollup_config". See routers/admin.py.
DEFAULT_ROLLUP_CONFIG = {
    "default_cutoff_days": 7,
    "metrics": {
        "steps": {"enabled": True, "cutoff_days": 7},
        "distance": {"enabled": True, "cutoff_days": 7},
        "calories": {"enabled": True, "cutoff_days": 7},
        "heart_rate": {"enabled": True, "cutoff_days": 7},
        "move_minutes": {"enabled": True, "cutoff_days": 7},
        "weight": {"enabled": False, "cutoff_days": 7},          # low volume — keep raw
        "body_fat_percentage": {"enabled": False, "cutoff_days": 7},
    },
}

# AppSettings key holding the global rollup configuration (JSON).
ROLLUP_CONFIG_KEY = "sync_rollup_config"


def _merge_rollup_config(stored_rollup: dict) -> dict:
    """Merge a stored rollup dict over the defaults."""
    import copy
    cfg = copy.deepcopy(DEFAULT_ROLLUP_CONFIG)
    stored = stored_rollup or {}
    if "default_cutoff_days" in stored:
        cfg["default_cutoff_days"] = stored["default_cutoff_days"]
    for name, m in stored.get("metrics", {}).items():
        if name in cfg["metrics"]:
            cfg["metrics"][name].update(m)
        else:
            cfg["metrics"][name] = m
    return cfg


async def get_rollup_config() -> dict:
    """Load the global rollup config from AppSettings, merged over defaults."""
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == ROLLUP_CONFIG_KEY)
            )
            row = result.scalar_one_or_none()
            if row:
                import json as _json
                return _merge_rollup_config(_json.loads(row.value))
    except Exception as e:
        logger.warning(f"Failed to load rollup config, using defaults: {e}")
    return _merge_rollup_config({})


def _metric_rollup_cutoff(data_type: str, rollup_cfg: dict):
    """Return (enabled, cutoff_days) for a metric from the merged rollup config."""
    m = rollup_cfg.get("metrics", {}).get(data_type)
    if not m:
        return False, rollup_cfg.get("default_cutoff_days", 7)
    cutoff = m.get("cutoff_days", rollup_cfg.get("default_cutoff_days", 7))
    return bool(m.get("enabled")), cutoff


async def _upsert_metric(
    db: AsyncSession,
    *,
    user_id: int,
    metric_type: str,
    value: float,
    unit: str,
    recorded_at,
    source: str,
    definition_id,
    granularity: str,
) -> bool:
    """Insert a HealthMetric row, updating the existing row for the same logical
    data point (user_id, metric_type, recorded_at, source) if one exists.

    Returns True when a NEW row was created, False when an existing row was
    refreshed. Upserting keeps re-synced values current (raw intervals can be
    corrected by later syncs) instead of silently keeping a stale duplicate.
    """
    from ..models.health_data import HealthMetric

    result = await db.execute(
        select(HealthMetric).where(
            HealthMetric.user_id == user_id,
            HealthMetric.metric_type == metric_type,
            HealthMetric.recorded_at == recorded_at,
            HealthMetric.source == source,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.value = value
        existing.unit = unit
        existing.granularity = granularity
        if definition_id is not None:
            existing.definition_id = definition_id
        await db.commit()
        return False

    db.add(HealthMetric(
        user_id=user_id,
        metric_type=metric_type,
        value=value,
        unit=unit,
        recorded_at=recorded_at,
        source=source,
        definition_id=definition_id,
        granularity=granularity,
    ))
    await db.commit()
    return True


async def sync_health_data(user_id: int, start_time, end_time, data_types: list[str] | None = None, settings_data: dict | None = None) -> int:
    """Fetch health data from the Google Health API and persist HealthMetric rows.

    Shared by the polling scheduler and the webhook handler. Idempotency relies
    on the (user_id, metric_type, recorded_at, source) unique constraint on
    HealthMetric — duplicate inserts are skipped on IntegrityError.

    For rollup-enabled metrics, data older than the metric's cutoff is fetched as
    a daily aggregate (one row/day) and recent data as raw granular points.
    Daily-only metrics (calories) always roll up. Rollup config is the global
    admin setting (no per-user override); `settings_data` is accepted for
    backward compatibility but ignored.

    Returns the number of new rows saved.
    """
    from ..models.health_data import HealthMetric
    from datetime import timedelta

    google_service = GoogleHealthService()
    types_to_sync = data_types or SYNC_DATA_TYPES
    rollup_cfg = await get_rollup_config()
    total_saved = 0

    account_not_linked = False
    for data_type in types_to_sync:
        if account_not_linked:
            break  # every type fails identically once the account is unlinked
        try:
            rollup_enabled, cutoff_days = _metric_rollup_cutoff(data_type, rollup_cfg)
            is_rollup = rollup_enabled and data_type in ROLLUP_CAPABLE

            # Build the list of (granularity, window_start, window_end) to fetch.
            windows: list[tuple[str, object, object]] = []
            if data_type in DAILY_ONLY_DATA_TYPES:
                # No raw endpoint exists (e.g. total-calories) -> always daily.
                if rollup_enabled:
                    windows.append(("daily", start_time, end_time))
            elif is_rollup and cutoff_days > 0:
                cutoff = end_time - timedelta(days=cutoff_days)
                if start_time < cutoff:
                    windows.append(("daily", start_time, min(cutoff, end_time)))
                if end_time > cutoff:
                    windows.append(("raw", max(cutoff, start_time), end_time))
            elif is_rollup and cutoff_days == 0:
                windows.append(("daily", start_time, end_time))  # roll up everything
            else:
                windows.append(("raw", start_time, end_time))

            async with async_session_factory() as db:
                saved_count = 0
                for granularity, w_start, w_end in windows:
                    if granularity == "daily":
                        points = await google_service.fetch_daily_rollup(
                            user_id=user_id, data_type=data_type,
                            start_time=w_start, end_time=w_end,
                        )
                    else:
                        points = await google_service.fetch_health_data(
                            user_id=user_id, data_type=data_type,
                            start_time=w_start, end_time=w_end,
                        )
                    if not points:
                        continue

                    # Each point may yield multiple rows (e.g. heart_rate avg/min/max).
                    # For heart_rate raw windows, aggregate samples into daily
                    # avg/min/max rows (A2) so recent days match the rollup shape.
                    window_rows: list[tuple[str, float, datetime]] = []
                    for point in points:
                        try:
                            window_rows.extend(
                                HealthSyncScheduler._extract_rows(data_type, point, granularity)
                            )
                        except Exception:
                            continue
                    if granularity == "raw":
                        window_rows = HealthSyncScheduler._aggregate_daily(window_rows, data_type)

                    for metric_label, value, recorded_at in window_rows:
                        try:
                            unit = UNIT_MAP.get(data_type, "unknown")
                            norm = await metric_normalizer.normalize(db, metric_label, unit)
                            definition_id = norm.definition.id if norm.definition else None
                            metric_type = norm.canonical_name if norm.definition else metric_label
                            # Daily rollups and pre-aggregated rows (heart_rate A2)
                            # are stored as authoritative daily values; everything
                            # else is a granular point/interval row.
                            row_granularity = "daily" if (
                                granularity == "daily" or data_type == "heart_rate"
                            ) else "raw"
                            is_new = await _upsert_metric(
                                db,
                                user_id=user_id,
                                metric_type=metric_type,
                                value=value,
                                unit=unit,
                                recorded_at=recorded_at,
                                source="google_health_connect",
                                definition_id=definition_id,
                                granularity=row_granularity,
                            )
                            if is_new:
                                saved_count += 1
                            # A daily aggregate supersedes that day's granular rows
                            # (e.g. a cutoff change re-synced the day as rollup).
                            if row_granularity == "daily":
                                await db.execute(
                                    delete(HealthMetric).where(
                                        HealthMetric.user_id == user_id,
                                        HealthMetric.metric_type == metric_type,
                                        HealthMetric.source == "google_health_connect",
                                        HealthMetric.granularity == "raw",
                                        func.date(HealthMetric.recorded_at) == recorded_at.date(),
                                    )
                                )
                                await db.commit()
                        except IntegrityError:
                            await db.rollback()  # duplicate — already synced
                        except Exception:
                            await db.rollback()
                            continue

                total_saved += saved_count
                if saved_count > 0:
                    logger.info(f"Saved {saved_count} {data_type} records for user {user_id}")

        except Exception as e:
            if NOT_LINKED_MARKER in str(e):
                account_not_linked = True
                logger.warning(
                    f"User {user_id}'s Google account is not linked to Google Health. "
                    "To link it: open the Fitbit mobile app, sign in with this Google account, "
                    "and complete setup. Skipping remaining data types for this user."
                )
            else:
                logger.warning(f"Error syncing {data_type} for user {user_id}: {e}")
            continue

    return total_saved


class HealthSyncScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self._sync_in_progress = False

    @property
    def sync_in_progress(self) -> bool:
        return self._sync_in_progress

    async def start(self):
        """Start the scheduler if enabled"""
        try:
            # Check if sync is enabled
            config = None
            async with async_session_factory() as db:
                result = await db.execute(select(ScheduleConfig))
                config = result.scalar_one_or_none()
            
            if not config or not config.is_enabled:
                logger.info("Health sync scheduler disabled")
                return

            # Add the scheduled job
            self.scheduler.add_job(
                self._run_sync,
                'cron',
                expression=config.cron_expression,
                id='health_sync_job',
                replace_existing=True,
                max_instances=1
            )

            self.scheduler.start()
            self.is_running = True
            logger.info(f"Health sync scheduler started with cron: {config.cron_expression}")

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

    def is_running(self) -> bool:
        """Return whether the scheduled scheduler is currently running."""
        return self.scheduler.running

    async def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("Health sync scheduler stopped")

    @staticmethod
    def _extract_rows(data_type: str, point: dict, granularity: str) -> list[tuple[str, float, datetime]]:
        """Return a list of (metric_label, value, recorded_at) rows for one point.

        A single point can produce multiple rows (e.g. a heart-rate daily rollup
        yields avg/min/max). `granularity` is "daily" (dailyRollUp aggregate) or
        "raw" (individual point). Returns [] when the point carries no usable value.
        """
        if granularity == "daily":
            return HealthSyncScheduler._extract_daily_rollup_rows(data_type, point)

        # Raw move/heart minutes carry a per-level/per-zone breakdown — emit one
        # row per level/zone instead of a single summed value.
        if data_type in ("move_minutes", "heart_minutes"):
            return HealthSyncScheduler._extract_raw_minutes_rows(data_type, point)

        extracted = HealthSyncScheduler._extract_v4_point(data_type, point)
        if not extracted:
            return []
        value, recorded_at = extracted
        return [(data_type, value, recorded_at)]

    @staticmethod
    def _extract_raw_minutes_rows(data_type: str, point: dict) -> list[tuple[str, float, datetime]]:
        """Extract per-level (move_minutes) or per-zone (heart_minutes) rows from a raw point.

        Raw move_minutes: activeMinutes.activeMinutesByActivityLevel[] ->
          {activityLevel, activeMinutes}. Raw heart_minutes: activeZoneMinutes ->
          {heartRateZone, activeZoneMinutes}. Timestamp from interval.startTime.
        """
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        union_field = "activeMinutes" if data_type == "move_minutes" else "activeZoneMinutes"
        payload = point.get(union_field) or {}
        ts_str = payload.get("interval", {}).get("startTime")
        if not ts_str:
            return []
        try:
            recorded_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return []
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        rows: list[tuple[str, float, datetime]] = []
        if data_type == "move_minutes":
            for e in payload.get("activeMinutesByActivityLevel", []):
                v = f(e.get("activeMinutes"))
                level = (e.get("activityLevel") or "").strip().title()
                if v is not None and level:
                    rows.append((f"Active Minutes ({level})", v, recorded_at))
        else:  # heart_minutes
            v = f(payload.get("activeZoneMinutes"))
            zone = (payload.get("heartRateZone") or "").replace("_", " ").strip().title()
            if v is not None and zone:
                rows.append((f"Heart Minutes ({zone})", v, recorded_at))
        return rows

    @staticmethod
    def _extract_daily_rollup_rows(data_type: str, point: dict) -> list[tuple[str, float, datetime]]:
        """Extract rows from a dailyRollUp aggregate point.

        Rollup shape (verified live 2026-08-14): the union payload holds the
        aggregate field(s) and the civil date sits at the top level of the point:
          {"civilStartTime": {"date": {"year": 2026, "month": 8, "day": 13}, ...},
           "steps": {"countSum": "10063"}}
          {"...": ..., "heartRate": {"beatsPerMinuteAvg": 95.1, "beatsPerMinuteMin": 69,
                                      "beatsPerMinuteMax": 128}}

        Returns a list of (metric_label, value, recorded_at); recorded_at is the
        civil date at UTC midnight.
        """
        date_info = point.get("civilStartTime", {}).get("date", {})
        if not date_info:
            return []
        recorded_at = datetime(
            date_info.get("year", 1970), date_info.get("month", 1), date_info.get("day", 1),
            tzinfo=timezone.utc,
        )

        def f(x):
            try:
                return float(x)  # int64 fields arrive as JSON strings
            except (TypeError, ValueError):
                return None

        rows: list[tuple[str, float, datetime]] = []

        if data_type == "steps":
            v = f(point.get("steps", {}).get("countSum"))
            if v is not None:
                rows.append(("steps", v, recorded_at))
        elif data_type == "distance":
            v = f(point.get("distance", {}).get("millimetersSum"))
            if v is not None:
                rows.append(("distance", v / 1000.0, recorded_at))  # mm -> meters
        elif data_type == "calories":
            v = f(point.get("totalCalories", {}).get("kcalSum"))
            if v is not None:
                rows.append(("calories", v, recorded_at))
        elif data_type == "weight":
            v = f(point.get("weight", {}).get("weightGramsAvg"))
            if v is not None:
                rows.append(("weight", v / 1000.0, recorded_at))  # g -> kg
        elif data_type == "body_fat_percentage":
            v = f(point.get("bodyFat", {}).get("bodyFatPercentageAvg"))
            if v is not None:
                rows.append(("body_fat_percentage", v, recorded_at))
        elif data_type == "heart_rate":
            hr = point.get("heartRate", {})
            avg = f(hr.get("beatsPerMinuteAvg"))
            mn = f(hr.get("beatsPerMinuteMin"))
            mx = f(hr.get("beatsPerMinuteMax"))
            if avg is not None:
                rows.append(("Heart Rate (Avg)", avg, recorded_at))
            if mn is not None:
                rows.append(("Heart Rate (Min)", mn, recorded_at))
            if mx is not None:
                rows.append(("Heart Rate (Max)", mx, recorded_at))
        elif data_type == "move_minutes":
            # One row per activity level (LIGHT/MODERATE/VIGOROUS) for a breakdown.
            for e in point.get("activeMinutes", {}).get("activeMinutesRollupByActivityLevel", []):
                v = f(e.get("activeMinutesSum"))
                level = (e.get("activityLevel") or "").strip().title()
                if v is not None and level:
                    rows.append((f"Active Minutes ({level})", v, recorded_at))
        elif data_type == "heart_minutes":
            # One row per heart-rate zone (Fat Burn/Cardio/Peak). UNVERIFIED field
            # name (no zone-minute data to probe) — per docs the rollup groups by
            # heartRateZone analogous to active-minutes. See docs/TODO.md.
            azm = point.get("activeZoneMinutes", {})
            entries = azm.get("activeZoneMinutesRollupByHeartRateZone") or []
            for e in entries:
                v = f(e.get("activeZoneMinutesSum") or e.get("activeZoneMinutes"))
                zone = (e.get("heartRateZone") or "").replace("_", " ").strip().title()
                if v is not None and zone:
                    rows.append((f"Heart Minutes ({zone})", v, recorded_at))
        return rows

    @staticmethod
    def _aggregate_daily(rows: list[tuple[str, float, datetime]], data_type: str) -> list[tuple[str, float, datetime]]:
        """Aggregate raw per-point rows into daily summary rows (A2).

        Only heart_rate uses this today: group samples by civil date and emit
        avg/min/max rows at UTC midnight. Other types pass through unchanged.
        """
        if data_type != "heart_rate" or not rows:
            return rows
        from collections import defaultdict
        by_day: dict = defaultdict(list)
        for _label, value, recorded_at in rows:
            by_day[recorded_at.date()].append(value)
        out: list[tuple[str, float, datetime]] = []
        for day, vals in by_day.items():
            midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
            out.append(("Heart Rate (Avg)", sum(vals) / len(vals), midnight))
            out.append(("Heart Rate (Min)", float(min(vals)), midnight))
            out.append(("Heart Rate (Max)", float(max(vals)), midnight))
        return out

    @staticmethod
    def _extract_v4_point(data_type: str, point: dict):
        """Extract (value, recorded_at) from a Google Health API v4 DataPoint.

        Returns None when the point carries no usable value (e.g. a steps
        "true zero" record, which omits the count field).

        Notes on v4 units (all conversions applied here):
          - int64 fields are serialized as JSON strings ("2038")
          - distance is millimeters, weight is grams, height is millimeters
          - sleep duration is computed as interval endTime - startTime

        Daily-rollup points are handled separately by `_extract_daily_rollup_rows`
        (dispatched via `_extract_rows`); this method handles only raw points.
        """
        # Internal name -> v4 DataPoint union field (camelCase)
        union_field_map = {
            "steps": "steps",
            "heart_rate": "heartRate",
            "sleep": "sleep",
            "weight": "weight",
            "distance": "distance",
            "calories": "totalCalories",
            "blood_glucose": "bloodGlucose",
            "body_temperature": "coreBodyTemperature",
            "oxygen_saturation": "dailyOxygenSaturation",
            "body_fat_percentage": "bodyFat",
            "height": "height",
            "heart_minutes": "activeZoneMinutes",
            "move_minutes": "activeMinutes",
        }
        union_field = union_field_map.get(data_type)
        if not union_field:
            return None

        payload = point.get(union_field)
        if not payload:
            return None

        interval = payload.get("interval", {})
        sample_time = payload.get("sampleTime", {})

        # Session types (sleep): duration in minutes from interval bounds
        if data_type == "sleep":
            start_str = interval.get("startTime")
            end_str = interval.get("endTime")
            if not start_str or not end_str:
                return None
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except ValueError:
                return None
            return (end_dt - start_dt).total_seconds() / 60, start_dt

        # Interval types: timestamp from interval.startTime
        if data_type == "steps":
            if "count" not in payload:  # true zero: device worn, no steps recorded
                return None
            value = float(payload["count"])
            ts_str = interval.get("startTime")
        elif data_type == "distance":
            if "millimeters" not in payload:
                return None
            value = float(payload["millimeters"]) / 1000.0  # mm -> meters
            ts_str = interval.get("startTime")
        elif data_type == "heart_minutes":
            if "activeZoneMinutes" not in payload:
                return None
            value = float(payload["activeZoneMinutes"])
            ts_str = interval.get("startTime")
        elif data_type == "move_minutes":
            entries = payload.get("activeMinutesByActivityLevel", [])
            value = sum(float(e.get("activeMinutes", 0)) for e in entries)
            ts_str = interval.get("startTime")
        # Daily types: timestamp from civil date (UTC midnight)
        elif data_type == "oxygen_saturation":
            if "averagePercentage" not in payload:
                return None
            value = float(payload["averagePercentage"])
            date_info = payload.get("date", {})
            if not date_info:
                return None
            ts_str = None
            recorded_at = datetime(
                date_info.get("year", 1970), date_info.get("month", 1), date_info.get("day", 1),
                tzinfo=timezone.utc,
            )
        # Sample types: timestamp from sampleTime.physicalTime
        elif data_type == "heart_rate":
            if "beatsPerMinute" not in payload:
                return None
            value = float(payload["beatsPerMinute"])
            ts_str = sample_time.get("physicalTime")
        elif data_type == "weight":
            if "weightGrams" not in payload:
                return None
            value = float(payload["weightGrams"]) / 1000.0  # g -> kg
            ts_str = sample_time.get("physicalTime")
        elif data_type == "blood_glucose":
            if "bloodGlucoseMilligramsPerDeciliter" not in payload:
                return None
            value = float(payload["bloodGlucoseMilligramsPerDeciliter"])
            ts_str = sample_time.get("physicalTime")
        elif data_type == "body_temperature":
            if "temperatureCelsius" not in payload:
                return None
            value = float(payload["temperatureCelsius"])
            ts_str = sample_time.get("physicalTime")
        elif data_type == "body_fat_percentage":
            if "percentage" not in payload:
                return None
            value = float(payload["percentage"])
            ts_str = sample_time.get("physicalTime")
        elif data_type == "height":
            if "heightMillimeters" not in payload:
                return None
            value = float(payload["heightMillimeters"]) / 1000.0  # mm -> meters
            ts_str = sample_time.get("physicalTime")
        else:
            return None

        if data_type != "oxygen_saturation":
            if not ts_str:
                return None
            try:
                recorded_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                return None

        return value, recorded_at

    async def _run_sync(self):
        """Main sync job - fetch data from Google Health API and store locally"""
        if self._sync_in_progress:
            _sync_log("Sync already in progress, skipping duplicate run")
            return

        self._sync_in_progress = True
        _sync_log("Starting health sync job...")
        job_started_at = datetime.now(timezone.utc)

        try:
            # ── Phase 1: Discover users with Google tokens ───────────────────────
            _sync_log("Phase 1: discovering users with Google tokens")
            async with async_session_factory() as db:
                result = await db.execute(
                    select(AppSettings).where(
                        AppSettings.key.like("google_health_tokens_%")
                    )
                )
                token_rows = result.scalars().all()

            # Extract user IDs from keys like "google_health_tokens_1"
            user_ids = []
            for row in token_rows:
                try:
                    user_id = int(row.key.split("_")[-1])
                    user_ids.append(user_id)
                except (ValueError, IndexError):
                    continue

            _sync_log(f"Found {len(user_ids)} user(s) with Google tokens to sync")

            if not user_ids:
                _sync_log("No users with Google tokens found, finishing sync job")
                return

            # ── Phase 2: Sync Google Health data for each user ───────────────────
            _sync_log("Phase 2: starting Google Health sync")
            for user_id in user_ids:
                # Load per-user sync settings
                sync_days_back = 7  # default
                last_google_sync = None
                settings_data: dict = {}
                try:
                    async with async_session_factory() as db:
                        settings_result = await db.execute(
                            select(AppSettings).where(
                                AppSettings.key == f"sync_settings_{user_id}"
                            )
                        )
                        settings_row = settings_result.scalar_one_or_none()
                        if settings_row:
                            import json as _json
                            settings_data = _json.loads(settings_row.value)
                            sync_days_back = settings_data.get("sync_days_back", 7)
                            last_str = settings_data.get("last_google_sync")
                            if last_str:
                                last_google_sync = datetime.fromisoformat(last_str)
                except Exception:
                    settings_data = {}

                # Compute time window: from last sync minus overlap, through now
                from datetime import timedelta as _timedelta
                end_time = datetime.now(timezone.utc)
                if last_google_sync:
                    # Start from last sync minus a small overlap (1 day) to catch late-arriving data
                    start_time = last_google_sync - _timedelta(days=1)
                else:
                    # First sync: go back sync_days_back days
                    start_time = end_time - _timedelta(days=sync_days_back)

                _sync_log(f"Syncing user {user_id}: {start_time.isoformat()} to {end_time.isoformat()} (days_back={sync_days_back})")

                await sync_health_data(user_id, start_time, end_time, settings_data=settings_data)
                _sync_log(f"Finished Google Health sync for user {user_id}")

                # Update last_google_sync for this user
                try:
                    async with async_session_factory() as db:
                        import json as _json
                        settings_key = f"sync_settings_{user_id}"
                        result = await db.execute(
                            select(AppSettings).where(AppSettings.key == settings_key)
                        )
                        existing = result.scalar_one_or_none()
                        data = {}
                        if existing:
                            try:
                                data = _json.loads(existing.value)
                            except (json.JSONDecodeError, TypeError):
                                pass
                        data["last_google_sync"] = end_time.isoformat()
                        value = _json.dumps(data)
                        if existing:
                            existing.value = value
                        else:
                            db.add(AppSettings(key=settings_key, value=value, description="User sync settings"))
                        await db.commit()
                        logger.info(f"Updated last_google_sync for user {user_id}")
                except Exception as e:
                    logger.warning(f"Failed to update last_google_sync for user {user_id}: {e}")

            # ── Phase 3: Scan Nextcloud documents for new files ────────────────
            # Find ALL users with Nextcloud configured (not just Google token users)
            _sync_log("Phase 3: starting Nextcloud document scan")
            try:
                # Cap the whole Nextcloud scan at 5 minutes so a slow/unreachable
                # server can't make the sync appear to hang forever.
                await asyncio.wait_for(self._scan_nextcloud_documents(), timeout=300)
                _sync_log("Phase 3: Nextcloud document scan finished")
            except asyncio.TimeoutError:
                _sync_log("Nextcloud document scan timed out after 5 minutes")
                logger.warning("Nextcloud document scan timed out after 5 minutes")
            except Exception as e:
                _sync_log(f"Nextcloud document scan failed: {e}")
                logger.warning(f"Nextcloud document scan failed: {e}")

            # ── Phase 4: Update last run time ──────────────────────────────────
            _sync_log("Phase 4: updating scheduler last_run time")
            async with async_session_factory() as db:
                from sqlalchemy import text
                await db.execute(
                    text("UPDATE schedule_configs SET last_run = :now WHERE is_enabled = true"),
                    {"now": datetime.now(timezone.utc)}
                )
                await db.commit()

            elapsed = (datetime.now(timezone.utc) - job_started_at).total_seconds()
            _sync_log(f"Health sync job completed in {elapsed:.1f}s")
            logger.info(f"Health sync job completed in {elapsed:.1f}s")

        except asyncio.CancelledError:
            _sync_log("Health sync job was cancelled")
            logger.warning("Health sync job was cancelled")
            raise
        except Exception as e:
            _sync_log(f"Health sync job failed: {e}")
            logger.error(f"Health sync job failed: {e}", exc_info=True)
        finally:
            self._sync_in_progress = False

    async def _scan_nextcloud_documents(self):
        """Scan Nextcloud Unprocessed folders and import documents.

        Isolated into its own method so the main sync job can apply a timeout
        and avoid having a slow/unreachable Nextcloud server make the whole
        sync appear to hang forever.
        """
        nc_user_ids = []
        try:
            async with async_session_factory() as db:
                from sqlalchemy import select as sa_select
                nc_result = await db.execute(
                    sa_select(AppSettings).where(
                        AppSettings.key.like("nextcloud_config_%")
                    )
                )
                for row in nc_result.scalars().all():
                    try:
                        uid = int(row.key.split("_")[-1])
                        nc_user_ids.append(uid)
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            _sync_log(f"Failed to discover Nextcloud users: {e}")
            return

        if not nc_user_ids:
            _sync_log("No users with Nextcloud configured, skipping document scan")
            return

        _sync_log(f"Found {len(nc_user_ids)} user(s) with Nextcloud configured for document scan")

        from ..services.nextcloud import NextcloudService
        from ..models.health_data import Document
        import os
        import uuid

        nc = NextcloudService()

        for user_id in nc_user_ids:
            try:
                await nc.ensure_folders(user_id)
                files = await nc.list_files(user_id, "Unprocessed")
                if not files:
                    continue

                logger.info(f"Found {len(files)} document(s) in Nextcloud Unprocessed for user {user_id}")

                async with async_session_factory() as db:
                    for file_info in files:
                        try:
                            filename = file_info["filename"]
                            file_url = file_info["url"]

                            ext = os.path.splitext(filename)[1].lower()
                            if ext not in ('.pdf', '.jpg', '.jpeg', '.png', '.txt', '.csv', '.json', '.xml'):
                                logger.debug(f"Skipping non-document file: {filename}")
                                continue

                            existing = await db.execute(
                                select(Document).where(
                                    Document.user_id == user_id,
                                    Document.filename == filename
                                )
                            )
                            if existing.scalar_one_or_none():
                                continue

                            content = await nc.download_file(user_id, file_url)
                            if not content:
                                continue

                            os.makedirs("uploads", exist_ok=True)
                            local_path = f"uploads/{uuid.uuid4()}_{filename}"
                            with open(local_path, "wb") as f:
                                f.write(content)

                            file_type_map = {'.pdf': 'pdf', '.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.txt': 'text', '.csv': 'text'}
                            doc = Document(
                                user_id=user_id,
                                filename=filename,
                                file_path=local_path,
                                file_type=file_type_map.get(ext, 'unknown'),
                                source="nextcloud",
                                size_bytes=len(content),
                                status=DocumentStatus.UNPROCESSED,
                            )
                            db.add(doc)
                            await db.commit()
                            await db.refresh(doc)

                            await nc.move_file(user_id, file_url, "Processed")
                            logger.info(f"Processed Nextcloud document: {filename} for user {user_id}")

                        except Exception as e:
                            logger.warning(f"Error processing Nextcloud file: {e}")
                            continue

            except Exception as e:
                logger.warning(f"Error scanning Nextcloud for user {user_id}: {e}")
                continue

    def update_schedule(self, cron_expression: str):
        """Update the schedule for the running scheduler"""
        if self.scheduler.running and self.is_running:
            # Remove existing job
            self.scheduler.remove_job('health_sync_job')
            
            # Add new job with updated cron expression
            self.scheduler.add_job(
                self._run_sync,
                'cron',
                expression=cron_expression,
                id='health_sync_job',
                replace_existing=True,
                max_instances=1
            )
            logger.info(f"Updated health sync schedule to: {cron_expression}")


# Global scheduler instance
scheduler = HealthSyncScheduler()
