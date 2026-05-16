"""Tab-based splitter: each Google Doc tab becomes a Section.

Uses the docs.export endpoint (which respects ?tab=...) for rich HTML,
then cleans it the same way gdoc2site did.
"""
from __future__ import annotations
from typing import Iterator

from ..models import Section, slugify
from . import gdocs
from .cleaner import clean_exported_html


def split(creds, doc_id: str, doc_name: str = "") -> Iterator[Section]:
    doc = gdocs.get_doc(creds, doc_id)
    revision = doc.get("revisionId", "")

    for tab in doc.get("tabs", []) or []:
        props = tab.get("tabProperties", {})
        tab_id = props.get("tabId", "")
        title = props.get("title", "").strip() or "untitled"

        raw = gdocs.export_tab_html(creds, doc_id, tab_id)
        body_html, style_html, text = clean_exported_html(raw)

        sid = slugify(title)
        yield Section(
            id=sid,
            title=title,
            html=body_html,
            style=style_html,
            text=text,
            source_doc_id=doc_id,
            source_doc_name=doc_name,
            source_anchor=tab_id,
            revision=revision,
            url_path=f"/{sid}/",
        )
