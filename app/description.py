"""Render descriptions without changing the stored Markdown or legacy HTML."""

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
