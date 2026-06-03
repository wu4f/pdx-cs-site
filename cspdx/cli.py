"""Command-line interface: `cspdx build`, `cspdx render-landing`, `cspdx serve`."""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

import yaml

from .admin import reload_chat
from .models import Section, dump_sections, load_sections
from .sources import gdocs, tab_splitter, heading_splitter
from .categorize import categorize_sections
from .render.page import render_sections
from .render.landing import render_landing


SPLITTERS = {
    "tabs": tab_splitter.split,
    "headings": heading_splitter.split,
}


def cmd_build(args):
    cfg = yaml.safe_load(Path(args.config).read_text())
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    site_dir = out_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)

    auth_mode = os.getenv("GDOC_AUTH_MODE", "oauth")
    creds = gdocs.get_creds(mode=auth_mode)

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

    allowed = cfg.get("categories", {}).get("allowed", ["other"])
    if "other" not in allowed:
        allowed = list(allowed) + ["other"]

    print(f"[build] categorizing {len(all_sections)} sections")
    categorize_sections(
        all_sections,
        allowed=allowed,
        cache_path=str(out_dir / "category_cache.json"),
        model=cfg.get("chat", {}).get("model", "gemini-2.5-flash"),
    )

    # Apply manual category overrides from config.
    overrides = cfg.get("category_overrides", {}) or {}
    if overrides:
        applied = 0
        for sec in all_sections:
            if sec.id in overrides:
                target = overrides[sec.id]
                if target not in allowed:
                    print(f"  ! override target {target!r} not in allowed categories; skipping {sec.id}")
                    continue
                if sec.category != target:
                    print(f"  override: {sec.id} {sec.category!r} -> {target!r}")
                    sec.category = target
                    applied += 1
        print(f"[build] applied {applied} category overrides")

    base_href = args.base_href or cfg.get("site", {}).get("base_href", "/")
    print(f"[build] rendering pages -> {site_dir}  (base_href={base_href!r})")
    template = cfg.get("templates", {}).get("page", "templates/base.html")
    render_sections(
        all_sections, template_path=template, out_dir=str(site_dir),
        base_href=base_href,
    )

    exclude_ids = cfg.get("landing_exclude", []) or []
    print(f"[build] rendering landing page (excluding {len(exclude_ids)} sections)")
    render_landing(
        all_sections,
        out_path=str(site_dir / "index.html"),
        base_href=base_href,
        exclude_ids=exclude_ids,
    )

    dump_sections(all_sections, str(out_dir / "sections.json"))
    print(f"[build] wrote {len(all_sections)} sections to {out_dir}/sections.json")

    # Tell a running chat server to re-read sections.json so its answers
    # reflect the new content without a manual restart. Best-effort: a
    # missing token or an offline server is just a warning.
    if args.no_reload:
        print("[build] --no-reload set; not notifying the chat server")
    else:
        ok, msg = reload_chat(reload_url=args.reload_url)
        print(f"[build] {msg}")


def cmd_render_landing(args):
    """Re-render only the landing page from an existing sections.json.

    Useful after editing `landing_exclude` (or other landing-only config) in
    content.yaml: it picks up the new config and rewrites index.html without
    re-fetching the Google Docs or re-categorizing.
    """
    cfg = yaml.safe_load(Path(args.config).read_text())
    sections_path = args.sections or str(Path(args.out) / "sections.json")
    if not Path(sections_path).exists():
        sys.exit(f"sections.json not found at {sections_path}; run `cspdx build` first")

    sections = load_sections(sections_path)
    base_href = args.base_href or cfg.get("site", {}).get("base_href", "/")
    exclude_ids = cfg.get("landing_exclude", []) or []
    out_path = str(Path(args.out) / "site" / "index.html")
    print(
        f"[render-landing] {len(sections)} sections from {sections_path}, "
        f"excluding {len(exclude_ids)}, base_href={base_href!r} -> {out_path}"
    )
    render_landing(
        sections,
        out_path=out_path,
        base_href=base_href,
        exclude_ids=exclude_ids,
    )


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
        "--base-href",
        default=None,
        help='Value for the <base> tag (default "/" from content.yaml). '
             'Use "https://web.cs.pdx.edu/" if pages must behave as production.',
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

    pr = sub.add_parser(
        "render-landing",
        help="Re-render only the landing page from an existing sections.json "
             "(e.g. after editing landing_exclude). No Google fetch.",
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

    ps = sub.add_parser("serve", help="Serve site + /ask")
    ps.add_argument("--host", default="0.0.0.0")
    ps.add_argument("--port", type=int, default=8080)
    ps.add_argument("--reload", action="store_true")
    ps.set_defaults(func=cmd_serve)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
