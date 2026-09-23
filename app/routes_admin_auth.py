from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.audit import audit_event
from app.auth_config import (
    AuthSettings,
    SESSION_TTL_OPTIONS,
    load_auth_settings,
    save_auth_settings,
)
from app.database import db_dependency
from app.modern_database import modern_db_dependency
from app.security import require_admin, validate_csrf
from app.views import render

router = APIRouter(prefix="/admin/settings/auth", tags=["admin-auth"])
Db = Depends(db_dependency)
ModernDb = Depends(modern_db_dependency)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def auth_settings_page(
    request: Request,
    saved: bool = False,
    db: Session = Db,
    modern_db: Session = ModernDb,
):
    require_admin(request, db)
    values = load_auth_settings(modern_db, request.app.state.settings)
    return render(
        request,
        "admin/auth_settings.html",
        auth_values=values,
        ttl_options=SESSION_TTL_OPTIONS,
        saved=saved,
        error=None,
        has_bind_password=bool(values.ldap_bind_password),
    )


@router.post("")
@router.post("/")
def auth_settings_save(
    request: Request,
    csrf: str = Form(...),
    require_login: str | None = Form(None),
    session_ttl_seconds: int = Form(...),
    ldap_enabled: str | None = Form(None),
    ldap_url: str = Form(""),
    ldap_bind_dn: str = Form(""),
    ldap_bind_password: str = Form(""),
    clear_bind_password: str | None = Form(None),
    ldap_base_dn: str = Form(""),
    ldap_user_base_dn: str = Form("OU=People"),
    ldap_user_filter: str = Form("(objectCategory=Person)"),
    ldap_username_attribute: str = Form("sAMAccountName"),
    ldap_display_name_attribute: str = Form("displayName"),
    ldap_email_attribute: str = Form("mail"),
    ldap_member_of_attribute: str = Form("memberOf"),
    ldap_access_group: str = Form(""),
    ldap_editor_group: str = Form(""),
    ldap_admin_group: str = Form(""),
    ldap_ca_cert_file: str = Form(""),
    db: Session = Db,
    modern_db: Session = ModernDb,
):
    principal = require_admin(request, db)
    validate_csrf(request, csrf)
    values = AuthSettings(
        require_login=require_login is not None,
        session_ttl_seconds=session_ttl_seconds,
        ldap_enabled=ldap_enabled is not None,
        ldap_url=ldap_url.strip(),
        ldap_bind_dn=ldap_bind_dn.strip(),
        ldap_base_dn=ldap_base_dn.strip(),
        ldap_user_base_dn=ldap_user_base_dn.strip(),
        ldap_user_filter=ldap_user_filter.strip(),
        ldap_username_attribute=ldap_username_attribute.strip(),
        ldap_display_name_attribute=ldap_display_name_attribute.strip(),
        ldap_email_attribute=ldap_email_attribute.strip(),
        ldap_member_of_attribute=ldap_member_of_attribute.strip(),
        ldap_access_group=ldap_access_group.strip(),
        ldap_editor_group=ldap_editor_group.strip(),
        ldap_admin_group=ldap_admin_group.strip(),
        ldap_ca_cert_file=ldap_ca_cert_file.strip(),
    )
    try:
        save_auth_settings(
            modern_db,
            request.app.state.settings,
            values,
            bind_password=ldap_bind_password,
            clear_bind_password=clear_bind_password is not None,
        )
        modern_db.commit()
    except ValueError as exc:
        modern_db.rollback()
        current = load_auth_settings(modern_db, request.app.state.settings)
        # Preserve non-secret submitted fields so the form is easy to correct.
        return render(
            request,
            "admin/auth_settings.html",
            auth_values=values,
            ttl_options=SESSION_TTL_OPTIONS,
            saved=False,
            error=str(exc),
            has_bind_password=bool(current.ldap_bind_password or ldap_bind_password),
            status_code=400,
        )
    audit_event(
        request,
        "auth_settings.update",
        principal=principal,
        object_type="auth_settings",
        details={"ldap_enabled": values.ldap_enabled, "require_login": values.require_login},
    )
    return RedirectResponse("/admin/settings/auth?saved=1", status_code=status.HTTP_303_SEE_OTHER)
