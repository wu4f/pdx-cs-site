"""Command-line interface: `cspdx build`, `cspdx render-landing`, `cspdx serve`."""
from __future__ import annotations
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

from . import buildmeta
from .admin import reload_chat
from .models import Section, dump_sections, load_sections
from .sources import gdocs, tab_splitter, heading_splitter, whole_splitter
from .categorize import categorize_sections
from .render.page import render_sections
from .render.landing import render_landing
from .schedule import generate_schedule_page
from .sitemap import generate_sitemap, generate_robots_txt


SPLITTERS = {
    "tabs": tab_splitter.split,
    "headings": heading_splitter.split,
    "whole": whole_splitter.split,
}


def _copy_static(static_dir: Path, site_dir: Path) -> int:
    """Copy everything under static_dir into site_dir, overlaying existing files.

    This makes build/site self-contained: version-controlled assets like the
    uploaded PDFs in static/files/ end up at build/site/files/ and are served
    by the same mechanism as the generated pages (/files/<name>.pdf)."""
    if not static_dir.is_dir():
        return 0
    shutil.copytree(static_dir, site_dir, dirs_exist_ok=True)
    return sum(1 for p in static_dir.rglob("*") if p.is_file())


def cmd_build(args):
    cfg = yaml.safe_load(Path(args.config).read_text())
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    site_dir = out_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)

    auth_mode = os.getenv("GDOC_AUTH_MODE", "oauth")
    creds = gdocs.get_creds(mode=auth_mode)

    # Cheap pre-flight: fetch each doc's revisionId + modifiedTime and compare
    # to the last build, so an unchanged set of docs can skip the full rebuild.
    meta_path = str(out_dir / "build_meta.json")
    doc_states = buildmeta.current_doc_states(creds, cfg["docs"])
    prev_meta = buildmeta.load_meta(meta_path)
    changed = buildmeta.changed_ids(prev_meta, doc_states)
    if args.skip_unchanged and prev_meta and not changed:
        print(
            f"[build] skip: no documents changed since "
            f"{prev_meta.get('built_at', '?')}; nothing to rebuild"
        )
        return
    if prev_meta:
        print(f"[build] {len(changed)} of {len(doc_states)} document(s) changed: "
              f"{', '.join(changed) if changed else '(none)'}")

    all_sections: list[Section] = []
    seen_ids: dict[str, str] = {}  # slug -> first-seen doc name (for collision warnings)

    def _disambiguate(sec: Section, doc_name: str) -> None:
        """If slug already used by another doc, prefix with a short doc tag."""
        if sec.id not in seen_ids:
            seen_ids[sec.id] = doc_name
            return
        # Collision. Build a short, stable prefix from the second doc's name.
        prefix_src = doc_name or sec.source_doc_id[:6]
        prefix = "".join(ch for ch in prefix_src.lower() if ch.isalnum())[:8] or "doc"
        new_id = f"{prefix}-{sec.id}"
        suffix_n = 2
        while new_id in seen_ids:
            new_id = f"{prefix}-{sec.id}-{suffix_n}"
            suffix_n += 1
        print(f"  ! slug collision: {sec.id!r} also in {seen_ids[sec.id]!r}; renaming to {new_id!r}")
        sec.id = new_id
        sec.url_path = f"/{new_id}/"
        seen_ids[new_id] = doc_name

    for d in cfg["docs"]:
        splitter = SPLITTERS[d["splitter"]]
        doc_name = d.get("name", "")
        print(f"[build] fetching {doc_name} ({d['id']}) via {d['splitter']}")
        for sec in splitter(creds, d["id"], doc_name):
            _disambiguate(sec, doc_name)
            print(f"  - {sec.id}: {sec.title}")
            all_sections.append(sec)
            time.sleep(0.2)

    allowed = cfg.get("categories", {}).get("allowed", ["about"])
    if "ignore" not in allowed:
        allowed = list(allowed) + ["ignore"]

    print(f"[build] categorizing {len(all_sections)} sections")
    categorize_sections(
        all_sections,
        allowed=allowed,
        cache_path=str(out_dir / "category.json"),
    )

    # Sections marked "ignore" get their HTML rendered (so existing URLs keep
    # working) but are excluded from the landing page, nav bar, and chatbot.
    active_sections = [s for s in all_sections if s.category != "ignore"]
    ignored_count = len(all_sections) - len(active_sections)
    if ignored_count:
        print(f"[build] {ignored_count} section(s) marked ignore: excluded from landing/nav/chat")

    base_href = args.base_href or cfg.get("site", {}).get("base_href", "/")
    print(f"[build] rendering pages -> {site_dir}  (base_href={base_href!r})")
    template = cfg.get("templates", {}).get("page", "templates/base.html")
    render_sections(
        all_sections, template_path=template, out_dir=str(site_dir),
        base_href=base_href,
        nav_sections=active_sections,
        nav_exclude_ids=[],
    )

    print(f"[build] rendering landing page ({len(active_sections)} active sections)")
    render_landing(
        active_sections,
        out_path=str(site_dir / "index.html"),
        base_href=base_href,
        exclude_ids=[],
    )

    static_dir = Path(args.static)
    copied = _copy_static(static_dir, site_dir)
    if copied:
        print(f"[build] copied {copied} static file(s) from {static_dir}/ -> {site_dir}/")

    if not args.no_schedule:
        try:
            print("[build] generating course schedule page...")
            generate_schedule_page(
                site_dir / "course-schedules" / "index.html",
                template_path=template,
                base_href=base_href,
                nav_sections=active_sections,
                nav_exclude_ids=[],
            )
        except Exception as exc:
            print(f"[build] WARNING: schedule generation failed: {exc}", flush=True)

    sitemap_base = generate_sitemap(
        active_sections,
        site_dir / "sitemap.xml",
        include_schedule=not args.no_schedule,
    )
    sitemap_count = 1 + len(active_sections) + (0 if args.no_schedule else 1)
    print(f"[build] wrote sitemap.xml ({sitemap_count} URLs, base={sitemap_base})")
    generate_robots_txt(site_dir / "robots.txt")
    print(f"[build] wrote robots.txt (Sitemap: {sitemap_base}/sitemap.xml)")

    dump_sections(active_sections, str(out_dir / "sections.json"))
    print(f"[build] wrote {len(active_sections)} sections to {out_dir}/sections.json")

    meta = buildmeta.write_meta(doc_states, meta_path)
    print(f"[build] recorded build metadata ({meta['built_at']}) -> {meta_path}")

    # Tell a running chat server to re-read sections.json so its answers
    # reflect the new content without a manual restart. Best-effort: a
    # missing token or an offline server is just a warning.
    if args.no_reload:
        print("[build] --no-reload set; not notifying the chat server")
    else:
        ok, msg = reload_chat(reload_url=args.reload_url)
        print(f"[build] {msg}")


def cmd_render_schedule(args):
    """Refresh the course schedule page from Banner without a full rebuild."""
    cfg = yaml.safe_load(Path(args.config).read_text())
    sections_path = args.sections or str(Path(args.out) / "sections.json")
    if not Path(sections_path).exists():
        sys.exit(f"sections.json not found at {sections_path}; run `cspdx build` first")

    sections = load_sections(sections_path)
    allowed = cfg.get("categories", {}).get("allowed", ["about"])
    if "ignore" not in allowed:
        allowed = list(allowed) + ["ignore"]
    categorize_sections(sections, allowed=allowed, cache_path=str(Path(args.out) / "category.json"))
    active_sections = [s for s in sections if s.category != "ignore"]

    base_href = args.base_href or cfg.get("site", {}).get("base_href", "/")
    template = cfg.get("templates", {}).get("page", "templates/base.html")
    site_dir = Path(args.out) / "site"
    out_path = site_dir / "course-schedules" / "index.html"

    generate_schedule_page(
        out_path,
        template_path=template,
        base_href=base_href,
        nav_sections=active_sections,
        nav_exclude_ids=[],
    )


def cmd_render_sitemap(args):
    """Regenerate sitemap.xml and robots.txt from an existing sections.json."""
    cfg = yaml.safe_load(Path(args.config).read_text())
    sections_path = args.sections or str(Path(args.out) / "sections.json")
    if not Path(sections_path).exists():
        sys.exit(f"sections.json not found at {sections_path}; run `cspdx build` first")

    sections = load_sections(sections_path)
    allowed = cfg.get("categories", {}).get("allowed", ["about"])
    if "ignore" not in allowed:
        allowed = list(allowed) + ["ignore"]
    categorize_sections(sections, allowed=allowed, cache_path=str(Path(args.out) / "category.json"))
    active_sections = [s for s in sections if s.category != "ignore"]

    site_dir = Path(args.out) / "site"
    sitemap_base = generate_sitemap(
        active_sections,
        site_dir / "sitemap.xml",
        include_schedule=not args.no_schedule,
    )
    sitemap_count = 1 + len(active_sections) + (0 if args.no_schedule else 1)
    print(f"[render-sitemap] wrote sitemap.xml ({sitemap_count} URLs, base={sitemap_base})")
    generate_robots_txt(site_dir / "robots.txt")
    print(f"[render-sitemap] wrote robots.txt (Sitemap: {sitemap_base}/sitemap.xml)")


def cmd_render_landing(args):
    """Re-render the landing page and all section pages from an existing sections.json.

    Useful after editing category.json without a full rebuild: re-applies
    category.json to re-categorize sections, rewrites index.html, and
    re-renders every section page so their nav bar dropdowns stay in sync.
    Note: sections that were marked 'ignore' at the last full build are absent
    from sections.json; un-ignoring them requires a full rebuild.
    """
    cfg = yaml.safe_load(Path(args.config).read_text())
    sections_path = args.sections or str(Path(args.out) / "sections.json")
    if not Path(sections_path).exists():
        sys.exit(f"sections.json not found at {sections_path}; run `cspdx build` first")

    sections = load_sections(sections_path)

    # Re-apply category.json so edits take effect without a full rebuild.
    allowed = cfg.get("categories", {}).get("allowed", ["about"])
    if "ignore" not in allowed:
        allowed = list(allowed) + ["ignore"]
    categorize_sections(sections, allowed=allowed, cache_path=str(Path(args.out) / "category.json"))

    active_sections = [s for s in sections if s.category != "ignore"]
    ignored_count = len(sections) - len(active_sections)
    if ignored_count:
        print(f"[render-landing] {ignored_count} section(s) re-categorized as ignore: excluded")

    base_href = args.base_href or cfg.get("site", {}).get("base_href", "/")
    site_dir = str(Path(args.out) / "site")
    out_path = str(Path(site_dir) / "index.html")

    template = cfg.get("templates", {}).get("page", "templates/base.html")
    print(f"[render-landing] re-rendering {len(active_sections)} section pages -> {site_dir}/")
    render_sections(
        active_sections, template_path=template, out_dir=site_dir,
        base_href=base_href,
        nav_sections=active_sections,
        nav_exclude_ids=[],
    )

    print(
        f"[render-landing] {len(active_sections)} sections from {sections_path}, "
        f"base_href={base_href!r} -> {out_path}"
    )
    render_landing(
        active_sections,
        out_path=out_path,
        base_href=base_href,
        exclude_ids=[],
    )


def cmd_render_sections(args):
    """Re-render all section pages from an existing sections.json. No Google fetch."""
    cfg = yaml.safe_load(Path(args.config).read_text())
    sections_path = args.sections or str(Path(args.out) / "sections.json")
    if not Path(sections_path).exists():
        sys.exit(f"sections.json not found at {sections_path}; run `cspdx build` first")

    sections = load_sections(sections_path)
    base_href = args.base_href or cfg.get("site", {}).get("base_href", "/")
    template = cfg.get("templates", {}).get("page", "templates/base.html")
    site_dir = str(Path(args.out) / "site")
    print(
        f"[render-sections] {len(sections)} sections from {sections_path}, "
        f"base_href={base_href!r} -> {site_dir}/"
    )
    render_sections(
        sections, template_path=template, out_dir=site_dir,
        base_href=base_href,
        nav_sections=sections,
        nav_exclude_ids=[],
    )
    print(f"[render-sections] done")


def cmd_serve(args):
    import uvicorn
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cspdx")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="Fetch docs and build site + index")
    pb.add_argument("--config", default="content.yaml")
    pb.add_argument("--out", default="build")
    pb.add_argument(
        "--static",
        default=os.getenv("STATIC_DIR", "static"),
        help="Directory whose contents are copied verbatim into build/site "
             "(e.g. static/files/*.pdf -> build/site/files/). Default 'static' "
             "or $STATIC_DIR.",
    )
    pb.add_argument(
        "--base-href",
        default=None,
        help='Value for the <base> tag (default "/" from content.yaml). '
             'Use "https://web.cs.pdx.edu/" if pages must behave as production.',
    )
    pb.add_argument(
        "--skip-unchanged",
        action="store_true",
        help="Skip the rebuild if no source document has changed (by revisionId "
             "or Drive modifiedTime) since the last recorded build "
             "(build/build_meta.json).",
    )
    pb.add_argument(
        "--no-schedule",
        action="store_true",
        help="Skip generating the course schedule page from Banner.",
    )

    pb.add_argument(
        "--no-reload",
        action="store_true",
        help="Don't POST to the chat server's /admin/reload after building.",
    )
    pb.add_argument(
        "--reload-url",
        default=None,
        help="Chat server reload endpoint (default $CSPDX_RELOAD_URL or "
             "http://127.0.0.1:8080/admin/reload).",
    )
    pb.set_defaults(func=cmd_build)

    psc = sub.add_parser(
        "render-schedule",
        help="Refresh the course schedule page from Banner. No Google Docs fetch.",
    )
    psc.add_argument("--config", default="content.yaml")
    psc.add_argument("--out", default="build")
    psc.add_argument(
        "--sections",
        default=None,
        help="Path to sections.json (default <out>/sections.json).",
    )
    psc.add_argument(
        "--base-href",
        default=None,
        help='Value for the <base> tag (default from content.yaml or "/").',
    )
    psc.set_defaults(func=cmd_render_schedule)

    psi = sub.add_parser(
        "render-sitemap",
        help="Regenerate sitemap.xml and robots.txt from an existing sections.json. No Google fetch.",
    )
    psi.add_argument("--config", default="content.yaml")
    psi.add_argument("--out", default="build")
    psi.add_argument(
        "--sections",
        default=None,
        help="Path to sections.json (default <out>/sections.json).",
    )
    psi.add_argument(
        "--no-schedule",
        action="store_true",
        help="Omit /course-schedules/ from the sitemap.",
    )
    psi.set_defaults(func=cmd_render_sitemap)

    pr = sub.add_parser(
        "render-landing",
        help="Re-render only the landing page from an existing sections.json. No Google fetch.",
    )
    pr.add_argument("--config", default="content.yaml")
    pr.add_argument("--out", default="build")
    pr.add_argument(
        "--sections",
        default=None,
        help="Path to sections.json (default <out>/sections.json).",
    )
    pr.add_argument(
        "--base-href",
        default=None,
        help='Value for the <base> tag (default from content.yaml or "/").',
    )
    pr.set_defaults(func=cmd_render_landing)

    prs = sub.add_parser(
        "render-sections",
        help="Re-render all section pages from an existing sections.json. No Google fetch.",
    )
    prs.add_argument("--config", default="content.yaml")
    prs.add_argument("--out", default="build")
    prs.add_argument(
        "--sections",
        default=None,
        help="Path to sections.json (default <out>/sections.json).",
    )
    prs.add_argument(
        "--base-href",
        default=None,
        help='Value for the <base> tag (default from content.yaml or "/").',
    )
    prs.set_defaults(func=cmd_render_sections)

    ps = sub.add_parser("serve", help="Serve site + /ask")
    ps.add_argument("--host", default="0.0.0.0")
    ps.add_argument("--port", type=int, default=8080)
    ps.add_argument("--reload", action="store_true")
    ps.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
