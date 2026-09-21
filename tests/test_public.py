from __future__ import annotations

import re


def test_health_and_home(app_env):
    _, client, _ = app_env
    assert client.get("/healthz").text == "ok"
    response = client.get("/")
    assert response.status_code == 200
    assert 'lang="en"' in response.text
    assert "Sample video" in response.text
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in response.text
    favicon = client.get("/static/favicon.svg")
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert "#385fe0" in favicon.text
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


def test_video_player_extra_controls_and_no_persistence(app_env):
    _, client, _ = app_env
    page = client.get("/media/sample-video/view")
    assert page.status_code == 200
    assert 'data-enhanced-player' in page.text
    assert 'data-player-seek="-10"' in page.text
    assert 'data-player-seek="10"' in page.text
    assert 'data-player-speed' in page.text
    assert 'data-player-wide' in page.text
    assert 'data-player-pip' in page.text
    assert 'Keyboard shortcuts' in page.text
    assert '<kbd>Space</kbd>' in page.text
    assert '<kbd>F</kbd>' in page.text
    assert '<kbd>P</kbd>' in page.text
    assert 'data-watch-page' in page.text
    assert 'class="watch-stage"' in page.text
    assert 'class="watch-sidebar"' in page.text
    assert 'class="watch-below"' in page.text

    script = client.get("/static/player.js")
    assert script.status_code == 200
    assert "requestPictureInPicture" in script.text
    assert "webkitSetPresentationMode" in script.text
    assert 'video.requestFullscreen' in script.text
    assert 'wrapper.requestFullscreen' not in script.text
    assert 'event.code === "Space" && event.target === video' in script.text
    assert 'watch-page--wide' in script.text
    assert "localStorage" not in script.text
    assert "sessionStorage" not in script.text

    stylesheet = client.get("/static/app.css")
    assert stylesheet.status_code == 200
    assert ".player-shell video:fullscreen" in stylesheet.text
    assert "max-height: none" in stylesheet.text
    assert ".watch-page--wide .watch-layout" in stylesheet.text
    assert 'grid-template-areas: "stage" "sidebar" "below"' in stylesheet.text


def test_featured_video_uses_same_extra_controls_without_page_wide_mode(app_env):
    _, client, _ = app_env
    page = client.get("/")
    assert page.status_code == 200
    assert page.text.count("data-enhanced-player") == 1
    assert 'data-player-wide' not in page.text
    assert 'src="/static/player.js?v=2"' in page.text
