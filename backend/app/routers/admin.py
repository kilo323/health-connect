"""Admin settings router for LLM configuration and schedule management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import logging

from ..database import get_db
from ..models.settings import AppSettings, ScheduleConfig
from ..schemas.auth import Token, UserResponse
from ..routers.users import get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """LLM configuration settings."""
    base_url: str
    api_key: str
    model: str = "gpt-4"


class ScheduleSettings(BaseModel):
    """Schedule configuration settings."""
    is_enabled: bool
    cron_expression: str = "0 2 * * *"


@router.get("/settings/llm")
async def get_llm_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get LLM configuration settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(
        AppSettings.__table__.select().where(AppSettings.key == "llm_config")
    )
    config = result.scalar_one_or_none()
    
    if not config:
        return LLMConfig(base_url="", api_key="", model="gpt-4")
    
    import json
    try:
        return LLMConfig(**json.loads(config.value))
    except (json.JSONDecodeError, TypeError):
        return LLMConfig(base_url="", api_key="", model="gpt-4")


@router.put("/settings/llm")
async def update_llm_config(
    config: LLMConfig,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update LLM configuration settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    import json
    
    # Check if config exists
    result = await db.execute(
        AppSettings.__table__.select().where(AppSettings.key == "llm_config")
    )
    existing = result.scalar_one_or_none()
    
    settings_value = json.dumps(config.model_dump())
    
    if existing:
        existing.value = settings_value
        existing.description = "LLM configuration"
    else:
        new_setting = AppSettings(
            key="llm_config",
            value=settings_value,
            description="LLM configuration"
        )
        db.add(new_setting)
    
    await db.commit()
    logger.info("LLM configuration updated")
    return {"message": "LLM configuration updated successfully"}


@router.get("/settings/schedule")
async def get_schedule_settings(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get schedule settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(
        ScheduleConfig.__table__.select().first()
    )
    config = result.scalar_one_or_none()
    
    if not config:
        return ScheduleSettings(is_enabled=False, cron_expression="0 2 * * *")
    
    return ScheduleSettings(
        is_enabled=config.is_enabled,
        cron_expression=config.cron_expression
    )


@router.put("/settings/schedule")
async def update_schedule_settings(
    settings: ScheduleSettings,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update schedule settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Check if config exists
    result = await db.execute(
        ScheduleConfig.__table__.select().first()
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.is_enabled = settings.is_enabled
        existing.cron_expression = settings.cron_expression
    else:
        new_config = ScheduleConfig(
            is_enabled=settings.is_enabled,
            cron_expression=settings.cron_expression
        )
        db.add(new_config)
    
    await db.commit()
    
    # Restart scheduler if needed
    from ..services.scheduler import scheduler
    
    if settings.is_enabled and not existing:
        await scheduler.start(settings.cron_expression)
        logger.info(f"Scheduler started with cron: {settings.cron_expression}")
    elif not settings.is_enabled and existing:
        await scheduler.stop()
        logger.info("Scheduler stopped")
    
    return {"message": "Schedule settings updated successfully"}


@router.get("/status/scheduler")
async def get_scheduler_status(
    current_user: UserResponse = Depends(get_current_user),
):
    """Get scheduler status."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    from ..services.scheduler import scheduler
    
    return {
        "is_running": scheduler.is_running(),
        "next_run": None  # Could be implemented to show next scheduled run time
    }
