from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager

from .config import settings
from .database import engine, init_db, get_db
from .routers import auth, health, users, admin
from .services.scheduler import scheduler

logging.basicConfig(level=logging.INFO)
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
    from sqlalchemy.ext.asyncio import AsyncSession
    
    async with get_db() as db:
        result = await db.execute(
            __import__('sqlalchemy').select(User).where(User.username == settings.admin_user)
        )
        admin_user = result.scalar_one_or_none()
        
        if not admin_user:
            from passlib.context import CryptContext
            pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
            
            new_admin = User(
                username=settings.admin_user,
                email=f"{settings.admin_user}@localhost",
                hashed_password=pwd_context.hash(settings.admin_password),
                role=Role.ADMIN,
                is_active=True
            )
            db.add(new_admin)
            await db.commit()
            logger.info(f"Admin account created: {settings.admin_user}")
        else:
            logger.info("Admin account already exists")
    
    # Start scheduler if enabled
    try:
        from sqlalchemy import select, update
        async with get_db() as db:
            result = await db.execute(select(ScheduleConfig).first())
            config = result.scalar_one_or_none()
            
            if config and config.is_enabled:
                await scheduler.start()
                logger.info(f"Scheduler started with cron: {config.cron_expression}")
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
app.include_router(admin.router, prefix="/api/admin")
