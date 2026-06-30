"""LLM-based category inference for sections, with revision-keyed cache.

Run once per (section.id, section.revision) -- cheap and idempotent.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Iterable

from google import genai
from google.genai import types

from . import DEFAULT_MODEL
from .models import Section


CATEGORIZE_PROMPT = """You categorize sections of a university Computer
Science department website. Pick exactly ONE category from this list:

{categories}

Rules:
- Return ONLY the category slug, nothing else.
- If multiple could apply, choose the most specific.
- If truly nothing fits, return "other".

Section title: {title}
Section excerpt (first 2000 chars):
{excerpt}
"""


def _load_cache(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_cache(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def categorize_sections(
    sections: Iterable[Section],
    allowed: list[str],
    cache_path: str = "build/category_cache.json",
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> None:
    """Mutates each Section's `category` field. Uses revision-keyed cache."""
    cache_file = Path(cache_path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(cache_file)

    client = genai.Client(api_key=api_key or os.getenv("GOOGLE_API_KEY"))
    cats_block = "\n".join(f"  - {c}" for c in allowed)

    for s in sections:
        cache_key = f"{s.id}@{s.revision}"
        if cache_key in cache and cache[cache_key] in allowed:
            s.category = cache[cache_key]
            continue

        prompt = CATEGORIZE_PROMPT.format(
            categories=cats_block,
            title=s.title,
            excerpt=s.text[:2000],
        )
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        guess = (resp.text or "").strip().lower().splitlines()[0]
        # Match against allowed (or default to "other")
        s.category = guess if guess in allowed else "other"
        cache[cache_key] = s.category

    _save_cache(cache_file, cache)
