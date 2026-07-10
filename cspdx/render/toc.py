"""Clickable table of contents injection for section pages."""
from __future__ import annotations

import html as _html

from bs4 import BeautifulSoup

_SUB_LEVELS = ["h2", "h3", "h4"]
_THRESHOLD = 7


def inject_toc(body_html: str, threshold: int = _THRESHOLD, url_path: str = "") -> str:
    """Return body_html with a TOC nav injected after the first h1.

    Does nothing when the page has fewer than `threshold` h2/h3/h4 headings.
    Relies on the id attributes already present in Google Docs-exported HTML.

    url_path must be passed (e.g. "/ms-in-cs/") so that fragment links resolve
    correctly when a <base href="/"> tag is present on the page.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    sub_headings = soup.find_all(_SUB_LEVELS)

    if len(sub_headings) < threshold:
        return body_html

    items: list[str] = []
    for h in sub_headings:
        anchor = h.get("id", "")
        text = _html.escape(h.get_text(strip=True))
        if anchor and text:
            items.append(
                f'<li class="toc-h{h.name[1]}"><a href="{url_path}#{anchor}">{text}</a></li>'
            )

    if not items:
        return body_html

    toc = (
        '<nav class="toc" aria-label="Table of contents">'
        '<p class="toc__heading">Contents</p>'
        f'<ol class="toc__list">{"".join(items)}</ol>'
        "</nav>"
    )

    h1_end = body_html.find("</h1>")
    if h1_end != -1:
        pos = h1_end + len("</h1>")
        return body_html[:pos] + toc + body_html[pos:]
    return toc + body_html
