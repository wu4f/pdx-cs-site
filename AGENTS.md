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

# Re-render only the landing page from an existing sections.json (no Google API calls)
python -m cspdx.cli render-landing

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
Google Docs ──► sources/ (splitters) ──► []Section ──► categorize.py (Gemini)
                                                    ──► render/page.py   → build/site/<slug>/index.html
                                                    ──► render/landing.py → build/site/index.html
                                                    ──► _copy_static()   → build/site/{files,images,...}
                                                    ──► build/sections.json (chat index)
```

The live server loads `sections.json` into a Gemini context-cached `ChatBackend` and answers `/ask` queries. A `POST /admin/reload` (or a new `cspdx build`) replaces that cache.

### Core data model: `Section` (`cspdx/models.py`)

Every content unit is a `Section` dataclass: `id` (URL slug), `title`, `html`, `style`, `text` (plain), `category`, `url_path`, and source provenance fields. Every pipeline stage consumes and/or mutates `list[Section]`.

### Sources / splitters (`cspdx/sources/`)

Three strategies, selected per-doc in `content.yaml`:

| splitter | unit of split |
|---|---|
| `tabs` | one Google Doc tab → one Section; exports via Drive HTML endpoint, 1 s between requests |
| `headings` | one H1 block → one Section |
| `whole` | entire doc → one Section |

All paths call `cleaner.clean_exported_html()` which strips Google's wrapper markup and returns `(body_html, style_html, plain_text)`.

### Categorization (`cspdx/categorize.py`)

Calls Gemini once per section to assign a category slug from `content.yaml`→`categories.allowed`. Cache key is `<id>@<revisionId>` stored in `build/category_cache.json` — the only file under `build/` that is committed to git. Manual overrides live in `content.yaml`→`category_overrides`.

### Rendering (`cspdx/render/`)

- **`page.py`** — Jinja2-renders `templates/base.html` for each section → `build/site/<id>/index.html`
- **`landing.py`** — Self-contained inline HTML (no template), writes the categorized landing page → `build/site/index.html`

**Critical build-order invariant**: in `cmd_build`, `render_landing()` runs first, then `_copy_static()` overlays `static/` onto `build/site/`. Placing an `index.html` in `static/` would overwrite the freshly generated landing page and must be avoided.

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
| `/admin/rebuild` | POST | Runs `cspdx build` as a subprocess |
| `/admin/upload` | POST | PDF upload — 10 MB cap, `%PDF-` magic-byte check, path-traversal-proof filename |
| `/admin/reload` | POST | Re-reads `sections.json` into the chat backend |
| `/` | — | `StaticFiles` mount on `build/site/`; registered **last** so dynamic routes take priority |

All `/admin/*` endpoints require `ADMIN_TOKEN` validated via `hmac.compare_digest` (timing-safe). An empty token disables them. Token is loaded from `$ADMIN_TOKEN` (directly or via `.env`) in `cspdx/admin.py`.

Chat backend (`cspdx/chat/rag.py`) is lazy-loaded on the first `/ask` request. It uploads all section text to Gemini's context cache once, then reuses the cache handle cheaply across queries.

### Configuration (`content.yaml`)

Single declarative config that drives the entire pipeline:

- `docs[]` — which Google Doc IDs to fetch and which splitter to use
- `categories.allowed` — valid category slugs for Gemini to assign
- `category_overrides` — force a specific section to a category
- `landing_exclude` — section slugs hidden from the landing page (pages still generated and chatbot-reachable)
- `chat.deprioritize` — slugs tagged `priority="secondary"` in the RAG prompt so the model prefers canonical versions over older duplicates
- `templates.page` — path to the Jinja2 section template

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

### `<base href>` convention

Every generated page has `<base href="{{ base_href }}">`. Template asset references use `{{ base_href }}images/...` (base-relative). The chat UI has no `<base>` tag and uses root-relative `/images/...`. Do not mix the two conventions.

### Build outputs

```
build/
  site/               # fully generated; nginx serves this directory
  sections.json       # loaded by the chat backend at startup / reload
  build_meta.json     # revisionId + modifiedTime of each doc at last build (gitignored)
  category_cache.json # ← committed; LLM categorization keyed by id@revision
```
