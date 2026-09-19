"""Admin settings router for LLM configuration and schedule management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from difflib import SequenceMatcher
from typing import Optional
import json
import logging
import os
import re

from ..config import settings
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


class MetricRollupSetting(BaseModel):
    """Per-metric rollup setting."""
    enabled: bool = True
    cutoff_days: int = 7


class RollupConfig(BaseModel):
    """Global metric rollup configuration (admin-wide, no per-user override)."""
    default_cutoff_days: int = 7
    metrics: dict[str, MetricRollupSetting] = {}


class MetricLLMConfig(BaseModel):
    """Metric-library LLM configuration settings (separate from document LLM)."""
    base_url: str = ""
    api_key: str = ""
    model: str = ""


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


@router.get("/settings/metric-llm")
async def get_metric_llm_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get metric-library LLM configuration settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "metric_llm_config")
    )
    config = result.scalar_one_or_none()

    if config:
        try:
            return MetricLLMConfig(**json.loads(config.value))
        except (json.JSONDecodeError, TypeError):
            pass

    # Fall back to .env / env vars
    env_config = await _load_metric_llm_config()
    return MetricLLMConfig(**env_config)


@router.put("/settings/metric-llm")
async def update_metric_llm_config(
    config: MetricLLMConfig,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update metric-library LLM configuration settings."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "metric_llm_config")
    )
    existing = result.scalar_one_or_none()

    settings_value = json.dumps(config.model_dump())

    if existing:
        existing.value = settings_value
        existing.description = "Metric-library LLM configuration"
    else:
        new_setting = AppSettings(
            key="metric_llm_config",
            value=settings_value,
            description="Metric-library LLM configuration"
        )
        db.add(new_setting)

    await db.commit()
    logger.info("Metric-library LLM configuration updated")
    return {"message": "Metric-library LLM configuration updated successfully"}


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


@router.get("/settings/rollup")
async def get_rollup_settings(
    current_user: UserResponse = Depends(get_current_user),
):
    """Get the global metric rollup configuration (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.scheduler import get_rollup_config, DEFAULT_ROLLUP_CONFIG
    cfg = await get_rollup_config()
    return {"config": cfg, "defaults": DEFAULT_ROLLUP_CONFIG}


@router.put("/settings/rollup")
async def update_rollup_settings(
    config: RollupConfig,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the global metric rollup configuration (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if config.default_cutoff_days < 0:
        raise HTTPException(status_code=400, detail="default_cutoff_days must be >= 0")
    for name, m in config.metrics.items():
        if m.cutoff_days < 0:
            raise HTTPException(status_code=400, detail=f"cutoff_days for {name} must be >= 0")

    import json
    from ..services.scheduler import ROLLUP_CONFIG_KEY
    value = json.dumps({
        "default_cutoff_days": config.default_cutoff_days,
        "metrics": {name: {"enabled": m.enabled, "cutoff_days": m.cutoff_days}
                    for name, m in config.metrics.items()},
    })

    result = await db.execute(select(AppSettings).where(AppSettings.key == ROLLUP_CONFIG_KEY))
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(AppSettings(key=ROLLUP_CONFIG_KEY, value=value,
                           description="Global metric rollup configuration"))
    await db.commit()
    return {"message": "Rollup configuration updated successfully"}


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
        "sync_in_progress": scheduler.sync_in_progress,
        "next_run": None  # Could be implemented to show next scheduled run time
    }


@router.get("/status/sync")
async def get_sync_status(
    current_user: UserResponse = Depends(get_current_user),
):
    """Return whether a health data sync is currently running."""
    from ..services.scheduler import scheduler
    return {"sync_in_progress": scheduler.sync_in_progress}


@router.post("/sync/reset")
async def reset_sync_flag(
    current_user: UserResponse = Depends(get_current_user),
):
    """Manually reset the in-progress sync flag (admin only).

    Use this if a sync task died or got stuck and the UI continues to report
    that a sync is running. This does not interrupt an actively running sync;
    it only clears the stale flag.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import asyncio
    from ..services.scheduler import scheduler
    was_in_progress = scheduler.sync_in_progress

    # Cancel any live _run_sync task so a wedged sync doesn't linger as an
    # orphaned coroutine (and potentially re-set the flag or write late data).
    cancelled = False
    for task in asyncio.all_tasks():
        coro = getattr(task.get_coro(), "__qualname__", "")
        if "_run_sync" in coro and not task.done():
            task.cancel()
            cancelled = True

    scheduler._sync_in_progress = False
    return {"was_in_progress": was_in_progress, "cancelled_task": cancelled, "sync_in_progress": False}


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


@router.get("/sync/debug")
async def debug_sync_task(
    current_user: UserResponse = Depends(get_current_user),
):
    """Return the current stack of any running _run_sync task (admin only)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    import asyncio
    import traceback
    from ..services.scheduler import scheduler

    for task in asyncio.all_tasks():
        coro = getattr(task.get_coro(), "__qualname__", "")
        if "_run_sync" in coro:
            return {
                "sync_in_progress": scheduler.sync_in_progress,
                "task_name": task.get_name(),
                "stack": traceback.format_stack(task.get_stack()[0]) if task.get_stack() else [],
            }

    return {"sync_in_progress": scheduler.sync_in_progress, "task_name": None, "stack": []}


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
        
        async with httpx.AsyncClient(timeout=30.0, verify=settings.llm_ssl_verify) as client:
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


# ─── LLM-driven metric definition proposals ────────────────────────────────

class ProposedDefinition(BaseModel):
    """A single LLM-proposed metric definition for admin review."""
    raw_metric_type: str
    name: str
    category: Optional[str] = None
    unit: Optional[str] = None
    data_type: str = "float"
    description: Optional[str] = None
    aliases: list[str] = []
    reference_ranges: list[dict] = []
    unit_conversions: dict = {}
    similar_definition_id: Optional[int] = None
    similar_definition_name: Optional[str] = None
    similarity_score: float = 0.0


class ProposeDefinitionsResponse(BaseModel):
    """Response from the propose endpoint."""
    proposals: list[ProposedDefinition]
    unmatched_count: int


class MergeProposalRequest(BaseModel):
    """Request to accept or merge a proposed definition."""
    raw_metric_type: str
    name: str
    category: Optional[str] = None
    unit: Optional[str] = None
    data_type: str = "float"
    description: Optional[str] = None
    aliases: list[str] = []
    reference_ranges: list[dict] = []
    unit_conversions: dict = {}
    merge_into_definition_id: Optional[int] = None


async def _load_metric_llm_config() -> dict[str, str]:
    """Load the metric-library LLM config.

    Priority: DB (metric_llm_config) > .env (METRIC_LLM_* > LLM_*).
    """
    # 1. Check DB first
    try:
        from ..database import async_session_factory
        async with async_session_factory() as db:
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == "metric_llm_config")
            )
            row = result.scalar_one_or_none()
            if row:
                cfg = json.loads(row.value)
                if cfg.get("base_url") and cfg.get("api_key") and cfg.get("model"):
                    return {
                        "base_url": cfg["base_url"].rstrip("/"),
                        "api_key": cfg["api_key"],
                        "model": cfg["model"],
                    }
    except Exception:
        pass

    # 2. Fall back to .env file
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    dotenv: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            dotenv[key.strip()] = value.strip().strip('"').strip("'")

    def _get(metric_key: str, fallback_key: str) -> str:
        return dotenv.get(metric_key) or dotenv.get(fallback_key) or ""

    return {
        "base_url": _get("METRIC_LLM_URL", "LLM_URL").rstrip("/"),
        "api_key": _get("METRIC_LLM_API_TOKEN", "LLM_API_TOKEN"),
        "model": _get("METRIC_LLM_MODEL", "LLM_MODEL"),
    }


async def _generate_metric_definitions(unmatched: list[dict], definitions: list[MetricDefinition]) -> list[dict]:
    """Ask the LLM to generate definitions for unmatched metrics.

    Processes metrics in batches to avoid gateway timeouts on large sets.
    """
    import httpx
    from ..config import settings

    config = await _load_metric_llm_config()
    if not config["base_url"] or not config["api_key"] or not config["model"]:
        raise HTTPException(status_code=400, detail="Metric LLM not configured in .env")

    existing_names = [d.name for d in definitions]
    existing_aliases: set[str] = set()
    for d in definitions:
        aliases = json.loads(d.aliases or "[]")
        if isinstance(aliases, list):
            existing_aliases.update(str(a) for a in aliases)

    truly_unmatched = []
    for m in unmatched:
        mt = m["metric_type"]
        mt_lower = mt.lower()
        if mt in existing_names or mt in existing_aliases:
            continue
        if any(mt_lower == n.lower() for n in existing_names):
            continue
        if any(mt_lower == a.lower() for a in existing_aliases):
            continue
        truly_unmatched.append(m)

    if not truly_unmatched:
        return []

    # Process in batches to avoid gateway timeouts on large prompt/response payloads.
    BATCH_SIZE = 10
    batches = [truly_unmatched[i:i + BATCH_SIZE] for i in range(0, len(truly_unmatched), BATCH_SIZE)]
    logger.info(f"Generating metric proposals for {len(truly_unmatched)} unmatched metrics in {len(batches)} batch(es)")

    base_url = config["base_url"]
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    all_results: list[dict] = []

    async with httpx.AsyncClient(timeout=300.0, verify=settings.llm_ssl_verify) as client:
        for batch_idx, batch in enumerate(batches, 1):
            unmatched_text = "\n".join(
                f'  - "{m["metric_type"]}" (unit: {m.get("unit") or "unknown"}, {m.get("count", 1)} record(s))'
                for m in batch
            )

            prompt = f"""You are a clinical data specialist. I have health metrics extracted from medical documents that don't yet have definitions in my metric library.

Here are the unmatched metrics:
{unmatched_text}

For each unmatched metric, provide a definition in the following JSON format. Use standard medical knowledge for reference ranges and aliases. If you're unsure about a reference range, omit it (use empty array) rather than guessing.

Return ONLY a JSON array — no markdown, no explanation, no code fences.

Each entry should follow this schema:
[
  {{
    "name": "Canonical Name",
    "category": "Category (e.g. CBC, Metabolic, Lipids, Thyroid, Hormones, Liver Function, Kidney Function, Electrolytes, Urinalysis, Body Composition, Vitamins, Inflammation, Iron, Diabetes, Screening)",
    "unit": "canonical unit",
    "data_type": "float or string",
    "description": "Brief description",
    "aliases": ["alias1", "alias2"],
    "reference_ranges": [
      {{"sex": "male", "low": 0.0, "high": 1.0, "source": "common"}},
      {{"sex": "female", "low": 0.0, "high": 1.0, "source": "common"}},
      {{"sex": null, "low": 0.0, "high": 1.0, "source": "common"}}
    ],
    "unit_conversions": {{"alternate_unit": multiplier}}
  }}
]

IMPORTANT:
- sex should be "male", "female", or null (for both)
- Use the EXACT metric_type string from the unmatched list as one of the aliases
- Include common alternate spellings/names as aliases
- Use null for low/high when it's a one-sided range (e.g. "> 60" means low=60, high=null, operator=">=")
- For operator-based ranges, add "operator": "<" or "<=" or ">" or ">=" to the range object
- reference_ranges.source should be "common" for standard published ranges
- If you truly cannot determine a reference range, use an empty array
- Be precise with units (mg/dL, ng/mL, g/dL, etc.)
"""

            request_payload = {
                "model": config["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a clinical data specialist. Return valid JSON only — no markdown, no code fences, no explanation.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 8192,
                "stream": True,
            }

            # Use streaming to avoid gateway timeouts — the litellm proxy may
            # 504 on a non-streaming request if the model is slow, but streaming
            # keeps the connection alive with periodic SSE chunks.
            max_attempts = 3
            content = ""
            last_empty_detail = "LLM returned empty response"

            for attempt in range(1, max_attempts + 1):
                logger.info(f"Batch {batch_idx}/{len(batches)}: requesting definitions for {len(batch)} metrics (attempt {attempt})")
                try:
                    async with client.stream(
                        "POST",
                        f"{base_url}/chat/completions",
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {config['api_key']}",
                        },
                        json=request_payload,
                    ) as response:
                        if response.status_code != 200:
                            body = await response.aread()
                            raise HTTPException(
                                status_code=502,
                                detail=f"LLM returned {response.status_code}: {body.decode()[:500]}"
                            )

                        content = ""
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                                token = delta.get("content") or ""
                                content += token
                            except json.JSONDecodeError:
                                continue
                except httpx.ReadTimeout:
                    logger.warning(f"Batch {batch_idx}: streaming read timeout (attempt {attempt})")
                    last_empty_detail = f"LLM streaming read timeout (attempt {attempt})"
                    continue

                if content.strip():
                    break

                last_empty_detail = f"LLM returned empty streamed response (attempt {attempt})"
                logger.warning(f"Batch {batch_idx}: {last_empty_detail}")
            else:
                raise HTTPException(status_code=502, detail=last_empty_detail)

            # Parse JSON
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    all_results.extend(parsed)
                elif isinstance(parsed, dict) and "definitions" in parsed:
                    all_results.extend(parsed["definitions"])
            except json.JSONDecodeError:
                cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip())
                cleaned = re.sub(r"\s*```$", "", cleaned)
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list):
                        all_results.extend(parsed)
                    else:
                        all_results.extend(parsed.get("definitions", []))
                except json.JSONDecodeError as e:
                    raise HTTPException(status_code=502, detail=f"Could not parse LLM response: {e}")

            logger.info(f"Batch {batch_idx}/{len(batches)}: got {len(all_results)} total definitions so far")

    return all_results


@router.post("/metric-definitions/propose", response_model=ProposeDefinitionsResponse)
async def propose_metric_definitions(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate LLM proposals for currently unmatched metrics (admin only).

    Returns proposed definitions plus the best existing similar match so the
    admin can decide whether to create new or merge into an existing definition.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.metric_normalizer import metric_normalizer
    await metric_normalizer.load(db)

    definitions = list((await db.execute(select(MetricDefinition))).scalars().all())
    unmatched = await metric_normalizer.get_unmatched_metrics(db)

    generated = await _generate_metric_definitions(unmatched, definitions)

    proposals: list[ProposedDefinition] = []
    for entry in generated:
        name = entry.get("name", "").strip()
        aliases = entry.get("aliases", [])
        if not name:
            continue

        # Find the raw metric type this proposal came from
        raw_metric_type = ""
        for alias in aliases:
            if any(alias == um["metric_type"] for um in unmatched):
                raw_metric_type = alias
                break
        if not raw_metric_type:
            # Fall back to fuzzy matching against unmatched metric types
            best_score = 0.0
            for um in unmatched:
                score = SequenceMatcher(None, name.lower(), um["metric_type"].lower()).ratio()
                if score > best_score:
                    best_score = score
                    raw_metric_type = um["metric_type"]

        similar_def, similarity_score = metric_normalizer.find_similar_definition(name)
        if not similar_def and raw_metric_type:
            similar_def, similarity_score = metric_normalizer.find_similar_definition(raw_metric_type)

        # Include the raw name as an alias if the LLM forgot it
        if raw_metric_type and raw_metric_type not in aliases:
            aliases = [raw_metric_type] + list(aliases)

        proposals.append(ProposedDefinition(
            raw_metric_type=raw_metric_type,
            name=name,
            category=entry.get("category"),
            unit=entry.get("unit"),
            data_type=entry.get("data_type", "float"),
            description=entry.get("description"),
            aliases=aliases,
            reference_ranges=entry.get("reference_ranges", []),
            unit_conversions=entry.get("unit_conversions", {}),
            similar_definition_id=similar_def.id if similar_def else None,
            similar_definition_name=similar_def.name if similar_def else None,
            similarity_score=round(similarity_score, 2),
        ))

    return ProposeDefinitionsResponse(proposals=proposals, unmatched_count=len(unmatched))


@router.post("/metric-definitions/merge-proposal", response_model=MetricDefinitionResponse)
async def merge_metric_proposal(
    payload: MergeProposalRequest,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Accept a proposed definition: create new or merge into existing (admin only).

    When merge_into_definition_id is provided, the raw metric type and any new
    aliases are added to the existing definition, and all matching unmatched
    health metrics are linked to it. Otherwise a new definition is created.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    from ..services.metric_normalizer import check_definition_duplicate, metric_normalizer

    raw = payload.raw_metric_type.strip()
    aliases = [a for a in payload.aliases if a != raw]
    if raw and raw.lower() != payload.name.lower():
        aliases = [raw] + aliases

    if payload.merge_into_definition_id:
        result = await db.execute(
            select(MetricDefinition).where(MetricDefinition.id == payload.merge_into_definition_id)
        )
        definition = result.scalar_one_or_none()
        if not definition:
            raise HTTPException(status_code=404, detail="Target definition not found")

        existing_aliases = set(json.loads(definition.aliases or "[]"))
        existing_aliases.update(a for a in aliases if a)
        definition.aliases = json.dumps(sorted(existing_aliases))

        # Update unmatched metrics (with conflict-safe dedup)
        metrics_result = await db.execute(
            select(HealthMetric).where(
                HealthMetric.metric_type == raw,
                HealthMetric.definition_id.is_(None),
            )
        )
        to_update = metrics_result.scalars().all()
        conflict_keys: set[tuple] = set()
        if to_update:
            user_ids = {m.user_id for m in to_update}
            recorded_ats = {m.recorded_at for m in to_update}
            sources = {m.source for m in to_update}
            conflict_result = await db.execute(
                select(HealthMetric.user_id, HealthMetric.recorded_at, HealthMetric.source).where(
                    HealthMetric.metric_type == definition.name,
                    HealthMetric.definition_id.is_not(None),
                    HealthMetric.user_id.in_(user_ids),
                    HealthMetric.recorded_at.in_(recorded_ats),
                    HealthMetric.source.in_(sources),
                )
            )
            conflict_keys = {(r[0], r[1], r[2]) for r in conflict_result.all()}
        updated = 0
        for m in to_update:
            if (m.user_id, m.recorded_at, m.source) in conflict_keys:
                await db.delete(m)
            else:
                m.definition_id = definition.id
                if m.metric_type != definition.name:
                    m.metric_type = definition.name
                updated += 1

        await db.commit()
        await db.refresh(definition)
        metric_normalizer._loaded = False
        logger.info(f"Merged proposal '{raw}' into '{definition.name}' ({updated} metrics linked)")
        return definition

    # Creating a new definition
    dup = await check_definition_duplicate(db, payload.name, aliases)
    if dup:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate: proposal matches existing definition '{dup.name}' (id={dup.id})"
        )

    definition = MetricDefinition(
        name=payload.name,
        category=payload.category,
        unit=payload.unit,
        data_type=payload.data_type,
        description=payload.description,
        aliases=json.dumps(aliases),
        reference_ranges=json.dumps(payload.reference_ranges or []),
        unit_conversions=json.dumps(payload.unit_conversions or {}),
    )
    db.add(definition)
    await db.commit()
    await db.refresh(definition)

    # Link unmatched metrics that use the raw name (with conflict-safe dedup)
    metrics_result = await db.execute(
        select(HealthMetric).where(
            HealthMetric.metric_type == raw,
            HealthMetric.definition_id.is_(None),
        )
    )
    to_link = metrics_result.scalars().all()
    conflict_keys_link: set[tuple] = set()
    if to_link:
        user_ids_l = {m.user_id for m in to_link}
        recorded_ats_l = {m.recorded_at for m in to_link}
        sources_l = {m.source for m in to_link}
        conflict_result_l = await db.execute(
            select(HealthMetric.user_id, HealthMetric.recorded_at, HealthMetric.source).where(
                HealthMetric.metric_type == definition.name,
                HealthMetric.definition_id.is_not(None),
                HealthMetric.user_id.in_(user_ids_l),
                HealthMetric.recorded_at.in_(recorded_ats_l),
                HealthMetric.source.in_(sources_l),
            )
        )
        conflict_keys_link = {(r[0], r[1], r[2]) for r in conflict_result_l.all()}
    for m in to_link:
        if (m.user_id, m.recorded_at, m.source) in conflict_keys_link:
            await db.delete(m)
        else:
            m.definition_id = definition.id
            if m.metric_type != definition.name:
                m.metric_type = definition.name

    await db.commit()
    metric_normalizer._loaded = False
    logger.info(f"Created metric definition from proposal: {definition.name}")
    return definition


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

    # Check for duplicate name or alias (case-insensitive)
    from ..services.metric_normalizer import check_definition_duplicate
    dup = await check_definition_duplicate(db, data.name, data.aliases or [])
    if dup:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate: '{data.name}' matches existing definition '{dup.name}' (id={dup.id})"
        )

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
    from ..services.metric_normalizer import check_definition_duplicate
    dup = await check_definition_duplicate(db, data.name, data.aliases or [], exclude_id=definition_id)
    if dup:
        raise HTTPException(
            status_code=409,
            detail=f"Duplicate: '{data.name}' conflicts with existing definition '{dup.name}' (id={dup.id})"
        )

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
    metrics_to_update = metrics_result.scalars().all()

    # Check for unique-constraint conflicts: rows that already exist with the
    # target metric_type AND same (user_id, recorded_at, source).
    conflict_keys: set[tuple] = set()
    if metrics_to_update:
        user_ids = {m.user_id for m in metrics_to_update}
        recorded_ats = {m.recorded_at for m in metrics_to_update}
        sources = {m.source for m in metrics_to_update}
        conflict_result = await db.execute(
            select(HealthMetric.user_id, HealthMetric.recorded_at, HealthMetric.source).where(
                HealthMetric.metric_type == definition.name,
                HealthMetric.definition_id.is_not(None),
                HealthMetric.user_id.in_(user_ids),
                HealthMetric.recorded_at.in_(recorded_ats),
                HealthMetric.source.in_(sources),
            )
        )
        conflict_keys = {(r[0], r[1], r[2]) for r in conflict_result.all()}

    updated = 0
    deleted = 0
    for m in metrics_to_update:
        is_conflict = (m.user_id, m.recorded_at, m.source) in conflict_keys
        if is_conflict:
            # A row with the target metric_type already exists at this
            # (user_id, recorded_at, source) – remove the duplicate rather
            # than trying to update it (which would violate the unique index).
            await db.delete(m)
            deleted += 1
        else:
            # Safe to rename; only assign if the value actually changes to
            # avoid SQLAlchemy marking the object dirty unnecessarily.
            if m.metric_type != definition.name:
                m.metric_type = definition.name
            m.definition_id = definition.id
            updated += 1

    await db.commit()

    # Invalidate normalizer cache
    from ..services.metric_normalizer import metric_normalizer
    metric_normalizer._loaded = False

    logger.info(f"Mapped '{metric_type}' -> '{definition.name}' (alias added, {updated} metrics updated, {deleted} duplicates removed)")
    return {
        "message": f"Mapped '{metric_type}' to '{definition.name}'",
        "alias_added": True,
        "metrics_updated": updated,
        "duplicates_deleted": deleted,
    }


