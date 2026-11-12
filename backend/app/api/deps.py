"""FastAPI dependencies: DB session, current user, plant access control."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.models import Plant, PlantMember, PlantRole, User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)

DB = Annotated[Session, Depends(get_db)]

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Viewers are read-only, but asking the AI analyst is a read in spirit (it only runs read tools)
_VIEWER_WRITE_ALLOWED = ("/ai/",)


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


def plant_role(db: Session, plant: Plant, user: User) -> str | None:
    """'owner' for owners and superusers, the member role for shared users, else None."""
    if plant.owner_id == user.id or user.is_superuser:
        return "owner"
    m = db.get(PlantMember, (plant.id, user.id))
    return m.role if m else None


def get_plant_for_user(
    plant_id: Annotated[int, Path(ge=1)],
    request: Request,
    db: DB,
    user: CurrentUser,
) -> Plant:
    """Plant-level access control: owners, superusers and invited members.

    Viewers may only read (plus ask the AI analyst); editors may change data and settings.
    Sharing and deleting the plant are owner-only (see ``PlantOwnerDep``).
    """
    plant = db.get(Plant, plant_id)
    if plant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plant not found")
    role = plant_role(db, plant, user)
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have access to this plant")
    if (
        role == PlantRole.VIEWER.value
        and request.method not in _SAFE_METHODS
        and not any(p in request.url.path for p in _VIEWER_WRITE_ALLOWED)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "View-only access: ask the plant owner to make changes"
        )
    return plant


PlantDep = Annotated[Plant, Depends(get_plant_for_user)]


def get_owned_plant(plant: PlantDep, db: DB, user: CurrentUser) -> Plant:
    if plant_role(db, plant, user) != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the plant owner can do this")
    return plant


PlantOwnerDep = Annotated[Plant, Depends(get_owned_plant)]


def user_plants(db: Session, user: User) -> list[Plant]:
    stmt = select(Plant).order_by(Plant.id)
    if not user.is_superuser:
        shared = select(PlantMember.plant_id).where(PlantMember.user_id == user.id)
        stmt = stmt.where(or_(Plant.owner_id == user.id, Plant.id.in_(shared)))
    return list(db.scalars(stmt))
