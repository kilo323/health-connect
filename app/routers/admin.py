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


# ─── Metric Definitions ────────────────────────────────────────────────────

from ..models.health_data import MetricDefinition, HealthMetric
from ..schemas.health_data import (
    MetricDefinitionCreate, MetricDefinitionResponse, UnmatchedMetric,
)


@router.get("/metric-definitions", response_model=list[MetricDefinitionResponse])
async def list_metric_definitions(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all metric definitions (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(select(MetricDefinition).order_by(MetricDefinition.name))
    return result.scalars().all()


@router.post("/metric-definitions", response_model=MetricDefinitionResponse, status_code=201)
async def create_metric_definition(
    data: MetricDefinitionCreate,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new metric definition (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    # Check for duplicate name
    existing = await db.execute(
        select(MetricDefinition).where(MetricDefinition.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Definition '{data.name}' already exists")

    import json as _json

    definition = MetricDefinition(
        name=data.name,
        category=data.category,
        unit=data.unit,
        data_type=data.data_type,
        description=data.description,
        aliases=_json.dumps(data.aliases or []),
        reference_ranges=_json.dumps([r.model_dump() for r in (data.reference_ranges or [])]),
        unit_conversions=_json.dumps(data.unit_conversions or {}),
    )
    db.add(definition)
    await db.commit()
    await db.refresh(definition)

    # Invalidate normalizer cache
    from ..services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False

    logger.info(f"Created metric definition: {definition.name}")
    return definition


@router.put("/metric-definitions/{definition_id}", response_model=MetricDefinitionResponse)
async def update_metric_definition(
    definition_id: int,
    data: MetricDefinitionCreate,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing metric definition (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        select(MetricDefinition).where(MetricDefinition.id == definition_id)
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail="Definition not found")

    import json as _json

    # Check for name collision with a different definition
    dup = await db.execute(
        select(MetricDefinition).where(
            MetricDefinition.name == data.name,
            MetricDefinition.id != definition_id,
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Another definition with name '{data.name}' already exists")

    definition.name = data.name
    definition.category = data.category
    definition.unit = data.unit
    definition.data_type = data.data_type
    definition.description = data.description
    definition.aliases = _json.dumps(data.aliases or [])
    definition.reference_ranges = _json.dumps([r.model_dump() for r in (data.reference_ranges or [])])
    definition.unit_conversions = _json.dumps(data.unit_conversions or {})

    await db.commit()
    await db.refresh(definition)

    # Invalidate normalizer cache
    from ..services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False

    logger.info(f"Updated metric definition: {definition.name}")
    return definition


@router.delete("/metric-definitions/{definition_id}")
async def delete_metric_definition(
    definition_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a metric definition and unlink associated health metrics (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        select(MetricDefinition).where(MetricDefinition.id == definition_id)
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail="Definition not found")

    # Unlink health metrics that reference this definition
    metrics_result = await db.execute(
        select(HealthMetric).where(HealthMetric.definition_id == definition_id)
    )
    for m in metrics_result.scalars().all():
        m.definition_id = None

    await db.delete(definition)
    await db.commit()

    # Invalidate normalizer cache
    from ..services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False

    logger.info(f"Deleted metric definition: {definition.name} (id={definition_id})")
    return {"message": f"Deleted '{definition.name}' and unlinked associated metrics"}


@router.get("/metric-definitions/unmatched", response_model=list[UnmatchedMetric])
async def get_unmatched_metrics(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get health metrics that have no matching metric definition (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.metric_normalizer import metric_normalizer
    return await metric_normalizer.get_unmatched_metrics(db)


@router.post("/metric-definitions/normalize")
async def run_batch_normalization(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run retroactive normalization on all unmatched health metrics (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.metric_normalizer import metric_normalizer
    await metric_normalizer.load(db)
    updated = await metric_normalizer.retroactive_normalize(db)
    return {"message": f"Normalized {updated} metrics", "updated_count": updated}


@router.post("/metric-definitions/map-unmatched")
async def map_unmatched_to_definition(
    payload: dict,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Map an unmatched metric_type to an existing definition (adds it as an alias).

    Body: { "metric_type": "Creatinine, Serum", "definition_id": 5 }
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    metric_type = payload.get("metric_type", "").strip()
    definition_id = payload.get("definition_id")

    if not metric_type or not definition_id:
        raise HTTPException(status_code=400, detail="metric_type and definition_id are required")

    import json as _json

    # Get the target definition
    result = await db.execute(
        select(MetricDefinition).where(MetricDefinition.id == definition_id)
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail="Definition not found")

    # Add metric_type as an alias if not already present
    aliases = []
    if definition.aliases:
        try:
            aliases = _json.loads(definition.aliases)
        except (json.JSONDecodeError, TypeError):
            aliases = []

    if metric_type not in aliases and metric_type.lower() != definition.name.lower():
        aliases.append(metric_type)
        definition.aliases = _json.dumps(aliases)

    # Update all health_metrics with this metric_type
    metrics_result = await db.execute(
        select(HealthMetric).where(
            HealthMetric.metric_type == metric_type,
            HealthMetric.definition_id.is_(None),
        )
    )
    updated = 0
    for m in metrics_result.scalars().all():
        m.definition_id = definition.id
        m.metric_type = definition.name
        updated += 1

    await db.commit()

    # Invalidate normalizer cache
    from ..services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False

    logger.info(f"Mapped '{metric_type}' -> '{definition.name}' (alias added, {updated} metrics updated)")
    return {
        "message": f"Mapped '{metric_type}' to '{definition.name}'",
        "alias_added": True,
        "metrics_updated": updated,
    }


@router.post("/metric-definitions/refresh-library")
async def refresh_from_library(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Load/update metric definitions from the built-in metric library.

    - Creates new definitions for metrics not yet in the database.
    - Updates existing definitions (merges aliases, overwrites ranges/conversions).
    - Returns a summary of created vs updated counts.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import json as _json
    from pathlib import Path

    # Locate the library file
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    library_path = data_dir / "metric_library.json"
    if not library_path.exists():
        # Try Docker path
        library_path = Path("/app/data/metric_library.json")
    if not library_path.exists():
        raise HTTPException(status_code=404, detail="metric_library.json not found")

    try:
        library = _json.loads(library_path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to read library: {e}")

    created = 0
    updated = 0
    skipped = 0

    for entry in library:
        name = entry.get("name", "").strip()
        if not name:
            skipped += 1
            continue

        # Check if definition already exists
        result = await db.execute(
            select(MetricDefinition).where(MetricDefinition.name == name)
        )
        existing = result.scalar_one_or_none()

        # Build JSON fields
        new_aliases = _json.dumps(entry.get("aliases", []))
        new_ranges = _json.dumps(entry.get("reference_ranges", []))
        new_conversions = _json.dumps(entry.get("unit_conversions", {}))

        if existing:
            # Merge aliases — keep existing + add new unique ones
            existing_aliases = set()
            try:
                existing_aliases = set(_json.loads(existing.aliases or "[]"))
            except (_json.JSONDecodeError, TypeError):
                pass
            new_alias_set = set(entry.get("aliases", []))
            merged_aliases = list(existing_aliases | new_alias_set)

            existing.aliases = _json.dumps(merged_aliases)
            existing.reference_ranges = new_ranges
            existing.unit_conversions = new_conversions
            existing.category = entry.get("category") or existing.category
            existing.unit = entry.get("unit") or existing.unit
            existing.data_type = entry.get("data_type", existing.data_type)
            existing.description = entry.get("description") or existing.description
            updated += 1
        else:
            definition = MetricDefinition(
                name=name,
                category=entry.get("category"),
                unit=entry.get("unit"),
                data_type=entry.get("data_type", "float"),
                description=entry.get("description"),
                aliases=new_aliases,
                reference_ranges=new_ranges,
                unit_conversions=new_conversions,
            )
            db.add(definition)
            created += 1

    await db.commit()

    # Invalidate normalizer cache and run normalization
    from ..services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False
    await metric_normalizer.load(db)
    normalized = await metric_normalizer.retroactive_normalize(db)

    logger.info(f"Library refresh: {created} created, {updated} updated, {skipped} skipped, {normalized} metrics normalized")
    return {
        "message": f"Library refreshed: {created} created, {updated} updated. Normalized {normalized} metrics.",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "normalized": normalized,
    }
