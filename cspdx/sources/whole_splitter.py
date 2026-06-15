"""Whole-document splitter: the entire Google Doc becomes a single Section.

Renders every body (root + tabs) with the same in-house renderer the
heading splitter uses, but without slicing on H1 -- the doc is one page.
This keeps fidelity identical to the heading-split version of the same doc;
the only difference is that here all H1 blocks live on one page instead of
being split into separate pages.
"""
from __future__ import annotations
from typing import Iterator

from ..models import Section, slugify
from . import gdocs
from .heading_splitter import _iter_bodies, _walk_elements


def split(creds, doc_id: str, doc_name: str = "") -> Iterator[Section]:
    from bs4 import BeautifulSoup
    doc = gdocs.get_doc(creds, doc_id)
    revision = doc.get("revisionId", "")

    title = (doc.get("title") or doc_name or "untitled").strip()

    html_parts: list[str] = []
    text_parts: list[str] = []
    for body in _iter_bodies(doc):
        for _kind, html, _title in _walk_elements(body):
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
