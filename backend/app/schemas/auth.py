from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserOut(ORMModel):
    id: int
    email: str
    full_name: str | None
    is_active: bool
    is_superuser: bool
    created_at: datetime
