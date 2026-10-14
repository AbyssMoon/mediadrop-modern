import pytest

from app.description import description_preview, render_description
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


@pytest.mark.parametrize(("source", "expected"), [
    ("**Заголовок**\n\nПервая\nВторая", "Заголовок Первая Вторая"),
    ("## Заголовок\n\n- Один\n- Два\n\n[Ссылка](https://example.com)", "Заголовок Один Два Ссылка"),
    ("<p>Один<br>Два</p><p>Три &amp; четыре</p>", "Один Два Три & четыре"),
    ("сло<strong>во</strong>", "слово"),
    ("`code`\n\n&lt;b&gt;literal&lt;/b&gt;", "code <b>literal</b>"),
    (None, ""),
])
def test_description_preview(source, expected):
    result = description_preview(source)
    assert result == expected
    assert type(result) is str


def test_home_plain_preview_preserves_full_description(app_env):
    import re

    app, client, _ = app_env
    source = "**Heading**\n\nFirst\nSecond [Link](https://example.com) &lt;img src=x onerror=alert(1)&gt;"
    with app.state.session_factory() as db:
        media = db.get(Media, 1)
        media.description, media.description_plain = sanitize_description(source)
        db.commit()
        stored = media.description
    home = client.get("/")
    previews = re.findall(r'<p class="home-description">(.*?)</p>', home.text)
    assert len(previews) >= 2  # Featured and list rows use the same text filter.
    for preview in previews:
        assert preview.startswith("Heading First Second Link")
        assert "**" not in preview and "https://" not in preview
        assert "<img" not in preview and "&lt;img" in preview
        assert "\n" not in preview
    full = client.get("/media/sample-video/view")
    assert "<strong>Heading</strong>" in full.text
    assert "First<br>\nSecond" in full.text
    with app.state.session_factory() as db:
        assert db.get(Media, 1).description == stored
