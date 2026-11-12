"""First-run bootstrap: admin user. Plants are created by the user (or demo seeding)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import User

log = logging.getLogger(__name__)


def ensure_admin_user(db: Session) -> User:
    settings = get_settings()
    email = settings.admin_email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            hashed_password=hash_password(settings.admin_password),
            full_name="Administrator",
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        log.info("Created bootstrap admin user %s", email)
    return user
