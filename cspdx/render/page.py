"""Render each Section to build/site/<id>/index.html using the existing base.html."""
from __future__ import annotations
import os
from pathlib import Path
import jinja2

from ..models import Section


def render_sections(
    sections: list[Section],
    template_path: str,
    out_dir: str,
    base_href: str = "/",
) -> None:
    """Render every Section to <out_dir>/<id>/index.html.

    `base_href` is written into the page's <base> tag. Use "/" for previews
    and self-hosted deploys; use "https://web.cs.pdx.edu/" if you want the
    pages to behave as if served from production no matter where they live.
    """
    tpl = jinja2.Template(Path(template_path).read_text(encoding="utf-8"))
    for s in sections:
        target = Path(out_dir) / s.id
        target.mkdir(parents=True, exist_ok=True)
        html = tpl.render(
            title=s.title, body=s.html, style=s.style, base_href=base_href
        )
        (target / "index.html").write_text(html, encoding="utf-8")
