from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import os
import uuid
import json
from datetime import datetime, timezone
from typing import List

from ..database import get_db
from ..models.user import User
from ..models.health_data import Document, PendingAnalysis, PendingMetric, MetricDefinition
from ..schemas.health_data import (
    HealthMetricCreate, HealthMetricResponse,
    SyncConfigCreate, SyncConfigResponse,
    MetricDefinitionCreate, MetricDefinitionResponse,
    DocumentCreate, DocumentResponse,
    AnalysisResult
)
from ..services.google_health import GoogleHealthService
from ..services.nextcloud import NextcloudService
from ..services.llm import LLMService

router = APIRouter(prefix="/health", tags=["Health Data"])


@router.get("/metrics/{metric_type}", response_model=List[HealthMetricResponse])
async def get_metrics(metric_type: str, limit: int = 100):
    """Get health metrics for the current user"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    result = await db.execute(
        select(HealthMetric).where(
            HealthMetric.user_id == user_id,
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
async def create_metric(metric_data: HealthMetricCreate):
    """Manually add a health metric"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    
    new_metric = HealthMetric(
        user_id=user_id,
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


@router.get("/sync/configs", response_model=List[SyncConfigResponse])
async def get_sync_configs():
    """Get sync configurations for the current user"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    result = await db.execute(
        select(SyncConfig).where(SyncConfig.user_id == user_id)
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
async def create_sync_config(config_data: SyncConfigCreate):
    """Create a sync configuration"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    
    new_config = SyncConfig(
        user_id=user_id,
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


@router.post("/google-health/connect")
async def connect_google_health():
    """Get OAuth URL to connect Google Health Connect"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    google_service = GoogleHealthService()
    oauth_url = await google_service.get_oauth_url(user_id)
    
    return {"oauth_url": oauth_url}


@router.post("/google-health/callback")
async def google_health_callback(code: str):
    """Handle OAuth callback from Google Health Connect"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    google_service = GoogleHealthService()
    result = await google_service.exchange_code(user_id, code)
    
    return result


@router.get("/documents", response_model=List[DocumentResponse])
async def list_documents():
    """List documents for the current user"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    result = await db.execute(
        select(Document).where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
    )
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
async def upload_document(file: UploadFile = File(...)):
    """Upload a medical document"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    
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
        user_id=user_id,
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


@router.post("/documents/{document_id}/analyze")
async def analyze_document(document_id: int):
    """Trigger LLM analysis of a document"""
    # In production, get user_id from JWT token
    user_id = 1  # Placeholder
    
    db = await next(get_db())
    
    # Get the document
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Read file content (simplified - would need proper parsing for PDFs)
    try:
        with open(document.file_path, 'r') as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read document: {str(e)}")
    
    # Analyze with LLM
    llm_service = LLMService()
    analysis_result = await llm_service.analyze_document(user_id, content)
    
    # Create pending analysis record
    pending_analysis = PendingAnalysis(
        document_id=document.id,
        user_id=user_id,
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
