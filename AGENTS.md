# Agent context for pdx-cs-site

AI coding agent bootstrap for this repository. Covers architecture, key invariants, and common commands.

## Common commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && $EDITOR .env   # fill GOOGLE_API_KEY and ADMIN_TOKEN

# Full build (fetches Google Docs, categorizes with Gemini, renders HTML, copies static/)
python -m cspdx.cli build

# Build skipping unchanged docs (compares revisionId / modifiedTime)
python -m cspdx.cli build --skip-unchanged

# Re-render the landing page and all section pages from an existing sections.json (no Google API calls)
python -m cspdx.cli render-landing

# Refresh only the course schedule page from Banner (no Google Docs fetch)
python -m cspdx.cli render-schedule

# Regenerate sitemap.xml and robots.txt from an existing sections.json (no Google API calls)
python -m cspdx.cli render-sitemap

# Run the dev server
python -m cspdx.cli serve               # http://localhost:8080
uvicorn server.app:app --host 0.0.0.0 --port 8080  # equivalent

# Restart the production server (PID in logs/server.pid)
kill $(cat logs/server.pid)
nohup .venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8080 >> logs/server.log 2>&1 &
echo $! > logs/server.pid
```

## Architecture

### Pipeline overview

```
Google Docs ──► sources/ (splitters) ──► []Section ──► categorize.py
                                                    ──► render/page.py    → build/site/<slug>/index.html
                                                    ──► render/landing.py → build/site/index.html
                                                    ──► _copy_static()    → build/site/{files,images,...}
                                                    ──► schedule.py       → build/site/course-schedules/index.html
                                                    ──► sitemap.py        → build/site/sitemap.xml
                                                    ──► sitemap.py        → build/site/robots.txt
                                                    ──► build/sections.json (chat index)
```

The live server loads `sections.json` into a Gemini context-cached `ChatBackend` and answers `/ask` queries. A `POST /admin/reload` (or a new `cspdx build`) replaces that cache.

### Core data model: `Section` (`cspdx/models.py`)

Every content unit is a `Section` dataclass: `id` (URL slug), `title`, `html`, `style`, `text` (plain), `category`, `url_path`, and source provenance fields. Every pipeline stage consumes and/or mutates `list[Section]`.

### Sources / splitters (`cspdx/sources/`)

Two strategies, selected per-doc in `content.yaml`:

| splitter | unit of split |
|---|---|
| `tabs` | one Google Doc tab → one Section; exports via Drive HTML endpoint, then `cleaner.clean_exported_html()` strips Google's wrapper markup and returns `(body_html, style_html, plain_text)`. 60 s timeout, up to 5 retries (exponential back-off) |
| `whole` | entire doc → one Section; renders the Docs API structural elements with a minimal in-house renderer in `whole_splitter.py` (paragraphs, text runs, headings, lists, tables; images/inline objects are skipped). Also fetches one HTML export per tab, but only to read list glyphs out of its stylesheet — see below |

Line breaks: the Docs API ends every paragraph with `\n` and represents a soft break (Shift+Enter) as `\v`. Both become `<br/>`, then `_strip_trailing_br()` drops the run of breaks at the end of each paragraph, heading, list item, and cell — otherwise every block ends with a dangling blank line on top of its own bottom margin.

Lists: a bullet paragraph carries `bullet.listId` + `bullet.nestingLevel`, and the run of consecutive bullet paragraphs is rendered together by `_render_list()` — a deeper level opens a sub-list *inside* the `<li>` above it (so that `<li>` stays open until its children are closed), a shallower one closes back out. Whether a level is `<ol>` or `<ul>` cannot be answered from the Docs API: it reports `glyphType: GLYPH_TYPE_UNSPECIFIED` with no `glyphSymbol` for most lists — in the graduate handbook a bulleted list and a decimal-numbered one come back as *byte-identical* `lists` entries, yet Google's own export renders one `<ul>` and the other `<ol>`. So `_list_glyphs()` fetches the HTML export (one request per tab, skipped entirely when the doc defines no lists) and `_glyphs_from_export()` reads the answer out of its stylesheet: each level appears as `ol.lst-<listId>-<level>` / `ul.lst-<listId>-<level>`, and ordered levels carry a `counter(…, <style>)` rule giving the list-style-type. Export class names embed the API's list ids with `.` written as `_`. An export failure is non-fatal — it degrades to the API's `glyphType`, which is right for the minority of lists that do report one, and to `<ul>` otherwise. `_Ctx.counts` tracks items emitted per `(listId, level)` so a numbered list interrupted by a paragraph resumes with `start=` instead of restarting at 1, while a nested level still restarts under each new parent item (Docs' own behaviour). Only list-style-types on a fixed whitelist are echoed into the page, so a malformed export can't inject CSS.

Internal cross-references: a link to another part of the same doc comes back as `link.heading = {id, tabId}` (or the legacy flat `link.headingId`), *not* as a URL — so a renderer that only looks at `link.url` drops the link and leaves the anchor text merely underlined, which is what the Google HTML export does too. `_resolve_internal_links()` runs once over the assembled document: it gives every heading an `id` (via `models.heading_anchor()`), maps each Docs `paragraphStyle.headingId` to that `id`, and rewrites the marker attributes the renderer left behind (`data-doc-heading-id` on headings, `data-doc-heading-ref` on links) into real fragment links. Three consequences worth knowing: (1) ids are assigned in the splitter, not in `toc.py`, because the TOC is only injected above a heading-count threshold and below it the links would have nothing to point at — `inject_toc()` keeps any id it finds, so the two agree; (2) fragments are written as `{url_path}#id`, since `<base href="/">` makes a bare `#id` resolve against the landing page (this is also why `cli.py`'s `_disambiguate()` rewrites in-page hrefs when a slug collision renames a section); (3) links to a heading that has since been deleted are unwrapped to plain text rather than left as a dead `<a>`. Bookmark links (`link.bookmark`) cannot be resolved at all: the API gives the bookmark's id on the link but never reveals where the bookmark sits in the content, so those stay plain text.

Table rendering in `whole_splitter.py` emits `<table class="doc-table">` with `<thead>`/`<tbody>`. Two things the Docs API makes non-obvious: (1) cells merged away by a neighbour's `rowSpan`/`columnSpan` still come back as empty placeholder cells at their grid position, so the renderer tracks covered `(row, col)` pairs and skips them — otherwise every merged row grows extra cells; (2) `tableRowStyle.tableHeader` is only set when the author pins a header row, which most docs never do, so the leading row is treated as the header by default. Only a *leading* run of pinned rows can become `<thead>`, since `<thead>` must precede `<tbody>`. The `doc-table` class exists so `templates/base.html` can relax the `white-space: nowrap` header rule that the short-headed schedule tables rely on.

### Categorization (`cspdx/categorize.py`)

Looks up each section's slug in `build/category.json` (slug → category) — the only file under `build/` that is committed to git. Five allowed categories: `about`, `undergraduate`, `graduate`, `resources`, and `ignore`. Slugs absent from the file default to `about` and are written back for manual review. No LLM calls; edit `build/category.json` directly to reclassify a section.

Sections with category `ignore` have their HTML pages rendered (so existing URLs keep working) but are excluded from the landing page, the nav bar on every section page, and `sections.json` (so the chatbot never sees them).

### Rendering (`cspdx/render/`)

- **`landing.py`** — Renders `templates/landing.html` → `build/site/index.html`. Exports `build_nav_groups(sections, exclude_ids)` (groups sections by category in `CATEGORY_ORDER` order, used by all pages), `meta_description(text)` (truncates plain text at last word boundary ≤160 chars), and `_site_base_url()` (reads `$SITE_BASE_URL`).
- **`page.py`** — Jinja2-renders `templates/base.html` for each section → `build/site/<id>/index.html`. Computes per-section `canonical_url` and `meta_description` from `section.text` (falls back to a generic sentence for empty text). Calls `inject_toc()` on each section's HTML before passing it to the template.
- **`toc.py`** — `inject_toc(body_html, threshold=7, url_path="")` injects a clickable table of contents when the page has ≥ 7 headings. Two modes: **single-section** (one H1, e.g. tab pages) counts h2/h3/h4 and injects after the first `</h1>`; **whole-doc** (two or more H1s) counts h1/h2/h3/h4 and injects before the first `<h1>` so the H1 sections themselves appear as top-level TOC entries, giving four levels of numbering (`4.3.2.1.`). `whole_splitter.py` renders Docs' HEADING_1 through HEADING_6, so an h5/h6 still renders as a heading and still gets an id — it just falls outside the contents list. Any heading (h1–h6) that lacks an `id` attribute gets one auto-generated from its text (slugified, deduplicated) by `models.heading_anchor()` — shared with `whole_splitter.py`, which assigns the same ids up front so its internal cross-references have targets; both must derive the same id from the same text or the two would disagree about a page's anchors. Links are prefixed with `url_path` so they resolve correctly under `<base href="/">`. Hierarchical numbering (`1.`, `1.2.`, `1.2.3.`) is computed in Python (not CSS counters — CSS `counter-reset` on a sibling `li` does not reset the counter for subsequent siblings in CSS 2.1 browser behavior); numbers are emitted as `<span class="toc-num">N.M.</span>` inline in each link. In **both modes**, the same number is also inserted into the body heading itself as `<span class="heading-num">N.M.</span>`, so the page matches its TOC. `_num_target()` decides where: Google's HTML export (so every tab page) wraps a heading's text in a `<span>` carrying its own `font-weight` — 400 for H3/H4, against the 600 `base.html` sets on the heading element — so a number placed *beside* that span renders bolder than the text it labels; the number therefore goes *inside* the span when the heading's first child is one, and directly in the heading otherwise (the whole splitter emits heading text with no wrapper). A blank Heading paragraph — a few source docs have one — is skipped without consuming a number, since it can be neither listed nor linked and a number spent on it would leave a visible gap in the sequence; it still counts towards `threshold`, so `faculty-and-staff-directory` (7 headings, one blank) keeps the TOC it already had. The `<h1 class="title">` that `whole_splitter.py` emits for a doc's TITLE style is excluded from the count entirely — it names the document, not a section, so numbering it would put every top-level section off by one. Heading text is read *before* the number is inserted, or TOC labels would repeat it. Numbering is applied to the render output only: `page.py` passes `inject_toc(s.html)` straight to the template without reassigning `s.html`, so `sections.json` stays clean and re-renders never double-number. Single-section pages use `.toc__list` and whole-doc pages use `.toc__list.toc__list--doc` (controls indentation only). All content headings carry `scroll-margin-top: 160px` to keep them clear of the sticky two-row header when navigating via anchor.

Both pages share a sticky two-row header: brand/CTA row + a horizontal category nav row. Each category entry has a text link (navigates to `/#category`) and a `▾` caret button that toggles a dropdown (JS, `position: fixed`) listing every page in that category. `position: fixed` is required because the nav row has `overflow-x: auto`, which would clip `position: absolute` dropdowns.

**Critical build-order invariant**: in `cmd_build`, `render_landing()` runs first, then `_copy_static()` overlays `static/` onto `build/site/`. `static/` must not contain an `index.html` — it would overwrite the generated landing page.

### Course schedule (`cspdx/schedule.py`)

Fetches the 8 most recent terms from Banner SSB (`app.banner.pdx.edu`) and renders a tabbed HTML table page via `templates/base.html` → `build/site/course-schedules/index.html`. Each term fetches both **CS** and **AI** subject codes by making a separate Banner SSB request per subject (each with its own `_establish_session` call), then merging the results. A single session cannot be reused across subjects — Banner carries subject state server-side and returns stale results if the session is reused. It shares the same nav bar as all other section pages. The page is generated automatically at the end of `cspdx build` (skip with `--no-schedule`); it can also be refreshed independently without a full rebuild via `cspdx render-schedule`.

### Sitemap and robots (`cspdx/sitemap.py`)

`generate_sitemap()` writes `build/site/sitemap.xml` listing the root `/`, every active section page, and `/course-schedules/` (omitted when `--no-schedule` is set). `generate_robots_txt()` writes `build/site/robots.txt` pointing at `<SITE_BASE_URL>/sitemap.xml`. Both are generated at the end of `cspdx build` and can be regenerated independently via `cspdx render-sitemap`.

### Static assets (`static/`)

Version-controlled source of non-generated assets. `cspdx build` copies everything here into `build/site/` via `shutil.copytree(dirs_exist_ok=True)`. Admin-uploaded PDFs go to `static/files/` AND are mirrored into `build/site/files/` immediately at upload time so they are served without a rebuild.

```
static/
  files/     # served at /files/<name>.pdf
  images/    # served at /images/<name>
```

### Server (`server/app.py`)

FastAPI app. Route summary:

| Route | Method | Notes |
|---|---|---|
| `/ask` | POST | JSON chatbot API |
| `/ask/` | GET | Chat UI (inline HTML string, no template; uses root-relative `/images/...` paths) |
| `/admin` | GET | Admin dashboard |
| `/admin/rebuild` | POST | Starts `cspdx build` in a background thread; redirects to admin page with live log |
| `/admin/rebuild/stream` | GET | SSE endpoint — streams build log lines as they arrive; no auth required |
| `/admin/clear-build` | POST | Resets build state to idle after a finished build; requires token |
| `/admin/upload` | POST | PDF upload — 10 MB cap, `%PDF-` magic-byte check, path-traversal-proof filename |
| `/admin/reload` | POST | Re-reads `sections.json` into the chat backend |
| `/` | — | `StaticFiles` mount on `build/site/`; registered **last** so dynamic routes take priority |

All `/admin/*` endpoints except `/admin/rebuild/stream` require `ADMIN_TOKEN` validated via `hmac.compare_digest` (timing-safe). An empty token disables them. Token is loaded from `$ADMIN_TOKEN` (directly or via `.env`) in `cspdx/admin.py`.

Background build state is kept in `_build_state` (guarded by `_build_lock`). The admin page opens an `EventSource` to `/admin/rebuild/stream` while a build is running, which polls `_build_state["log"]` every 0.5 s and pushes new lines as SSE events. When the build finishes the stream sends a named `done` event and the browser reloads. A "Clear log" button appears after a finished build; the token is stored in `sessionStorage` on rebuild form submit to avoid re-entry.

Chat backend (`cspdx/chat/rag.py`) is lazy-loaded on the first `/ask` request. It uploads all section text to Gemini's context cache once, then reuses the cache handle cheaply across queries.

### Configuration (`content.yaml`)

Single declarative config that drives the entire pipeline:

- `docs[]` — which Google Doc IDs to fetch and which splitter to use
- `categories.allowed` — valid category slugs (`about`, `undergraduate`, `graduate`, `resources`, `ignore`)
- `templates.page` — path to the Jinja2 section template
- `chat.model` — Gemini model for the chatbot

To suppress a page, set its slug to `ignore` in `build/category.json`.

### nginx split (production)

nginx serves `build/site/` directly for all static traffic. Only `/ask` and `/admin` are proxied to the Python app on port 8080. See `nginx.conf.example`. The `/admin` proxy location needs `client_max_body_size 11m` for PDF uploads.

### Environment variables

`cspdx/__init__.py` loads `.env` via python-dotenv with `override=False` (real shell env always wins).

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Gemini API key (categorization + chat) |
| `ADMIN_TOKEN` | Guards all `/admin/*` endpoints |
| `GDOC_AUTH_MODE` | `oauth` (dev, `credentials.json`/`token.json`) or `service_account` |
| `STATIC_DIR` | Asset source directory (default `static/`) |
| `SITE_DIR` | Generated site directory to serve (default `build/site`) |
| `CSPDX_RELOAD_URL` | Where `cspdx build` POSTs after finishing (default `http://127.0.0.1:8080/admin/reload`) |
| `GEMINI_MODEL` | Gemini model for categorization and chat (default `gemini-3.5-flash`; overrides `content.yaml` `chat.model`) |
| `SITE_BASE_URL` | Canonical base URL written into `sitemap.xml` `<loc>` tags and `robots.txt` (default `https://web.cs.pdx.edu`) |

### SEO (`templates/base.html`, `templates/landing.html`)

Every generated page includes:

- `<link rel="canonical" href="{{ canonical_url }}">` — absolute self-referencing URL built from `$SITE_BASE_URL + url_path`
- `<meta name="description">` — section pages use `meta_description(section.text)` (first ≤160 chars of plain text); landing and schedule pages use hand-written descriptions
- Open Graph tags: `og:type`, `og:site_name`, `og:title`, `og:description`, `og:url`
- JSON-LD `CollegeOrUniversity` structured data block (in `base.html`)

`canonical_url` and `meta_description` are passed as template variables by each renderer (`render/page.py`, `render/landing.py`, `schedule.py`). Both depend on `$SITE_BASE_URL`.

### `<base href>` convention

Every generated page has `<base href="{{ base_href }}">`. Template asset references use `{{ base_href }}images/...` (base-relative). The chat UI has no `<base>` tag and uses root-relative `/images/...`. Do not mix the two conventions.

### Build outputs

```
build/
  site/                          # fully generated; nginx serves this directory
    index.html                   # landing page
    <slug>/index.html            # one page per section
    course-schedules/index.html  # Banner course schedule (tabbed by term)
    sitemap.xml                  # all active page URLs (absolute, base = $SITE_BASE_URL)
    robots.txt                   # Allow: / + Sitemap: pointer
    files/                       # PDFs (copied from static/files/)
    images/                      # images (copied from static/images/)
  sections.json       # loaded by the chat backend at startup / reload
  build_meta.json     # revisionId + modifiedTime of each doc at last build (gitignored)
  category.json       # ← committed; manual slug → category map (edit to reclassify)
```
