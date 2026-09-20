from __future__ import annotations

import hashlib
import os
import secrets
from functools import wraps

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def verify_legacy_password(password: str, stored: str | None) -> bool:
    """Validate MediaDrop's historical 40-char salt + SHA1 digest format."""
    if not stored or len(stored) != 80:
        return False
    salt = stored[:40]
    digest = hashlib.sha1(password.encode("utf-8") + salt.encode("ascii")).hexdigest()
    return secrets.compare_digest(stored[40:], digest)


def hash_legacy_password(password: str) -> str:
    salt = hashlib.sha1(os.urandom(60)).hexdigest()
    digest = hashlib.sha1(password.encode("utf-8") + salt.encode("ascii")).hexdigest()
    return salt + digest


def login_user(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(32)


def logout_user(request: Request) -> None:
    request.session.clear()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def validate_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.scalar(select(User).where(User.id == int(user_id)))


def require_admin(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if user.has_permission("edit") or user.has_permission("admin"):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
