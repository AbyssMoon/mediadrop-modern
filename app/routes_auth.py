from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth_config import load_auth_settings
from app.database import db_dependency
from app.ldap_auth import authenticate_ldap
from app.models import Group, User
from app.modern_database import modern_db_dependency
from app.security import (
    login_ldap_user,
    login_user,
    logout_user,
    validate_csrf,
    verify_legacy_password,
)
from app.views import render

router = APIRouter()
Db = Depends(db_dependency)
ModernDb = Depends(modern_db_dependency)


def _safe_next(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if (
        parts.scheme
        or parts.netloc
        or not parts.path.startswith("/")
        or parts.path.startswith("//")
        or "\\" in value
        or "\r" in value
        or "\n" in value
    ):
        return None
    return value


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = None, modern_db: Session = ModernDb):
    auth = load_auth_settings(modern_db, request.app.state.settings)
    return render(request, "login.html", error=None, next=_safe_next(next) or "", auth_values=auth)


@router.post("/login")
@router.post("/login/submit")
def login_submit(
    request: Request,
    csrf: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Db,
    modern_db: Session = ModernDb,
):
    validate_csrf(request, csrf)
    auth = load_auth_settings(modern_db, request.app.state.settings)
    destination = _safe_next(next)

    # LDAP is deliberately tried first. If it does not authenticate the
    # submitted credentials, legacy/local MediaDrop accounts remain a fallback.
    if auth.ldap_enabled:
        ldap_identity = authenticate_ldap(auth, username.strip(), password)
        if ldap_identity is not None:
            login_ldap_user(request, ldap_identity)
            if destination:
                return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
            target = "/admin" if ldap_identity.permissions & {"edit", "admin"} else "/"
            return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)

    user = db.scalar(
        select(User)
        .where(or_(User.user_name == username, User.email_address == username))
        .options(selectinload(User.groups).selectinload(Group.permissions))
    )
    if user is None or not verify_legacy_password(password, user.password):
        return render(
            request,
            "login.html",
            error="error.invalid_credentials",
            status_code=401,
            next=destination or "",
            auth_values=auth,
        )
    login_user(request, user)
    if destination:
        return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    can_edit = user.has_permission("edit") or user.has_permission("admin") or any(
        group.group_name.casefold() in {"admin", "admins", "administrators"}
        for group in user.groups
    )
    return RedirectResponse("/admin" if can_edit else "/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    validate_csrf(request, csrf)
    logout_user(request)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout_legacy(request: Request):
    logout_user(request)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
