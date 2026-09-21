"""List media that have files but no browser-playable audio/video variant.

Read-only helper for migration cleanup. It does not modify the database or files.

Usage:
    docker compose run --rm app python scripts/list_unplayable_media.py
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import build_session_factory
from app.models import Media
from app.services import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, media_file_path

PLAYABLE_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


def _container(value: str | None) -> str:
    return (value or "").strip().lower()


def main() -> int:
    settings = get_settings()
    engine, session_factory = build_session_factory(settings)
    missing_file_count = 0
    rows: list[tuple[Media, list]] = []
    try:
        with session_factory() as db:
            media_rows = list(
                db.scalars(
                    select(Media)
                    .options(selectinload(Media.files))
                    .order_by(Media.id)
                ).all()
            )

            for media in media_rows:
                av_files = [f for f in media.files if f.type in {"video", "audio"}]
                if not av_files:
                    continue
                if any(_container(f.container) in PLAYABLE_EXTENSIONS for f in av_files):
                    continue
                rows.append((media, av_files))

        print(f"Media without a browser-playable variant: {len(rows)}")
        for media, files in rows:
            print(f"\n#{media.id}  {media.title}  /{media.slug}")
            for media_file in files:
                path = media_file_path(settings, media_file)
                exists = path.is_file()
                if not exists:
                    missing_file_count += 1
                marker = "OK" if exists else "MISSING"
                print(
                    f"  [{marker}] file_id={media_file.id} "
                    f"format={_container(media_file.container) or '?'} "
                    f"unique_id={media_file.unique_id or '-'} path={path}"
                )

        if rows:
            print(
                "\nThis report is read-only. Keep legacy files unchanged; "
                "convert replacements separately before attaching them to the same media item."
            )
        if missing_file_count:
            print(f"Missing source files referenced by these rows: {missing_file_count}")
            return 2
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
