from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import asyncio
import logging

from ..database import async_session_factory
from ..models.settings import ScheduleConfig, AppSettings
from ..services.google_health import GoogleHealthService
from ..services.nextcloud import NextcloudService

logger = logging.getLogger(__name__)


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

    async def _run_sync(self):
        """Main sync job - fetch data from Google Fit and store in Nextcloud"""
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

            google_service = GoogleHealthService()

            data_types = ["steps", "heart_rate", "sleep", "weight", "distance", "calories"]
            unit_map = {
                "steps": "count",
                "heart_rate": "bpm",
                "sleep": "ms",
                "weight": "kg",
                "distance": "meters",
                "calories": "kcal",
            }

            for user_id in user_ids:
                for data_type in data_types:
                    try:
                        health_data = await google_service.fetch_health_data(
                            user_id=user_id,
                            data_type=data_type
                        )

                        if not health_data:
                            continue

                        # Store data points in the local database
                        async with async_session_factory() as db:
                            from ..models.health_data import HealthMetric
                            from datetime import datetime, timezone

                            saved_count = 0
                            for point in health_data:
                                try:
                                    # Google Fit data points have fpVal or intVal
                                    values = point.get("value", [])
                                    if not values:
                                        continue

                                    val_obj = values[0]
                                    if "fpVal" in val_obj:
                                        value = val_obj["fpVal"]
                                    elif "intVal" in val_obj:
                                        value = float(val_obj["intVal"])
                                    else:
                                        continue

                                    # Convert nanosecond timestamp to datetime
                                    ts_ns = int(point.get("startTimeNanos", 0))
                                    recorded_at = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)

                                    metric = HealthMetric(
                                        user_id=user_id,
                                        metric_type=data_type,
                                        value=value,
                                        unit=unit_map.get(data_type, "unknown"),
                                        recorded_at=recorded_at,
                                        source="google_fit",
                                    )
                                    db.add(metric)
                                    saved_count += 1
                                except Exception:
                                    continue

                            await db.commit()
                            if saved_count > 0:
                                logger.info(f"Saved {saved_count} {data_type} records for user {user_id}")

                    except Exception as e:
                        logger.warning(f"Error syncing {data_type} for user {user_id}: {e}")
                        continue

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
                                        status="unprocessed",
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
