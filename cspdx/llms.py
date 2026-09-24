"""Generate llms.txt and llms-full.txt (https://llmstxt.org/) for the built site.

`llms.txt` is a Markdown index: the site name, a one-line summary, and one link
per active page grouped by category. `llms-full.txt` is the whole site's
content in a single Markdown file, so an agent can read everything in one
fetch instead of crawling and de-chroming each HTML page.

Page bodies are converted from `Section.html` rather than taken from
`Section.text`: the plain text is one run-on line per page, which loses the
headings, lists, tables, and links that make the content navigable. The HTML
comes from our own Docs API renderer, so the converter only has to handle its
small tag set (h1-h6, p, br, ul/ol/li, table, a, strong/em/u, hr).
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from .models import Section
from .render.landing import (
    CATEGORY_LABELS,
    _LANDING_DESCRIPTION,
    build_nav_groups,
    meta_description,
)
from .schedule import SCHEDULE_SECTION_ID
from .sitemap import DEFAULT_BASE_URL

SITE_NAME = "Portland State University Computer Science"

_BLOCK_TAGS = {"p", "ul", "ol", "table", "hr", "div", "section",
               "h1", "h2", "h3", "h4", "h5", "h6"}


def _base(base_url: str | None) -> str:
    return (base_url or os.getenv("SITE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")


# ---------------------------------------------------------------------------
# HTML -> Markdown
# ---------------------------------------------------------------------------

def _wrap(text: str, mark: str, close: str | None = None) -> str:
    """Wrap text in an emphasis marker, keeping edge whitespace outside it.

    The renderer emits runs like `<strong>Title </strong>`; `**Title **` is not
    valid emphasis, so the space has to move outside the markers. Links get
    the same treatment (`close` differs from `mark`), or `<a>CS 161 </a>(4)`
    would lose the space before "(4)".
    """
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return f"{lead}{mark}{core}{mark if close is None else close}{trail}"


def _inline(node, base: str, plain: bool = False) -> str:
    """Render a node's inline content. `plain` drops emphasis (for headings)."""
    if isinstance(node, NavigableString):
        # Docs use literal asterisks as footnote marks ("elective**"); escape
        # them so they can't pair up into emphasis.
        return re.sub(r"\s+", " ", str(node)).replace("*", "\\*")
    if not isinstance(node, Tag):
        return ""
    name = node.name
    if name == "br":
        return "\n"
    inner = "".join(_inline(c, base, plain) for c in node.children)
    if name == "a":
        href = (node.get("href") or "").strip()
        if not href or not inner.strip():
            return inner
        return _wrap(inner, "[", f"]({urljoin(base + '/', href)})")
    if plain:
        return inner
    if name in ("strong", "b"):
        return _wrap(inner, "**")
    if name in ("em", "i"):
        return _wrap(inner, "*")
    return inner


def _clean_lines(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().split("\n"))


def _list(node: Tag, base: str, depth: int) -> list[str]:
    ordered = node.name == "ol"
    n = int(node.get("start", 1) or 1)
    indent = "   " * depth
    out: list[str] = []
    for li in node.find_all("li", recursive=False):
        marker = f"{n}." if ordered else "-"
        n += 1
        inline_parts: list[str] = []
        children: list[str] = []
        for c in li.children:
            if isinstance(c, Tag) and c.name in ("ul", "ol"):
                children += _list(c, base, depth + 1)
            elif isinstance(c, Tag) and c.name in _BLOCK_TAGS:
                # Continuation paragraph inside a multi-paragraph item.
                para = _clean_lines(_inline(c, base))
                if para:
                    children.append(f"{indent}   {para}")
            else:
                inline_parts.append(_inline(c, base))
        text = _clean_lines("".join(inline_parts)).replace("\n", f"\n{indent}   ")
        out.append(f"{indent}{marker} {text}".rstrip())
        out += children
    return out


def _table(node: Tag, base: str) -> str:
    """GFM table. Merged cells repeat their text in every grid slot they cover,
    since GFM has no spans and a blank slot reads as "no value"."""
    rows: list[list[str]] = []
    covered: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(node.find_all("tr")):
        row: list[str] = []
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (r, c) in covered:
                row.append(covered.pop((r, c)))
                c += 1
            text = _clean_lines(_inline(cell, base)).replace("\n", "<br>")
            text = text.replace("|", "\\|")
            span = int(cell.get("colspan", 1) or 1)
            rspan = int(cell.get("rowspan", 1) or 1)
            for k in range(span):
                row.append(text)
                for dr in range(1, rspan):
                    covered[(r + dr, c + k)] = text
            c += span
        while (r, c) in covered:
            row.append(covered.pop((r, c)))
            c += 1
        rows.append(row)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _blocks(node, base: str) -> list[str]:
    out: list[str] = []
    pending: list[str] = []  # loose inline content between blocks

    def flush():
        text = _clean_lines("".join(pending))
        if text:
            out.append(text)
        pending.clear()

    for c in node.children:
        if not isinstance(c, Tag):
            pending.append(_inline(c, base))
            continue
        name = c.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            flush()
            text = " ".join(_inline(c, base, plain=True).split())
            if text:
                out.append("#" * int(name[1]) + " " + text)
        elif name == "p":
            flush()
            text = _clean_lines(_inline(c, base))
            if text:
                out.append(text)
        elif name in ("ul", "ol"):
            flush()
            out.append("\n".join(_list(c, base, 0)))
        elif name == "table":
            flush()
            out.append(_table(c, base))
        elif name == "hr":
            flush()
            out.append("---")
        elif name in ("div", "section", "thead", "tbody", "nav"):
            flush()
            out += _blocks(c, base)
        elif name in ("style", "script"):
            continue
        else:
            pending.append(_inline(c, base))
    flush()
    return [b for b in out if b]


def html_to_markdown(html: str, base_url: str | None = None) -> str:
    """Convert a section body to Markdown; relative links become absolute."""
    soup = BeautifulSoup(html or "", "html.parser")
    return "\n\n".join(_blocks(soup, _base(base_url)))


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _page_description(s: Section) -> str:
    """Page summary for llms.txt, without the page's own H1 repeated in front.

    `Section.text` starts with the heading text, which in the index would read
    "[Advising](…): Advising Academic advising is…".
    """
    if s.id == SCHEDULE_SECTION_ID:
        return ("CS and AI course sections for recent and upcoming terms: CRN, "
                "title, credits, meeting days/times, and instructor")
    text = " ".join(s.text.split())
    h1 = BeautifulSoup(s.html or "", "html.parser").find(["h1", "h2"])
    lead = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""
    if lead and text.startswith(lead):
        text = text[len(lead):].lstrip()
    return meta_description(text) if text else ""


def generate_llms_txt(
    sections: list[Section],
    out_path: Path,
    *,
    include_schedule: bool = True,
    base_url: str | None = None,
) -> int:
    """Write llms.txt; returns the number of page links written."""
    base = _base(base_url)
    lines = [
        f"# {SITE_NAME}",
        "",
        f"> {_LANDING_DESCRIPTION}",
        "",
        "This site covers the Computer Science department's degree programs, "
        "admissions, courses, policies, and student resources. The full text of "
        f"every page is available as a single Markdown file at {base}/llms-full.txt.",
    ]
    count = 0
    for cat, secs in build_nav_groups(sections):
        if not include_schedule:
            secs = [s for s in secs if s.id != SCHEDULE_SECTION_ID]
        if not secs:
            continue
        lines += ["", f"## {CATEGORY_LABELS.get(cat, cat.title())}", ""]
        for s in secs:
            desc = _page_description(s)
            entry = f"- [{s.title}]({base}{s.url_path})"
            lines.append(f"{entry}: {desc}" if desc else entry)
            count += 1
    lines += [
        "",
        "## Optional",
        "",
        f"- [Full site content]({base}/llms-full.txt): every page above in one Markdown file",
        f"- [Sitemap]({base}/sitemap.xml)",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def generate_llms_full_txt(
    sections: list[Section],
    out_path: Path,
    *,
    include_schedule: bool = True,
    base_url: str | None = None,
) -> int:
    """Write llms-full.txt; returns its size in characters.

    The `course-schedules` section's HTML is only the placeholder tab, so its
    body comes from `.text` instead — `apply_schedule_text()` must already have
    run, which is the case for `sections.json` and for `cmd_build` in order.
    """
    base = _base(base_url)
    parts = [
        f"# {SITE_NAME}",
        f"> {_LANDING_DESCRIPTION}",
        f"Source: {base}/ — generated from the same content as the HTML pages; "
        "each page below begins with its canonical URL.",
    ]
    for _cat, secs in build_nav_groups(sections):
        for s in secs:
            if s.id == SCHEDULE_SECTION_ID:
                if not include_schedule:
                    continue
                body = f"# {s.title}\n\n```\n{s.text.strip()}\n```"
            else:
                body = html_to_markdown(s.html, base)
                if not body.startswith("# "):
                    body = f"# {s.title}\n\n{body}"
            parts.append(f"---\n\nURL: {base}{s.url_path}\n\n{body}")
    content = "\n\n".join(parts) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return len(content)
