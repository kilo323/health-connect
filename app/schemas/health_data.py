from pydantic import BaseModel, field_validator
from typing import Optional, List, Dict
from datetime import datetime
import json


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
    source_document: Optional[str] = None
    definition_id: Optional[int] = None
    reference_range: Optional[str] = None
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


class ReferenceRange(BaseModel):
    sex: Optional[str] = None  # "male", "female", or None for both
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    low: Optional[float] = None
    high: Optional[float] = None
    operator: Optional[str] = None  # "<", ">", "<=", ">=" for single-bound ranges
    unit: Optional[str] = None
    source: Optional[str] = None  # e.g. "mayo", "quest", "manual"


class MetricDefinitionCreate(BaseModel):
    name: str
    category: Optional[str] = None
    unit: Optional[str] = None
    data_type: str = "float"
    description: Optional[str] = None
    aliases: Optional[List[str]] = []
    reference_ranges: Optional[List[ReferenceRange]] = []
    unit_conversions: Optional[dict] = {}  # {"unit_name": multiplier_to_canonical}


class MetricDefinitionResponse(BaseModel):
    id: int
    name: str
    category: Optional[str]
    unit: Optional[str]
    data_type: str
    description: Optional[str]
    aliases: Optional[List[str]] = []
    reference_ranges: Optional[List[ReferenceRange]] = []
    unit_conversions: Optional[dict] = {}

    class Config:
        from_attributes = True

    @field_validator("aliases", mode="before")
    @classmethod
    def parse_aliases(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v

    @field_validator("reference_ranges", mode="before")
    @classmethod
    def parse_reference_ranges(cls, v):
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v

    @field_validator("unit_conversions", mode="before")
    @classmethod
    def parse_unit_conversions(cls, v):
        if v is None or v == "":
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
        return v


class UnmatchedMetric(BaseModel):
    metric_type: str
    count: int
    latest_value: Optional[str] = None
    latest_unit: Optional[str] = None
    documents: List[str] = []


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
    test_date: Optional[datetime] = None
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
