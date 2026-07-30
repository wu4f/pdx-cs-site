"""Clickable table of contents injection for section pages."""
from __future__ import annotations

import html as _html

from bs4 import BeautifulSoup, NavigableString

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


def _num_target(h):
    """Return the element a heading's number should be inserted into.

    Google's HTML export wraps a heading's text in a `<span>` carrying its own
    `font-weight` — 400 for H3/H4, against the 600 that `base.html` sets on the
    heading itself — so a number placed beside that span would render bolder
    than the text it labels. Put it inside the span instead, where it picks up
    the same styling as the text. The whole splitter emits heading text
    directly, with no wrapper; there the heading itself is the target.
    """
    for kid in h.contents:
        if isinstance(kid, NavigableString):
            if kid.strip():
                break
            continue
        return kid if kid.name == "span" else h
    return h


def inject_toc(body_html: str, threshold: int = _THRESHOLD, url_path: str = "") -> str:
    """Return body_html with a TOC nav injected.

    Single-section pages (one H1): counts H2/H3/H4; injects after the H1.
    Whole-doc pages (two or more H1s): counts H1/H2/H3/H4; injects before the
    first H1. Either way each heading's number is repeated in the heading
    itself so the body matches the TOC. Adds id attributes to any heading that
    is missing one (H1 through H6, so that even a heading too deep to be listed
    can still be linked to). Does nothing when the heading count is below
    `threshold`.

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
    for h in soup.find_all(_ALL_HEADINGS):
        if not h.get("id"):
            h["id"] = heading_anchor(h.get_text(strip=True), seen)

    counters = [0] * len(levels)
    items: list[str] = []
    for h in counted:
        # Read the heading text before numbering it, or the TOC label would
        # repeat the number ("1. 1. Introduction").
        text = _html.escape(h.get_text(strip=True))
        # A blank Heading paragraph (a few of the source docs have one) can be
        # neither listed nor linked, so it is skipped without taking a number —
        # otherwise the visible sequence would jump. It still counts towards
        # `threshold`, so numbering never costs a page the TOC it already had.
        if not text:
            continue
        level_idx = levels.index(h.name)
        counters[level_idx] += 1
        for i in range(level_idx + 1, len(counters)):
            counters[i] = 0
        num_str = ".".join(str(counters[i]) for i in range(level_idx + 1)) + "."
        items.append(
            f'<li class="toc-h{h.name[1]}">'
            f'<a href="{url_path}#{h["id"]}">'
            f'<span class="toc-num">{num_str}</span> {text}'
            f'</a></li>'
        )
        # Repeat the number in the body heading so the page matches its TOC.
        num = soup.new_tag("span", attrs={"class": "heading-num"})
        num.string = num_str
        target = _num_target(h)
        target.insert(0, num)
        target.insert(1, " ")

    if not items:
        return body_html

    list_cls = "toc__list toc__list--doc" if multi_h1 else "toc__list"
    toc = (
        '<nav class="toc" aria-label="Table of contents">'
        '<p class="toc__heading">Contents</p>'
        f'<ol class="{list_cls}">{"".join(items)}</ol>'
        "</nav>"
    )

    # Past this point the soup — not body_html — is the source of truth: it
    # carries the ids assigned above and the numbers inserted into the
    # headings. Re-serialising the exported markup only drops per-line
    # indentation; none of the source docs contain whitespace-sensitive tags.
    body = soup.body
    inject_html = body.decode_contents() if body else str(soup)

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
