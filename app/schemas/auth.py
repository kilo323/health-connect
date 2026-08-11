from pydantic import BaseModel, EmailStr
from typing import Optional
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


class MetricSearchResult(BaseModel):
    id: int
    name: str
    category: str | None
    canonical_unit: str | None
    available_units: list[str]
    preferred_unit: str | None = None  # user's current preference, if set
