from __future__ import annotations

import re


def test_health_and_home(app_env):
    _, client, _ = app_env
    assert client.get("/healthz").text == "ok"
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="en"' in response.text
    assert "Sample video" in response.text
    assert "/thumbnails/media/1/m.jpg" in response.text
    assert re.search(r'href="/categories/demos"[^>]*><strong>Demos</strong><span>1</span>', response.text)
    thumb = client.get("/thumbnails/media/1/m.jpg")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")


def test_media_view_file_and_api(app_env):
    _, client, _ = app_env
    page = client.get("/media/sample-video/view")
    assert page.status_code == 200
    assert "<video" in page.text
    assert "/files/" in page.text

    api = client.get("/api/media")
    assert api.status_code == 200
    payload = api.json()["media"]
    assert payload[0]["slug"] == "sample-video"

    file_id = payload[0]["files"][0]["id"]
    media_file = client.get(f"/files/{file_id}-sample-video.mp4")
    assert media_file.status_code == 200
    assert media_file.content.startswith(b"fake-mp4")


def test_public_upload_is_immediately_encoded(app_env):
    app, client, _ = app_env
    page = client.get("/upload")
    token = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/upload",
        data={"csrf": token, "title": "Uploaded video", "description": "hello"},
        files={"file": ("ready.mp4", b"web-ready-data", "video/mp4")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    from sqlalchemy import select
    from app.models import Media

    with app.state.session_factory() as db:
        media = db.scalar(select(Media).where(Media.slug == "uploaded-video"))
        assert media is not None
        assert media.encoded is True
        assert media.reviewed is True
        assert media.publishable is True


def test_language_switch(app_env):
    _, client, _ = app_env
    response = client.get("/language/en?next=/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    page = client.get("/")
    assert 'lang="en"' in page.text
    assert "Most popular" in page.text


def test_categories_and_podcasts_index(app_env):
    _, client, _ = app_env
    categories = client.get("/categories")
    assert categories.status_code == 200
    assert "Demos" in categories.text

    podcasts = client.get("/podcasts")
    assert podcasts.status_code == 200
    assert "Sample podcast" in podcasts.text


def test_legacy_language_choices_and_rtl(app_env):
    _, client, _ = app_env
    de = client.get("/language/de?next=/", follow_redirects=False)
    assert de.status_code == 303
    page = client.get("/")
    assert 'lang="de"' in page.text
    assert "Übersicht" in page.text

    ar = client.get("/language/ar?next=/", follow_redirects=False)
    assert ar.status_code == 303
    page = client.get("/")
    assert 'lang="ar"' in page.text
    assert 'dir="rtl"' in page.text
