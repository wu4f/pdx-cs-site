"""Whole-document splitter: the entire Google Doc becomes a single Section.

Renders every body (root + tabs) with a minimal in-house renderer, which
handles the common subset of structural elements: paragraphs, text runs
(bold/italic/underline/links), headings, and lists. Tables/images/inline
objects are rendered as placeholders; extend as needed.
"""
from __future__ import annotations
from html import escape
from typing import Iterator

from ..models import Section, slugify
from . import gdocs


# --- Tiny structural-elements renderer -------------------------------------

_HEADING_TAGS = {
    "HEADING_1": "<h1>{}</h1>",
    "HEADING_2": "<h2>{}</h2>",
    "HEADING_3": "<h3>{}</h3>",
    "TITLE": "<h1 class='title'>{}</h1>",
}


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


def _render_paragraph(p: dict) -> str:
    style = (p.get("paragraphStyle") or {}).get("namedStyleType", "NORMAL_TEXT")
    inner = "".join(_render_text_run(e) for e in p.get("elements", []))
    tag = _HEADING_TAGS.get(style)
    if not tag:
        return f"<p>{inner}</p>"
    # Headings carry a trailing newline from the paragraph, which our renderer
    # turns into a stray <br/>. Drop it so headings render cleanly.
    heading = inner.rstrip()
    if heading.endswith("<br/>"):
        heading = heading[: -len("<br/>")].rstrip()
    return tag.format(heading)


def _walk_elements(body_content: list[dict]) -> Iterator[str]:
    """Yield HTML fragments for each structural element. Lists collapsed to <ul>."""
    list_buffer: list[str] = []
    for el in body_content:
        if "paragraph" in el:
            p = el["paragraph"]
            # Detect list items by presence of bullet
            if p.get("bullet"):
                inner = "".join(_render_text_run(e) for e in p.get("elements", []))
                list_buffer.append(f"<li>{inner}</li>")
                continue
            if list_buffer:
                yield "<ul>" + "".join(list_buffer) + "</ul>"
                list_buffer = []
            yield _render_paragraph(p)
        elif "table" in el:
            yield "<!-- TODO: table rendering -->"
    if list_buffer:
        yield "<ul>" + "".join(list_buffer) + "</ul>"


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

    title = (doc.get("title") or doc_name or "untitled").strip()

    html_parts: list[str] = []
    text_parts: list[str] = []
    for body in _iter_bodies(doc):
        for html in _walk_elements(body):
            html_parts.append(html)
            text_parts.append(
                BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            )

    sid = slugify(title)
    yield Section(
        id=sid,
        title=title,
        html="\n".join(html_parts),
        style="",
        text=" ".join(text_parts).strip(),
        source_doc_id=doc_id,
        source_doc_name=doc_name,
        source_anchor="",
        revision=revision,
        url_path=f"/{sid}/",
    )
