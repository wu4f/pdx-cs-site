"""Core data model. A 'Section' is the unit of content shared across:
- tab-split docs (one tab == one section)
- whole docs (one doc == one section)
- HTML page generation (one section == one /slug/index.html)
- LLM context (one section == one <section> tag in the prompt)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import re
import json


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "untitled"


def heading_anchor(text: str, seen: dict[str, int]) -> str:
    """Return a unique HTML id for a heading with the given text.

    `seen` accumulates across a document so repeated heading text gets a
    numeric suffix. Shared by the whole splitter (which assigns ids as it
    renders, so internal cross-references have something to point at) and by
    `render/toc.py` (which fills in ids for any page whose source didn't) —
    both must derive the same id from the same text or the two would disagree
    about a page's anchors.
    """
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-") or "heading"
    if slug in seen:
        seen[slug] += 1
        slug = f"{slug}-{seen[slug]}"
    else:
        seen[slug] = 0
    return slug


@dataclass
class Section:
    # Stable identity
    id: str                          # slug, used in URL and prompt
    title: str                       # human-readable

    # Content
    html: str = ""                   # cleaned HTML body (for page render)
    style: str = ""                  # extracted <style> (Google CSS, filtered)
    text: str = ""                   # plain text (for LLM context)

    # Provenance (for citation + diffing)
    source_doc_id: str = ""
    source_doc_name: str = ""
    source_anchor: str = ""          # tab_id OR heading anchor inside doc
    revision: str = ""               # Docs API revisionId at fetch time

    # Derived
    category: str = ""               # filled by categorize.py
    url_path: str = ""               # e.g. "/graduate-admissions/"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Section":
        return cls(**d)


def dump_sections(sections: list[Section], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([s.to_json() for s in sections], f, indent=2, ensure_ascii=False)


def load_sections(path: str) -> list[Section]:
    with open(path, "r", encoding="utf-8") as f:
        return [Section.from_json(d) for d in json.load(f)]
