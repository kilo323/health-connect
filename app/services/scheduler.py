from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
import asyncio
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from typing import NamedTuple

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
    # Added 2026-10-02 (verified available in Google Health API v4):
    #   - HRV, raw SpO2, and VO2 max are sample types (no dailyRollUp).
    #   - exercise is a session type (no rollup, client-side time filter).
    "heart_rate_variability", "oxygen_saturation_raw", "vo2_max", "exercise",
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
    # New types. Each maps to the canonical unit of the definition it resolves
    # to, so display conversion (user_unit_preferences) has a valid pair.
    "heart_rate_variability": "ms",
    "oxygen_saturation_raw": "%",
    "vo2_max": "ml/kg/min",
    # exercise is a session type: its sub-metrics each carry their own unit, so
    # no single entry here (the extractor supplies per-row units for it).
}

# Metrics that only have a dailyRollUp endpoint in Google's API (no raw list
# endpoint) — they are ALWAYS fetched as a daily aggregate, even within the
# cutoff window. total-calories is the one such type we sync.
DAILY_ONLY_DATA_TYPES = {"calories"}

# Rows buffered before a COMMIT during sync. One transaction per row cost
# ~6.5 ms (fsync under the rollback journal) and made a 30-day backfill take
# longer than a sync process survives; batching measured ~13x faster while
# keeping each row isolated in a SAVEPOINT.
BATCH_COMMIT_ROWS = 500

# API points extracted (and written) per slice. Bounds peak memory for very large
# raw windows — a 30-day heart-rate window is ~1M points — and caps how much work
# a killed run can lose.
POINT_SLICE = 5000

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
    "heart-rate-variability": "heart_rate_variability",
    "oxygen-saturation": "oxygen_saturation_raw",
    "vo2-max": "vo2_max",
    "exercise": "exercise",
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


async def _write_sync_cursors(
    user_id: int,
    type_cursors: dict[str, datetime],
    overall: datetime,
) -> None:
    """Persist per-data-type sync cursors for a user.

    `type_cursors` is the authoritative record of how far each data type has been
    synced. `last_google_sync` is kept as the aggregate (the oldest type cursor) so
    the existing admin backfill endpoint and any older readers keep working.
    """
    import json as _json

    try:
        async with async_session_factory() as db:
            settings_key = f"sync_settings_{user_id}"
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == settings_key)
            )
            existing = result.scalar_one_or_none()
            data: dict = {}
            if existing:
                try:
                    data = _json.loads(existing.value)
                except (json.JSONDecodeError, TypeError):
                    pass
            data["type_cursors"] = {k: v.isoformat() for k, v in type_cursors.items()}
            data["last_google_sync"] = overall.isoformat()
            value = _json.dumps(data)
            if existing:
                existing.value = value
            else:
                db.add(AppSettings(
                    key=settings_key, value=value, description="User sync settings"))
            await db.commit()
            logger.info(
                f"Updated sync cursors for user {user_id} "
                f"({len(type_cursors)} type(s), overall={overall.isoformat()})"
            )
    except Exception as e:
        logger.warning(f"Failed to update sync cursors for user {user_id}: {e}")


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
) -> None:
    """Write a HealthMetric row, updating the existing row for the same logical
    data point (user_id, metric_type, recorded_at, source) if one exists.

    Upserting keeps re-synced values current (raw intervals can be corrected by
    later syncs) instead of silently keeping a stale duplicate.

    Implemented as a single native ``INSERT ... ON CONFLICT DO UPDATE`` against
    the uq_health_metric_point unique index. The previous read-then-write (SELECT
    + INSERT + SAVEPOINT) cost ~4.5 ms per row, which made a heart-rate backfill
    write-bound; the upsert is one statement and can never raise IntegrityError,
    so rows can share a transaction and be committed in batches
    (see BATCH_COMMIT_ROWS).

    The row is not committed here — the caller owns the transaction.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from ..models.health_data import HealthMetric

    stmt = sqlite_insert(HealthMetric).values(
        user_id=user_id,
        metric_type=metric_type,
        value=value,
        unit=unit,
        recorded_at=recorded_at,
        source=source,
        definition_id=definition_id,
        granularity=granularity,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            HealthMetric.user_id,
            HealthMetric.metric_type,
            HealthMetric.recorded_at,
            HealthMetric.source,
        ],
        set_={
            "value": value,
            "unit": unit,
            "granularity": granularity,
            # Keep a previously resolved definition when this pass has none.
            "definition_id": func.coalesce(definition_id, HealthMetric.definition_id),
        },
    )
    await db.execute(stmt)


class SyncOutcome(NamedTuple):
    """Result of one sync_health_data run.

    `failed_from` is the earliest window start whose fetch raised, or None when
    every data type succeeded. The scheduler uses it to rewind the sync cursor so
    a failed window is re-fetched instead of being skipped forever.

    `completed` lists the data types that ran to completion. The scheduler
    persists a cursor for each one as it finishes, so a run that is killed part
    way through keeps the progress of the types that already succeeded instead
    of restarting the whole window from scratch on the next run.
    """
    saved: int
    failed_from: datetime | None = None
    completed: list[str] = []


async def sync_health_data(
    user_id: int,
    start_time,
    end_time,
    data_types: list[str] | None = None,
    settings_data: dict | None = None,
    type_cursors: dict | None = None,
    on_type_complete=None,
) -> SyncOutcome:
    """Fetch health data from the Google Health API and persist HealthMetric rows.

    Shared by the polling scheduler and the webhook handler. Idempotency relies
    on the (user_id, metric_type, recorded_at, source) unique constraint on
    HealthMetric — each row is written with INSERT ... ON CONFLICT DO UPDATE, so
    re-syncing refreshes a point instead of duplicating it.

    For rollup-enabled metrics, data older than the metric's cutoff is fetched as
    a daily aggregate (one row/day) and recent data as raw granular points.
    Daily-only metrics (calories) always roll up. Rollup config is the global
    admin setting (no per-user override); `settings_data` is accepted for
    backward compatibility but ignored.

    `type_cursors` maps a data type to the last datetime successfully synced for
    it. Each type then starts from its own cursor (minus a 1-day overlap) rather
    than the shared `start_time`, so a backfill advances one type at a time and
    survives interruption. `on_type_complete` is awaited with the data type name
    after that type finishes, letting the caller persist progress immediately.

    Returns a SyncOutcome (rows saved, earliest failed window start, types done).
    """
    from ..models.health_data import HealthMetric
    from datetime import timedelta

    google_service = GoogleHealthService()
    types_to_sync = data_types or SYNC_DATA_TYPES
    rollup_cfg = await get_rollup_config()
    total_saved = 0
    failed_from: datetime | None = None
    completed: list[str] = []
    type_cursors = type_cursors or {}
    # normalize() falls back to a SequenceMatcher fuzzy match when a label is not
    # an exact name/alias hit, which costs milliseconds. A window yields the same
    # handful of labels tens of thousands of times (raw heart rate alone is ~24k
    # rows/day), so resolve each (label, unit) pair once per run.
    norm_cache: dict[tuple[str, str], object] = {}

    account_not_linked = False
    for data_type in types_to_sync:
        if account_not_linked:
            break  # every type fails identically once the account is unlinked
        # Resume this type from its own cursor; never before the caller's floor.
        type_start = start_time
        prior = type_cursors.get(data_type)
        if prior:
            resumed = prior - timedelta(days=1)
            if resumed > type_start:
                type_start = resumed
        try:
            rollup_enabled, cutoff_days = _metric_rollup_cutoff(data_type, rollup_cfg)
            is_rollup = rollup_enabled and data_type in ROLLUP_CAPABLE

            # Build the list of (granularity, window_start, window_end) to fetch.
            windows: list[tuple[str, object, object]] = []
            if data_type in DAILY_ONLY_DATA_TYPES:
                # No raw endpoint exists (e.g. total-calories) -> always daily.
                if rollup_enabled:
                    windows.append(("daily", type_start, end_time))
            elif is_rollup and cutoff_days > 0:
                cutoff = end_time - timedelta(days=cutoff_days)
                if type_start < cutoff:
                    windows.append(("daily", type_start, min(cutoff, end_time)))
                if end_time > cutoff:
                    windows.append(("raw", max(cutoff, type_start), end_time))
            elif is_rollup and cutoff_days == 0:
                windows.append(("daily", type_start, end_time))  # roll up everything
            else:
                windows.append(("raw", type_start, end_time))

            async with async_session_factory() as db:
                saved_count = 0
                uncommitted = 0
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

                    # Stream the window in slices instead of materialising every
                    # row first: a raw heart-rate window can hold ~1M samples, and
                    # holding them all as tuples costs hundreds of MB. Each slice is
                    # written and committed before the next is extracted, so peak
                    # memory stays flat and partial progress is durable.
                    for slice_start in range(0, len(points), POINT_SLICE):
                        window_rows: list[tuple[str, float, datetime, str | None]] = []
                        for point in points[slice_start:slice_start + POINT_SLICE]:
                            try:
                                window_rows.extend(
                                    HealthSyncScheduler._extract_rows(data_type, point, granularity)
                                )
                            except Exception:
                                continue

                        # A daily aggregate supersedes that day's granular rows (e.g. a
                        # cutoff change re-synced the day as a rollup). Collect the
                        # affected (metric, day) pairs and delete once per slice
                        # instead of once per row.
                        superseded: set[tuple[str, object]] = set()

                        for metric_label, value, recorded_at, row_unit in window_rows:
                            try:
                                # row_unit is set only for rows whose unit differs from
                                # the data type's canonical unit (exercise sub-metrics).
                                unit = row_unit if row_unit is not None else UNIT_MAP.get(data_type, "unknown")
                                cache_key = (metric_label, unit)
                                norm = norm_cache.get(cache_key)
                                if norm is None:
                                    norm = await metric_normalizer.normalize(
                                        db, metric_label, unit)
                                    norm_cache[cache_key] = norm
                                definition_id = norm.definition.id if norm.definition else None
                                metric_type = norm.canonical_name if norm.definition else metric_label
                                # Daily rollups are stored as authoritative daily
                                # values; everything else is a granular point row
                                # (including raw heart-rate samples).
                                row_granularity = "daily" if granularity == "daily" else "raw"
                                await _upsert_metric(
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
                                saved_count += 1
                                uncommitted += 1
                                if row_granularity == "daily":
                                    superseded.add((metric_type, recorded_at.date()))
                                if uncommitted >= BATCH_COMMIT_ROWS:
                                    await db.commit()
                                    uncommitted = 0
                            except Exception:
                                logger.debug(
                                    f"Skipping {data_type} row {metric_label}@{recorded_at}",
                                    exc_info=True,
                                )
                                continue

                        for metric_type, day in superseded:
                            # Half-open day range rather than func.date(...) == day:
                            # wrapping the column in date() makes the predicate
                            # non-sargable, so this delete scanned the whole
                            # health_metrics table for every superseded day.
                            day_start = datetime(day.year, day.month, day.day)
                            day_end = day_start + timedelta(days=1)
                            await db.execute(
                                delete(HealthMetric).where(
                                    HealthMetric.user_id == user_id,
                                    HealthMetric.metric_type == metric_type,
                                    HealthMetric.source == "google_health_connect",
                                    HealthMetric.granularity == "raw",
                                    HealthMetric.recorded_at >= day_start,
                                    HealthMetric.recorded_at < day_end,
                                )
                            )
                        # End of slice: make the slice durable so a long window
                        # never loses more than POINT_SLICE points of work.
                        if uncommitted:
                            await db.commit()
                            uncommitted = 0

                total_saved += saved_count
                if saved_count > 0:
                    logger.info(
                        f"Saved {saved_count} {data_type} records for user {user_id}"
                    )

            # This type's whole window is now in the database. Persist its cursor
            # immediately so a run killed later still counts this progress.
            completed.append(data_type)
            if on_type_complete is not None:
                try:
                    await on_type_complete(data_type)
                except Exception:
                    logger.warning(
                        f"Could not persist sync cursor for {data_type} (user {user_id})",
                        exc_info=True,
                    )

        except Exception as e:
            if NOT_LINKED_MARKER in str(e):
                account_not_linked = True
                logger.warning(
                    f"User {user_id}'s Google account is not linked to Google Health. "
                    "To link it: open the Fitbit mobile app, sign in with this Google account, "
                    "and complete setup. Skipping remaining data types for this user."
                )
            else:
                # Remember the earliest window we failed to fetch. The cursor must
                # not advance past it, or this range is never requested again and the
                # data in it is lost permanently (e.g. a sparse metric like body fat,
                # whose only measurement fell inside a window that errored).
                if failed_from is None or type_start < failed_from:
                    failed_from = type_start
                logger.warning(f"Error syncing {data_type} for user {user_id}: {e}")
            continue

    return SyncOutcome(total_saved, failed_from, completed)


class HealthSyncScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._sync_in_progress = False

    @property
    def sync_in_progress(self) -> bool:
        return self._sync_in_progress

    @property
    def is_running(self) -> bool:
        """Whether the underlying APScheduler is actually running.

        A property (not an instance attribute) so it can never shadow itself:
        an ``is_running = False`` assignment in ``__init__`` previously
        replaced this method on the instance, making ``scheduler.is_running()``
        raise ``TypeError: 'bool' object is not callable``.
        """
        return self.scheduler.running

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

            # Add the scheduled job. APScheduler 3.x has no `expression=`
            # kwarg on CronTrigger — a 5-field crontab string must go through
            # CronTrigger.from_crontab(), otherwise add_job() raises TypeError
            # (silently swallowed by the except below, so the scheduler never
            # started even though enable() returned OK).
            self.scheduler.add_job(
                self._run_sync,
                CronTrigger.from_crontab(config.cron_expression),
                id='health_sync_job',
                replace_existing=True,
                max_instances=1
            )

            # Nightly compaction of raw Google samples into daily rollups. Runs
            # separately from the sync job (and at a different hour) so a long
            # sync cannot collide with it, and so a sync failure cannot stop
            # compaction from keeping the table bounded.
            self.scheduler.add_job(
                self._run_compaction,
                'cron',
                hour=3,
                minute=17,
                id='health_compaction_job',
                replace_existing=True,
                max_instances=1
            )

            # Safe to call repeatedly: replace_existing=True updates the cron
            # in place, and the running check below stops us from calling
            # AsyncIOScheduler.start() twice (which raises).
            if self.scheduler.running:
                logger.info(
                    f"Health sync scheduler already running with cron: {config.cron_expression}"
                )
                return
            self.scheduler.start()
            logger.info(f"Health sync scheduler started with cron: {config.cron_expression}")

        except Exception as e:
            # exc_info so a failure here (e.g. a bad cron string or an invalid
            # add_job kwarg) is visible in the logs instead of silently
            # leaving the scheduler stopped while the UI reports enabled.
            logger.exception(f"Failed to start scheduler: {e}")

    async def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            # APScheduler's shutdown() is deferred via call_soon_threadsafe, so
            # `running` stays True until the loop runs that callback. Yield once
            # so is_running is accurate by the time stop() returns — otherwise a
            # quick disable→enable sees "already running" and skips the restart.
            await asyncio.sleep(0)
            logger.info("Health sync scheduler stopped")

    async def _run_compaction(self):
        """Nightly job: compact raw Google samples into daily rollups and prune.

        Separate from `_run_sync` because it must run even when no data was
        synced, and it must not be blocked by a sync in progress.
        """
        from .compaction import compact_raw_metrics

        _sync_log("Running raw-data compaction...")
        try:
            summary = await compact_raw_metrics(dry_run=False)
        except Exception:
            # A scheduled job that raises is only logged by APScheduler, so log it
            # here too and swallow it: an unhandled exception would be invisible in
            # the app's own log stream.
            _sync_log("Raw-data compaction FAILED")
            logger.exception("Raw-data compaction failed")
            return
        if summary.get("skipped"):
            _sync_log(f"Compaction skipped: {summary['skipped']}")
        _sync_log(
            f"Compaction done: {summary['days_compacted']} group(s), "
            f"{summary['daily_rows_written']} daily row(s), "
            f"{summary['raw_rows_deleted']} raw row(s) deleted, "
            f"{len(summary['days_kept_no_daily'])} day(s) kept"
        )
        if summary.get("errors"):
            logger.warning(
                f"Compaction reported {len(summary['errors'])} error(s): "
                f"{summary['errors'][:3]}")
        return summary

    @staticmethod
    def _extract_rows(data_type: str, point: dict, granularity: str) -> list[tuple[str, float, datetime, str | None]]:
        """Return a list of (metric_label, value, recorded_at, unit) rows for one point.

        `unit` is None when the row uses the data type's canonical unit from
        UNIT_MAP; a non-None value overrides it (exercise sub-metrics each carry a
        different unit). A single point can produce multiple rows (a heart-rate
        daily rollup yields avg/min/max; an exercise session yields one row per
        workout metric). `granularity` is "daily" (dailyRollUp aggregate) or "raw".
        Returns [] when the point carries no usable value.
        """
        if granularity == "daily":
            return [(label, v, ts, None)
                    for label, v, ts in HealthSyncScheduler._extract_daily_rollup_rows(data_type, point)]

        # Exercise sessions are not a time series: emit one row per workout metric,
        # each with its own unit (they are "event" cadence and never daily-folded).
        if data_type == "exercise":
            return HealthSyncScheduler._extract_exercise_rows(point)

        # Raw move/heart minutes carry a per-level/per-zone breakdown — emit one
        # row per level/zone instead of a single summed value.
        if data_type in ("move_minutes", "heart_minutes"):
            return [(label, v, ts, None)
                    for label, v, ts in HealthSyncScheduler._extract_raw_minutes_rows(data_type, point)]

        extracted = HealthSyncScheduler._extract_v4_point(data_type, point)
        if not extracted:
            return []
        value, recorded_at = extracted
        # Scalar types: the stored metric_type equals the internal data_type, which
        # UNIT_MAP maps to the definition's canonical unit.
        return [(data_type, value, recorded_at, None)]

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
    def _extract_exercise_rows(point: dict) -> list[tuple[str, float, datetime, str | None]]:
        """Extract one row per workout metric from an exercise (session) point.

        A workout session carries several metrics at once; each is stored as its
        own "event" metric with a name distinct from the daily totals (e.g.
        "Workout Calories" vs. "Calories") so they never merge into the daily
        Steps/Distance/Calories rollups. All sub-metrics share the session's
        startTime. Verified payload fields (live, 2026-10-02):
          activeDuration "1268s"; metricsSummary.{caloriesKcal, distanceMillimeters,
          averageHeartRateBeatsPerMinute, steps}; interval.startTime.
        """
        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        payload = point.get("exercise") or {}
        ts_str = (payload.get("interval") or {}).get("startTime")
        if not ts_str:
            return []
        try:
            recorded_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return []
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        summary = payload.get("metricsSummary") or {}
        rows: list[tuple[str, float, datetime, str | None]] = []

        # activeDuration "1268s" -> minutes
        dur = f(payload.get("activeDuration"))
        if dur is not None:
            rows.append(("Workout Duration", dur / 60.0, recorded_at, "minutes"))
        v = f(summary.get("caloriesKcal"))
        if v is not None:
            rows.append(("Workout Calories", v, recorded_at, "kcal"))
        v = f(summary.get("distanceMillimeters"))
        if v is not None:
            rows.append(("Workout Distance", v / 1000.0, recorded_at, "meters"))
        v = f(summary.get("averageHeartRateBeatsPerMinute"))
        if v is not None:
            rows.append(("Workout Avg Heart Rate", v, recorded_at, "bpm"))
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
            # New sample types (2026-10-02).
            "heart_rate_variability": "heartRateVariability",
            "oxygen_saturation_raw": "oxygenSaturation",
            "vo2_max": "vo2Max",
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
        elif data_type == "heart_rate_variability":
            # HRV-RMSSD, already in milliseconds (a float in the payload).
            if "rootMeanSquareOfSuccessiveDifferencesMilliseconds" not in payload:
                return None
            value = float(payload["rootMeanSquareOfSuccessiveDifferencesMilliseconds"])
            ts_str = sample_time.get("physicalTime")
        elif data_type == "oxygen_saturation_raw":
            # Per-minute SpO2 sample (distinct from the daily `oxygen_saturation`).
            if "percentage" not in payload:
                return None
            value = float(payload["percentage"])
            ts_str = sample_time.get("physicalTime")
        elif data_type == "vo2_max":
            # VO2 max in ml/kg/min (a number), sparse/event data.
            if "vo2Max" not in payload:
                return None
            value = float(payload["vo2Max"])
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
                type_cursors: dict[str, datetime] = {}
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
                            # Per-type cursors: a backfill advances one data type at
                            # a time, and a run that dies part way through keeps the
                            # types it already finished.
                            for dt, iso in (settings_data.get("type_cursors") or {}).items():
                                try:
                                    type_cursors[dt] = datetime.fromisoformat(iso)
                                except (TypeError, ValueError):
                                    continue
                except Exception:
                    settings_data = {}

                # Compute time window: from last sync minus overlap, through now.
                # sync_days_back acts as a floor on every run — the window always
                # reaches back at least that far so changing the setting (or a
                # first successful run with zero saved rows) never permanently
                # narrows the fetch window to 1 day.
                from datetime import timedelta as _timedelta
                end_time = datetime.now(timezone.utc)
                floor_time = end_time - _timedelta(days=sync_days_back)
                if last_google_sync:
                    # Start from last sync minus a small overlap (1 day) to catch
                    # late-arriving data, but never before the sync_days_back floor.
                    start_time = max(last_google_sync - _timedelta(days=1), floor_time)
                else:
                    start_time = floor_time

                _sync_log(f"Syncing user {user_id}: {start_time.isoformat()} to {end_time.isoformat()} (days_back={sync_days_back})")

                async def _save_cursors(data_type: str) -> None:
                    """Persist one type's cursor the moment it finishes.

                    Written per type rather than once at the end of the run: a
                    30-day backfill takes minutes, and a restart/reload part way
                    through used to discard ALL of that work.
                    """
                    type_cursors[data_type] = end_time
                    await _write_sync_cursors(
                        user_id,
                        type_cursors,
                        overall=min(
                            type_cursors.values(),
                            default=floor_time,
                        ),
                    )

                outcome = await sync_health_data(
                    user_id,
                    start_time,
                    end_time,
                    settings_data=settings_data,
                    type_cursors=type_cursors,
                    on_type_complete=_save_cursors,
                )
                _sync_log(
                    f"Finished Google Health sync for user {user_id} "
                    f"(saved={outcome.saved}, types_completed={len(outcome.completed)}"
                    f"/{len(SYNC_DATA_TYPES)})"
                )
                if outcome.failed_from is not None:
                    _sync_log(
                        f"Window {outcome.failed_from.isoformat()} failed to fetch; "
                        f"its cursor was not advanced so it is retried next run"
                    )

                # Final write: record any type that finished during the last call
                # and refresh the aggregate cursor.
                await _write_sync_cursors(
                    user_id,
                    type_cursors,
                    overall=min(type_cursors.values(), default=floor_time),
                )

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
        if self.is_running:
            # Remove existing job
            self.scheduler.remove_job('health_sync_job')
            
            # Add new job with updated cron expression
            self.scheduler.add_job(
                self._run_sync,
                CronTrigger.from_crontab(cron_expression),
                id='health_sync_job',
                replace_existing=True,
                max_instances=1
            )
            logger.info(f"Updated health sync schedule to: {cron_expression}")


# Global scheduler instance
scheduler = HealthSyncScheduler()
