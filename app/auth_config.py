from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.config import Settings
from app.modern_database import load_modern_settings, set_modern_setting

SESSION_TTL_OPTIONS: tuple[tuple[int, str], ...] = (
    (8 * 60 * 60, "8h"),
    (24 * 60 * 60, "1d"),
    (7 * 24 * 60 * 60, "7d"),
    (30 * 24 * 60 * 60, "30d"),
)
SESSION_TTL_VALUES = {seconds for seconds, _ in SESSION_TTL_OPTIONS}
DEFAULT_SESSION_TTL = 24 * 60 * 60


@dataclass(frozen=True)
class AuthSettings:
    require_login: bool = False
    session_ttl_seconds: int = DEFAULT_SESSION_TTL
    ldap_enabled: bool = False
    ldap_url: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_base_dn: str = ""
    ldap_user_base_dn: str = "OU=People"
    ldap_user_filter: str = "(objectCategory=Person)"
    ldap_username_attribute: str = "sAMAccountName"
    ldap_display_name_attribute: str = "displayName"
    ldap_email_attribute: str = "mail"
    ldap_member_of_attribute: str = "memberOf"
    ldap_access_group: str = ""
    ldap_editor_group: str = ""
    ldap_admin_group: str = ""
    ldap_ca_cert_file: str = ""


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _fernet(runtime: Settings) -> Fernet:
    digest = hashlib.sha256(runtime.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(runtime: Settings, value: str) -> str:
    if not value:
        return ""
    return _fernet(runtime).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(runtime: Settings, value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet(runtime).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        return ""


def load_auth_settings(db: Session, runtime: Settings) -> AuthSettings:
    raw = load_modern_settings(db)
    ttl = _int(raw.get("auth.session_ttl_seconds"), DEFAULT_SESSION_TTL)
    if ttl not in SESSION_TTL_VALUES:
        ttl = DEFAULT_SESSION_TTL
    return AuthSettings(
        require_login=_bool(raw.get("auth.require_login"), False),
        session_ttl_seconds=ttl,
        ldap_enabled=_bool(raw.get("ldap.enabled"), False),
        ldap_url=(raw.get("ldap.url") or "").strip(),
        ldap_bind_dn=(raw.get("ldap.bind_dn") or "").strip(),
        ldap_bind_password=decrypt_secret(runtime, raw.get("ldap.bind_password") or ""),
        ldap_base_dn=(raw.get("ldap.base_dn") or "").strip(),
        ldap_user_base_dn=(raw.get("ldap.user_base_dn") or "OU=People").strip(),
        ldap_user_filter=(raw.get("ldap.user_filter") or "(objectCategory=Person)").strip(),
        ldap_username_attribute=(raw.get("ldap.username_attribute") or "sAMAccountName").strip(),
        ldap_display_name_attribute=(raw.get("ldap.display_name_attribute") or "displayName").strip(),
        ldap_email_attribute=(raw.get("ldap.email_attribute") or "mail").strip(),
        ldap_member_of_attribute=(raw.get("ldap.member_of_attribute") or "memberOf").strip(),
        ldap_access_group=(raw.get("ldap.access_group") or "").strip(),
        ldap_editor_group=(raw.get("ldap.editor_group") or "").strip(),
        ldap_admin_group=(raw.get("ldap.admin_group") or "").strip(),
        ldap_ca_cert_file=(raw.get("ldap.ca_cert_file") or "").strip(),
    )


def validate_auth_settings(values: AuthSettings) -> None:
    if values.session_ttl_seconds not in SESSION_TTL_VALUES:
        raise ValueError("Unsupported session lifetime")
    if not values.ldap_enabled:
        return
    parsed = urlparse(values.ldap_url)
    if parsed.scheme not in {"ldap", "ldaps"} or not parsed.hostname:
        raise ValueError("LDAP URL must start with ldap:// or ldaps:// and include a host")
    required = {
        "Base DN": values.ldap_base_dn,
        "User base DN": values.ldap_user_base_dn,
        "Username attribute": values.ldap_username_attribute,
        "memberOf attribute": values.ldap_member_of_attribute,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError("Missing LDAP settings: " + ", ".join(missing))
    if "{" in values.ldap_user_filter or "}" in values.ldap_user_filter:
        raise ValueError("LDAP user filter must be a fixed filter without format placeholders")


def save_auth_settings(
    db: Session,
    runtime: Settings,
    values: AuthSettings,
    *,
    bind_password: str | None = None,
    clear_bind_password: bool = False,
) -> None:
    validate_auth_settings(values)
    set_modern_setting(db, "auth.require_login", values.require_login)
    set_modern_setting(db, "auth.session_ttl_seconds", values.session_ttl_seconds)
    set_modern_setting(db, "ldap.enabled", values.ldap_enabled)
    set_modern_setting(db, "ldap.url", values.ldap_url)
    set_modern_setting(db, "ldap.bind_dn", values.ldap_bind_dn)
    set_modern_setting(db, "ldap.base_dn", values.ldap_base_dn)
    set_modern_setting(db, "ldap.user_base_dn", values.ldap_user_base_dn)
    set_modern_setting(db, "ldap.user_filter", values.ldap_user_filter)
    set_modern_setting(db, "ldap.username_attribute", values.ldap_username_attribute)
    set_modern_setting(db, "ldap.display_name_attribute", values.ldap_display_name_attribute)
    set_modern_setting(db, "ldap.email_attribute", values.ldap_email_attribute)
    set_modern_setting(db, "ldap.member_of_attribute", values.ldap_member_of_attribute)
    set_modern_setting(db, "ldap.access_group", values.ldap_access_group)
    set_modern_setting(db, "ldap.editor_group", values.ldap_editor_group)
    set_modern_setting(db, "ldap.admin_group", values.ldap_admin_group)
    set_modern_setting(db, "ldap.ca_cert_file", values.ldap_ca_cert_file)
    if clear_bind_password:
        set_modern_setting(db, "ldap.bind_password", "")
    elif bind_password:
        set_modern_setting(db, "ldap.bind_password", encrypt_secret(runtime, bind_password))
