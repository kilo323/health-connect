from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.config import settings

# Use NullPool so connections are not kept open between requests. SQLite over a
# Docker bind mount (especially on Windows) is prone to "database is locked"
# errors when idle pooled connections hold file locks. A long busy timeout lets
# concurrent writers wait briefly instead of failing immediately.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,
    connect_args={"timeout": 30},
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[type-var]
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add test_date column to pending_analyses if it doesn't exist (migration)
        try:
            await conn.execute(text("ALTER TABLE pending_analyses ADD COLUMN test_date DATETIME"))
        except Exception:
            pass  # Column already exists
        # Add source_document column to health_metrics if it doesn't exist (migration)
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN source_document VARCHAR(255)"))
        except Exception:
            pass  # Column already exists
        # Add definition_id column to health_metrics (metric normalization)
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN definition_id INTEGER REFERENCES metric_definitions(id)"))
        except Exception:
            pass  # Column already exists
        # Add reference_range column to health_metrics
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN reference_range VARCHAR(200)"))
        except Exception:
            pass  # Column already exists
        # Add granularity column to health_metrics ("raw" granular point vs "daily" aggregate)
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN granularity VARCHAR(20) NOT NULL DEFAULT 'raw'"))
        except Exception:
            pass  # Column already exists
        # ── One-time cleanup of legacy synced data ────────────────────────────
        # Older databases were missing the (user, metric, recorded_at, source)
        # idempotency constraint, so re-syncs inserted duplicate rows, and
        # rollup-cutoff changes left days with BOTH granular rows and a daily
        # aggregate (double counting). Collapse duplicates, tag daily rollups,
        # drop the superseded granular rows, then create the unique index.
        # All statements are idempotent, so they are safe to re-run at startup.
        #
        # A Google row at UTC midnight is classified as a daily rollup when:
        #   (a) it is the day's only row, OR
        #   (b) it dominates the day's granular rows (value >= 5x the largest
        #       same-day granular row), OR
        #   (c) it was created >= 1h AFTER the day's granular rows (a later
        #       rollup sync superseding earlier raw rows). The time gap avoids
        #       misclassifying granular midnight intervals written in the same
        #       sync batch (e.g. a "1 step at 00:00" interval row).
        cleanup_statements = [
            # Keep only the newest row per logical data point
            text("""
                DELETE FROM health_metrics
                WHERE id NOT IN (
                    SELECT MAX(id) FROM health_metrics
                    GROUP BY user_id, metric_type, recorded_at, source
                )
            """),
            # Tag Google daily-rollup rows
            text("""
                UPDATE health_metrics SET granularity = 'daily'
                WHERE id IN (
                    SELECT h.id FROM health_metrics h
                    WHERE h.source = 'google_health_connect'
                      AND substr(h.recorded_at, 12, 8) = '00:00:00'
                      AND (
                        NOT EXISTS (
                            SELECT 1 FROM health_metrics h2
                            WHERE h2.user_id = h.user_id
                              AND h2.metric_type = h.metric_type
                              AND date(h2.recorded_at) = date(h.recorded_at)
                              AND h2.id <> h.id
                        )
                        OR (
                            (
                                lower(h.metric_type) LIKE '%steps%'
                                OR lower(h.metric_type) LIKE '%distance%'
                                OR lower(h.metric_type) LIKE '%calor%'
                                OR lower(h.metric_type) LIKE '%minutes%'
                                OR lower(h.metric_type) LIKE '%heart rate%'
                                OR lower(h.metric_type) LIKE '%weight%'
                                OR lower(h.metric_type) LIKE '%body fat%'
                            )
                            AND (
                                h.value >= 5.0 * (
                                    SELECT coalesce(MAX(h2.value), 0) FROM health_metrics h2
                                    WHERE h2.user_id = h.user_id
                                      AND h2.metric_type = h.metric_type
                                      AND date(h2.recorded_at) = date(h.recorded_at)
                                      AND h2.id <> h.id
                                      AND substr(h2.recorded_at, 12, 8) <> '00:00:00'
                                )
                                OR CAST(strftime('%s', h.created_at) AS INTEGER) >= 3600 + (
                                    SELECT coalesce(MAX(CAST(strftime('%s', h2.created_at) AS INTEGER)), 0)
                                    FROM health_metrics h2
                                    WHERE h2.user_id = h.user_id
                                      AND h2.metric_type = h.metric_type
                                      AND date(h2.recorded_at) = date(h.recorded_at)
                                      AND h2.id <> h.id
                                      AND substr(h2.recorded_at, 12, 8) <> '00:00:00'
                                )
                            )
                        )
                      )
                )
            """),
            # A daily aggregate supersedes that day's granular rows
            text("""
                DELETE FROM health_metrics
                WHERE granularity = 'raw'
                  AND source = 'google_health_connect'
                  AND EXISTS (
                    SELECT 1 FROM health_metrics d
                    WHERE d.user_id = health_metrics.user_id
                      AND d.metric_type = health_metrics.metric_type
                      AND d.granularity = 'daily'
                      AND date(d.recorded_at) = date(health_metrics.recorded_at)
                  )
            """),
            # Enforce sync idempotency going forward
            text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_health_metric_point
                ON health_metrics (user_id, metric_type, recorded_at, source)
            """),
        ]
        for stmt in cleanup_statements:
            try:
                await conn.execute(stmt)
            except Exception:
                pass  # Already cleaned up / index already exists
        # Add aliases column to metric_definitions
        try:
            await conn.execute(text("ALTER TABLE metric_definitions ADD COLUMN aliases TEXT"))
        except Exception:
            pass  # Column already exists
        # Add reference_ranges column to metric_definitions
        try:
            await conn.execute(text("ALTER TABLE metric_definitions ADD COLUMN reference_ranges TEXT"))
        except Exception:
            pass  # Column already exists
        # Add unit_conversions column to metric_definitions
        try:
            await conn.execute(text("ALTER TABLE metric_definitions ADD COLUMN unit_conversions TEXT"))
        except Exception:
            pass  # Column already exists
        # Create user_unit_preferences table (created by create_all if new, but ensure for existing DBs)
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_unit_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    metric_definition_id INTEGER NOT NULL REFERENCES metric_definitions(id),
                    preferred_unit VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL,
                    CONSTRAINT uq_user_metric_pref UNIQUE (user_id, metric_definition_id)
                )
            """))
        except Exception:
            pass  # Table already exists
        # Add dashboard_metrics column to users
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN dashboard_metrics TEXT"))
        except Exception:
            pass  # Column already exists
