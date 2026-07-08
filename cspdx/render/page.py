"""Render each Section to build/site/<id>/index.html using the existing base.html."""
from __future__ import annotations
import os
from pathlib import Path
import jinja2

from ..models import Section
from .landing import build_nav_groups, CATEGORY_LABELS, CATEGORY_ICONS, meta_description, _site_base_url


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
    base_url = _site_base_url()
    tpl_path = Path(template_path)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tpl_path.parent)))
    tpl = env.get_template(tpl_path.name)
    for s in sections:
        target = Path(out_dir) / s.id
        target.mkdir(parents=True, exist_ok=True)
        desc = (
            meta_description(s.text) if s.text.strip()
            else f"Information about {s.title} for the Department of Computer Science at Portland State University."
        )
        html = tpl.render(
            title=s.title, body=s.html, style=s.style, base_href=base_href,
            nav_groups=nav_groups,
            cat_labels=CATEGORY_LABELS,
            cat_icons=CATEGORY_ICONS,
            canonical_url=base_url + s.url_path,
            meta_description=desc,
        )
        (target / "index.html").write_text(html, encoding="utf-8")
