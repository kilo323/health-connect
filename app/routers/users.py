from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from passlib.context import CryptContext
from jose import jwt, JWTError
from difflib import SequenceMatcher
import json
import os
from typing import List

from ..database import get_db, async_session_factory
from ..models.user import User, Role, UserUnitPreference
from ..models.health_data import MetricDefinition
from ..schemas.auth import (
    UserCreate, UserUpdate, UserResponse, LoginResponse,
    ProfileUpdate, PasswordChange, UnitPreferenceSet, UnitPreferenceResponse,
    MetricSearchResult, DashboardMetricsSet, DashboardMetricsResponse,
)

router = APIRouter(tags=["Users"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")


async def get_current_user(request: Request) -> User:
    """Get the current authenticated user from JWT token (header or query param)."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # Fall back to query parameter (needed for SSE/EventSource which can't set headers)
        token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token claims")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == int(user_id_str)))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User does not exist or is inactive")

    return user


async def get_current_active_user(request: Request) -> User:
    """Get the current active user (must be authenticated and active)."""
    user = await get_current_user(request)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive account")
    return user


async def get_current_active_admin(request: Request) -> User:
    """Get the current admin user (must be authenticated and have admin role)."""
    user = await get_current_user(request)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive account")
    user_role = user.role.value if isinstance(user.role, Role) else str(user.role)
    if user_role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_active_user)):
    """Get current authenticated user profile"""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role.value if isinstance(current_user.role, Role) else current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at
    )


def _available_units(definition: MetricDefinition) -> list[str]:
    """Return all displayable units for a metric definition.

    The canonical unit plus every unit listed in unit_conversions keys.
    """
    units: list[str] = []
    if definition.unit:
        units.append(definition.unit)
    if definition.unit_conversions:
        try:
            conversions = json.loads(definition.unit_conversions)
            if isinstance(conversions, dict):
                for u in conversions.keys():
                    if u and u not in units:
                        units.append(u)
        except (json.JSONDecodeError, TypeError):
            pass
    return units


@router.put("/me", response_model=UserResponse)
async def update_profile(
    profile_data: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update the current user's own profile (email)."""
    if profile_data.email:
        result = await db.execute(
            select(User).where(User.email == profile_data.email, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Email already in use")

    current_user.email = profile_data.email
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        role=current_user.role.value if isinstance(current_user.role, Role) else current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )


@router.put("/me/password")
async def change_password(
    password_data: PasswordChange,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Change the current user's password."""
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    if not pwd_context.verify(password_data.current_password.encode("utf-8")[:72], current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if len(password_data.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    current_user.hashed_password = pwd_context.hash(password_data.new_password.encode("utf-8")[:72])
    db.add(current_user)
    await db.commit()

    return {"message": "Password updated successfully"}


@router.get("/me/unit-preferences", response_model=list[UnitPreferenceResponse])
async def get_unit_preferences(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List the current user's unit preferences."""
    result = await db.execute(
        select(UserUnitPreference, MetricDefinition)
        .join(MetricDefinition, UserUnitPreference.metric_definition_id == MetricDefinition.id)
        .where(UserUnitPreference.user_id == current_user.id)
        .order_by(MetricDefinition.name)
    )
    rows = result.all()
    return [
        UnitPreferenceResponse(
            id=pref.id,
            metric_definition_id=pref.metric_definition_id,
            metric_name=defn.name,
            canonical_unit=defn.unit,
            preferred_unit=pref.preferred_unit,
        )
        for pref, defn in rows
    ]


@router.put("/me/unit-preferences")
async def set_unit_preference(
    pref: UnitPreferenceSet,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Set or update the preferred display unit for a metric definition."""
    # Validate the metric definition exists
    result = await db.execute(
        select(MetricDefinition).where(MetricDefinition.id == pref.metric_definition_id)
    )
    definition = result.scalar_one_or_none()
    if not definition:
        raise HTTPException(status_code=404, detail="Metric definition not found")

    # Validate the unit is available for this metric
    available = _available_units(definition)
    if pref.preferred_unit not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Unit '{pref.preferred_unit}' is not available for '{definition.name}'. Available: {available}",
        )

    # Upsert
    result = await db.execute(
        select(UserUnitPreference).where(
            UserUnitPreference.user_id == current_user.id,
            UserUnitPreference.metric_definition_id == pref.metric_definition_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.preferred_unit = pref.preferred_unit
    else:
        db.add(UserUnitPreference(
            user_id=current_user.id,
            metric_definition_id=pref.metric_definition_id,
            preferred_unit=pref.preferred_unit,
        ))
    await db.commit()

    return {"message": f"Preference set: {definition.name} -> {pref.preferred_unit}"}


@router.delete("/me/unit-preferences/{metric_definition_id}")
async def delete_unit_preference(
    metric_definition_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Remove a unit preference (revert to canonical unit)."""
    result = await db.execute(
        select(UserUnitPreference).where(
            UserUnitPreference.user_id == current_user.id,
            UserUnitPreference.metric_definition_id == metric_definition_id,
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        raise HTTPException(status_code=404, detail="Preference not found")

    await db.delete(pref)
    await db.commit()
    return {"message": "Preference removed"}


@router.get("/me/unit-preferences/search", response_model=list[MetricSearchResult])
async def search_metrics_for_units(
    q: str = Query(default="", description="Search query for metric name"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Fuzzy search metric definitions and return their available units with current preference."""
    # Get all definitions that have unit conversions (i.e., units to choose from)
    result = await db.execute(select(MetricDefinition).order_by(MetricDefinition.name))
    all_defs = result.scalars().all()

    # Get user's current preferences
    pref_result = await db.execute(
        select(UserUnitPreference).where(UserUnitPreference.user_id == current_user.id)
    )
    user_prefs = {p.metric_definition_id: p.preferred_unit for p in pref_result.scalars().all()}

    # Build results with fuzzy matching
    query_lower = q.strip().lower()
    scored: list[tuple[float, MetricDefinition]] = []

    for d in all_defs:
        units = _available_units(d)
        if len(units) < 2:
            continue  # Skip metrics with only one unit — nothing to choose

        name_lower = d.name.lower()
        # Also match against aliases
        aliases: list[str] = []
        if d.aliases:
            try:
                parsed = json.loads(d.aliases)
                if isinstance(parsed, list):
                    aliases = [a.lower() for a in parsed if isinstance(a, str)]
            except (json.JSONDecodeError, TypeError):
                pass

        if not query_lower:
            scored.append((1.0, d))
            continue

        # Exact or substring match
        if query_lower in name_lower:
            scored.append((1.0, d))
            continue
        if any(query_lower in a for a in aliases):
            scored.append((0.95, d))
            continue

        # Fuzzy match on name
        best = SequenceMatcher(None, query_lower, name_lower).ratio()
        for alias in aliases:
            best = max(best, SequenceMatcher(None, query_lower, alias).ratio())

        if best >= 0.5:
            scored.append((best, d))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        MetricSearchResult(
            id=d.id,
            name=d.name,
            category=d.category,
            canonical_unit=d.unit,
            available_units=_available_units(d),
            preferred_unit=user_prefs.get(d.id),
        )
        for _, d in scored[:20]
    ]


# ── Dashboard metric selection ────────────────────────────────────────────────


@router.get("/me/dashboard-metrics", response_model=DashboardMetricsResponse)
async def get_dashboard_metrics(
    current_user: User = Depends(get_current_active_user),
):
    """Return the user's selected dashboard metrics. Falls back to the default set."""
    if current_user.dashboard_metrics:
        try:
            names = json.loads(current_user.dashboard_metrics)
            if isinstance(names, list) and names:
                return DashboardMetricsResponse(metric_names=names, is_default=False)
        except (json.JSONDecodeError, TypeError):
            pass
    return DashboardMetricsResponse(metric_names=[], is_default=True)


@router.put("/me/dashboard-metrics")
async def set_dashboard_metrics(
    body: DashboardMetricsSet,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Set the user's dashboard metric selection. Pass an empty list to revert to default."""
    if not body.metric_names:
        current_user.dashboard_metrics = None
    else:
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for n in body.metric_names:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        current_user.dashboard_metrics = json.dumps(unique)
    db.add(current_user)
    await db.commit()
    return {"message": "Dashboard metrics updated"}


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    """Create a new user (admin only)"""
    # Check if username is taken
    result = await db.execute(select(User).where(User.username == user_data.username))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    if user_data.email:
        result = await db.execute(select(User).where(User.email == user_data.email))
        existing_email = result.scalar_one_or_none()
        if existing_email:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=pwd_context.hash(user_data.password.encode("utf-8")[:72]),
        role=Role.ADMIN if user_data.role == "admin" else Role.USER,
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    return UserResponse(
        id=new_user.id,
        username=new_user.username,
        email=new_user.email,
        role=new_user.role.value if isinstance(new_user.role, Role) else new_user.role,
        is_active=new_user.is_active,
        created_at=new_user.created_at
    )


@router.get("", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    """List all users (admin only)"""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users_list = result.scalars().all()

    return [
        UserResponse(
            id=u.id,
            username=u.username,
            email=u.email,
            role=u.role.value if isinstance(u.role, Role) else u.role,
            is_active=u.is_active,
            created_at=u.created_at
        )
        for u in users_list
    ]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    """Get a specific user by ID (admin only)"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role.value if isinstance(user.role, Role) else user.role,
        is_active=user.is_active,
        created_at=user.created_at
    )


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    """Update a user's details (admin only)"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check username uniqueness if changed
    if user_data.username != user.username:
        existing = await db.execute(select(User).where(User.username == user_data.username))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already exists")

    user.username = user_data.username
    user.email = user_data.email
    user.role = Role.ADMIN if user_data.role == "admin" else Role.USER
    await db.commit()

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role.value if isinstance(user.role, Role) else user.role,
        is_active=user.is_active,
        created_at=user.created_at
    )


@router.put("/{user_id}/role")
async def update_user_role(
    user_id: int,
    new_role: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    """Update a user's role (admin only). Only admins can change roles."""
    if new_role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role. Must be 'user' or 'admin'")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = Role.ADMIN if new_role == "admin" else Role.USER
    await db.commit()

    return {"message": f"User role updated to {new_role}", "user_id": user_id}


@router.put("/{user_id}/activate")
async def toggle_user_activation(
    user_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_admin)
):
    """Activate or deactivate a user (admin only)"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent admins from deactivating themselves
    if current_user.id == user_id and is_active == False:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    user.is_active = is_active
    await db.commit()

    return {"message": f"User {'activated' if is_active else 'deactivated'}", "user_id": user_id}
