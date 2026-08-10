from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
import sys
from contextlib import asynccontextmanager
from sqlalchemy import select

from .config import settings
from .database import engine, init_db, async_session_factory
from .routers import auth, health, users, admin
from .services.scheduler import scheduler
from .models.settings import ScheduleConfig

# Configure logging to stdout with explicit flush
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - startup and shutdown events"""
    logger.info("Starting Health Tracker application...")
    
    # Initialize database tables
    await init_db()
    logger.info("Database initialized successfully")
    
    # Seed admin account if needed
    from .models.user import User, Role
    
    db = async_session_factory()
    try:
        result = await db.execute(
            __import__('sqlalchemy').select(User).where(User.username == settings.admin_user)
        )
        admin_user = result.scalar_one_or_none()

        if not admin_user:
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

            # bcrypt has a 72-byte limit on the raw password bytes
            password_bytes = settings.admin_password.encode("utf-8")[:72]
            new_admin = User(
                username=settings.admin_user,
                email=None,
                hashed_password=pwd_context.hash(password_bytes),
                role=Role.ADMIN,
                is_active=True
            )
            db.add(new_admin)
            await db.commit()
            logger.info(f"Admin account created: {settings.admin_user}")
        else:
            # Update password if it doesn't match the configured one
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
            password_bytes = settings.admin_password.encode("utf-8")[:72]
            if not pwd_context.verify(settings.admin_password, admin_user.hashed_password):
                admin_user.hashed_password = pwd_context.hash(password_bytes)
                await db.commit()
                logger.info(f"Admin password updated for: {settings.admin_user}")
            else:
                logger.info("Admin account already exists")
    finally:
        await db.close()

    # Seed LLM configuration from environment variables if no admin config exists yet
    from .models.settings import AppSettings
    import json as _json

    if settings.llm_url and settings.llm_api_token:
        db = async_session_factory()
        try:
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == "llm_config")
            )
            existing = result.scalar_one_or_none()

            if not existing:
                llm_config = _json.dumps({
                    "base_url": settings.llm_url,
                    "api_key": settings.llm_api_token,
                    "model": settings.llm_model,
                })
                db.add(AppSettings(
                    key="llm_config",
                    value=llm_config,
                    description="LLM configuration (seeded from environment variables)",
                ))
                await db.commit()
                logger.info("LLM configuration seeded from environment variables")
            else:
                logger.info("LLM configuration already exists in database, skipping env seed")
        finally:
            await db.close()
    else:
        logger.info("LLM_URL / LLM_API_TOKEN not set — skipping LLM config seed")
    
    # Start scheduler if enabled
    try:
        db = async_session_factory()
        try:
            result = await db.execute(select(ScheduleConfig))
            config = result.scalar_one_or_none()
            
            if config and config.is_enabled:
                await scheduler.start()
                logger.info(f"Scheduler started with cron: {config.cron_expression}")
        finally:
            await db.close()
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}")
    
    yield
    
    # Shutdown cleanup
    logger.info("Shutting down Health Tracker application...")
    await scheduler.stop()


app = FastAPI(
    title="Health Tracker API",
    description="API for health tracking with Google Health Connect and Nextcloud integration",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "message": "Health Tracker API",
        "docs": "/docs",
        "version": "1.0.0"
    }


# Include routers with their prefixes and tags
app.include_router(auth.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(users.router, prefix="/api/users")
app.include_router(admin.router, prefix="/api")
