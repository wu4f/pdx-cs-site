"""Whole-document splitter: the entire Google Doc becomes a single Section.

Renders every body (root + tabs) with a minimal in-house renderer, which
handles the common subset of structural elements: paragraphs, text runs
(bold/italic/underline/links), headings, lists, and tables (including
merged cells and nested tables). Images/inline objects are skipped;
extend as needed.
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
    # The Docs API uses \v for a soft line break (Shift+Enter) and \n for the
    # paragraph terminator; both are line breaks here. Emitting \v verbatim
    # would leave a raw control character in the HTML.
    content = escape(raw).replace("\v", "<br/>").replace("\n", "<br/>")
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


def _strip_trailing_br(inner: str) -> str:
    """Drop trailing <br/>s: the paragraph's terminating newline, plus any soft
    breaks the author left at the end of it. Both render as dangling blank
    lines, and the paragraph's own bottom margin already provides the gap."""
    out = inner.rstrip()
    while out.endswith("<br/>"):
        out = out[: -len("<br/>")].rstrip()
    return out


def _render_paragraph(p: dict) -> str:
    style = (p.get("paragraphStyle") or {}).get("namedStyleType", "NORMAL_TEXT")
    inner = _strip_trailing_br(
        "".join(_render_text_run(e) for e in p.get("elements", []))
    )
    tag = _HEADING_TAGS.get(style)
    if not tag:
        return f"<p>{inner}</p>"
    return tag.format(inner)


def _render_cell(cell: dict) -> str:
    """Render a table cell's structural elements (paragraphs, lists, sub-tables)."""
    content = cell.get("content") or []
    paragraphs = [el["paragraph"] for el in content if "paragraph" in el]
    if len(content) == len(paragraphs) == 1 and not paragraphs[0].get("bullet"):
        # Single-paragraph cells are the common case: emit the runs inline so the
        # cell text doesn't pick up <p>'s block margins and colour.
        inner = "".join(_render_text_run(e) for e in paragraphs[0].get("elements", []))
        return _strip_trailing_br(inner)
    return "".join(_walk_elements(content))


def _render_table(table: dict) -> str:
    rows = table.get("tableRows") or []
    if not rows:
        return ""

    # Only the *leading* run of pinned rows can become <thead>; <thead> must
    # precede <tbody>, so honouring a flag further down would reorder the table.
    header_rows: set[int] = set()
    for i, row in enumerate(rows):
        if not (row.get("tableRowStyle") or {}).get("tableHeader"):
            break
        header_rows.add(i)
    # Google Docs only sets tableHeader when the author pins a header row, which
    # most docs never do. Fall back to treating the first row as the header so
    # screen readers get a <th scope="col"> to announce per column.
    if not header_rows and len(rows) > 1:
        header_rows = {0}

    # Cells merged away by a neighbour's rowSpan/columnSpan still come back from
    # the API as empty placeholders at their grid position; track those
    # positions so we don't emit duplicate cells for them.
    covered: set[tuple[int, int]] = set()
    head_trs: list[str] = []
    body_trs: list[str] = []

    for r, row in enumerate(rows):
        cells: list[str] = []
        for c, cell in enumerate(row.get("tableCells") or []):
            if (r, c) in covered:
                continue
            cell_style = cell.get("tableCellStyle") or {}
            rowspan = cell_style.get("rowSpan") or 1
            colspan = cell_style.get("columnSpan") or 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr or dc:
                        covered.add((r + dr, c + dc))
            tag = "th" if r in header_rows else "td"
            attrs = ' scope="col"' if tag == "th" else ""
            if rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            if colspan > 1:
                attrs += f' colspan="{colspan}"'
            cells.append(f"<{tag}{attrs}>{_render_cell(cell)}</{tag}>")
        tr = "<tr>" + "".join(cells) + "</tr>"
        (head_trs if r in header_rows else body_trs).append(tr)

    # .doc-table lets the stylesheet relax the nowrap header rule that the
    # (short-headed) schedule tables rely on; doc headers are prose-length.
    out = '<table class="doc-table">'
    if head_trs:
        out += "<thead>" + "".join(head_trs) + "</thead>"
    if body_trs:
        out += "<tbody>" + "".join(body_trs) + "</tbody>"
    return out + "</table>"


def _walk_elements(body_content: list[dict]) -> Iterator[str]:
    """Yield HTML fragments for each structural element. Lists collapsed to <ul>."""
    list_buffer: list[str] = []
    for el in body_content:
        if "paragraph" in el:
            p = el["paragraph"]
            # Detect list items by presence of bullet
            if p.get("bullet"):
                inner = "".join(_render_text_run(e) for e in p.get("elements", []))
                list_buffer.append(f"<li>{_strip_trailing_br(inner)}</li>")
                continue
            if list_buffer:
                yield "<ul>" + "".join(list_buffer) + "</ul>"
                list_buffer = []
            yield _render_paragraph(p)
        elif "table" in el:
            if list_buffer:
                yield "<ul>" + "".join(list_buffer) + "</ul>"
                list_buffer = []
            yield _render_table(el["table"])
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
