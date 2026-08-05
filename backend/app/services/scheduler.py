from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import asyncio
import logging

from ..database import get_db
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
            db = await next(get_db())
            result = await db.execute(select(ScheduleConfig).first())
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
        """Main sync job - fetch data from Google Health Connect and store in Nextcloud"""
        logger.info("Starting health sync job...")

        try:
            db = await next(get_db())
            
            # Get all active sync configs for users with Google Health Connect enabled
            result = await db.execute(
                select(SyncConfig).where(
                    SyncConfig.data_type == "google_health_connect",
                    SyncConfig.is_enabled == True
                )
            )
            sync_configs = result.scalars().all()

            google_service = GoogleHealthService()
            nextcloud_service = NextcloudService()

            for config in sync_configs:
                try:
                    # Fetch data from Google Health Connect
                    health_data = await google_service.fetch_health_data(
                        user_id=config.user_id,
                        data_type=config.data_type
                    )

                    if not health_data:
                        logger.info(f"No new data for user {config.user_id}")
                        continue

                    # Store in Nextcloud as JSON files
                    import json
                    from datetime import datetime
                    
                    filename = f"health_sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    file_content = json.dumps(health_data, indent=2).encode('utf-8')

                    await nextcloud_service.upload_file(
                        user_id=config.user_id,
                        filename=filename,
                        file_content=file_content,
                        dest_path="Unprocessed"
                    )

                    logger.info(f"Synced {len(health_data)} records for user {config.user_id}")

                except Exception as e:
                    logger.error(f"Error syncing data for user {config.user_id}: {e}")
                    continue

            # Update last run time
            await db.execute(
                "UPDATE schedule_configs SET last_run = :now WHERE is_enabled = true",
                {"now": datetime.now(timezone.utc)}
            )
            await db.commit()

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
