from pydantic import BaseModel, EmailStr
from typing import Literal, Optional
from datetime import datetime


class UserCreate(BaseModel):
    username: str
    email: EmailStr | None = None
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    username: str
    email: EmailStr | None = None
    role: str = "user"


class UserLogin(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    email: str | None
    role: str
    is_active: bool
    created_at: datetime
    timezone: str | None = None

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    user: UserResponse
    token: Token


class ProfileUpdate(BaseModel):
    email: EmailStr | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UnitPreferenceSet(BaseModel):
    metric_definition_id: int
    preferred_unit: str


class UnitPreferenceResponse(BaseModel):
    id: int
    metric_definition_id: int
    metric_name: str
    canonical_unit: str | None
    preferred_unit: str

    class Config:
        from_attributes = True


class UnitSystemSet(BaseModel):
    """Global measurement system. Send null to fall back to per-metric units only."""
    unit_system: Literal["metric", "imperial"] | None = None


class TimezoneInfo(BaseModel):
    """Effective timezone for day bucketing and how it was resolved."""
    timezone: str              # effective IANA name (user override or server default)
    user_timezone: str | None  # explicit override, None = server default
    server_timezone: str


class TimezoneSet(BaseModel):
    """Send null to fall back to the server timezone."""
    timezone: str | None = None


class MetricUnitView(BaseModel):
    """One metric definition with its resolved display state for the current user."""
    id: int
    name: str
    category: str | None
    canonical_unit: str | None
    available_units: list[str]
    aliases: list[str] = []
    unit_conversions: dict = {}
    preferred_unit: str | None = None      # explicit per-metric override, if any
    system_unit: str | None = None         # what the global system selects, if anything
    effective_unit: str | None = None      # what will actually be displayed


class UnitsOverview(BaseModel):
    """Everything the client needs to render units and pick new preferences."""
    unit_system: str | None
    metrics: list[MetricUnitView]


class DashboardMetricsSet(BaseModel):
    """Set the list of metric names shown on the dashboard. Empty list = revert to default."""
    metric_names: list[str]


class DashboardMetricsResponse(BaseModel):
    """The user's dashboard metric selection. is_default=True when no custom selection is stored."""
    metric_names: list[str]
    is_default: bool
