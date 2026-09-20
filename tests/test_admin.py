from __future__ import annotations

import re

from sqlalchemy import select

from app.models import Media


def _preview_png() -> bytes:
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (800, 450), (20, 120, 180)).save(buffer, "PNG")
    return buffer.getvalue()


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_unauthenticated_admin_redirects(app_env):
    _, client, _ = app_env
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_legacy_login_and_admin(logged_in):
    _, client, _ = logged_in
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Панель управления" in response.text


def test_admin_can_create_ready_media(logged_in):
    app, client, _ = logged_in
    page = client.get("/admin/media/new")
    token = _csrf(page.text)
    response = client.post(
        "/admin/media/new",
        data={
            "csrf": token,
            "title": "Admin upload",
            "slug": "admin-upload",
            "description": "<p>Ready</p>",
            "reviewed": "on",
            "publishable": "on",
        },
        files={
            "file": ("admin.mp4", b"ready-media", "video/mp4"),
            "thumbnail": ("preview.png", _preview_png(), "image/png"),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as db:
        media = db.scalar(select(Media).where(Media.slug == "admin-upload"))
        assert media is not None
        assert media.encoded is True
        assert len(media.files) == 1
        media_id = media.id
    thumb = client.get(f"/thumbnails/media/{media_id}/m.jpg")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")


def test_admin_settings_are_saved(logged_in):
    app, client, _ = logged_in
    page = client.get("/admin/settings")
    assert page.status_code == 200
    assert "Настройки" in page.text
    token = _csrf(page.text)
    response = client.post(
        "/admin/settings",
        data={
            "csrf": token,
            "site_name": "Updated MediaDrop",
            "primary_language": "ru",
            "comments_enabled": "on",
            "require_comment_approval": "on",
            "enable_podcasts": "on",
            "show_download": "on",
            "show_like": "on",
            "show_dislike": "on",
            "rss_enabled": "on",
            "sitemap_enabled": "on",
            "max_upload_mb": "512",
            "accent_color": "#123456",
            "footer_text": "Footer",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    home = client.get("/")
    assert "Updated MediaDrop" in home.text
    assert "--primary: #123456" in home.text

    from app.models import Setting
    with app.state.session_factory() as db:
        assert db.scalar(select(Setting).where(Setting.key == "general_site_name")).value == "Updated MediaDrop"
