"""Heading-1 splitter: walk the doc's structural elements, slice on H1.

Renders each slice to HTML using a minimal in-house renderer (Google's
export endpoint can't address an arbitrary H1 the way it can a tab).
"""
from __future__ import annotations
from html import escape
from typing import Iterator

from ..models import Section, slugify
from . import gdocs


# --- Tiny structural-elements renderer -------------------------------------
# Handles the common subset: paragraphs, text runs (bold/italic/links), lists.
# Tables/images/inline objects are rendered as placeholders; extend as needed.

def _render_text_run(run: dict) -> str:
    el = run.get("textRun")
    if not el:
        return ""
    raw = el.get("content", "")
    content = escape(raw).replace("\n", "<br/>")
    style = el.get("textStyle", {}) or {}
    if style.get("link", {}).get("url"):
        # WCAG 2.4.4 / 2.4.9 (link purpose): never emit an <a> with no
        # accessible name. If the text content is empty or only whitespace,
        # skip the link wrap rather than create an empty <a>.
        if raw.strip():
            content = f'<a href="{escape(style["link"]["url"])}">{content}</a>'
    if style.get("bold"):
        content = f"<strong>{content}</strong>"
    if style.get("italic"):
        content = f"<em>{content}</em>"
    if style.get("underline"):
        content = f"<u>{content}</u>"
    return content


def _render_paragraph(p: dict) -> tuple[str, str]:
    """Return (html, heading_level_or_empty)."""
    style = (p.get("paragraphStyle") or {}).get("namedStyleType", "NORMAL_TEXT")
    inner = "".join(_render_text_run(e) for e in p.get("elements", []))
    # Headings carry a trailing newline from the paragraph, which our renderer
    # turns into a stray <br/>. Drop it so headings render cleanly.
    heading = inner.rstrip()
    if heading.endswith("<br/>"):
        heading = heading[: -len("<br/>")].rstrip()
    if style == "HEADING_1":
        return f"<h1>{heading}</h1>", "H1"
    if style == "HEADING_2":
        return f"<h2>{heading}</h2>", "H2"
    if style == "HEADING_3":
        return f"<h3>{heading}</h3>", "H3"
    if style == "TITLE":
        return f"<h1 class='title'>{heading}</h1>", ""
    return f"<p>{inner}</p>", ""


def _walk_elements(body_content: list[dict]):
    """Yield (kind, html, h1_title_text) tuples. Lists collapsed to <ul>."""
    list_buffer: list[str] = []
    for el in body_content:
        if "paragraph" in el:
            p = el["paragraph"]
            # Detect list items by presence of bullet
            if p.get("bullet"):
                inner = "".join(_render_text_run(e) for e in p.get("elements", []))
                list_buffer.append(f"<li>{inner}</li>")
                continue
            else:
                if list_buffer:
                    yield ("html", "<ul>" + "".join(list_buffer) + "</ul>", "")
                    list_buffer = []
            html, level = _render_paragraph(p)
            if level == "H1":
                # Extract plain title text
                title = "".join(
                    (e.get("textRun") or {}).get("content", "")
                    for e in p.get("elements", [])
                ).strip()
                yield ("h1", html, title)
            else:
                yield ("html", html, "")
        elif "table" in el:
            yield ("html", "<!-- TODO: table rendering -->", "")
    if list_buffer:
        yield ("html", "<ul>" + "".join(list_buffer) + "</ul>", "")


def _iter_bodies(doc: dict):
    """Yield every body.content list in the doc, whether at the root or inside
    a (possibly nested) tab. Some Google Docs put all content inside a single
    tab even when they're conceptually 'one document'."""
    root_body = (doc.get("body") or {}).get("content") or []
    if root_body:
        yield root_body
    def walk(tabs):
        for t in tabs or []:
            dt = t.get("documentTab") or {}
            content = (dt.get("body") or {}).get("content") or []
            if content:
                yield content
            yield from walk(t.get("childTabs") or [])
    yield from walk(doc.get("tabs") or [])


def split(creds, doc_id: str, doc_name: str = "") -> Iterator[Section]:
    from bs4 import BeautifulSoup
    doc = gdocs.get_doc(creds, doc_id)
    revision = doc.get("revisionId", "")

    current_title: str | None = None
    current_html: list[str] = []
    current_text: list[str] = []

    def flush():
        if current_title is None:
            return None
        sid = slugify(current_title)
        return Section(
            id=sid,
            title=current_title,
            html="\n".join(current_html),
            style="",
            text=" ".join(current_text).strip(),
            source_doc_id=doc_id,
            source_doc_name=doc_name,
            source_anchor=f"h1:{sid}",
            revision=revision,
            url_path=f"/{sid}/",
        )

    for body in _iter_bodies(doc):
        for kind, html, title in _walk_elements(body):
            if kind == "h1":
                sec = flush()
                if sec:
                    yield sec
                current_title = title or "untitled"
                # Keep the H1 itself in the section body so the rendered page
                # has a heading, just like the tab-based pages do.
                current_html = [html]
                current_text = [current_title]
            else:
                if current_title is None:
                    continue
                current_html.append(html)
                current_text.append(
                    BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
                )

    sec = flush()
    if sec:
        yield sec
