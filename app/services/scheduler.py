from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
import asyncio
import logging
from datetime import datetime, timezone

from ..database import async_session_factory
from ..models.settings import ScheduleConfig, AppSettings
from ..models.health_data import DocumentStatus
from ..services.google_health import GoogleHealthService, NOT_LINKED_MARKER
from ..services.nextcloud import NextcloudService

logger = logging.getLogger(__name__)

# Internal data type names synced from the Google Health API v4.
# (blood_pressure, bmr, speed have no v4 equivalent and were dropped.)
SYNC_DATA_TYPES = [
    "steps", "heart_rate", "sleep", "weight", "distance",
    "blood_glucose", "body_temperature",
    "oxygen_saturation", "body_fat_percentage", "height",
    "heart_minutes", "move_minutes",
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
}

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


async def sync_health_data(user_id: int, start_time, end_time, data_types: list[str] | None = None) -> int:
    """Fetch health data from the Google Health API and persist HealthMetric rows.

    Shared by the polling scheduler and the webhook handler. Idempotency relies
    on the (user_id, metric_type, recorded_at, source) unique constraint on
    HealthMetric — duplicate inserts are skipped on IntegrityError.

    Returns the number of new rows saved.
    """
    from ..models.health_data import HealthMetric

    google_service = GoogleHealthService()
    types_to_sync = data_types or SYNC_DATA_TYPES
    total_saved = 0

    account_not_linked = False
    for data_type in types_to_sync:
        if account_not_linked:
            break  # every type fails identically once the account is unlinked
        try:
            health_data = await google_service.fetch_health_data(
                user_id=user_id,
                data_type=data_type,
                start_time=start_time,
                end_time=end_time,
            )

            if not health_data:
                continue

            async with async_session_factory() as db:
                saved_count = 0
                for point in health_data:
                    try:
                        extracted = HealthSyncScheduler._extract_v4_point(data_type, point)
                        if not extracted:
                            continue
                        value, recorded_at = extracted

                        db.add(HealthMetric(
                            user_id=user_id,
                            metric_type=data_type,
                            value=value,
                            unit=UNIT_MAP.get(data_type, "unknown"),
                            recorded_at=recorded_at,
                            source="google_health_connect",
                        ))
                        await db.commit()
                        saved_count += 1
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

    async def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("Health sync scheduler stopped")

    @staticmethod
    def _extract_v4_point(data_type: str, point: dict):
        """Extract (value, recorded_at) from a Google Health API v4 DataPoint.

        Returns None when the point carries no usable value (e.g. a steps
        "true zero" record, which omits the count field).

        Notes on v4 units (all conversions applied here):
          - int64 fields are serialized as JSON strings ("2038")
          - distance is millimeters, weight is grams, height is millimeters
          - sleep duration is computed as interval endTime - startTime
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
        logger.info("Starting health sync job...")

        try:
            async with async_session_factory() as db:
                # Find all users who have Google tokens stored
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

            logger.info(f"Found {len(user_ids)} user(s) with Google tokens to sync")

            if not user_ids:
                logger.info("No users with Google tokens found, finishing sync job")
                return

            for user_id in user_ids:
                # Load per-user sync settings
                sync_days_back = 7  # default
                last_google_sync = None
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
                    pass

                # Compute time window: from last sync minus overlap, through now
                from datetime import datetime, timedelta, timezone
                end_time = datetime.now(timezone.utc)
                if last_google_sync:
                    # Start from last sync minus a small overlap (1 day) to catch late-arriving data
                    start_time = last_google_sync - timedelta(days=1)
                else:
                    # First sync: go back sync_days_back days
                    start_time = end_time - timedelta(days=sync_days_back)

                logger.info(f"Syncing user {user_id}: {start_time.isoformat()} to {end_time.isoformat()} (days_back={sync_days_back})")

                await sync_health_data(user_id, start_time, end_time)

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

            # --- Scan Nextcloud documents for new files ---
            # Find ALL users with Nextcloud configured (not just Google token users)
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
            except Exception:
                pass

            if nc_user_ids:
                logger.info(f"Found {len(nc_user_ids)} user(s) with Nextcloud configured for document scan")

            try:
                from ..services.nextcloud import NextcloudService
                from ..models.health_data import Document
                import os
                import uuid

                nc = NextcloudService()

                for user_id in nc_user_ids:
                    try:
                        # Ensure folder structure exists
                        await nc.ensure_folders(user_id)

                        # List files in Unprocessed folder
                        files = await nc.list_files(user_id, "Unprocessed")
                        if not files:
                            continue

                        logger.info(f"Found {len(files)} document(s) in Nextcloud Unprocessed for user {user_id}")

                        async with async_session_factory() as db:
                            for file_info in files:
                                try:
                                    filename = file_info["filename"]
                                    file_url = file_info["url"]

                                    # Skip non-document files
                                    ext = os.path.splitext(filename)[1].lower()
                                    if ext not in ('.pdf', '.jpg', '.jpeg', '.png', '.txt', '.csv', '.json', '.xml'):
                                        logger.debug(f"Skipping non-document file: {filename}")
                                        continue

                                    # Check if already processed (by filename)
                                    existing = await db.execute(
                                        select(Document).where(
                                            Document.user_id == user_id,
                                            Document.filename == filename
                                        )
                                    )
                                    if existing.scalar_one_or_none():
                                        continue

                                    # Download file
                                    content = await nc.download_file(user_id, file_url)
                                    if not content:
                                        continue

                                    # Save locally
                                    os.makedirs("uploads", exist_ok=True)
                                    local_path = f"uploads/{uuid.uuid4()}_{filename}"
                                    with open(local_path, "wb") as f:
                                        f.write(content)

                                    # Create document record
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

                                    # Move to Processed folder
                                    await nc.move_file(user_id, file_url, "Processed")
                                    logger.info(f"Processed Nextcloud document: {filename} for user {user_id}")

                                except Exception as e:
                                    logger.warning(f"Error processing Nextcloud file: {e}")
                                    continue

                    except Exception as e:
                        logger.warning(f"Error scanning Nextcloud for user {user_id}: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Nextcloud document scan failed: {e}")

            # Update last run time
            async with async_session_factory() as db:
                from sqlalchemy import text
                from datetime import datetime, timezone
                await db.execute(
                    text("UPDATE schedule_configs SET last_run = :now WHERE is_enabled = true"),
                    {"now": datetime.now(timezone.utc)}
                )
                await db.commit()

            logger.info("Health sync job completed")

        except Exception as e:
            logger.error(f"Health sync job failed: {e}")

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
