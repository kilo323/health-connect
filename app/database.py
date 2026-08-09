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
