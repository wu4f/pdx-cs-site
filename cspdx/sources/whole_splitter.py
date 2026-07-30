"""Whole-document splitter: the entire Google Doc becomes a single Section.

Renders every body (root + tabs) with a minimal in-house renderer, which
handles the common subset of structural elements: paragraphs, text runs
(bold/italic/underline/links), headings, lists (ordered and unordered, nested
to any depth), and tables (including merged cells and nested tables).
Images/inline objects are skipped; extend as needed.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from html import escape
from typing import Iterator
from urllib.parse import urlsplit

from ..models import Section, heading_anchor, slugify
from . import gdocs


@dataclass
class _Ctx:
    """Document-wide state the renderer threads through every element.

    `glyphs` and `lists` decide whether a bullet belongs in an <ol> or a <ul>;
    `counts` tracks how many items each (listId, nesting level) has produced so
    far, so an interrupted numbered list can resume where it left off.
    """
    glyphs: dict[tuple[str, int], tuple[str, str]] = field(default_factory=dict)
    lists: dict = field(default_factory=dict)
    counts: dict[tuple[str, int], int] = field(default_factory=dict)


# --- Tiny structural-elements renderer -------------------------------------

_HEADING_TAGS = {
    "HEADING_1": ("h1", ""),
    "HEADING_2": ("h2", ""),
    "HEADING_3": ("h3", ""),
    "HEADING_4": ("h4", ""),
    "HEADING_5": ("h5", ""),
    "HEADING_6": ("h6", ""),
    "TITLE": ("h1", "title"),
}

# Every level Docs can produce. The TOC numbers the first four (see
# render/toc.py); h5/h6 still render as headings and still get ids, they just
# don't appear in the contents list.
_HEADING_ELEMENTS = ["h1", "h2", "h3", "h4", "h5", "h6"]

# Scratch attributes carrying the Docs-side ids through rendering; both are
# consumed (and removed) by _resolve_internal_links().
_HEADING_ID_ATTR = "data-doc-heading-id"   # on a heading: its Docs headingId
_HEADING_REF_ATTR = "data-doc-heading-ref"  # on an <a>: the headingId it targets


def _link_heading_id(link: dict) -> str:
    """Return the Docs headingId a link points at, or '' if it isn't one.

    With includeTabsContent=true the API returns `heading: {id, tabId}`;
    `headingId` is the legacy single-tab shape. The tab is irrelevant here —
    this splitter renders every tab onto one page, so all targets are local.
    Bookmark links (`link.bookmark`) can't be resolved at all: the API exposes
    the bookmark id on the link but never says where the bookmark itself sits
    in the content, so there is nothing to anchor to.
    """
    return (link.get("heading") or {}).get("id") or link.get("headingId") or ""


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
    link = style.get("link") or {}
    # WCAG 2.4.4 / 2.4.9 (link purpose): never emit an <a> with no accessible
    # name. If the text content is empty or only whitespace, skip the link wrap
    # rather than create an empty <a>.
    if raw.strip():
        heading_id = _link_heading_id(link)
        if link.get("url"):
            content = f'<a href="{escape(link["url"])}">{content}</a>'
        elif heading_id:
            # A cross-reference to another part of this doc. The target's HTML
            # anchor isn't known until every heading has been rendered, so
            # stash the Docs id and let _resolve_internal_links() fill in href.
            content = f'<a {_HEADING_REF_ATTR}="{escape(heading_id)}">{content}</a>'
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
    pstyle = p.get("paragraphStyle") or {}
    style = pstyle.get("namedStyleType", "NORMAL_TEXT")
    inner = _strip_trailing_br(
        "".join(_render_text_run(e) for e in p.get("elements", []))
    )
    heading = _HEADING_TAGS.get(style)
    if not heading:
        return f"<p>{inner}</p>"
    tag, cls = heading
    attrs = f' class="{cls}"' if cls else ""
    # Carry the Docs headingId so cross-references can be pointed at this
    # heading's generated anchor once the whole doc has been rendered.
    if pstyle.get("headingId"):
        attrs += f' {_HEADING_ID_ATTR}="{escape(pstyle["headingId"])}"'
    return f"<{tag}{attrs}>{inner}</{tag}>"


# --- Lists ------------------------------------------------------------------

# CSS list-style-types we'll echo out of the exported stylesheet, so a
# malformed export can't inject arbitrary declarations into the page.
_LIST_STYLE_TYPES = {
    "decimal", "decimal-leading-zero", "lower-alpha", "lower-latin",
    "upper-alpha", "upper-latin", "lower-roman", "upper-roman",
}

# glyphType -> CSS list-style-type, for the lists the Docs API does describe.
_ORDERED_GLYPHS = {
    "DECIMAL": "decimal",
    "ZERO_DECIMAL": "decimal-leading-zero",
    "ALPHA": "lower-alpha",
    "UPPER_ALPHA": "upper-alpha",
    "ROMAN": "lower-roman",
    "UPPER_ROMAN": "upper-roman",
}


def _glyphs_from_export(export_html: str) -> dict[tuple[str, int], tuple[str, str]]:
    """Map (list key, nesting level) -> (list tag, CSS list-style-type).

    The Docs API is not a reliable source for this: it reports
    `glyphType: GLYPH_TYPE_UNSPECIFIED` and no `glyphSymbol` for most lists —
    in the graduate handbook, a bulleted list and a decimal-numbered one come
    back as byte-identical `lists` entries. Google's own HTML export does know,
    so read the answer out of its stylesheet, where each list level appears as
    `ol.lst-<listId>-<level>` / `ul.lst-<listId>-<level>` and ordered levels
    carry a `counter(..., <style>)` rule. The class names embed the same list
    ids the API uses, with `.` written as `_`.
    """
    styles: dict[tuple[str, int], str] = {}
    for key, level, style in re.findall(
        r"\.lst-([A-Za-z0-9_-]+?)-(\d+)\s*>\s*li:before\s*\{[^}]*?"
        r"counter\(lst-ctn-[^,]+,\s*([\w-]+)\)",
        export_html,
    ):
        styles[(key, int(level))] = style

    out: dict[tuple[str, int], tuple[str, str]] = {}
    for tag, key, level in re.findall(
        r"\b(ol|ul)\.lst-([A-Za-z0-9_-]+?)-(\d+)\b", export_html
    ):
        k = (key, int(level))
        if tag == "ul":
            out[k] = ("ul", "")
        else:
            style = styles.get(k, "decimal")
            out[k] = ("ol", style if style in _LIST_STYLE_TYPES else "decimal")
    return out


def _list_glyphs(creds, doc_id: str, doc: dict) -> dict[tuple[str, int], tuple[str, str]]:
    """Build the glyph map for a whole doc, one HTML export per tab.

    A failed export costs list numbering, not the build: fall back to whatever
    the API does say (see _list_kind) and carry on.
    """
    glyphs: dict[tuple[str, int], tuple[str, str]] = {}
    if not any(lists for _, lists in _iter_bodies(doc)):
        return glyphs  # no lists anywhere; don't pay for the export
    for tab_id in _tab_ids(doc) or [None]:
        try:
            glyphs.update(_glyphs_from_export(gdocs.export_tab_html(creds, doc_id, tab_id)))
        except Exception as e:  # noqa: BLE001 - any export failure is non-fatal
            print(f"  [warn] list glyph export failed ({e}); "
                  f"numbered lists may render as bullets")
    return glyphs


def _nesting_level(ctx: _Ctx, list_id: str, level: int) -> dict:
    levels = ((ctx.lists.get(list_id) or {}).get("listProperties") or {}).get(
        "nestingLevels"
    ) or []
    return levels[level] if 0 <= level < len(levels) else {}


def _list_kind(ctx: _Ctx, list_id: str, level: int) -> tuple[str, str]:
    """Return (tag, CSS list-style-type) for one level of one list."""
    hit = ctx.glyphs.get((list_id.replace(".", "_"), level))
    if hit:
        return hit
    style = _ORDERED_GLYPHS.get(_nesting_level(ctx, list_id, level).get("glyphType", ""))
    return ("ol", style) if style else ("ul", "")


def _open_list(tag: str, style: str, start: int) -> str:
    attrs = ""
    if tag == "ol":
        if style and style != "decimal":
            attrs += f' style="list-style-type: {style}"'
        if start > 1:
            attrs += f' start="{start}"'
    return f"<{tag}{attrs}>"


def _render_list(ctx: _Ctx, items: list[tuple[dict, str]]) -> str:
    """Render a consecutive run of bullet paragraphs as nested <ul>/<ol>.

    Each item carries its own `listId` and `nestingLevel`; a deeper level opens
    a sub-list *inside* the item above it (which is why the enclosing <li> is
    left open until its children are done), and a shallower one closes back out.
    """
    out: list[str] = []
    stack: list[tuple[int, str, str]] = []  # (level, tag, style)
    li_open = False

    for bullet, inner in items:
        list_id = bullet.get("listId", "")
        level = bullet.get("nestingLevel", 0) or 0
        tag, style = _list_kind(ctx, list_id, level)

        # Docs restarts a nested level under each new parent item, but keeps
        # counting at the same level across an interruption (a paragraph
        # dropped between two runs of one list) — which HTML won't do on its
        # own, hence the explicit start= below.
        for stale in [k for k in ctx.counts if k[0] == list_id and k[1] > level]:
            del ctx.counts[stale]
        emitted = ctx.counts.get((list_id, level), 0)
        ctx.counts[(list_id, level)] = emitted + 1
        start = (_nesting_level(ctx, list_id, level).get("startNumber") or 1) + emitted

        if stack and level > stack[-1][0]:
            out.append(_open_list(tag, style, start))  # nests in the open <li>
            stack.append((level, tag, style))
            li_open = False
        else:
            while len(stack) > 1 and level < stack[-1][0]:
                if li_open:
                    out.append("</li>")
                out.append(f"</{stack.pop()[1]}>")
                li_open = True  # the parent's <li> is still open
            if li_open:
                out.append("</li>")
                li_open = False
            if not stack or (stack[-1][1], stack[-1][2]) != (tag, style):
                if stack:
                    out.append(f"</{stack.pop()[1]}>")
                out.append(_open_list(tag, style, start))
                stack.append((level, tag, style))
            else:
                # Same list, but the run started deeper than it continues.
                stack[-1] = (level, tag, style)
        out.append(f"<li>{inner}")
        li_open = True

    while stack:
        if li_open:
            out.append("</li>")
        out.append(f"</{stack.pop()[1]}>")
        li_open = True
    return "".join(out)


def _render_cell(cell: dict, ctx: _Ctx) -> str:
    """Render a table cell's structural elements (paragraphs, lists, sub-tables)."""
    content = cell.get("content") or []
    paragraphs = [el["paragraph"] for el in content if "paragraph" in el]
    if len(content) == len(paragraphs) == 1 and not paragraphs[0].get("bullet"):
        # Single-paragraph cells are the common case: emit the runs inline so the
        # cell text doesn't pick up <p>'s block margins and colour.
        inner = "".join(_render_text_run(e) for e in paragraphs[0].get("elements", []))
        return _strip_trailing_br(inner)
    return "".join(_walk_elements(content, ctx))


def _render_table(table: dict, ctx: _Ctx) -> str:
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
            cells.append(f"<{tag}{attrs}>{_render_cell(cell, ctx)}</{tag}>")
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


def _walk_elements(body_content: list[dict], ctx: _Ctx) -> Iterator[str]:
    """Yield HTML fragments for each structural element.

    Consecutive bullet paragraphs are buffered and handed to _render_list()
    together, since only the whole run tells us where sub-lists open and close.
    """
    run: list[tuple[dict, str]] = []
    for el in body_content:
        if "paragraph" in el:
            p = el["paragraph"]
            # Detect list items by presence of bullet
            if p.get("bullet"):
                inner = "".join(_render_text_run(e) for e in p.get("elements", []))
                run.append((p["bullet"], _strip_trailing_br(inner)))
                continue
            if run:
                yield _render_list(ctx, run)
                run = []
            yield _render_paragraph(p)
        elif "table" in el:
            if run:
                yield _render_list(ctx, run)
                run = []
            yield _render_table(el["table"], ctx)
    if run:
        yield _render_list(ctx, run)


def _iter_bodies(doc: dict) -> Iterator[tuple[list[dict], dict]]:
    """Yield (body.content, lists) for the root body and every (possibly
    nested) tab. Some Google Docs put all content inside a single tab even when
    they're conceptually 'one document'. The `lists` map lives beside the body
    it belongs to — at the root when the doc has no tabs, on each documentTab
    otherwise."""
    root_body = (doc.get("body") or {}).get("content") or []
    if root_body:
        yield root_body, doc.get("lists") or {}
    def walk(tabs):
        for t in tabs or []:
            dt = t.get("documentTab") or {}
            content = (dt.get("body") or {}).get("content") or []
            if content:
                yield content, dt.get("lists") or {}
            yield from walk(t.get("childTabs") or [])
    yield from walk(doc.get("tabs") or [])


def _tab_ids(doc: dict) -> list[str]:
    """Every tab id in the doc, outer tabs before their children."""
    ids: list[str] = []
    def walk(tabs):
        for t in tabs or []:
            tab_id = (t.get("tabProperties") or {}).get("tabId")
            if tab_id:
                ids.append(tab_id)
            walk(t.get("childTabs") or [])
    walk(doc.get("tabs") or [])
    return ids


def _self_link_heading(href: str, doc_id: str) -> str:
    """Return the headingId of a plain URL that points back into this same doc.

    Google's "copy link to this heading" produces an ordinary external URL
    (…/document/d/<id>/edit?tab=t.0#heading=h.abc) rather than a heading link,
    so those need the same treatment or they bounce the reader out to the
    Google Doc.
    """
    if not doc_id or f"/document/d/{doc_id}" not in href:
        return ""
    m = re.search(r"heading=(h\.[\w-]+)", urlsplit(href).fragment)
    return m.group(1) if m else ""


def _resolve_internal_links(html: str, url_path: str, doc_id: str) -> str:
    """Give every heading a stable id and turn the doc's internal
    cross-references into fragment links to those ids.

    Ids are assigned here rather than left to `render/toc.py` because the TOC
    is only injected above a heading-count threshold — below it the links would
    have nothing to point at. `toc.inject_toc()` keeps any id it finds, so the
    two stay in agreement.

    Fragments are prefixed with `url_path` because every generated page carries
    a `<base href="/">`, under which a bare `#anchor` resolves against the
    landing page instead of the current one.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    seen: dict[str, int] = {}
    anchors: dict[str, str] = {}  # Docs headingId -> HTML id
    for h in soup.find_all(_HEADING_ELEMENTS):
        if not h.get("id"):
            h["id"] = heading_anchor(h.get_text(strip=True), seen)
        doc_heading_id = h.get(_HEADING_ID_ATTR)
        if doc_heading_id:
            anchors[doc_heading_id] = h["id"]
            del h[_HEADING_ID_ATTR]

    for a in soup.find_all("a"):
        ref = a.get(_HEADING_REF_ATTR) or _self_link_heading(a.get("href", ""), doc_id)
        if not ref:
            continue
        a.attrs.pop(_HEADING_REF_ATTR, None)
        if ref in anchors:
            a["href"] = f"{url_path}#{anchors[ref]}"
        elif not a.get("href"):
            # Target heading was deleted from the doc: keep the text, drop the
            # link, rather than emit an <a> that goes nowhere.
            a.unwrap()
    return str(soup)


def split(creds, doc_id: str, doc_name: str = "") -> Iterator[Section]:
    from bs4 import BeautifulSoup
    doc = gdocs.get_doc(creds, doc_id)
    revision = doc.get("revisionId", "")

    title = (doc.get("title") or doc_name or "untitled").strip()

    ctx = _Ctx(glyphs=_list_glyphs(creds, doc_id, doc))

    html_parts: list[str] = []
    text_parts: list[str] = []
    for body, lists in _iter_bodies(doc):
        ctx.lists.update(lists)
        for html in _walk_elements(body, ctx):
            html_parts.append(html)
            text_parts.append(
                BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
            )

    sid = slugify(title)
    url_path = f"/{sid}/"
    yield Section(
        id=sid,
        title=title,
        html=_resolve_internal_links("\n".join(html_parts), url_path, doc_id),
        style="",
        text=" ".join(text_parts).strip(),
        source_doc_id=doc_id,
        source_doc_name=doc_name,
        source_anchor="",
        revision=revision,
        url_path=url_path,
    )
