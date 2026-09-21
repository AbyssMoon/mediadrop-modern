from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.i18n import (
    LOCALES,
    format_date,
    locale_dir,
    locale_name,
    normalize_locale,
    translate,
    views_text,
)
from app.models import Media, MediaFile
from app.security import csrf_token, current_principal
from app.services import thumbnail_path
from app.site_settings import get_site_settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def media_url(media: Media) -> str:
    if media.podcast:
        return f"/podcasts/{media.podcast.slug}/{media.slug}/view"
    return f"/media/{media.slug}/view"


def file_url(media_file: MediaFile, slug: str = "media") -> str:
    return f"/files/{media_file.id}-{slug}.{media_file.container or 'bin'}"


def playable_file(media: Media) -> MediaFile | None:
    """Pick formats modern browsers can play natively; legacy FLV is ignored."""
    priorities = {
        "mp4": 10,
        "m4v": 20,
        "webm": 30,
        "ogv": 40,
        "ogg": 50,
        "mp3": 60,
        "m4a": 70,
        "aac": 80,
        "oga": 90,
        "wav": 100,
        "flac": 110,
    }
    candidates = [
        f
        for f in media.files
        if f.type in {"video", "audio"} and (f.container or "").lower() in priorities
    ]
    return min(candidates, key=lambda f: priorities[(f.container or "").lower()], default=None)


def preview_file(media: Media) -> MediaFile | None:
    # Compatibility for any image-like records added directly to media_files.
    for media_file in media.files:
        container = (media_file.container or "").lower()
        if media_file.type in {"image", "thumbnail", "poster"} or container in {
            "jpg",
            "jpeg",
            "png",
            "webp",
            "gif",
        }:
            return media_file
    return None


def thumbnail_url(settings: Settings, media: Media, size: str = "m") -> str | None:
    if not media.id:
        return None
    path = thumbnail_path(settings, media.id, size)
    if path is None:
        return None
    try:
        version = f"?v={path.stat().st_mtime_ns}"
    except OSError:
        version = ""
    return f"/thumbnails/media/{media.id}/{size}.jpg{version}"


TEMPLATES.env.globals.update(
    media_url=media_url,
    file_url=file_url,
    preview_file=preview_file,
    playable_file=playable_file,
)


def render(request: Request, name: str, **context):
    status_code = int(context.pop("status_code", 200))
    runtime = request.app.state.settings
    with request.app.state.session_factory() as db:
        site = get_site_settings(db, runtime)
        principal = current_principal(request, db)
    locale = normalize_locale(
        request.cookies.get("mediadrop_locale"), str(site["primary_language"])
    )
    context.setdefault("request", request)
    context.setdefault("settings", runtime)
    context.setdefault("site", site)
    context.setdefault("principal", principal)
    context.setdefault("csrf_token", csrf_token(request))
    context.setdefault("locale", locale)
    context.setdefault("locale_dir", locale_dir(locale))
    context.setdefault("locale_name", locale_name(locale))
    context.setdefault("locales", LOCALES)
    context.setdefault("t", lambda key, **values: translate(locale, key, **values))
    context.setdefault(
        "format_date", lambda value, include_time=False: format_date(value, locale, include_time)
    )
    context.setdefault("views_text", lambda count: views_text(count, locale))
    context.setdefault("thumbnail_url", lambda media, size="m": thumbnail_url(runtime, media, size))
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
    )
