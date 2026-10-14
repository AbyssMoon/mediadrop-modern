"""Render descriptions without changing the stored Markdown or legacy HTML."""

from html.parser import HTMLParser

import bleach
import markdown
from markupsafe import Markup

from app.services import SAFE_DESCRIPTION_ATTRS, SAFE_DESCRIPTION_TAGS


def render_description(value: str | None) -> Markup:
    # Markdown leaves existing HTML blocks intact. Sanitize AFTER rendering so
    # Markdown links and legacy HTML go through the same safety boundary.
    rendered = markdown.markdown(
        value or "", extensions=["nl2br", "sane_lists", "fenced_code"]
    )
    return Markup(bleach.clean(
        rendered,
        tags=[*SAFE_DESCRIPTION_TAGS, "h1", "h2", "h3", "h4", "h5", "h6",
              "pre", "code", "hr"],
        attributes=SAFE_DESCRIPTION_ATTRS,
        protocols=["http", "https", "mailto"],
        strip=True,
    ))


class _DescriptionText(HTMLParser):
    """Keep word boundaries at block elements and explicit line breaks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "ul", "ol", "li", "blockquote", "pre", "hr",
                   "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        self.handle_starttag(tag, [])


def description_preview(value: str | None) -> str:
    """Plain single-line preview; leave escaping to the template engine."""
    parser = _DescriptionText()
    parser.feed(str(render_description(value)))
    parser.close()
    return " ".join("".join(parser.parts).split())
