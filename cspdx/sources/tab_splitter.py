"""Tab-based splitter: each Google Doc tab becomes a Section.

Uses the Docs API and the same renderer as whole_splitter, avoiding
the HTML-export endpoint that is prone to 429 rate limits.
"""
from __future__ import annotations
from typing import Iterator

from bs4 import BeautifulSoup

from ..models import Section, slugify
from . import gdocs
from .whole_splitter import _Ctx, _walk_elements, _resolve_internal_links


def split(creds, doc_id: str, doc_name: str = "") -> Iterator[Section]:
    doc = gdocs.get_doc(creds, doc_id)
    revision = doc.get("revisionId", "")

    for tab in doc.get("tabs", []) or []:
        props = tab.get("tabProperties", {})
        tab_id = props.get("tabId", "")
        title = props.get("title", "").strip() or "untitled"

        dt = tab.get("documentTab") or {}
        body_content = (dt.get("body") or {}).get("content") or []
        lists = dt.get("lists") or {}

        ctx = _Ctx()
        ctx.lists = lists

        html_parts: list[str] = []
        text_parts: list[str] = []
        for fragment in _walk_elements(body_content, ctx):
            html_parts.append(fragment)
            text_parts.append(
                BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True)
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
            source_anchor=tab_id,
            revision=revision,
            url_path=url_path,
        )
