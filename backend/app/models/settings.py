from sqlalchemy import String, Text, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class ScheduleConfig(Base):
    __tablename__ = "schedule_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(default=False)
    cron_expression: Mapped[str] = mapped_column(String(100), default="0 2 * * *")
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
