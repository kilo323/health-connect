from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
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
