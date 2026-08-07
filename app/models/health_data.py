from sqlalchemy import String, Text, DateTime, Boolean, Float, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone
import enum

# Import Base for model inheritance
from ..database import Base  # noqa: E402


class SyncMode(str, enum.Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class DocumentStatus(str, enum.Enum):
    UNPROCESSED = "unprocessed"
    PENDING_ANALYSIS = "pending_analysis"
    ANALYZED_PENDING_REVIEW = "analyzed_pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class HealthMetric(Base):
    __tablename__ = "health_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "steps", "heart_rate"
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(50))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(100))  # "google_health_connect", "manual"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="health_metrics")


class SyncConfig(Base):
    __tablename__ = "sync_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    data_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "steps", "sleep"
    sync_mode: Mapped[SyncMode] = mapped_column(SAEnum(SyncMode), default=SyncMode.READ_ONLY)
    is_enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="sync_configs")


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(50))  # e.g., "Blood Work", "Vital Signs"
    unit: Mapped[str] = mapped_column(String(50))
    data_type: Mapped[str] = mapped_column(String(20), default="float")  # float, int, string, boolean
    description: Mapped[str | None] = mapped_column(Text)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)  # local path or Nextcloud path
    file_type: Mapped[str] = mapped_column(String(50))  # pdf, image, text
    status: Mapped[DocumentStatus] = mapped_column(SAEnum(DocumentStatus), default=DocumentStatus.UNPROCESSED)
    source: Mapped[str] = mapped_column(String(100))  # "upload", "nextcloud"
    size_bytes: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="documents")
    pending_analyses = relationship("PendingAnalysis", back_populates="document", cascade="all, delete-orphan")


class PendingAnalysis(Base):
    __tablename__ = "pending_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    raw_analysis: Mapped[str | None] = mapped_column(Text)  # JSON string of LLM output
    status: Mapped[str] = mapped_column(String(50), default="pending_review")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    document = relationship("Document", back_populates="pending_analyses")
    user = relationship("User", back_populates="pending_analyses")
    pending_metrics = relationship("PendingMetric", back_populates="pending_analysis", cascade="all, delete-orphan")


class PendingMetric(Base):
    __tablename__ = "pending_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    pending_analysis_id: Mapped[int] = mapped_column(ForeignKey("pending_analyses.id"), nullable=False)
    metric_definition_id: Mapped[int | None] = mapped_column(ForeignKey("metric_definitions.id"))
    value: Mapped[str | None] = mapped_column(Text)  # stored as string, converted on approve
    is_selected: Mapped[bool] = mapped_column(default=False)

    pending_analysis = relationship("PendingAnalysis", back_populates="pending_metrics")
    metric_definition = relationship("MetricDefinition")


# Relationship fixes should be done in __init__.py to avoid circular imports
__all__ = ["HealthMetric", "SyncConfig", "Document", "PendingAnalysis", "PendingMetric"]
