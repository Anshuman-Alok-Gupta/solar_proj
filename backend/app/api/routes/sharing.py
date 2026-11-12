"""Plant sharing: invite links, members and roles.

Invites are one-time links (no e-mail server needed): the owner creates one, copies the
link and sends it however they like. Only the SHA-256 of the token is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB, CurrentUser, PlantOwnerDep
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import Invitation, PlantMember, PlantRole, User
from app.schemas.auth import TokenResponse

router = APIRouter(tags=["sharing"])

MIN_PASSWORD = 8


class InviteCreate(BaseModel):
    email: EmailStr
    role: PlantRole = PlantRole.VIEWER
    expires_days: int = Field(7, ge=1, le=30)


class RoleUpdate(BaseModel):
    role: PlantRole


class InviteAccept(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    full_name: str | None = Field(None, max_length=255)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _status(inv: Invitation) -> str:
    if inv.revoked_at:
        return "revoked"
    if inv.accepted_at:
        return "accepted"
    if inv.expires_at <= datetime.now(UTC):
        return "expired"
    return "pending"


def _inv_out(inv: Invitation) -> dict:
    return {
        "id": inv.id,
        "email": inv.email,
        "role": inv.role,
        "status": _status(inv),
        "expires_at": inv.expires_at.isoformat(),
        "accepted_at": inv.accepted_at.isoformat() if inv.accepted_at else None,
        "created_at": inv.created_at.isoformat(),
    }


@router.get("/plants/{plant_id}/members")
def list_members(plant: PlantOwnerDep, db: DB) -> dict:
    owner = db.get(User, plant.owner_id)
    members = db.scalars(
        select(PlantMember).where(PlantMember.plant_id == plant.id).order_by(PlantMember.created_at)
    ).all()
    invites = db.scalars(
        select(Invitation).where(Invitation.plant_id == plant.id).order_by(Invitation.created_at.desc())
    ).all()
    return {
        "owner": {"id": owner.id, "email": owner.email, "full_name": owner.full_name} if owner else None,
        "members": [
            {
                "user_id": m.user_id,
                "email": m.user.email,
                "full_name": m.user.full_name,
                "role": m.role,
                "created_at": m.created_at.isoformat(),
            }
            for m in members
        ],
        "invitations": [_inv_out(i) for i in invites if _status(i) == "pending"],
    }


@router.post("/plants/{plant_id}/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(payload: InviteCreate, plant: PlantOwnerDep, db: DB, user: CurrentUser) -> dict:
    email = payload.email.lower()
    owner = db.get(User, plant.owner_id)
    if owner is not None and owner.email == email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That address already owns this plant")
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None and db.get(PlantMember, (plant.id, existing.id)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That user already has access; change their role instead"
        )
    token = secrets.token_urlsafe(32)
    inv = Invitation(
        token_hash=_hash(token),
        email=email,
        plant_id=plant.id,
        role=payload.role.value,
        invited_by_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_days),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    # The raw token is returned exactly once; the frontend turns it into /invite/<token>
    return {**_inv_out(inv), "token": token}


@router.delete("/plants/{plant_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invitation(invitation_id: int, plant: PlantOwnerDep, db: DB) -> None:
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if inv.revoked_at is None and inv.accepted_at is None:
        inv.revoked_at = datetime.now(UTC)
        db.commit()


@router.patch("/plants/{plant_id}/members/{user_id}")
def update_member(user_id: int, payload: RoleUpdate, plant: PlantOwnerDep, db: DB) -> dict:
    m = db.get(PlantMember, (plant.id, user_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    m.role = payload.role.value
    db.commit()
    return {"user_id": user_id, "role": m.role}


@router.delete("/plants/{plant_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(user_id: int, plant: PlantOwnerDep, db: DB) -> None:
    m = db.get(PlantMember, (plant.id, user_id))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    db.delete(m)
    db.commit()


def _load_invite(db: Session, token: str) -> Invitation:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == _hash(token)))
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    return inv


@router.get("/invitations/{token}")
def get_invitation(token: str, db: DB) -> dict:
    """Public: what the invite link is for (only what the link holder is entitled to see)."""
    inv = _load_invite(db, token)
    return {
        "email": inv.email,
        "plant_name": inv.plant.name,
        "role": inv.role,
        "invited_by": (inv.invited_by.full_name or inv.invited_by.email) if inv.invited_by else None,
        "expires_at": inv.expires_at.isoformat(),
        "status": _status(inv),
        "account_exists": db.scalar(select(User.id).where(User.email == inv.email)) is not None,
    }


@router.post("/invitations/{token}/accept", response_model=TokenResponse)
def accept_invitation(token: str, payload: InviteAccept, db: DB) -> TokenResponse:
    inv = _load_invite(db, token)
    st = _status(inv)
    if st != "pending":
        raise HTTPException(status.HTTP_410_GONE, f"This invitation is {st}")
    user = db.scalar(select(User).where(User.email == inv.email))
    if user is None:
        if len(payload.password) < MIN_PASSWORD:
            raise HTTPException(
                422,
                f"Password must be at least {MIN_PASSWORD} characters",
            )
        user = User(
            email=inv.email,
            hashed_password=hash_password(payload.password),
            full_name=(payload.full_name or "").strip() or None,
            is_active=True,
        )
        db.add(user)
        db.flush()
    elif not user.is_active or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "An account with this e-mail already exists: enter its current password",
        )
    if inv.plant.owner_id != user.id:
        member = db.get(PlantMember, (inv.plant_id, user.id))
        if member is None:
            db.add(PlantMember(plant_id=inv.plant_id, user_id=user.id, role=inv.role))
        else:
            member.role = inv.role
    inv.accepted_at = datetime.now(UTC)
    db.commit()
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(str(user.id), {"email": user.email}),
        expires_in_minutes=settings.access_token_expire_minutes,
    )
