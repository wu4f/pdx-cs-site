"""Clickable table of contents injection for section pages."""
from __future__ import annotations

import html as _html
import re

from bs4 import BeautifulSoup

_SUB_LEVELS = ["h2", "h3", "h4"]
_DOC_LEVELS = ["h1", "h2", "h3"]  # whole-doc pages: H1 is top-level section
_THRESHOLD = 7


def _make_id(text: str, seen: dict[str, int]) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-") or "heading"
    if slug in seen:
        seen[slug] += 1
        slug = f"{slug}-{seen[slug]}"
    else:
        seen[slug] = 0
    return slug


def inject_toc(body_html: str, threshold: int = _THRESHOLD, url_path: str = "") -> str:
    """Return body_html with a TOC nav injected.

    Single-section pages (one H1): counts H2/H3/H4; injects after the H1.
    Whole-doc pages (two or more H1s): counts H1/H2/H3; injects before the
    first H1. Adds id attributes to any heading that is missing one.
    Does nothing when the heading count is below `threshold`.

    url_path must be passed (e.g. "/ms-in-cs/") so that fragment links resolve
    correctly when a <base href="/"> tag is present on the page.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    h1s = soup.find_all("h1")
    multi_h1 = len(h1s) >= 2  # whole-doc: multiple H1 sections on one page

    levels = _DOC_LEVELS if multi_h1 else _SUB_LEVELS
    counted = soup.find_all(levels)

    if len(counted) < threshold:
        return body_html

    # Assign ids to any heading that is missing one.
    seen: dict[str, int] = {}
    ids_added = False
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        if not h.get("id"):
            h["id"] = _make_id(h.get_text(strip=True), seen)
            ids_added = True

    counters = [0] * len(levels)
    items: list[str] = []
    for h in counted:
        level_idx = levels.index(h.name)
        counters[level_idx] += 1
        for i in range(level_idx + 1, len(counters)):
            counters[i] = 0
        num_str = ".".join(str(counters[i]) for i in range(level_idx + 1)) + "."
        anchor = h.get("id", "")
        text = _html.escape(h.get_text(strip=True))
        if anchor and text:
            items.append(
                f'<li class="toc-h{h.name[1]}">'
                f'<a href="{url_path}#{anchor}">'
                f'<span class="toc-num">{num_str}</span> {text}'
                f'</a></li>'
            )

    if not items:
        return body_html

    list_cls = "toc__list toc__list--doc" if multi_h1 else "toc__list"
    toc = (
        '<nav class="toc" aria-label="Table of contents">'
        '<p class="toc__heading">Contents</p>'
        f'<ol class="{list_cls}">{"".join(items)}</ol>'
        "</nav>"
    )

    # When ids were added we must use the soup-serialised HTML so the new
    # id attributes are actually present in the output.
    if ids_added:
        body = soup.body
        inject_html = body.decode_contents() if body else str(soup)
    else:
        inject_html = body_html

    if multi_h1:
        # Whole-doc: place TOC before the first H1 section.
        h1_start = inject_html.find("<h1")
        if h1_start != -1:
            return inject_html[:h1_start] + toc + inject_html[h1_start:]
        return toc + inject_html
    else:
        # Single-section: place TOC after the page-title H1.
        h1_end = inject_html.find("</h1>")
        if h1_end != -1:
            pos = h1_end + len("</h1>")
            return inject_html[:pos] + toc + inject_html[pos:]
        return toc + inject_html
