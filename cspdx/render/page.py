"""Render each Section to build/site/<id>/index.html using the existing base.html."""
from __future__ import annotations
import os
from pathlib import Path
import jinja2

from ..models import Section
from .landing import build_nav_groups, CATEGORY_LABELS, CATEGORY_ICONS


def render_sections(
    sections: list[Section],
    template_path: str,
    out_dir: str,
    base_href: str = "/",
    nav_sections: list[Section] | None = None,
    nav_exclude_ids: list[str] | None = None,
) -> None:
    """Render every Section to <out_dir>/<id>/index.html.

    `base_href` is written into the page's <base> tag. Use "/" for previews
    and self-hosted deploys; use "https://web.cs.pdx.edu/" if you want the
    pages to behave as if served from production no matter where they live.

    `nav_sections` and `nav_exclude_ids` control the category nav bar that
    appears on each section page. Pass nav_sections to enable the nav bar.
    """
    nav_groups = build_nav_groups(nav_sections, nav_exclude_ids) if nav_sections else []
    tpl = jinja2.Template(Path(template_path).read_text(encoding="utf-8"))
    for s in sections:
        target = Path(out_dir) / s.id
        target.mkdir(parents=True, exist_ok=True)
        html = tpl.render(
            title=s.title, body=s.html, style=s.style, base_href=base_href,
            nav_groups=nav_groups,
            cat_labels=CATEGORY_LABELS,
            cat_icons=CATEGORY_ICONS,
        )
        (target / "index.html").write_text(html, encoding="utf-8")
