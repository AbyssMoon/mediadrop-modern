from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings as RuntimeSettings
from app.i18n import normalize_locale
from app.models import Setting


TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "builtin"}
FALSE_VALUES = {"0", "false", "no", "off", "disabled", ""}


def as_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def load_raw_settings(db: Session) -> dict[str, str]:
    rows = db.scalars(select(Setting)).all()
    return {row.key: row.value or "" for row in rows}


def set_setting(db: Session, key: str, value: str | int | bool | None) -> None:
    row = db.scalar(select(Setting).where(Setting.key == key))
    if row is None:
        row = Setting(key=key, value="")
        db.add(row)
    if isinstance(value, bool):
        row.value = "True" if value else "False"
    elif value is None:
        row.value = ""
    else:
        row.value = str(value)


def _int(raw: dict[str, str], key: str, default: int) -> int:
    try:
        return int(raw.get(key, "") or default)
    except (TypeError, ValueError):
        return default


def _legacy_upload_mb(raw: dict[str, str], runtime: RuntimeSettings) -> int:
    modern = raw.get("modern_max_upload_mb")
    if modern:
        try:
            return max(1, int(modern))
        except ValueError:
            pass
    legacy = raw.get("max_upload_size")
    if legacy:
        try:
            value = int(legacy)
            # Historical MediaDrop stored this as bytes. Some installations
            # contain a human-entered MB value, so handle both safely.
            if value > 1024 * 1024:
                return max(1, value // (1024 * 1024))
            return max(1, value)
        except ValueError:
            pass
    return runtime.max_upload_mb



def _accent_color(raw: dict[str, str]) -> str:
    value = (raw.get("modern_accent_color") or "#3157d5").strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.lower()
    return "#3157d5"


def get_site_settings(db: Session, runtime: RuntimeSettings) -> dict[str, object]:
    raw = load_raw_settings(db)
    comments_engine = raw.get("comments_engine", "builtin") or "builtin"
    primary_language = normalize_locale(raw.get("primary_language"), runtime.locale)
    featured_category = _int(raw, "featured_category", 0)

    return {
        "site_name": raw.get("general_site_name") or runtime.app_name,
        "primary_language": primary_language,
        "featured_category": featured_category or None,
        "comments_enabled": comments_engine != "disabled",
        "comments_engine": comments_engine,
        "require_comment_approval": as_bool(raw.get("req_comment_approval"), True),
        "enable_podcasts": as_bool(raw.get("appearance_enable_podcast_tab"), True),
        "enable_user_uploads": as_bool(
            raw.get("appearance_enable_user_uploads"), runtime.public_uploads
        ),
        "show_download": as_bool(raw.get("appearance_show_download"), True),
        "show_like": as_bool(raw.get("appearance_show_like"), True),
        "show_dislike": as_bool(raw.get("appearance_show_dislike"), True),
        "rss_enabled": as_bool(raw.get("rss_display"), True),
        "sitemap_enabled": as_bool(raw.get("sitemaps_display"), True),
        "max_upload_mb": _legacy_upload_mb(raw, runtime),
        "accent_color": _accent_color(raw),
        "footer_text": raw.get("modern_footer_text") or "",
    }
