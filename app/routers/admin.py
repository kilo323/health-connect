"""Admin settings router for LLM configuration and schedule management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
import logging
import os

from ..database import get_db
from ..models.settings import AppSettings, ScheduleConfig
from ..schemas.auth import Token, UserResponse
from ..routers.users import get_current_user
import httpx

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """LLM configuration settings."""
    base_url: str
    api_key: str
    model: str = "gpt-4"


class ScheduleSettings(BaseModel):
    """Schedule configuration settings."""
    is_enabled: bool = False
    cron_expression: str = "0 2 * * *"


class FetchModelsRequest(BaseModel):
    """Request to fetch available models from an endpoint."""
    base_url: str
    api_key: str


@router.get("/settings/llm")
async def get_llm_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get LLM configuration settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "llm_config")
    )
    config = result.scalar_one_or_none()
    
    if not config:
        return LLMConfig(
            base_url=os.getenv("LLM_URL", ""),
            api_key=os.getenv("LLM_API_TOKEN", ""),
            model=os.getenv("LLM_MODEL", "gpt-4"),
        )
    
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
        select(AppSettings).where(AppSettings.key == "llm_config")
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
        select(ScheduleConfig).limit(1)
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
        select(ScheduleConfig).limit(1)
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


@router.post("/sync/now")
async def sync_now(
    current_user: UserResponse = Depends(get_current_user),
):
    """Trigger an immediate health data sync."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.scheduler import scheduler
    import asyncio

    # Run the sync in the background so the response returns immediately
    asyncio.create_task(scheduler._run_sync())
    return {"message": "Sync started"}


@router.get("/llm/models")
async def fetch_llm_models(
    base_url: str,
    api_key: str,
    current_user: UserResponse = Depends(get_current_user),
):
    """Fetch available models from an OpenAI-compatible endpoint."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        # Normalize the base URL
        clean_url = base_url.rstrip("/")
        if not clean_url.endswith("/v1"):
            clean_url = f"{clean_url}/v1"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{clean_url}/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
            )
        
        if response.status_code == 200:
            data = response.json()
            # Standard OpenAI format has "data" array with "id" field
            models = [m.get("id", "") for m in data.get("data", [])]
            return {"models": models, "error": None}
        else:
            return {"models": [], "error": f"Endpoint returned status {response.status_code}: {response.text[:200]}"}
    except httpx.TimeoutException:
        return {"models": [], "error": "Request timed out. Please check the endpoint URL."}
    except Exception as e:
        return {"models": [], "error": f"Failed to fetch models: {str(e)}"}


class GoogleOAuthConfig(BaseModel):
    """Google OAuth configuration (app-level, set by admin)."""
    client_id: str
    client_secret: str
    redirect_uri: str = ""


@router.get("/settings/google-oauth")
async def get_google_oauth_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get the app-level Google OAuth configuration (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "google_oauth_config")
    )
    config = result.scalar_one_or_none()

    if not config:
        return GoogleOAuthConfig(client_id="", client_secret="", redirect_uri="")

    import json
    try:
        return GoogleOAuthConfig(**json.loads(config.value))
    except (json.JSONDecodeError, TypeError):
        return GoogleOAuthConfig(client_id="", client_secret="", redirect_uri="")


@router.put("/settings/google-oauth")
async def update_google_oauth_config(
    config: GoogleOAuthConfig,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update the app-level Google OAuth configuration (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import json

    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "google_oauth_config")
    )
    existing = result.scalar_one_or_none()

    settings_value = json.dumps(config.model_dump())

    if existing:
        existing.value = settings_value
        existing.description = "Google OAuth configuration (app-level)"
    else:
        new_setting = AppSettings(
            key="google_oauth_config",
            value=settings_value,
            description="Google OAuth configuration (app-level)"
        )
        db.add(new_setting)

    await db.commit()
    logger.info("Google OAuth configuration updated")
    return {"message": "Google OAuth configuration updated successfully"}


# ---------------------------------------------------------------------------
# LLM Prompt management
# ---------------------------------------------------------------------------

class PromptPayload(BaseModel):
    """Request body for saving the LLM prompt."""
    content: str


@router.get("/settings/llm-prompt")
async def get_llm_prompt(
    current_user: UserResponse = Depends(get_current_user),
):
    """Get the current LLM prompt template content."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.llm import load_prompt_template, get_prompt_file_path
    content = load_prompt_template()
    return {"content": content, "path": str(get_prompt_file_path())}


@router.put("/settings/llm-prompt")
async def update_llm_prompt(
    payload: PromptPayload,
    current_user: UserResponse = Depends(get_current_user),
):
    """Save the LLM prompt template to disk."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.llm import save_prompt_template
    save_prompt_template(payload.content)
    logger.info("LLM prompt template updated")
    return {"message": "Prompt saved successfully"}


@router.post("/settings/llm-prompt/reset")
async def reset_llm_prompt(
    current_user: UserResponse = Depends(get_current_user),
):
    """Reset the LLM prompt to the built-in default."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.llm import _DEFAULT_PROMPT, save_prompt_template
    save_prompt_template(_DEFAULT_PROMPT)
    logger.info("LLM prompt template reset to default")
    return {"message": "Prompt reset to default", "content": _DEFAULT_PROMPT}
