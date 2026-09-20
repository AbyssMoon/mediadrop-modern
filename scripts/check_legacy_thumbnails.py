from __future__ import annotations

from sqlalchemy import select

from app.config import get_settings
from app.database import build_session_factory
from app.models import Media
from app.services import thumbnail_path


def _under(path, root) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def main() -> None:
    settings = get_settings()
    engine, session_factory = build_session_factory(settings)
    try:
        with session_factory() as db:
            media_ids = list(db.scalars(select(Media.id).order_by(Media.id)).all())

        modern = 0
        legacy = 0
        missing = []
        for media_id in media_ids:
            path = thumbnail_path(settings, media_id, "m")
            if path is None:
                missing.append(media_id)
            elif _under(path, settings.image_root):
                modern += 1
            elif settings.legacy_image_root and _under(path, settings.legacy_image_root):
                legacy += 1

        print(f"Media rows: {len(media_ids)}")
        print(f"Modern previews: {modern}")
        print(f"Legacy previews: {legacy}")
        print(f"Missing previews: {len(missing)}")
        if missing:
            sample = ", ".join(str(x) for x in missing[:30])
            print(f"First missing media IDs: {sample}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
