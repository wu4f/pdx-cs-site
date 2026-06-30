"""Manual category assignment for sections, backed by build/category.json.

Looks up each section's slug in category.json. Unknown slugs are added with
a default of "about" and the file is saved so they can be manually adjusted.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Iterable

from .models import Section


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def categorize_sections(
    sections: Iterable[Section],
    allowed: list[str],
    cache_path: str = "build/category.json",
    **_kwargs,
) -> None:
    """Mutates each Section's `category` field from the category.json map.

    Slugs absent from the file are assigned "about" and written back so they
    can be manually reviewed and corrected.
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
                print(f"  [categorize] new slug {s.id!r} -> default 'about'")
                mapping[s.id] = "about"
                changed = True
            s.category = "about"

    if changed:
        _save(cat_file, mapping)
