from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.db.models import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: DB) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.hashed_password) or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    settings = get_settings()
    token = create_access_token(str(user.id), {"email": user.email})
    return TokenResponse(access_token=token, expires_in_minutes=settings.access_token_expire_minutes)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
