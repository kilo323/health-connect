from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File
from fastapi.responses import RedirectResponse
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
from ..models.health_data import Document, PendingAnalysis, PendingMetric, MetricDefinition, HealthMetric
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


@router.get("/metrics", response_model=List[HealthMetricResponse])
async def list_all_metrics(
    metric_type: str = None,
    limit: int = 100,
    current_user: UserResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all health metrics for the current user, optionally filtered by type"""
    query = select(HealthMetric).where(HealthMetric.user_id == current_user.id)
    if metric_type:
        query = query.where(HealthMetric.metric_type == metric_type)
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
            created_at=m.created_at
        )
        for m in metrics
    ]


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
        unit=metric_data.unit,
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
    """Check if Google Health Connect is linked for the current user."""
    result = await db.execute(
        select(AppSettings).where(AppSettings.key == f"google_health_tokens_{current_user.id}")
    )
    tokens = result.scalar_one_or_none()
    is_linked = tokens is not None and bool(tokens.value)
    return {"is_linked": is_linked}


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
        "scope": "https://www.googleapis.com/auth/fitness.activity.read https://www.googleapis.com/auth/fitness.body.read https://www.googleapis.com/auth/fitness.heart_rate.read https://www.googleapis.com/auth/fitness.sleep.read https://www.googleapis.com/auth/fitness.blood_pressure.read https://www.googleapis.com/auth/fitness.blood_glucose.read",
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
        existing.description = "Google Fit OAuth tokens"
    else:
        db.add(AppSettings(key=token_key, value=encrypted_tokens, description="Google Fit OAuth tokens"))

    await db.commit()

    # Redirect back to the frontend settings page
    return RedirectResponse(url="http://localhost:3000/settings")


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
            status=d.status.value if isinstance(d.status, type) else d.status,
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

    # Import each finding as a health metric
    imported = 0
    for finding in findings:
        try:
            value_str = str(finding.get("value", "")).strip()
            if not value_str:
                continue
            value = float(value_str)
        except (ValueError, TypeError):
            continue

        metric = HealthMetric(
            user_id=current_user.id,
            metric_type=finding.get("metric_name", "unknown"),
            value=value,
            unit=finding.get("unit", ""),
            recorded_at=datetime.now(timezone.utc),
            source="document_analysis",
        )
        db.add(metric)
        imported += 1

    # Update analysis status
    pa.status = "approved"

    # Update document status
    doc_result = await db.execute(select(Document).where(Document.id == pa.document_id))
    doc = doc_result.scalar_one_or_none()
    if doc:
        doc.status = "approved"

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

    pa.status = "rejected"

    doc_result = await db.execute(select(Document).where(Document.id == pa.document_id))
    doc = doc_result.scalar_one_or_none()
    if doc:
        doc.status = "rejected"

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
    
    # Read file content based on type
    try:
        if document.file_type in ('text', 'csv', 'json', 'xml'):
            with open(document.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        elif document.file_type == 'pdf':
            import pdfplumber
            with pdfplumber.open(document.file_path) as pdf:
                content = "\n".join(page.extract_text() or "" for page in pdf.pages)
        elif document.file_type == 'image':
            from PIL import Image
            import pytesseract
            img = Image.open(document.file_path)
            content = pytesseract.image_to_string(img)
        else:
            with open(document.file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read document: {str(e)}")
    
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="Document appears to be empty or unreadable")
    
    # Analyze with LLM
    llm_service = LLMService()
    try:
        analysis_result = await llm_service.analyze_document(current_user.id, content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM analysis failed: {str(e)}")
    
    # Create or update pending analysis record
    existing_analysis = await db.execute(
        select(PendingAnalysis).where(PendingAnalysis.document_id == document.id)
    )
    pending_analysis = existing_analysis.scalar_one_or_none()

    if pending_analysis:
        pending_analysis.raw_analysis = json.dumps(analysis_result)
        pending_analysis.status = "pending_review"
    else:
        pending_analysis = PendingAnalysis(
            document_id=document.id,
            user_id=current_user.id,
            raw_analysis=json.dumps(analysis_result),
            status="pending_review"
        )
        db.add(pending_analysis)
    
    # Update document status
    document.status = "analyzed_pending_review"
    
    await db.commit()
    await db.refresh(pending_analysis)
    
    return {
        "status": "success",
        "pending_analysis_id": pending_analysis.id,
        "analysis": analysis_result
    }


@router.get("/metrics/definitions", response_model=List[MetricDefinitionResponse])
async def list_metric_definitions():
    """List all metric definitions"""
    db = await next(get_db())
    result = await db.execute(select(MetricDefinition).order_by(MetricDefinition.name))
    definitions = result.scalars().all()
    
    return [
        MetricDefinitionResponse(
            id=d.id,
            name=d.name,
            category=d.category,
            unit=d.unit,
            data_type=d.data_type,
            description=d.description
        )
        for d in definitions
    ]


@router.post("/metrics/definitions", response_model=MetricDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_metric_definition(definition_data: MetricDefinitionCreate):
    """Create a new metric definition (admin only)"""
    db = await next(get_db())
    
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
    
    return MetricDefinitionResponse(
        id=new_definition.id,
        name=new_definition.name,
        category=new_definition.category,
        unit=new_definition.unit,
        data_type=new_definition.data_type,
        description=new_definition.description
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

        output.append({
            "id": pa.id,
            "document_id": pa.document_id,
            "filename": filename,
            "status": pa.status,
            "analysis_summary": summary,
            "metrics_extracted": [
                {"name": f.get("metric_name", ""), "value": f.get("value", ""), "unit": f.get("unit", ""), "is_selected": True}
                for f in findings
            ],
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
