"""Manual category assignment for sections, backed by build/category.json.

Looks up each section's slug in category.json. Unknown slugs are added with
a default of "ignore" and the file is saved so they can be manually adjusted.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Iterable

from .models import Section

# A slug we've never seen is hidden until a human classifies it: a new Doc tab
# is usually a draft, and defaulting it to a visible category would publish it
# — and put it on the landing page and in the chatbot's context — the moment it
# appears. "ignore" still renders the page, so the URL works for review.
DEFAULT_CATEGORY = "ignore"


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save(path: Path, data: dict) -> None:
    # Trailing newline: this file is committed, so without it every build
    # produces a spurious one-line diff on the closing brace.
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def categorize_sections(
    sections: Iterable[Section],
    allowed: list[str],
    cache_path: str = "build/category.json",
    **_kwargs,
) -> None:
    """Mutates each Section's `category` field from the category.json map.

    Slugs absent from the file are assigned DEFAULT_CATEGORY and written back
    so they can be manually reviewed and corrected.
    """
    cat_file = Path(cache_path)
    cat_file.parent.mkdir(parents=True, exist_ok=True)
    mapping = _load(cat_file)

    changed = False
    for s in sections:
        if s.id in mapping and mapping[s.id] in allowed:
            s.category = mapping[s.id]
        else:
            if s.id not in mapping:
                print(
                    f"  [categorize] new slug {s.id!r} -> default "
                    f"{DEFAULT_CATEGORY!r} (set it in {cat_file} to publish)"
                )
                mapping[s.id] = DEFAULT_CATEGORY
                changed = True
            else:
                print(
                    f"  [categorize] slug {s.id!r} has category "
                    f"{mapping[s.id]!r}, which is not in {allowed} -> "
                    f"{DEFAULT_CATEGORY!r}"
                )
            s.category = DEFAULT_CATEGORY

    if changed:
        _save(cat_file, mapping)
