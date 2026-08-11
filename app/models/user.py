from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Boolean, Enum as SAEnum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase

from ..database import Base  # noqa: E402
import enum


class Role(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.USER)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    # JSON list of metric names to show on the dashboard; None = use default set
    dashboard_metrics: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships (use string annotations for forward references)
    health_metrics: Mapped[list["HealthMetric"]] = relationship("HealthMetric", back_populates="user", cascade="all, delete-orphan")
    sync_configs: Mapped[list["SyncConfig"]] = relationship("SyncConfig", back_populates="user", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    pending_analyses: Mapped[list["PendingAnalysis"]] = relationship("PendingAnalysis", back_populates="user", cascade="all, delete-orphan")
    unit_preferences: Mapped[list["UserUnitPreference"]] = relationship("UserUnitPreference", back_populates="user", cascade="all, delete-orphan")


class UserUnitPreference(Base):
    """Per-user preferred display unit for a metric definition."""
    __tablename__ = "user_unit_preferences"
    __table_args__ = (UniqueConstraint("user_id", "metric_definition_id", name="uq_user_metric_pref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    metric_definition_id: Mapped[int] = mapped_column(ForeignKey("metric_definitions.id"), nullable=False)
    preferred_unit: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user = relationship("User", back_populates="unit_preferences")
    metric_definition = relationship("MetricDefinition")
