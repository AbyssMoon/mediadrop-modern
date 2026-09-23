from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ldap_auth import LdapIdentity
from app.models import Group, User


@dataclass(frozen=True)
class AuthPrincipal:
    backend: str
    username: str
    display_name: str
    email: str
    permissions: frozenset[str]
    groups: tuple[str, ...] = ()
    local_user_id: int | None = None

    def has_permission(self, name: str) -> bool:
        return name in self.permissions

    @property
    def can_edit(self) -> bool:
        return "edit" in self.permissions or self.is_admin

    @property
    def is_admin(self) -> bool:
        if "admin" in self.permissions:
            return True
        # Legacy MediaDrop commonly used a group literally named "admins"
        # while granting it only the historical "edit" permission. Preserve
        # that install/restore behavior without changing the old schema.
        return self.backend == "local" and any(
            group.casefold() in {"admin", "admins", "administrators"}
            for group in self.groups
        )


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


def _local_principal(user: User) -> AuthPrincipal:
    permissions = {
        permission.permission_name
        for group in user.groups
        for permission in group.permissions
    }
    groups = tuple(group.group_name for group in user.groups)
    return AuthPrincipal(
        backend="local",
        username=user.user_name,
        display_name=user.display_name or user.user_name,
        email=user.email_address,
        permissions=frozenset(permissions),
        groups=groups,
        local_user_id=user.id,
    )


def login_user(request: Request, user: User) -> None:
    """Log in a legacy/local MediaDrop user."""
    request.session.clear()
    request.session["auth_backend"] = "local"
    request.session["user_id"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(32)


def login_ldap_user(request: Request, identity: LdapIdentity) -> None:
    request.session.clear()
    request.session["auth_backend"] = "ldap"
    request.session["ldap_username"] = identity.username
    request.session["ldap_display_name"] = identity.display_name
    request.session["ldap_email"] = identity.email
    request.session["ldap_permissions"] = sorted(identity.permissions)
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


def session_has_identity(session: dict) -> bool:
    backend = session.get("auth_backend")
    if backend == "ldap":
        return bool(session.get("ldap_username"))
    if backend == "local":
        return bool(session.get("user_id"))
    # Compatibility with cookies issued by v0.4 and earlier.
    return bool(session.get("user_id"))


def current_user(request: Request, db: Session) -> User | None:
    if request.session.get("auth_backend") == "ldap":
        return None
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.scalar(
        select(User)
        .where(User.id == int(user_id))
        .options(selectinload(User.groups).selectinload(Group.permissions))
    )
    if user is None:
        return None

    # Local account state lives in the modern sidecar DB so the legacy schema
    # stays untouched. Re-check it on every request so disabling a user also
    # invalidates an already-issued local session.
    from app.local_accounts import is_local_user_enabled

    with request.app.state.modern_session_factory() as modern_db:
        if not is_local_user_enabled(modern_db, user.id):
            request.session.clear()
            return None
    return user


def current_principal(request: Request, db: Session) -> AuthPrincipal | None:
    backend = request.session.get("auth_backend")
    if backend == "ldap":
        username = str(request.session.get("ldap_username") or "")
        if not username:
            return None
        return AuthPrincipal(
            backend="ldap",
            username=username,
            display_name=str(request.session.get("ldap_display_name") or username),
            email=str(request.session.get("ldap_email") or ""),
            permissions=frozenset(str(item) for item in request.session.get("ldap_permissions", [])),
            groups=(),
        )
    user = current_user(request, db)
    return _local_principal(user) if user is not None else None


def require_authenticated(request: Request, db: Session) -> AuthPrincipal:
    principal = current_principal(request, db)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return principal


def require_editor(request: Request, db: Session) -> AuthPrincipal:
    principal = require_authenticated(request, db)
    if principal.can_edit:
        return principal
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def require_admin(request: Request, db: Session) -> AuthPrincipal:
    principal = require_authenticated(request, db)
    if principal.is_admin:
        return principal
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
