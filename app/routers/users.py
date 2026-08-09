from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from passlib.context import CryptContext
from jose import jwt, JWTError
import os
from typing import List

from ..database import get_db, async_session_factory
from ..models.user import User, Role
from ..schemas.auth import UserCreate, UserUpdate, UserResponse, LoginResponse

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
