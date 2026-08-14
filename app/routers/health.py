from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import List

from ..database import get_db, async_session_factory
from ..models.user import User
from ..models.health_data import Document, PendingAnalysis, PendingMetric, MetricDefinition, HealthMetric, BatchJob, DocumentStatus
from ..models.settings import AppSettings
from ..schemas.health_data import (
    HealthMetricCreate, HealthMetricResponse,
    SyncConfigCreate, SyncConfigResponse,
    MetricDefinitionCreate, MetricDefinitionResponse,
    DocumentCreate, DocumentResponse,
    AnalysisResult
)
from ..schemas.auth import UserResponse
from ..routers.users import get_current_user
from ..services.google_health import GoogleHealthService
from ..services.nextcloud import NextcloudService
from ..services.llm import LLMService
from ..services.encryption import encryption_service

router = APIRouter(prefix="/health", tags=["Health Data"])
logger = logging.getLogger(__name__)


def _extract_document_content(document: "Document") -> tuple[str, str | list[str]]:
    """Extract content from a document for LLM analysis.

    Returns:
        ("text", content_string) for text-based documents (PDFs with embedded text,
         text/csv/json/xml files, etc.)
        ("images", [base64_str, ...]) for image-based documents (scanned PDFs,
         image files) to be sent to the LLM's vision endpoint.
    """
    import base64

    try:
        if document.file_type in ('text', 'csv', 'json', 'xml'):
            with open(document.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return ("text", f.read())

        elif document.file_type == 'pdf':
            # Strategy 1: pdfplumber (best for text-based PDFs)
            text_content = ""
            try:
                import pdfplumber
                with pdfplumber.open(document.file_path) as pdf:
                    text_content = "\n".join(page.extract_text() or "" for page in pdf.pages)
            except Exception:
                pass

            # Strategy 2: PyMuPDF text extraction (handles some scanned PDFs better)
            if not text_content or not text_content.strip():
                logger.info(f"pdfplumber got no text from {document.filename}, trying PyMuPDF...")
                try:
                    import pymupdf
                    doc = pymupdf.open(document.file_path)
                    pymupdf_pages = []
                    for page in doc:
                        pymupdf_pages.append(page.get_text())
                    text_content = "\n".join(pymupdf_pages)
                    doc.close()
                except Exception as e:
                    logger.warning(f"PyMuPDF text extraction failed for {document.filename}: {e}")

            # If we got text, return it directly
            if text_content and text_content.strip():
                return ("text", text_content)

            # Strategy 3: Render scanned PDF pages as images for LLM vision
            logger.info(f"No embedded text in {document.filename}, rendering pages as images for LLM vision...")
            try:
                import pymupdf
                doc = pymupdf.open(document.file_path)
                page_images = []
                for page in doc:
                    pix = page.get_pixmap(dpi=200)
                    img_bytes = pix.tobytes("png")
                    page_images.append(base64.b64encode(img_bytes).decode("utf-8"))
                doc.close()
                if page_images:
                    return ("images", page_images)
            except Exception as e:
                logger.warning(f"Failed to render PDF pages as images for {document.filename}: {e}")

            return ("text", "")

        elif document.file_type == 'image':
            try:
                with open(document.file_path, "rb") as f:
                    img_bytes = f.read()
                return ("images", [base64.b64encode(img_bytes).decode("utf-8")])
            except Exception as e:
                logger.warning(f"Failed to read image {document.filename}: {e}")
                return ("text", "")

        else:
            with open(document.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return ("text", f.read())

    except Exception as e:
        logger.warning(f"Failed to extract content from {document.filename}: {e}")
        return ("text", "")


@router.get("/metrics", response_model=List[HealthMetricResponse])
async def list_all_metrics(
    metric_type: str = None,
    year: int = None,
    limit: int = 100,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all health metrics for the current user, optionally filtered by type and year"""
    from sqlalchemy import func
    # Build base filter
    base_filters = [HealthMetric.user_id == current_user.id]
    if metric_type:
        base_filters.append(HealthMetric.metric_type == metric_type)
    if year:
        base_filters.append(func.strftime('%Y', HealthMetric.recorded_at) == str(year))

    # Deduplicate by taking latest per metric_type per day
    subq = (
        select(
            HealthMetric.metric_type,
            func.date(HealthMetric.recorded_at).label('day'),
            func.max(HealthMetric.id).label('max_id'),
        )
        .where(*base_filters)
        .group_by(HealthMetric.metric_type, func.date(HealthMetric.recorded_at))
        .subquery()
    )
    query = select(HealthMetric).where(HealthMetric.id == subq.c.max_id)
    query = query.order_by(HealthMetric.recorded_at.desc()).limit(limit)

    result = await db.execute(query)
    metrics = result.scalars().all()

    return [
        HealthMetricResponse(
            id=m.id,
            user_id=m.user_id,
            metric_type=m.metric_type,
            value=m.value,
            unit=m.unit,
            recorded_at=m.recorded_at,
            source=m.source,
            source_document=m.source_document,
            created_at=m.created_at
        )
        for m in metrics
    ]


@router.get("/metrics/definitions", response_model=List[MetricDefinitionResponse])
async def get_metric_definitions(
    db: AsyncSession = Depends(get_db),
):
    """List all metric definitions with reference ranges"""
    result = await db.execute(select(MetricDefinition).order_by(MetricDefinition.name))
    return result.scalars().all()


@router.post("/metrics/definitions", response_model=MetricDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_health_metric_definition(
    definition_data: MetricDefinitionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new metric definition (admin only)"""
    new_definition = MetricDefinition(
        name=definition_data.name,
        category=definition_data.category,
        unit=definition_data.unit,
        data_type=definition_data.data_type,
        description=definition_data.description
    )
    
    db.add(new_definition)
    await db.commit()
    await db.refresh(new_definition)
    
    return new_definition


@router.get("/metrics/{metric_type}", response_model=List[HealthMetricResponse])
async def get_metrics(
    metric_type: str,
    limit: int = 100,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get health metrics for the current user"""
    result = await db.execute(
        select(HealthMetric).where(
            HealthMetric.user_id == current_user.id,
            HealthMetric.metric_type == metric_type
        ).order_by(HealthMetric.recorded_at.desc()).limit(limit)
    )
    metrics = result.scalars().all()
    
    return [
        HealthMetricResponse(
            id=m.id,
            user_id=m.user_id,
            metric_type=m.metric_type,
            value=m.value,
            unit=m.unit,
            recorded_at=m.recorded_at,
            source=m.source,
            created_at=m.created_at
        )
        for m in metrics
    ]


@router.post("/metrics", response_model=HealthMetricResponse, status_code=status.HTTP_201_CREATED)
async def create_metric(
    metric_data: HealthMetricCreate,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Manually add a health metric"""
    new_metric = HealthMetric(
        user_id=current_user.id,
        metric_type=metric_data.metric_type,
        value=metric_data.value,
        unit=metric_data.unit or "",
        recorded_at=metric_data.recorded_at or datetime.now(timezone.utc),
        source=metric_data.source
    )
    
    db.add(new_metric)
    await db.commit()
    await db.refresh(new_metric)
    
    return HealthMetricResponse(
        id=new_metric.id,
        user_id=new_metric.user_id,
        metric_type=new_metric.metric_type,
        value=new_metric.value,
        unit=new_metric.unit,
        recorded_at=new_metric.recorded_at,
        source=new_metric.source,
        created_at=new_metric.created_at
    )


@router.delete("/metrics/{metric_id}")
async def delete_metric(
    metric_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a health metric (only own data)"""
    result = await db.execute(
        select(HealthMetric).where(HealthMetric.id == metric_id, HealthMetric.user_id == current_user.id)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")

    await db.delete(metric)
    await db.commit()
    return {"message": "Metric deleted"}


@router.get("/sync/configs", response_model=List[SyncConfigResponse])
async def get_sync_configs(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get sync configurations for the current user"""
    result = await db.execute(
        select(SyncConfig).where(SyncConfig.user_id == current_user.id)
    )
    configs = result.scalars().all()
    
    return [
        SyncConfigResponse(
            id=c.id,
            user_id=c.user_id,
            data_type=c.data_type,
            sync_mode=c.sync_mode.value if isinstance(c.sync_mode, type) else c.sync_mode,
            is_enabled=c.is_enabled,
            created_at=c.created_at
        )
        for c in configs
    ]


@router.post("/sync/configs", response_model=SyncConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_sync_config(
    config_data: SyncConfigCreate,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a sync configuration"""
    new_config = SyncConfig(
        user_id=current_user.id,
        data_type=config_data.data_type,
        sync_mode=config_data.sync_mode if isinstance(config_data.sync_mode, type) else config_data.sync_mode.value,
        is_enabled=config_data.is_enabled
    )
    
    db.add(new_config)
    await db.commit()
    await db.refresh(new_config)
    
    return SyncConfigResponse(
        id=new_config.id,
        user_id=new_config.user_id,
        data_type=new_config.data_type,
        sync_mode=new_config.sync_mode.value if isinstance(new_config.sync_mode, type) else new_config.sync_mode,
        is_enabled=new_config.is_enabled,
        created_at=new_config.created_at
    )


@router.get("/google-health/config")
async def get_google_oauth_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get the app-level Google OAuth config (read-only for non-admins)."""
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "google_oauth_config")
    )
    config = result.scalar_one_or_none()

    if not config:
        return {"client_id": "", "is_configured": False}

    try:
        data = json.loads(config.value)
        # Mask the client_secret for non-admins
        return {
            "client_id": data.get("client_id", ""),
            "is_configured": bool(data.get("client_id")),
        }
    except (json.JSONDecodeError, TypeError):
        return {"client_id": "", "is_configured": False}


@router.get("/google-health/status")
async def get_google_health_status(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if Google Health Connect is linked for the current user.

    Returns:
        is_linked: a Google OAuth token is stored for the user.
        account_linked: whether that Google account has a Google Health
            (Fitbit) profile behind it — True/False when known, omitted when
            the state can't be determined (e.g. expired token).
    """
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"google_health_tokens_{current_user.id}")
    )
    tokens = result.scalar_one_or_none()
    is_linked = tokens is not None and bool(tokens.value)
    if not is_linked:
        return {"is_linked": False, "account_linked": None}

    account_linked = await GoogleHealthService().check_account_linked(current_user.id)
    return {"is_linked": True, "account_linked": account_linked}


@router.post("/google-health/disconnect")
async def disconnect_google_health(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Disconnect Google Health Connect - revokes token with Google and removes local tokens."""
    import httpx

    # Load stored tokens
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"google_health_tokens_{current_user.id}")
    )
    tokens_row = result.scalar_one_or_none()

    if not tokens_row:
        return {"message": "Not connected"}

    # Try to revoke the token with Google
    try:
        import json
        tokens = json.loads(tokens_row.value)
        access_token = encryption_service.decrypt(tokens.get("access_token", ""))

        async with httpx.AsyncClient(timeout=10.0) as client:
            revoke_response = await client.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": access_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if revoke_response.status_code == 200:
            logger.info(f"Google token revoked for user {current_user.id}")
        else:
            logger.warning(f"Google token revoke returned {revoke_response.status_code}: {revoke_response.text}")
    except Exception as e:
        logger.warning(f"Failed to revoke Google token for user {current_user.id}: {e}")

    # Delete stored tokens from database
    await db.delete(tokens_row)
    await db.commit()
    logger.info(f"Google Health Connect disconnected for user {current_user.id}")

    return {"message": "Disconnected successfully"}


@router.get("/reports/overview")
async def get_report_overview(
    days: int = 30,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get health report overview with latest values, trends, and time series data"""
    from sqlalchemy import func as sa_func, and_
    from datetime import timedelta

    try:
        now = datetime.now(timezone.utc)
        user_id = current_user.id
        # days == 0 is a sentinel for "all time"; otherwise apply a date window
        start_date = now - timedelta(days=days) if days > 0 else None

        # Get latest value per metric type (by most recent recorded_at, breaking ties with max id)
        max_date_subq = (
            select(
                HealthMetric.metric_type,
                sa_func.max(HealthMetric.recorded_at).label("max_recorded_at"),
            )
            .where(HealthMetric.user_id == user_id)
            .group_by(HealthMetric.metric_type)
            .subquery()
        )
        # For metrics with multiple entries on the same latest date, pick the one with the highest id
        max_id_subq = (
            select(
                HealthMetric.metric_type,
                sa_func.max(HealthMetric.id).label("max_id"),
            )
            .select_from(HealthMetric)
            .join(
                max_date_subq,
                and_(
                    HealthMetric.metric_type == max_date_subq.c.metric_type,
                    HealthMetric.recorded_at == max_date_subq.c.max_recorded_at,
                ),
            )
            .where(HealthMetric.user_id == user_id)
            .group_by(HealthMetric.metric_type)
            .subquery()
        )
        latest_result = await db.execute(
            select(HealthMetric).join(max_id_subq, HealthMetric.id == max_id_subq.c.max_id)
        )
        latest_metrics = latest_result.scalars().all()

        # Get time series data for the requested period
        ts_filters = [HealthMetric.user_id == user_id]
        if start_date is not None:
            ts_filters.append(HealthMetric.recorded_at >= start_date)
        ts_result = await db.execute(
            select(HealthMetric)
            .where(*ts_filters)
            .order_by(HealthMetric.recorded_at.asc())
        )
        all_metrics = ts_result.scalars().all()

        # Group time series by metric type and date
        by_type: dict[str, list] = {}
        for m in all_metrics:
            by_type.setdefault(m.metric_type, []).append(m)

        # Build time series (daily values) for charting
        time_series: dict[str, list] = {}
        for metric_type, metrics in by_type.items():
            daily: dict[str, dict] = {}
            for m in metrics:
                if not m.recorded_at:
                    continue
                day_key = m.recorded_at.strftime("%Y-%m-%d")
                if day_key not in daily or m.recorded_at > daily[day_key]["_ts"]:
                    daily[day_key] = {
                        "date": day_key,
                        "value": m.value,
                        "unit": m.unit or "",
                        "source": m.source or "",
                        "_ts": m.recorded_at,
                    }
            # Remove internal _ts field and sort
            time_series[metric_type] = sorted(
                [{"date": v["date"], "value": v["value"], "unit": v["unit"], "source": v["source"]} for v in daily.values()],
                key=lambda x: x["date"],
            )

        # Build summary cards
        summary = []
        for m in latest_metrics:
            week_ago = now - timedelta(days=7)
            two_weeks_ago = now - timedelta(days=14)

            recent_result = await db.execute(
                select(sa_func.avg(HealthMetric.value))
                .where(
                    HealthMetric.user_id == user_id,
                    HealthMetric.metric_type == m.metric_type,
                    HealthMetric.recorded_at >= week_ago,
                )
            )
            recent_avg = recent_result.scalar()

            prior_result = await db.execute(
                select(sa_func.avg(HealthMetric.value))
                .where(
                    HealthMetric.user_id == user_id,
                    HealthMetric.metric_type == m.metric_type,
                    HealthMetric.recorded_at >= two_weeks_ago,
                    HealthMetric.recorded_at < week_ago,
                )
            )
            prior_avg = prior_result.scalar()

            trend = None
            trend_pct = None
            if recent_avg is not None and prior_avg is not None and prior_avg != 0:
                trend_pct = round(((recent_avg - prior_avg) / abs(prior_avg)) * 100, 1)
                trend = "up" if trend_pct > 0 else "down" if trend_pct < 0 else "flat"

            summary.append({
                "metric_type": m.metric_type,
                "latest_value": m.value,
                "unit": m.unit or "",
                "recorded_at": m.recorded_at.isoformat() if m.recorded_at else None,
                "date": m.recorded_at.strftime("%Y-%m-%d") if m.recorded_at else None,
                "trend": trend,
                "trend_pct": trend_pct,
                "recent_avg": round(recent_avg, 2) if recent_avg else None,
                "prior_avg": round(prior_avg, 2) if prior_avg else None,
            })

        return {
            "period_days": days,
            "summary": summary,
            "time_series": time_series,
        }
    except Exception as e:
        logger.error(f"Reports overview failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate report: {str(e)[:200]}")


@router.get("/sync/settings")
async def get_sync_settings(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current user's sync settings."""
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"sync_settings_{current_user.id}")
    )
    config = result.scalar_one_or_none()
    if not config:
        return {"sync_days_back": 7, "last_google_sync": None}
    try:
        return json.loads(config.value)
    except (json.JSONDecodeError, TypeError):
        return {"sync_days_back": 7, "last_google_sync": None}


@router.put("/sync/settings")
async def update_sync_settings(
    settings: dict,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update current user's sync settings."""
    key = f"sync_settings_{current_user.id}"
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    existing = result.scalar_one_or_none()

    # Merge with existing
    existing_data = {}
    if existing:
        try:
            existing_data = json.loads(existing.value)
        except (json.JSONDecodeError, TypeError):
            pass

    if "sync_days_back" in settings:
        days = settings["sync_days_back"]
        if not isinstance(days, int) or days < 1 or days > 365:
            raise HTTPException(status_code=400, detail="sync_days_back must be between 1 and 365")
        existing_data["sync_days_back"] = days

    value = json.dumps(existing_data)
    if existing:
        existing.value = value
    else:
        db.add(AppSettings(key=key, value=value, description="User sync settings"))

    await db.commit()
    return {"message": "Sync settings updated", **existing_data}


@router.get("/nextcloud/config")
async def get_nextcloud_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get current user's Nextcloud configuration."""
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"nextcloud_config_{current_user.id}")
    )
    config = result.scalar_one_or_none()
    if not config:
        return {"server_url": "", "username": "", "is_configured": False}
    try:
        data = json.loads(config.value)
        return {
            "server_url": data.get("server_url", ""),
            "username": data.get("username", ""),
            "sync_path": data.get("sync_path", "/"),
            "is_configured": bool(data.get("server_url")),
        }
    except (json.JSONDecodeError, TypeError):
        return {"server_url": "", "username": "", "sync_path": "/", "is_configured": False}


@router.put("/nextcloud/config")
async def update_nextcloud_config(
    config: dict,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Save current user's Nextcloud configuration and create folder structure."""
    key = f"nextcloud_config_{current_user.id}"
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    existing = result.scalar_one_or_none()

    # Merge with existing config — only override fields that are actually provided
    existing_data = {}
    if existing:
        try:
            existing_data = json.loads(existing.value)
        except (json.JSONDecodeError, TypeError):
            pass

    for config_key, val in config.items():
        if val:  # Only override if the value is non-empty
            existing_data[config_key] = val

    value = json.dumps(existing_data)
    if existing:
        existing.value = value
        existing.description = "Nextcloud configuration"
    else:
        db.add(AppSettings(key=key, value=value, description="Nextcloud configuration"))

    await db.commit()

    # Create Unprocessed/Processed/Archived folders using the saved config directly
    try:
        from ..services.nextcloud import NextcloudService
        nc = NextcloudService()
        # Pass the merged config directly to avoid a separate DB read
        server_url = existing_data.get("server_url", "").rstrip("/")
        username = existing_data.get("username", "")
        password = existing_data.get("password", "")
        sync_path = existing_data.get("sync_path", "/").rstrip("/")

        if server_url and username and password:
            folders = [f"{sync_path}/Unprocessed", f"{sync_path}/Processed", f"{sync_path}/Archived"]
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=15.0, verify=False) as client:
                for folder in folders:
                    webdav_url = f"{server_url}/remote.php/dav/files/{username}{folder}"
                    try:
                        await client.request("MKCOL", webdav_url, auth=(username, password))
                    except _httpx.HTTPStatusError as e:
                        if e.response.status_code != 405:
                            logger.warning(f"Failed to create folder {folder}: {e}")
    except Exception as e:
        logger.warning(f"Failed to create Nextcloud folders: {e}")

    return {"message": "Nextcloud configuration updated"}


@router.delete("/nextcloud/config")
async def delete_nextcloud_config(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove current user's Nextcloud configuration."""
    key = f"nextcloud_config_{current_user.id}"
    result = await db.execute(select(AppSettings).where(AppSettings.key == key))
    config = result.scalar_one_or_none()
    if config:
        await db.delete(config)
        await db.commit()
    return {"message": "Nextcloud configuration removed"}


@router.get("/nextcloud/browse")
async def browse_nextcloud(
    path: str = "/",
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Browse Nextcloud folders via WebDAV"""
    import httpx
    from urllib.parse import unquote

    # Decode any URL-encoded path
    path = unquote(path)

    # Load user's Nextcloud config
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"nextcloud_config_{current_user.id}")
    )
    config_row = result.scalar_one_or_none()
    if not config_row:
        raise HTTPException(status_code=400, detail="Nextcloud not configured")

    config = json.loads(config_row.value)
    server_url = config.get("server_url", "").rstrip("/")
    username = config.get("username", "")
    password = config.get("password", "")

    if not server_url or not username or not password:
        raise HTTPException(status_code=400, detail="Nextcloud credentials incomplete")

    # WebDAV PROPFIND to list folder contents
    webdav_url = f"{server_url}/remote.php/dav/files/{username}{path}"

    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        resp = await client.request(
            "PROPFIND",
            webdav_url,
            auth=(username, password),
            headers={"Depth": "1"},
            content="<?xml version='1.0' encoding='utf-8'?>"
                    "<d:propfind xmlns:d='DAV:'>"
                    "<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop>"
                    "</d:propfind>",
        )

    if resp.status_code not in (200, 207):
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Nextcloud authentication failed — check your credentials")
        raise HTTPException(status_code=resp.status_code, detail=f"Failed to browse Nextcloud (HTTP {resp.status_code})")

    # Parse WebDAV XML response
    import xml.etree.ElementTree as ET
    root = ET.fromstring(resp.text)
    ns = {"d": "DAV:"}

    folders = []
    for response in root.findall("d:response", ns):
        href = response.findtext("d:href", "", ns)
        # resourcetype is nested inside d:propstat/d:prop
        propstat = response.find("d:propstat", ns)
        is_folder = False
        if propstat is not None:
            prop = propstat.find("d:prop", ns)
            if prop is not None:
                resourcetype = prop.find("d:resourcetype", ns)
                if resourcetype is not None:
                    is_folder = resourcetype.find("d:collection", ns) is not None or len(resourcetype) > 0

        if is_folder:
            # Convert WebDAV href to a clean path (decode URL encoding)
            from urllib.parse import unquote
            folder_path = unquote(href).rstrip("/").replace(f"/remote.php/dav/files/{username}", "") or "/"
            if folder_path != path:  # Skip current directory
                folders.append({
                    "path": folder_path + "/",
                    "name": folder_path.split("/")[-1] or "/",
                })

    return {"path": path, "folders": folders}


@router.post("/nextcloud/mkdir")
async def mkdir_nextcloud(
    body: dict,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new folder in Nextcloud via WebDAV"""
    import httpx

    folder_path = body.get("path", "")
    if not folder_path:
        raise HTTPException(status_code=400, detail="Path is required")

    # Load user's Nextcloud config
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"nextcloud_config_{current_user.id}")
    )
    config_row = result.scalar_one_or_none()
    if not config_row:
        raise HTTPException(status_code=400, detail="Nextcloud not configured")

    config = json.loads(config_row.value)
    server_url = config.get("server_url", "").rstrip("/")
    username = config.get("username", "")
    password = config.get("password", "")

    if not server_url or not username or not password:
        raise HTTPException(status_code=400, detail="Nextcloud credentials incomplete")

    webdav_url = f"{server_url}/remote.php/dav/files/{username}{folder_path}"

    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        resp = await client.request(
            "MKCOL",
            webdav_url,
            auth=(username, password),
        )

    if resp.status_code in (200, 201):
        return {"message": f"Folder created: {folder_path}"}
    elif resp.status_code == 405:
        raise HTTPException(status_code=409, detail="Folder already exists")
    else:
        raise HTTPException(status_code=resp.status_code, detail="Failed to create folder")


@router.post("/google-health/connect")
async def connect_google_health(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get OAuth URL to connect Google Health Connect"""
    # Load app-level Google OAuth config (set by admin)
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "google_oauth_config")
    )
    config_row = result.scalar_one_or_none()

    if not config_row:
        raise HTTPException(status_code=400, detail="Google OAuth not configured. Ask your admin to set up credentials.")

    try:
        config_data = json.loads(config_row.value)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid Google OAuth configuration.")

    client_id = config_data.get("client_id", "")
    client_secret = config_data.get("client_secret", "")
    redirect_uri = config_data.get("redirect_uri", "")

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Google OAuth client_id and client_secret are required. Ask your admin to configure them.")

    # Build OAuth URL with proper encoding
    from urllib.parse import urlencode
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri or f"{request.base_url.scheme}://{request.base_url.netloc}/api/health/google-health/callback",
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly https://www.googleapis.com/auth/googlehealth.sleep.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": str(current_user.id),
    }
    oauth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    return {"oauth_url": oauth_url}


@router.get("/google-health/callback")
async def google_health_callback(request: Request, code: str, state: str = "", db: AsyncSession = Depends(get_db)):
    """Handle OAuth callback from Google - exchanges code for tokens."""
    user_id = int(state) if state else 1

    # Load admin-level Google OAuth config
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == "google_oauth_config")
    )
    config_row = result.scalar_one_or_none()
    if not config_row:
        raise HTTPException(status_code=400, detail="Google OAuth not configured")

    import json
    config_data = json.loads(config_row.value)
    client_id = config_data.get("client_id", "")
    client_secret = config_data.get("client_secret", "")
    redirect_uri = config_data.get("redirect_uri", "") or f"{request.base_url.scheme}://{request.base_url.netloc}/api/health/google-health/callback"

    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Google OAuth credentials missing")

    # Exchange authorization code for tokens using Google's token endpoint
    import httpx
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if token_response.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_response.text}")

    tokens = token_response.json()

    # Store tokens per-user
    from ..services.encryption import encryption_service
    encrypted_tokens = json.dumps({
        "access_token": encryption_service.encrypt(tokens["access_token"]),
        "refresh_token": encryption_service.encrypt(tokens.get("refresh_token", "")),
        "expires_in": tokens.get("expires_in", 0),
    })

    token_key = f"google_health_tokens_{user_id}"
    result = await db.execute(select(AppSettings).where(AppSettings.key == token_key))
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = encrypted_tokens
        existing.description = "Google Health API OAuth tokens"
    else:
        db.add(AppSettings(key=token_key, value=encrypted_tokens, description="Google Health API OAuth tokens"))

    await db.commit()

    # Best-effort: map the Google Health healthUserId to this local user so
    # webhook notifications can be routed (used by /webhooks/google-health).
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            identity_resp = await client.get(
                "https://health.googleapis.com/v4/users/me/identity",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
        if identity_resp.status_code == 200:
            health_user_id = identity_resp.json().get("healthUserId")
            if health_user_id:
                uid_key = f"google_health_uid_{health_user_id}"
                uid_result = await db.execute(select(AppSettings).where(AppSettings.key == uid_key))
                uid_row = uid_result.scalar_one_or_none()
                if uid_row:
                    uid_row.value = str(user_id)
                else:
                    db.add(AppSettings(key=uid_key, value=str(user_id), description="Google Health user ID mapping"))
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to fetch Google Health identity for user {user_id}: {e}")

    # Probe whether the Google account is actually linked to Google Health so
    # the frontend can immediately flag "connected but no health data".
    try:
        account_linked = await GoogleHealthService().check_account_linked(user_id)
    except Exception:
        account_linked = None

    # Redirect back to the frontend settings page. Derive the origin from the
    # incoming request (which honors X-Forwarded-Proto/Host via --proxy-headers), so
    # it works on local dev and reverse-proxied deployments with zero config.
    # PUBLIC_URL, when set, is an explicit override (e.g. if the API is reached on a
    # different host than the frontend).
    from ..config import settings as _settings
    if _settings.public_url:
        base = _settings.public_url.rstrip("/")
    else:
        base = f"{request.base_url.scheme}://{request.base_url.netloc}"
    redirect_url = f"{base}/settings"
    if account_linked is False:
        redirect_url += "?google_account_linked=false"
    return RedirectResponse(url=redirect_url)


@router.get("/documents", response_model=List[DocumentResponse])
async def list_documents(
    status_filter: str = None,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List documents for the current user, optionally filtered by status"""
    query = select(Document).where(Document.user_id == current_user.id)
    if status_filter:
        # SAEnum stores enum names; compare using the value attribute
        query = query.where(Document.status == DocumentStatus[status_filter.upper()])
    query = query.order_by(Document.created_at.desc())
    result = await db.execute(query)
    documents = result.scalars().all()
    
    return [
        DocumentResponse(
            id=d.id,
            user_id=d.user_id,
            filename=d.filename,
            file_path=d.file_path,
            file_type=d.file_type,
            status=d.status.value if hasattr(d.status, 'value') else str(d.status),
            source=d.source,
            size_bytes=d.size_bytes,
            created_at=d.created_at
        )
        for d in documents
    ]


@router.post("/documents/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload a medical document"""
    # Save file locally first
    os.makedirs("uploads", exist_ok=True)
    unique_filename = f"{uuid.uuid4()}_{file.filename}"
    file_path = f"uploads/{unique_filename}"
    
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Determine file type from extension
    ext = os.path.splitext(file.filename)[1].lower()
    file_type_map = {
        '.pdf': 'pdf',
        '.jpg': 'image',
        '.jpeg': 'image',
        '.png': 'image',
        '.txt': 'text'
    }
    file_type = file_type_map.get(ext, 'unknown')
    
    new_document = Document(
        user_id=current_user.id,
        filename=file.filename,
        file_path=file_path,
        file_type=file_type,
        source="upload",
        size_bytes=len(content)
    )
    
    db.add(new_document)
    await db.commit()
    await db.refresh(new_document)
    
    return DocumentResponse(
        id=new_document.id,
        user_id=new_document.user_id,
        filename=new_document.filename,
        file_path=new_document.file_path,
        file_type=new_document.file_type,
        status="unprocessed",
        source=new_document.source,
        size_bytes=new_document.size_bytes,
        created_at=new_document.created_at
    )


@router.post("/documents/{document_id}/retry")
async def retry_document(
    document_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Reset a rejected/failed document back to unprocessed for re-analysis"""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == current_user.id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete associated pending analysis if any
    pa_result = await db.execute(
        select(PendingAnalysis).where(PendingAnalysis.document_id == document.id)
    )
    pa = pa_result.scalar_one_or_none()
    if pa:
        await db.delete(pa)

    document.status = DocumentStatus.UNPROCESSED
    await db.commit()
    return {"message": "Document reset for re-analysis"}


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a document and its associated analysis/metrics"""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == current_user.id)
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete the physical file
    try:
        if os.path.exists(document.file_path):
            os.remove(document.file_path)
    except OSError:
        pass

    await db.delete(document)
    await db.commit()
    return {"message": "Document deleted"}

@router.post("/pending-analysis/{analysis_id}/approve")
async def approve_analysis(
    analysis_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Approve a pending analysis and import extracted metrics as health data"""
    result = await db.execute(
        select(PendingAnalysis).where(
            PendingAnalysis.id == analysis_id,
            PendingAnalysis.user_id == current_user.id
        )
    )
    pa = result.scalar_one_or_none()
    if not pa:
        raise HTTPException(status_code=404, detail="Pending analysis not found")

    # Parse the raw analysis
    findings = []
    try:
        parsed = json.loads(pa.raw_analysis) if pa.raw_analysis else {}
        findings = parsed.get("findings", [])
    except (json.JSONDecodeError, TypeError):
        pass

    # Import each finding as a health metric (skip duplicates)
    imported = 0
    # Use the test_date from the document analysis if available, otherwise fall back to now
    recorded_at = pa.test_date if pa.test_date else datetime.now(timezone.utc)
    recorded_date = recorded_at.date()

    # Get source document filename
    source_doc_result = await db.execute(select(Document).where(Document.id == pa.document_id))
    source_doc = source_doc_result.scalar_one_or_none()
    source_filename = source_doc.filename if source_doc else None

    # Load metric normalizer for name/unit/reference normalization
    from ..services.metric_normalizer import metric_normalizer
    await metric_normalizer.load(db)

    for finding in findings:
        try:
            value_str = str(finding.get("value", "")).strip()
            if not value_str:
                continue
            value = float(value_str)
        except (ValueError, TypeError):
            continue

        metric_name = finding.get("metric_name", "unknown")
        llm_unit = finding.get("unit") or ""
        llm_ref_range = finding.get("reference_range") or ""

        # Normalize the metric name and unit via the normalizer
        normalized = await metric_normalizer.normalize(db, metric_name, llm_unit, llm_ref_range)

        # Use canonical name if matched, otherwise keep original
        canonical_name = normalized.canonical_name
        canonical_unit = normalized.normalized_unit
        definition_id = normalized.definition.id if normalized.definition else None

        # Check if this metric already exists for this date (use first() to handle multiple matches)
        day_start = datetime.combine(recorded_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        day_end = datetime.combine(recorded_date, datetime.max.time()).replace(tzinfo=timezone.utc)

        # Check for duplicates using the canonical name
        existing = await db.execute(
            select(HealthMetric).where(
                HealthMetric.user_id == current_user.id,
                HealthMetric.metric_type == canonical_name,
                HealthMetric.source == "document_analysis",
                HealthMetric.recorded_at >= day_start,
                HealthMetric.recorded_at <= day_end,
            ).limit(1)
        )
        if existing.scalars().first():
            continue

        metric = HealthMetric(
            user_id=current_user.id,
            metric_type=canonical_name,
            value=value,
            unit=canonical_unit,
            recorded_at=recorded_at,
            source="document_analysis",
            source_document=source_filename,
            definition_id=definition_id,
            reference_range=llm_ref_range if llm_ref_range else None,
        )
        db.add(metric)
        imported += 1

    # Update analysis status
    pa.status = DocumentStatus.APPROVED

    # Update document status
    doc_result = await db.execute(select(Document).where(Document.id == pa.document_id))
    doc = doc_result.scalar_one_or_none()
    if doc:
        doc.status = DocumentStatus.APPROVED

    await db.commit()

    return {"message": f"Approved: {imported} metrics imported", "imported_count": imported}


@router.post("/pending-analysis/{analysis_id}/reject")
async def reject_analysis(
    analysis_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Reject a pending analysis"""
    result = await db.execute(
        select(PendingAnalysis).where(
            PendingAnalysis.id == analysis_id,
            PendingAnalysis.user_id == current_user.id
        )
    )
    pa = result.scalar_one_or_none()
    if not pa:
        raise HTTPException(status_code=404, detail="Pending analysis not found")

    pa.status = DocumentStatus.REJECTED

    doc_result = await db.execute(select(Document).where(Document.id == pa.document_id))
    doc = doc_result.scalar_one_or_none()
    if doc:
        doc.status = DocumentStatus.REJECTED

    await db.commit()
    return {"message": "Analysis rejected"}

@router.post("/documents/{document_id}/analyze")
async def analyze_document(
    document_id: int,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Trigger LLM analysis of a document"""
    # Get the document (only if it belongs to the current user)
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == current_user.id)
    )
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Read file content — text extraction or image rendering for LLM vision
    doc_type, content = _extract_document_content(document)

    if doc_type == "text" and (not content or not content.strip()):
        raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable")

    # Analyze with LLM
    llm_service = LLMService()
    try:
        if doc_type == "images":
            analysis_result = await llm_service.analyze_document(current_user.id, image_content=content)
        else:
            analysis_result = await llm_service.analyze_document(current_user.id, document_content=content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM analysis failed: {str(e)}")
    
    # Parse test_date from LLM response
    test_date = None
    test_date_str = analysis_result.get("test_date")
    if test_date_str:
        try:
            test_date = datetime.strptime(test_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            logger.warning(f"Could not parse test_date from LLM response: {test_date_str}")

    # Create or update pending analysis record
    existing_analysis = await db.execute(
        select(PendingAnalysis).where(PendingAnalysis.document_id == document.id)
    )
    pending_analysis = existing_analysis.scalar_one_or_none()

    if pending_analysis:
        pending_analysis.raw_analysis = json.dumps(analysis_result)
        pending_analysis.status = "pending_review"
        pending_analysis.test_date = test_date
    else:
        pending_analysis = PendingAnalysis(
            document_id=document.id,
            user_id=current_user.id,
            raw_analysis=json.dumps(analysis_result),
            status="pending_review",
            test_date=test_date,
        )
        db.add(pending_analysis)
    
    # Update document status
    document.status = DocumentStatus.ANALYZED_PENDING_REVIEW
    
    await db.commit()
    await db.refresh(pending_analysis)
    
    return {
        "status": "success",
        "pending_analysis_id": pending_analysis.id,
        "analysis": analysis_result
    }


# In-memory event bus for batch job progress (job_id → asyncio.Queue)
import asyncio
_batch_queues: dict[int, asyncio.Queue] = {}


async def _run_batch_analysis(job_id: int, user_id: int):
    """Background task that processes documents and emits progress events."""
    try:
        async with async_session_factory() as db:
            # Load job record
            result = await db.execute(select(BatchJob).where(BatchJob.id == job_id))
            job = result.scalar_one_or_none()
            if not job:
                return

            # Get unprocessed documents
            docs_result = await db.execute(
                select(Document).where(
                    Document.user_id == user_id,
                    Document.status == DocumentStatus.UNPROCESSED,
                )
            )
            documents = docs_result.scalars().all()
            job.total = len(documents)
            await db.commit()

            if not documents:
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()
                queue = _batch_queues.get(job_id)
                if queue:
                    await queue.put({"event": "complete", "data": {"processed": 0, "total": 0, "errors": 0}})
                return

            llm_service = LLMService()
            error_details = []

            for i, document in enumerate(documents):
                # Update current filename in DB
                job.current_filename = document.filename
                await db.commit()

                # Emit progress event
                queue = _batch_queues.get(job_id)
                if queue:
                    await queue.put({
                        "event": "progress",
                        "data": {
                            "processed": job.processed,
                            "total": job.total,
                            "filename": document.filename,
                            "errors": job.errors,
                        },
                    })

                try:
                    # Read file content — text extraction or image rendering for LLM vision
                    doc_type, content = _extract_document_content(document)

                    if doc_type == "text" and (not content or not content.strip()):
                        error_details.append({"filename": document.filename, "error": "empty or unreadable"})
                        job.errors += 1
                        job.processed += 1
                        await db.commit()
                        continue

                    # Analyze with LLM
                    if doc_type == "images":
                        analysis_result = await llm_service.analyze_document(user_id, image_content=content)
                    else:
                        analysis_result = await llm_service.analyze_document(user_id, document_content=content)

                    # Parse test_date
                    test_date = None
                    test_date_str = analysis_result.get("test_date")
                    if test_date_str:
                        try:
                            test_date = datetime.strptime(test_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            pass

                    # Create pending analysis
                    pending_analysis = PendingAnalysis(
                        document_id=document.id,
                        user_id=user_id,
                        raw_analysis=json.dumps(analysis_result),
                        status="pending_review",
                        test_date=test_date,
                    )
                    db.add(pending_analysis)
                    document.status = DocumentStatus.ANALYZED_PENDING_REVIEW
                    job.processed += 1
                    await db.commit()

                except Exception as e:
                    error_details.append({"filename": document.filename, "error": str(e)[:200]})
                    job.errors += 1
                    job.processed += 1
                    await db.commit()
                    continue

            # Mark job complete
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.current_filename = None
            job.error_details = json.dumps(error_details) if error_details else None
            await db.commit()

            # Emit complete event
            queue = _batch_queues.get(job_id)
            if queue:
                await queue.put({
                    "event": "complete",
                    "data": {
                        "processed": job.processed,
                        "total": job.total,
                        "errors": job.errors,
                        "error_details": error_details if error_details else None,
                    },
                })

    except Exception as e:
        logger.error(f"Batch job {job_id} failed: {e}", exc_info=True)
        try:
            async with async_session_factory() as db:
                result = await db.execute(select(BatchJob).where(BatchJob.id == job_id))
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.completed_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:
            pass
        queue = _batch_queues.get(job_id)
        if queue:
            await queue.put({"event": "error", "data": {"message": str(e)[:200]}})
    finally:
        # Cleanup queue after a delay
        await asyncio.sleep(60)
        _batch_queues.pop(job_id, None)


@router.post("/documents/analyze-all")
async def analyze_all_documents(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Start batch analysis of all unprocessed documents. Returns job_id for SSE progress tracking."""
    # Check for existing running job
    existing = await db.execute(
        select(BatchJob).where(
            BatchJob.user_id == current_user.id,
            BatchJob.status == "running",
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A batch analysis is already running")

    # Count unprocessed documents
    docs_result = await db.execute(
        select(Document).where(
            Document.user_id == current_user.id,
            Document.status == DocumentStatus.UNPROCESSED,
        )
    )
    documents = docs_result.scalars().all()
    if not documents:
        return {"message": "No unprocessed documents found", "job_id": None}

    # Create batch job record
    job = BatchJob(
        user_id=current_user.id,
        status="running",
        total=len(documents),
        processed=0,
        errors=0,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Create event queue for this job
    _batch_queues[job.id] = asyncio.Queue()

    # Spawn background task
    asyncio.create_task(_run_batch_analysis(job.id, current_user.id))

    return {"message": f"Batch analysis started for {len(documents)} documents", "job_id": job.id}


@router.get("/batch-progress/{job_id}")
async def batch_progress(job_id: int, current_user: UserResponse = Depends(get_current_user)):
    """SSE endpoint that streams batch analysis progress."""
    # Verify job belongs to user
    async with async_session_factory() as db:
        result = await db.execute(
            select(BatchJob).where(BatchJob.id == job_id, BatchJob.user_id == current_user.id)
        )
        job = result.scalar_one_or_none()

    if not job:
        raise HTTPException(status_code=404, detail="Batch job not found")

    async def event_stream():
        queue = _batch_queues.get(job_id)

        # If job is already done, send final state and close
        if job.status in ("completed", "failed"):
            yield f"event: complete\ndata: {json.dumps({'processed': job.processed, 'total': job.total, 'errors': job.errors})}\n\n"
            return

        # Stream events from the queue
        if queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"event: {event['event']}\ndata: {json.dumps(event['data'])}\n\n"
                    if event["event"] in ("complete", "error"):
                        break
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/pending-analysis", response_model=List)
async def list_pending_analysis(
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List pending analyses for the current user"""
    result = await db.execute(
        select(PendingAnalysis).where(PendingAnalysis.user_id == current_user.id)
        .order_by(PendingAnalysis.created_at.desc())
    )
    analyses = result.scalars().all()

    # Pre-load normalizer once for match-status checking
    from ..services.metric_normalizer import metric_normalizer
    has_pending = any(pa.status in ("pending_review", "analyzed_pending_review") for pa in analyses)
    if has_pending:
        await metric_normalizer.load(db)

    output = []
    for pa in analyses:
        # Get document filename
        doc_result = await db.execute(select(Document).where(Document.id == pa.document_id))
        doc = doc_result.scalar_one_or_none()
        filename = doc.filename if doc else "Unknown"

        # Parse raw_analysis JSON
        summary = ""
        findings = []
        try:
            parsed = json.loads(pa.raw_analysis) if pa.raw_analysis else {}
            summary = parsed.get("summary", "")
            findings = parsed.get("findings", [])
        except (json.JSONDecodeError, TypeError):
            pass

        metrics_out = []
        for f in findings:
            entry = {
                "name": f.get("metric_name", ""),
                "value": f.get("value", ""),
                "unit": f.get("unit", ""),
                "reference_range": f.get("reference_range", ""),
                "is_selected": True,
                "match_status": "none",
            }
            # Add match status if analysis is still pending
            if pa.status in ("pending_review", "analyzed_pending_review"):
                normalized = await metric_normalizer.normalize(
                    db, f.get("metric_name", ""), f.get("unit", "")
                )
                entry["match_status"] = normalized.match_type
            metrics_out.append(entry)

        output.append({
            "id": pa.id,
            "document_id": pa.document_id,
            "filename": filename,
            "status": pa.status,
            "analysis_summary": summary,
            "test_date": pa.test_date.isoformat() if pa.test_date else None,
            "metrics_extracted": metrics_out,
            "created_at": pa.created_at,
        })

    return output


@router.get("/pending-analysis/{analysis_id}", response_model=AnalysisResult)
async def get_pending_analysis(analysis_id: int):
    """Get pending analysis with extracted metrics"""
    db = await next(get_db())
    
    # Get the pending analysis
    result = await db.execute(select(PendingAnalysis).where(PendingAnalysis.id == analysis_id))
    pending_analysis = result.scalar_one_or_none()
    
    if not pending_analysis:
        raise HTTPException(status_code=404, detail="Pending analysis not found")
    
    # Get associated metrics
    metrics_result = await db.execute(
        select(PendingMetric).where(PendingMetric.pending_analysis_id == analysis_id)
    )
    metrics = metrics_result.scalars().all()
    
    return AnalysisResult(
        pending_analysis=PendingAnalysisResponse(
            id=pending_analysis.id,
            document_id=pending_analysis.document_id,
            user_id=pending_analysis.user_id,
            raw_analysis=pending_analysis.raw_analysis,
            status=pending_analysis.status,
            created_at=pending_analysis.created_at
        ),
        metrics=[
            PendingMetricResponse(
                id=m.id,
                pending_analysis_id=m.pending_analysis_id,
                metric_definition_id=m.metric_definition_id,
                value=m.value,
                is_selected=m.is_selected
            )
            for m in metrics
        ]
    )
