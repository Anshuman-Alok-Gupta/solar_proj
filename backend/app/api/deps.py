"""FastAPI dependencies: DB session, current user, plant access control."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Path, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.models import Plant, User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]


def get_current_user(
    db: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    try:
        user_id = int(payload.get("sub", ""))
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_plant_for_user(
    plant_id: Annotated[int, Path(ge=1)],
    db: DB,
    user: CurrentUser,
) -> Plant:
    """Plant-level access control: owners and superusers only."""
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    if plant.owner_id != user.id and not user.is_superuser:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this plant")
    return plant


PlantDep = Annotated[Plant, Depends(get_plant_for_user)]


def user_plants(db: Session, user: User) -> list[Plant]:
    stmt = select(Plant).order_by(Plant.id)
    if not user.is_superuser:
        stmt = stmt.where(Plant.owner_id == user.id)
    return list(db.scalars(stmt))
