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

Three strategies, selected per-doc in `content.yaml`:

| splitter | unit of split |
|---|---|
| `tabs` | one Google Doc tab → one Section; exports via Drive HTML endpoint with a 60 s timeout and up to 5 retries (exponential back-off) |
| `headings` | one H1 block → one Section |
| `whole` | entire doc → one Section |

All paths call `cleaner.clean_exported_html()` which strips Google's wrapper markup and returns `(body_html, style_html, plain_text)`.

### Categorization (`cspdx/categorize.py`)

Looks up each section's slug in `build/category.json` (slug → category) — the only file under `build/` that is committed to git. Five allowed categories: `about`, `undergraduate`, `graduate`, `resources`, and `ignore`. Slugs absent from the file default to `about` and are written back for manual review. No LLM calls; edit `build/category.json` directly to reclassify a section.

Sections with category `ignore` have their HTML pages rendered (so existing URLs keep working) but are excluded from the landing page, the nav bar on every section page, and `sections.json` (so the chatbot never sees them).

### Rendering (`cspdx/render/`)

- **`landing.py`** — Renders `templates/landing.html` → `build/site/index.html`. Exports `build_nav_groups(sections, exclude_ids)` (groups sections by category in `CATEGORY_ORDER` order, used by all pages), `meta_description(text)` (truncates plain text at last word boundary ≤160 chars), and `_site_base_url()` (reads `$SITE_BASE_URL`).
- **`page.py`** — Jinja2-renders `templates/base.html` for each section → `build/site/<id>/index.html`. Computes per-section `canonical_url` and `meta_description` from `section.text` (falls back to a generic sentence for empty text).

Both pages share a sticky two-row header: brand/CTA row + a horizontal category nav row. Each category entry has a text link (navigates to `/#category`) and a `▾` caret button that toggles a dropdown (JS, `position: fixed`) listing every page in that category. `position: fixed` is required because the nav row has `overflow-x: auto`, which would clip `position: absolute` dropdowns.

**Critical build-order invariant**: in `cmd_build`, `render_landing()` runs first, then `_copy_static()` overlays `static/` onto `build/site/`. `static/` must not contain an `index.html` — it would overwrite the generated landing page.

### Course schedule (`cspdx/schedule.py`)

Fetches the 8 most recent terms from Banner SSB (`app.banner.pdx.edu`) and renders a tabbed HTML table page via `templates/base.html` → `build/site/course-schedules/index.html`. It shares the same nav bar as all other section pages. The page is generated automatically at the end of `cspdx build` (skip with `--no-schedule`); it can also be refreshed independently without a full rebuild via `cspdx render-schedule`.

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
