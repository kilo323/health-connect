from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from passlib.context import CryptContext
from jose import jwt, JWTError
import os
from typing import List

from ..database import get_db
from ..models.user import User, Role
from ..schemas.auth import UserResponse, LoginResponse

router = APIRouter(tags=["Users"])

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")


async def get_current_user(request: Request) -> User:
    """Get the current authenticated user from JWT token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = auth_header[7:]  # Remove "Bearer " prefix
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id_str = payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token claims")
    except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    async with get_db() as db:
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
