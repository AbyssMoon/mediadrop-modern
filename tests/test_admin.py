from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import select

from app.models import Media


def _preview_png(width: int = 800, height: int = 450) -> bytes:
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (20, 120, 180)).save(buffer, "PNG")
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
    assert "Control panel" in response.text


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
    assert "Settings" in page.text
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
    assert 'lang="ru"' in home.text

    from app.models import Setting
    with app.state.session_factory() as db:
        assert db.scalar(select(Setting).where(Setting.key == "general_site_name")).value == "Updated MediaDrop"


def test_new_thumbnails_reach_fullhd_without_schema_changes(logged_in):
    app, client, _ = logged_in
    page = client.get("/admin/media/new")
    token = _csrf(page.text)
    response = client.post(
        "/admin/media/new",
        data={
            "csrf": token,
            "title": "Full HD preview",
            "slug": "full-hd-preview",
            "reviewed": "on",
            "publishable": "on",
        },
        files={
            "file": ("fullhd.mp4", b"ready-media", "video/mp4"),
            "thumbnail": ("preview.png", _preview_png(2560, 1440), "image/png"),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    from PIL import Image

    with app.state.session_factory() as db:
        media = db.scalar(select(Media).where(Media.slug == "full-hd-preview"))
        assert media is not None
        media_id = media.id

    media_dir = app.state.settings.image_root / "media"
    assert Image.open(media_dir / f"{media_id}s.jpg").size == (320, 180)
    assert Image.open(media_dir / f"{media_id}m.jpg").size == (960, 540)
    assert Image.open(media_dir / f"{media_id}l.jpg").size == (1920, 1080)


def test_new_media_prefills_identity_date_and_server_slug_fallback(logged_in):
    app, client, _ = logged_in
    today = datetime.now().date()
    page = client.get("/admin/media/new")
    assert page.status_code == 200
    assert 'data-media-create' in page.text
    assert 'name="author_name" maxlength="50" value="Admin"' in page.text
    assert 'name="author_email" type="email" value="admin@example.test"' in page.text
    assert f'name="publish_on" value="{today.isoformat()}T' in page.text
    assert 'src="/static/admin-media.js?v=1"' in page.text
    assert 'class="field-label-hint"' in page.text
    assert "generated from title" in page.text
    assert "Generated automatically from the title" not in page.text

    script = client.get("/static/admin-media.js")
    assert script.status_code == 200
    assert 'ё: "yo"' in script.text
    assert 'manuallyEdited' in script.text

    token = _csrf(page.text)
    response = client.post(
        "/admin/media/new",
        data={
            "csrf": token,
            "title": "Привет мир",
            "slug": "",
            "author_name": "",
            "author_email": "",
            "reviewed": "on",
            "publishable": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.session_factory() as db:
        media = db.scalar(select(Media).where(Media.slug == "privet-mir"))
        assert media is not None
        assert media.author_name == "Admin"
        assert media.author_email == "admin@example.test"
        assert media.publish_on is not None
        assert media.publish_on.date() == today


def test_media_form_categories_show_hierarchy_and_flow_down_columns(logged_in):
    app, client, _ = logged_in
    from app.models import Category

    with app.state.session_factory() as db:
        alpha = Category(name="AAA Root", slug="aaa-root")
        child = Category(name="AAA Child", slug="aaa-child", parent=alpha)
        grandchild = Category(name="AAA Grandchild", slug="aaa-grandchild", parent=child)
        zulu = Category(name="ZZZ Root", slug="zzz-root")
        db.add_all([alpha, child, grandchild, zulu])
        db.commit()

    page = client.get("/admin/media/new")
    assert page.status_code == 200
    assert 'class="wide category-picker"' in page.text
    assert 'class="category-checkbox-columns"' in page.text
    assert 'style="--category-indent: 18px"' in page.text
    assert 'style="--category-indent: 36px"' in page.text
    assert "↳" in page.text

    # Tree traversal is pre-order: parent, descendants, then the next root.
    root_pos = page.text.index("AAA Root")
    child_pos = page.text.index("AAA Child")
    grandchild_pos = page.text.index("AAA Grandchild")
    demos_pos = page.text.index("Demos")
    zulu_pos = page.text.index("ZZZ Root")
    assert root_pos < child_pos < grandchild_pos < demos_pos < zulu_pos

    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".category-checkbox-columns { column-count: 3;" in css.text
    assert "break-inside: avoid-column" in css.text


def test_media_form_hides_disabled_podcasts_and_does_not_drop_existing_link(logged_in):
    app, client, _ = logged_in
    from app.models import Media, Podcast
    from app.site_settings import set_setting

    with app.state.session_factory() as db:
        podcast = db.scalar(select(Podcast).where(Podcast.slug == "sample-podcast"))
        media = db.scalar(select(Media).where(Media.slug == "sample-video"))
        media.podcast_id = podcast.id
        set_setting(db, "appearance_enable_podcast_tab", False)
        db.commit()
        media_id = media.id
        podcast_id = podcast.id

    create_page = client.get("/admin/media/new")
    assert create_page.status_code == 200
    assert 'name="podcast_id"' not in create_page.text

    edit_page = client.get(f"/admin/media/{media_id}/edit")
    assert edit_page.status_code == 200
    assert 'name="podcast_id"' not in edit_page.text
    token = _csrf(edit_page.text)
    response = client.post(
        f"/admin/media/{media_id}/edit",
        data={
            "csrf": token,
            "title": "Sample video edited",
            "slug": "sample-video",
            "reviewed": "on",
            "publishable": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    create_token = _csrf(create_page.text)
    created = client.post(
        "/admin/media/new",
        data={
            "csrf": create_token,
            "title": "No podcast while disabled",
            "slug": "no-podcast-disabled",
            "podcast_id": str(podcast_id),
            "reviewed": "on",
            "publishable": "on",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    with app.state.session_factory() as db:
        edited = db.get(Media, media_id)
        assert edited.podcast_id == podcast_id
        new_media = db.scalar(select(Media).where(Media.slug == "no-podcast-disabled"))
        assert new_media is not None
        assert new_media.podcast_id is None
