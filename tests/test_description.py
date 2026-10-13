import pytest

from app.description import render_description
from app.models import Media
from app.services import sanitize_description


def test_saved_markdown_and_line_breaks():
    source = "**Заголовок**\n\nПервая строка\nВторая строка\n\n- Один\n- Два\n\n[Ссылка](https://example.com)"
    stored, _ = sanitize_description(source)
    result = render_description(stored)
    assert "<strong>Заголовок</strong>" in result
    assert "Первая строка<br>\nВторая строка" in result
    assert "<ul>\n<li>Один</li>\n<li>Два</li>\n</ul>" in result
    assert '<a href="https://example.com">Ссылка</a>' in result


def test_legacy_html_is_preserved():
    source = '<p>Первый <strong>абзац</strong><br>Строка</p>\n<p>Второй &amp; третий</p>'
    assert render_description(source) == source


@pytest.mark.parametrize("source", [
    '<script>alert(1)</script><p onclick="alert(1)">Text</p>',
    '[Text](javascript:alert%281%29)',
    '<a href="javascript:alert(1)">Text</a><img src=x onerror=alert(1)>',
])
def test_rendered_html_is_sanitized(source):
    result = render_description(source)
    for forbidden in ("<script", "onclick", "onerror", "javascript:", "<img"):
        assert forbidden not in result


def test_headings_code_and_windows_newlines():
    result = render_description("## Heading\r\n\r\nOne\r\nTwo\r\n\r\n`code`")
    assert "<h2>Heading</h2>" in result
    assert "One<br>\nTwo" in result
    assert "<code>code</code>" in result
    assert render_description(None) == ""


def test_media_page_renders_without_rewriting_description(app_env):
    app, client, _ = app_env
    source = "**Heading**\n\nFirst\nSecond"
    with app.state.session_factory() as db:
        media = db.get(Media, 1)
        media.description = source
        db.commit()
    page = client.get("/media/sample-video/view")
    assert page.status_code == 200
    assert '<div class="prose media-description"><p><strong>Heading</strong></p>' in page.text
    assert "First<br>\nSecond" in page.text
    with app.state.session_factory() as db:
        assert db.get(Media, 1).description == source
