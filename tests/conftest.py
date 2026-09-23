from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Base, Category, Group, Media, MediaFile, Permission, Podcast, Storage, User
from app.security import hash_legacy_password


@pytest.fixture()
def app_env(tmp_path: Path):
    database = tmp_path / "test.db"
    modern_database = tmp_path / "modern.db"
    media_root = tmp_path / "media"
    image_root = tmp_path / "images"
    legacy_media_root = tmp_path / "legacy-media"
    legacy_image_root = tmp_path / "legacy-images"
    settings = Settings(
        environment="test",
        secret_key="test-secret-that-is-long-enough",
        database_url=f"sqlite:///{database}",
        modern_database_url=f"sqlite:///{modern_database}",
        media_root=media_root,
        image_root=image_root,
        legacy_media_root=legacy_media_root,
        legacy_image_root=legacy_image_root,
        cookie_secure=False,
        public_uploads=True,
        uploads_require_review=False,
        max_upload_mb=10,
        audit_log_path=tmp_path / "audit.log",
    )
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)

    with app.state.session_factory() as db:
        edit = Permission(permission_name="edit", description="edit")
        admins = Group(group_name="admins", display_name="Administrators")
        admins.permissions.append(edit)
        admin = User(
            user_name="admin",
            email_address="admin@example.test",
            display_name="Admin",
            password=hash_legacy_password("secret"),
        )
        admin.groups.append(admins)
        storage = Storage(
            engine_type="LocalFileStorage",
            display_name="Local File Storage",
            enabled=True,
            data="{}",
        )
        category = Category(name="Demos", slug="demos")
        podcast = Podcast(
            slug="sample-podcast",
            title="Sample podcast",
            subtitle="Demo series",
            author_name="",
            author_email="",
        )
        media = Media(
            slug="sample-video",
            title="Sample video",
            reviewed=True,
            encoded=True,
            publishable=True,
            publish_on=datetime.now(),
            description="<p>Sample</p>",
            description_plain="Sample",
            type="video",
            author_name="",
            author_email="",
        )
        media.categories.append(category)
        db.add_all([admin, storage, category, podcast, media])
        db.flush()
        from PIL import Image
        legacy_thumb_dir = legacy_image_root / "media"
        legacy_thumb_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), (40, 80, 160)).save(legacy_thumb_dir / f"{media.id}m.jpg", "JPEG")
        filename = "sample.mp4"
        media_root.mkdir(parents=True, exist_ok=True)
        (media_root / filename).write_bytes(b"fake-mp4-for-routing-test")
        db.add(
            MediaFile(
                media=media,
                storage=storage,
                type="video",
                container="mp4",
                display_name="sample.mp4",
                unique_id=filename,
                size=25,
            )
        )
        db.commit()

    with TestClient(app) as client:
        yield app, client, media_root


@pytest.fixture()
def logged_in(app_env):
    app, client, media_root = app_env
    login_page = client.get("/login")
    assert login_page.status_code == 200
    csrf = client.cookies.get("mediadrop_session")
    # The session cookie itself is opaque/signed; extract CSRF from the HTML.
    import re

    token = re.search(r'name="csrf" value="([^"]+)"', login_page.text).group(1)
    response = client.post(
        "/login",
        data={"csrf": token, "username": "admin", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return app, client, media_root
