from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class HealthMetricCreate(BaseModel):
    metric_type: str
    value: float
    unit: Optional[str] = None
    recorded_at: Optional[datetime] = None
    source: str = "manual"


class HealthMetricResponse(BaseModel):
    id: int
    user_id: int
    metric_type: str
    value: float
    unit: Optional[str]
    recorded_at: datetime
    source: str
    created_at: datetime

    class Config:
        from_attributes = True


class SyncConfigCreate(BaseModel):
    data_type: str
    sync_mode: str = "read_only"
    is_enabled: bool = True


class SyncConfigResponse(BaseModel):
    id: int
    user_id: int
    data_type: str
    sync_mode: str
    is_enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MetricDefinitionCreate(BaseModel):
    name: str
    category: Optional[str] = None
    unit: Optional[str] = None
    data_type: str = "float"
    description: Optional[str] = None


class MetricDefinitionResponse(BaseModel):
    id: int
    name: str
    category: Optional[str]
    unit: Optional[str]
    data_type: str
    description: Optional[str]

    class Config:
        from_attributes = True


class DocumentCreate(BaseModel):
    filename: str
    file_path: str
    file_type: Optional[str] = None
    source: str = "upload"
    size_bytes: int = 0


class DocumentResponse(BaseModel):
    id: int
    user_id: int
    filename: str
    file_path: str
    file_type: Optional[str]
    status: str
    source: str
    size_bytes: int
    created_at: datetime

    class Config:
        from_attributes = True


class PendingAnalysisResponse(BaseModel):
    id: int
    document_id: int
    user_id: int
    raw_analysis: Optional[str]
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class PendingMetricResponse(BaseModel):
    id: int
    pending_analysis_id: int
    metric_definition_id: Optional[int]
    value: Optional[str]
    is_selected: bool

    class Config:
        from_attributes = True


class AnalysisResult(BaseModel):
    pending_analysis: PendingAnalysisResponse
    metrics: List[PendingMetricResponse]
