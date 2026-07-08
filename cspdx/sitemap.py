"""Generate sitemap.xml for the built site."""
from __future__ import annotations
import os
from pathlib import Path

from .models import Section

DEFAULT_BASE_URL = "https://web.cs.pdx.edu"


def generate_sitemap(
    sections: list[Section],
    out_path: Path,
    *,
    include_schedule: bool = True,
    base_url: str | None = None,
) -> str:
    base = (base_url or os.getenv("SITE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")

    paths = ["/"] + [s.url_path for s in sections]
    if include_schedule:
        paths.append("/course-schedules/")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path in paths:
        lines.append(f"  <url><loc>{base}{path}</loc></url>")
    lines.append("</urlset>")

    xml = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(xml, encoding="utf-8")
    return base


def generate_robots_txt(out_path: Path, *, base_url: str | None = None) -> None:
    base = (base_url or os.getenv("SITE_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
    content = f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
