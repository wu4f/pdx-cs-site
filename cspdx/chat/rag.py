"""Full-context Q&A with Gemini + context caching.

Two sections worth of text easily fit in Gemini's context window.
We use the explicit Caches API so the giant context blob is processed
once, then re-used across queries cheaply.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from google import genai
from google.genai import types

from ..models import Section, load_sections


SYSTEM_INSTRUCTION = """You are the Portland State University Department of
Computer Science assistant. Answer questions using ONLY the <section> elements
provided in context.

Each <section> tag has these attributes you must read:
  id="..."  title="..."  url="..."  category="..."  priority="..."

The `url` attribute is always a relative path beginning with "/" and ending
with "/", for example: url='/masters-degree-ms/'

The `priority` attribute is either "primary" or "secondary":
- "primary" sections are the canonical, authoritative source for their topic.
- "secondary" sections are older or duplicated versions kept only for
  completeness. They usually have a corresponding "primary" section that
  covers the same material more accurately.

Source-selection rules (IMPORTANT):
- When the same information is available in both a primary and a secondary
  section, draw your answer from the PRIMARY section and cite only the
  primary section's url.
- Only cite a secondary section if it contains information that no primary
  section provides.
- Never cite both a primary and a secondary section on the same topic; pick
  the primary.

Style rules:
- Be concise and direct. Use bullet lists for steps or enumerations.
- If the user's question is not answerable from the sections, reply with a
  short apology and DO NOT include a Sources list.
- When you DO answer, end with a "Sources:" block formatted like this
  EXAMPLE (the markdown link target is exactly the section's `url` value,
  with no angle brackets, no quotes, no extra text):

    Sources:
    - [Master's Degree (M.S.)](/masters-degree-ms/)
    - [Cybersecurity Certificate](/cybersecurity-certificate/)

- The text in the markdown link target MUST start with "/" and end with "/".
- Do NOT use https:// links, do NOT use angle brackets, do NOT prefix with
  "section url=", do NOT quote the path. Just the relative path itself.
- Only list sections you actually drew material from. One per line.
"""


def build_context_block(
    sections: list[Section], deprioritize: set[str] | None = None
) -> str:
    """Stable order so cache hits.

    Sections whose id is in `deprioritize` are tagged priority='secondary'
    so the model can prefer the canonical version when they overlap.
    """
    deprio = deprioritize or set()
    parts = []
    for s in sorted(sections, key=lambda x: x.id):
        priority = "secondary" if s.id in deprio else "primary"
        parts.append(
            f"<section id='{s.id}' title='{s.title.replace(chr(39),'')}' "
            f"url='{s.url_path}' category='{s.category}' priority='{priority}'>\n"
            f"{s.text}\n"
            f"</section>"
        )
    return "\n\n".join(parts)


@dataclass
class ChatBackend:
    sections_path: str
    model: str = "gemini-2.5-flash"
    api_key: Optional[str] = None
    cache_ttl_minutes: int = 60
    deprioritize: tuple[str, ...] = ()  # section ids to mark priority='secondary'

    def __post_init__(self):
        self._client = genai.Client(api_key=self.api_key or os.getenv("GOOGLE_API_KEY"))
        self._cache_name: Optional[str] = None
        self._cache_expires: Optional[datetime] = None
        self._sections_signature: Optional[str] = None
        self._sections: list[Section] = []
        self._deprio_set: set[str] = set(self.deprioritize)
        self._load_sections()

    # ---- Sections + cache lifecycle --------------------------------------

    def _load_sections(self):
        self._sections = load_sections(self.sections_path)
        # Signature includes the deprioritize set so changes invalidate cache.
        self._sections_signature = "|".join(
            f"{s.id}@{s.revision}" for s in self._sections
        ) + "::deprio:" + ",".join(sorted(self._deprio_set))

    def reload(self):
        """Call after a rebuild to pick up new content."""
        self._load_sections()
        self._cache_name = None
        self._cache_expires = None

    def _ensure_cache(self) -> Optional[str]:
        """Create (or reuse) a Gemini content cache for the big context block.

        Returns the cache name, or None if context is too small to cache
        (Gemini has a minimum-token threshold; we fall back to inline context).
        """
        now = datetime.now(timezone.utc)
        if self._cache_name and self._cache_expires and now < self._cache_expires:
            return self._cache_name

        context = build_context_block(self._sections, self._deprio_set)
        try:
            cache = self._client.caches.create(
                model=self.model,
                config=types.CreateCachedContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    contents=[context],
                    ttl=f"{self.cache_ttl_minutes * 60}s",
                ),
            )
            self._cache_name = cache.name
            self._cache_expires = now + timedelta(minutes=self.cache_ttl_minutes - 1)
            return self._cache_name
        except Exception as e:
            # Most common reason: context below the minimum cacheable size.
            # That's fine -- we'll just inline it every query (still cheap).
            print(f"[rag] context caching disabled: {e}")
            return None

    # ---- Public API ------------------------------------------------------

    def answer(self, question: str) -> str:
        cache_name = self._ensure_cache()
        if cache_name:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=question,
                config=types.GenerateContentConfig(
                    cached_content=cache_name,
                    temperature=0.2,
                ),
            )
        else:
            context = build_context_block(self._sections, self._deprio_set)
            resp = self._client.models.generate_content(
                model=self.model,
                contents=[f"{context}\n\n---\n\nQuestion: {question}"],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                ),
            )
        return resp.text or ""
