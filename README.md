# pdx-cs-site

Unified successor to `gdoc2site` + `pdx-cs-ask`. One pipeline:

```
Google Docs ──► Sections ──► HTML pages + auto-categorized landing + chatbot
```

## What it does

1. Fetches two Google Docs (one with tabs, one with H1 separators).
2. Normalizes both into a common `Section` model.
3. Renders each section as `/<slug>/index.html`.
4. Auto-categorizes sections with an LLM and builds the landing page.
5. Serves everything from a single FastAPI app, including an `/ask`
   endpoint that answers questions using the full text of both docs
   (no Chroma, no scrape — Gemini context caching handles the heavy lift).

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Auth (one of):
#   - OAuth (dev):     put credentials.json in the repo root
#   - Service account: put service_account.json in the repo root
#                      and share both docs with the SA email (Viewer)

# Config: copy the template and fill it in. The .env is git-ignored and
# auto-loaded on startup (GOOGLE_API_KEY, ADMIN_TOKEN, GDOC_AUTH_MODE, ...).
cp .env.example .env
$EDITOR .env

python -m cspdx.cli build           # produces build/site/ + build/sections.json
python -m cspdx.cli serve           # http://localhost:8080
```

Plain `export GOOGLE_API_KEY=...` still works too — real environment variables
override `.env`, which is how Docker / Cloud Run supply config (see below).

The chat UI lives at `/ask/`. POST `{"question": "..."}` to `/ask` for JSON.

## Deploy to Cloud Run

```bash
gcloud builds submit --tag gcr.io/PROJECT/pdx-cs-site \
  --substitutions=_GOOGLE_API_KEY=$GOOGLE_API_KEY
gcloud run deploy pdx-cs-site \
  --image gcr.io/PROJECT/pdx-cs-site \
  --set-env-vars GOOGLE_API_KEY=$GOOGLE_API_KEY \
  --allow-unauthenticated
```

Share both docs with the Cloud Build / Cloud Run service-account email,
or supply `service_account.json` as a build secret.

## Admin: rebuild + file uploads

The `/admin` page (gated by `$ADMIN_TOKEN`) can rebuild the site and upload PDFs.
Uploaded files are validated (must be a real `%PDF-`, ≤ 10 MB, filename sanitized
against path traversal), stored in the top-level `files/` directory, and served at
`/files/<name>.pdf`. `files/` lives outside `build/`, so a rebuild never wipes it;
its contents are git-ignored (the directory is kept via `files/.gitkeep`). Override
the location with `$FILES_DIR`.

## Deploy behind nginx

1. `python -m cspdx.cli build`  → produces `build/site/`
2. Run `uvicorn server.app:app --port 8080` under systemd
3. Point nginx at `build/site/` and proxy `/ask` + `/admin` to `127.0.0.1:8080`;
   serve `/files/` straight from disk (see `nginx.conf.example`). The `/admin`
   proxy raises `client_max_body_size` so 10 MB uploads pass through.

## Rebuilding when docs change

The cheapest option is a cron (or Cloud Scheduler → Cloud Run job) that
runs `cspdx build` every N minutes. `gdocs.get_revision()` lets you
skip work when nothing has changed.
