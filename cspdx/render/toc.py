"""Clickable table of contents injection for section pages."""
from __future__ import annotations

import html as _html

from bs4 import BeautifulSoup

from ..models import heading_anchor

_SUB_LEVELS = ["h2", "h3", "h4"]
_DOC_LEVELS = ["h1", "h2", "h3", "h4"]  # whole-doc pages: H1 is top-level section
_ALL_HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_THRESHOLD = 7


def _is_doc_title(h) -> bool:
    """True for the <h1 class="title"> the whole splitter emits for a doc's TITLE
    style. It names the document rather than a section, so it is left out of the
    numbering — otherwise every top-level section is off by one."""
    return h.name == "h1" and "title" in (h.get("class") or [])


def inject_toc(body_html: str, threshold: int = _THRESHOLD, url_path: str = "") -> str:
    """Return body_html with a TOC nav injected.

    Single-section pages (one H1): counts H2/H3/H4; injects after the H1.
    Whole-doc pages (two or more H1s): counts H1/H2/H3/H4; injects before the
    first H1, and repeats each heading's number in the heading itself so the
    body matches the TOC. Adds id attributes to any heading that is missing
    one (H1 through H6, so that even a heading too deep to be listed can still
    be linked to). Does nothing when the heading count is below `threshold`.

    url_path must be passed (e.g. "/ms-in-cs/") so that fragment links resolve
    correctly when a <base href="/"> tag is present on the page.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    h1s = soup.find_all("h1")
    multi_h1 = len(h1s) >= 2  # whole-doc: multiple H1 sections on one page

    levels = _DOC_LEVELS if multi_h1 else _SUB_LEVELS
    counted = [h for h in soup.find_all(levels) if not _is_doc_title(h)]

    if len(counted) < threshold:
        return body_html

    # Assign ids to any heading that is missing one.
    seen: dict[str, int] = {}
    ids_added = False
    for h in soup.find_all(_ALL_HEADINGS):
        if not h.get("id"):
            h["id"] = heading_anchor(h.get_text(strip=True), seen)
            ids_added = True

    counters = [0] * len(levels)
    items: list[str] = []
    numbered = False
    for h in counted:
        level_idx = levels.index(h.name)
        counters[level_idx] += 1
        for i in range(level_idx + 1, len(counters)):
            counters[i] = 0
        num_str = ".".join(str(counters[i]) for i in range(level_idx + 1)) + "."
        anchor = h.get("id", "")
        # Read the heading text before numbering it, or the TOC label would
        # repeat the number ("1. 1. Introduction").
        text = _html.escape(h.get_text(strip=True))
        if anchor and text:
            items.append(
                f'<li class="toc-h{h.name[1]}">'
                f'<a href="{url_path}#{anchor}">'
                f'<span class="toc-num">{num_str}</span> {text}'
                f'</a></li>'
            )
            if multi_h1:
                # Whole-doc pages only: tab pages number their TOC but their
                # headings (course codes, policy names) read better unnumbered.
                num = soup.new_tag("span", attrs={"class": "heading-num"})
                num.string = num_str
                h.insert(0, num)
                h.insert(1, " ")
                numbered = True

    if not items:
        return body_html

    list_cls = "toc__list toc__list--doc" if multi_h1 else "toc__list"
    toc = (
        '<nav class="toc" aria-label="Table of contents">'
        '<p class="toc__heading">Contents</p>'
        f'<ol class="{list_cls}">{"".join(items)}</ol>'
        "</nav>"
    )

    # When ids were added or headings were numbered we must use the
    # soup-serialised HTML so those edits are actually present in the output.
    if ids_added or numbered:
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
