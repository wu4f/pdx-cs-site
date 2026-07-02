"""FastAPI app: serves the generated site + a /ask endpoint + a tiny chat UI."""
from __future__ import annotations
import asyncio
import hmac
import html as _html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


from cspdx.chat.rag import ChatBackend
from cspdx.admin import load_admin_token
from cspdx import buildmeta


SITE_DIR = os.getenv("SITE_DIR", "build/site")
SECTIONS_PATH = os.getenv("SECTIONS_PATH", "build/sections.json")
CONTENT_YAML = os.getenv("CONTENT_YAML", "content.yaml")
BUILD_META_PATH = os.getenv("BUILD_META_PATH", "build/build_meta.json")
# Repo root (parent of server/), so the rebuild subprocess runs from the same
# place a manual `cspdx build` would (finds content.yaml, build/, token.json).
REPO_ROOT = Path(__file__).resolve().parent.parent
# Token guarding /admin/* endpoints: $ADMIN_TOKEN (set directly or via .env).
ADMIN_TOKEN = load_admin_token()
# Version-controlled static assets. `cspdx build` copies STATIC_DIR/* into
# build/site/, so build/site is self-contained (served by the "/" mount + nginx).
STATIC_DIR = Path(os.getenv("STATIC_DIR", str(REPO_ROOT / "static"))).resolve()
# Admin-uploaded PDFs land here (the version-controlled source) and are also
# mirrored into build/site/files/ on upload so they're served immediately.
FILES_DIR = (STATIC_DIR / "files").resolve()
FILES_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

app = FastAPI(title="pdx-cs-site")

# Lazy: don't load sections at import time so the server can start before
# `cspdx build` has been run, and the venv can import server.app for tests.
_chat: ChatBackend | None = None

# Background build state — written by the build thread, read by the admin UI.
_build_lock = threading.Lock()
_build_state: dict = {"status": "idle", "log": "", "started_at": None, "finished_at": None}


def _load_chat_config() -> dict:
    """Read chat-related options from content.yaml (model, deprioritize)."""
    p = Path(CONTENT_YAML)
    if not p.exists():
        return {}
    try:
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("chat", {}) or {}
    except Exception as e:
        print(f"[server] could not read {CONTENT_YAML}: {e}")
        return {}


def get_chat() -> ChatBackend:
    global _chat
    if _chat is None:
        if not Path(SECTIONS_PATH).exists():
            raise HTTPException(
                503,
                f"sections.json not found at {SECTIONS_PATH}; run `cspdx build` first",
            )
        chat_cfg = _load_chat_config()
        _chat = ChatBackend(
            sections_path=SECTIONS_PATH,
            model=os.getenv("GEMINI_MODEL") or chat_cfg.get("model", "gemini-3.5-flash"),
            deprioritize=tuple(chat_cfg.get("deprioritize", []) or []),
        )
    return _chat


class AskBody(BaseModel):
    question: str


@app.post("/ask")
def ask(body: AskBody):
    if not body.question.strip():
        raise HTTPException(400, "empty question")
    return {"answer": get_chat().answer(body.question)}


@app.post("/admin/reload")
def admin_reload(token: str):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(403, "forbidden")
    global _chat
    _chat = None  # forces re-load on next /ask
    return {"status": "ok"}


def _format_ts(iso: str) -> str:
    """Render an ISO/RFC-3339 timestamp as 'YYYY-MM-DD HH:MM UTC' (best-effort)."""
    if not iso:
        return "—"  # em dash
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso


def _load_docs_cfg() -> list[dict]:
    """Read the `docs:` list from content.yaml (id/splitter/name per entry)."""
    p = Path(CONTENT_YAML)
    if not p.exists():
        return []
    try:
        return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("docs", []) or []
    except Exception as e:
        print(f"[server] could not read docs from {CONTENT_YAML}: {e}")
        return []


def _admin_status() -> str:
    """HTML block: last-build time + links to each configured Google Doc."""
    prev_meta = buildmeta.load_meta(BUILD_META_PATH)
    built_at = _format_ts(prev_meta.get("built_at", "")) if prev_meta else "never"

    docs_cfg = _load_docs_cfg()
    seen: dict[str, dict] = {}
    for d in docs_cfg:
        seen.setdefault(d["id"], d)
    unique = list(seen.values())

    if unique:
        items = ""
        for d in unique:
            doc_id = _html.escape(d["id"])
            name = _html.escape(d.get("name", d["id"]))
            url = f"https://docs.google.com/document/d/{doc_id}/edit"
            items += f'<li><a href="{url}" target="_blank" rel="noopener">{name}</a></li>\n'
        links = f"<ul>\n{items}</ul>"
    else:
        links = '<p class="hint">No documents configured.</p>'

    return f"""<section class="status">
  <p><strong>Last build:</strong> {built_at}</p>
  <p><strong>Source documents</strong></p>
  {links}
</section>"""


def _safe_pdf_name(original: str) -> str:
    """Derive a safe, path-traversal-proof .pdf filename from an uploaded name.

    Keeps the admin's original name (case + spaces) but strips any directory
    components and disallowed characters, neutralizes '..' / leading dots, and
    forces a .pdf extension. The result contains no '/', '\\', or '..', so it
    cannot escape FILES_DIR (a second containment check in the handler confirms).
    """
    base = os.path.basename((original or "").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9 ._()\-]", "", base)
    base = base.replace("..", "").lstrip(". ")
    base = re.sub(r"\s+", " ", base).strip()
    stem = os.path.splitext(base)[0] or "upload"
    return f"{stem}.pdf"


def _files_section() -> str:
    """HTML list of the PDFs currently published under /files/."""
    try:
        pdfs = sorted(p for p in FILES_DIR.glob("*.pdf") if p.is_file())
    except Exception:
        pdfs = []
    if not pdfs:
        return '<p class="hint">No files uploaded yet.</p>'
    rows = ""
    for p in pdfs:
        href = "/files/" + urllib.parse.quote(p.name)
        kb = max(1, round(p.stat().st_size / 1024))
        rows += (
            f'<tr><td><a href="{_html.escape(href)}">{_html.escape(p.name)}</a></td>'
            f"<td>{kb:,} KB</td></tr>\n"
        )
    return (
        '<table><thead><tr><th>File</th><th>Size</th></tr></thead>\n'
        f"<tbody>\n{rows}</tbody></table>"
    )


_CLEAR_FORM = (
    '<form method="post" action="/admin/clear-build" style="margin-top:8px">'
    '<input type="hidden" name="token" id="clear-token">'
    '<button class="btn-sm" type="submit">Clear log</button>'
    '</form>'
    '<script>'
    '(function(){var t=sessionStorage.getItem("admin-token");'
    'if(t)document.getElementById("clear-token").value=t;})();'
    '</script>'
)

_SSE_JS = (
    '<script>'
    'var _lp=document.getElementById("live-log");'
    'var _es=new EventSource("/admin/rebuild/stream");'
    '_es.onmessage=function(e){_lp.textContent+=e.data+"\\n";_lp.scrollTop=_lp.scrollHeight;};'
    '_es.addEventListener("done",function(){_es.close();location.reload();});'
    '_es.onerror=function(){_es.close();};'
    '</script>'
)


def _build_status_block() -> tuple[str, bool]:
    """Return (HTML block, needs_meta_refresh) describing the current background build."""
    with _build_lock:
        state = dict(_build_state)
    status = state["status"]
    if status == "idle":
        return "", False
    started = _format_ts(state.get("started_at") or "")
    finished = _format_ts(state.get("finished_at") or "")
    log = _html.escape((state.get("log") or "").strip())
    if status == "running":
        # Meta-refresh is the reliable fallback; SSE updates the log live and
        # reloads immediately when done (bypassing the 5 s wait when it works).
        return (
            f'<p class="banner ok">&#9654; Rebuild in progress since {started}.</p>'
            '<pre class="log" id="live-log"></pre>'
            + _SSE_JS
        ), True
    if status == "ok":
        skipped = "[build] skip:" in (state.get("log") or "")
        msg = ("No documents changed — nothing to rebuild." if skipped
               else f"Rebuild succeeded (finished {finished}).")
        log_block = f'<pre class="log">{log}</pre>' if log else ""
        return f'<p class="banner ok">{msg}</p>{log_block}{_CLEAR_FORM}', False
    # error / timeout
    label = "timed out" if status == "timeout" else f"failed (finished {finished})"
    log_block = f'<pre class="log">{log}</pre>' if log else ""
    return f'<p class="banner err">Rebuild {label}.</p>{log_block}{_CLEAR_FORM}', False


def _admin_page(banner: str = "", status: str = "") -> str:
    """Render the /admin form, optionally preceded by a result banner and a
    status block (last build + per-document timestamps)."""
    files = _files_section()
    build_block, is_running = _build_status_block()
    refresh_tag = '<meta http-equiv="refresh" content="5; url=/admin">' if is_running else ""
    # Prepend the background-build block before any caller-supplied banner.
    banner = build_block + banner
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Admin · Rebuild site</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
{refresh_tag}
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
         max-width: 760px; margin: 6vh auto; padding: 0 20px; color: #1e2230; }}
  h1 {{ font-size: 1.4rem; }}
  form {{ display: flex; gap: 10px 16px; align-items: flex-end; flex-wrap: wrap;
          margin: 24px 0; }}
  label {{ display: block; font-weight: 600; margin-bottom: 6px; }}
  input[type=password] {{ padding: 10px 12px; font-size: 1rem; min-width: 280px;
          border: 1px solid #aab; border-radius: 6px; }}
  input[type=file] {{ padding: 8px 0; font-size: 0.95rem; }}
  h2 {{ font-size: 1.15rem; margin-top: 36px;
        border-top: 1px solid #e6e8ee; padding-top: 24px; }}
  a {{ color: #1e6b3a; }}
  button {{ padding: 10px 18px; font-size: 1rem; font-weight: 600; cursor: pointer;
          background: #1e6b3a; color: #fff; border: 0; border-radius: 6px; }}
  button:hover {{ background: #14512b; }}
  .check {{ font-weight: 400; display: flex; align-items: center; gap: 8px;
          margin-bottom: 8px; }}
  .check label {{ font-weight: 400; margin: 0; }}
  .banner {{ padding: 12px 16px; border-radius: 6px; font-weight: 600; }}
  .banner.ok {{ background: #e6f4ea; color: #0d652d; border: 1px solid #9ad3ab; }}
  .banner.err {{ background: #fce8e6; color: #a50e0e; border: 1px solid #f2b3ad; }}
  .log {{ background: #1e2230; color: #e8e8e8; padding: 14px; border-radius: 6px;
          overflow-x: auto; white-space: pre-wrap; font-size: 0.85rem;
          max-height: 50vh; }}
  .hint {{ color: #555; font-size: 0.9rem; }}
  .status {{ margin: 20px 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #e6e8ee; }}
  th {{ color: #555; font-weight: 600; }}
  .btn-sm {{ padding: 6px 12px; font-size: 0.85rem; font-weight: 600; cursor: pointer;
             background: #6b7280; color: #fff; border: 0; border-radius: 6px; }}
  .btn-sm:hover {{ background: #4b5563; }}
</style>
</head>
<body>
<h1>Rebuild the site</h1>
<p class="hint">Re-fetches the Google Docs, regenerates every page, and reloads
the chat index — the same as running <code>cspdx build</code>. By default the
rebuild is skipped if no source document has changed. This can take a minute;
leave the tab open until it finishes.</p>
{status}
{banner}
<form id="rebuild-form" method="post" action="/admin/rebuild">
  <div>
    <label for="token">Admin token</label>
    <input type="password" id="token" name="token"
           autocomplete="current-password" required autofocus>
  </div>
  <button type="submit">Rebuild now</button>
  <div class="check">
    <input type="checkbox" id="force" name="force" value="1">
    <label for="force">Force rebuild even if unchanged</label>
  </div>
</form>

<h2>Upload a PDF</h2>
<p class="hint">Stores a PDF (max 10&nbsp;MB) so it's served at
<code>/files/&lt;name&gt;.pdf</code>. The original filename is kept, sanitized;
re-uploading the same name overwrites it.</p>
<form method="post" action="/admin/upload" enctype="multipart/form-data">
  <div>
    <label for="utoken">Admin token</label>
    <input type="password" id="utoken" name="token"
           autocomplete="current-password" required>
  </div>
  <div>
    <label for="pdf">PDF file</label>
    <input type="file" id="pdf" name="pdf" accept="application/pdf,.pdf" required>
  </div>
  <button type="submit">Upload</button>
</form>

<h2>Published files</h2>
{files}
<script>
  document.getElementById('rebuild-form').addEventListener('submit', function() {{
    var t = document.getElementById('token').value;
    if (t) sessionStorage.setItem('admin-token', t);
  }});
</script>
</body>
</html>"""


_BUILD_TIMEOUT = 600  # seconds


@app.get("/admin", response_class=HTMLResponse)
def admin_ui():
    return _admin_page(status=_admin_status())


def _build_worker(force: bool) -> None:
    """Run `cspdx build` in a background thread, streaming output line-by-line."""
    global _chat
    cmd = [sys.executable, "-u", "-m", "cspdx.cli", "build"]
    if not force:
        cmd.append("--skip-unchanged")
    with _build_lock:
        _build_state.update({"status": "running", "log": "",
                             "started_at": datetime.now(timezone.utc).isoformat(),
                             "finished_at": None})
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        timed_out = threading.Event()

        def _kill():
            timed_out.set()
            proc.kill()

        timer = threading.Timer(_BUILD_TIMEOUT, _kill)
        timer.start()
        try:
            for line in proc.stdout:
                with _build_lock:
                    _build_state["log"] = (_build_state["log"] + line)[-6000:]
            proc.wait()
        finally:
            timer.cancel()

        if timed_out.is_set():
            with _build_lock:
                _build_state["status"] = "timeout"
                _build_state["log"] = (_build_state["log"]
                                       + f"\nBuild exceeded {_BUILD_TIMEOUT} s and was killed.")[-6000:]
                _build_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        else:
            ok = proc.returncode == 0
            with _build_lock:
                _build_state["status"] = "ok" if ok else "error"
                _build_state["finished_at"] = datetime.now(timezone.utc).isoformat()
            if ok:
                _chat = None
    except Exception as e:
        with _build_lock:
            _build_state["status"] = "error"
            _build_state["log"] = (_build_state["log"] + f"\n{e}")[-6000:]
            _build_state["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/admin/rebuild", response_class=HTMLResponse)
async def admin_rebuild(request: Request):
    qs = urllib.parse.parse_qs((await request.body()).decode("utf-8"))
    token = qs.get("token", [""])[0]
    force = bool(qs.get("force"))
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return HTMLResponse(
            _admin_page('<p class="banner err">Invalid admin token.</p>'),
            status_code=403,
        )
    with _build_lock:
        already_running = _build_state["status"] == "running"
    if already_running:
        return HTMLResponse(
            _admin_page('<p class="banner ok">A rebuild is already in progress — '
                        'refresh this page to see its status.</p>',
                        status=_admin_status()),
        )
    # Pre-set state to "running" before starting the thread so the response
    # page renders the live-log block (with SSE JS) without a race.
    with _build_lock:
        _build_state.update({"status": "running", "log": "",
                             "started_at": datetime.now(timezone.utc).isoformat(),
                             "finished_at": None})
    threading.Thread(target=_build_worker, args=(force,), daemon=True).start()
    return HTMLResponse(_admin_page(status=_admin_status()))


@app.get("/admin/rebuild/stream")
async def rebuild_stream():
    """SSE endpoint: streams build log lines as they arrive. No auth needed (output is not sensitive)."""
    async def generate():
        last_len = 0
        while True:
            with _build_lock:
                log = _build_state.get("log", "")
                status = _build_state["status"]
            new_text = log[last_len:]
            if new_text:
                for line in new_text.split("\n"):
                    if line:
                        yield f"data: {line}\n\n"
                last_len = len(log)
            if status != "running":
                yield "event: done\ndata: done\n\n"
                return
            await asyncio.sleep(0.5)
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/admin/clear-build", response_class=HTMLResponse)
async def admin_clear_build(request: Request):
    qs = urllib.parse.parse_qs((await request.body()).decode("utf-8"))
    token = qs.get("token", [""])[0]
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return HTMLResponse(
            _admin_page('<p class="banner err">Invalid admin token.</p>', status=_admin_status()),
            status_code=403,
        )
    with _build_lock:
        if _build_state["status"] == "running":
            return HTMLResponse(_admin_page(
                '<p class="banner err">Cannot clear — build is still running.</p>',
                status=_admin_status()))
        _build_state.update({"status": "idle", "log": "", "started_at": None, "finished_at": None})
    return HTMLResponse(_admin_page("", status=_admin_status()))


def _upload_error(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(
        _admin_page(f'<p class="banner err">{_html.escape(message)}</p>',
                    status=_admin_status()),
        status_code=status_code,
    )


@app.post("/admin/upload", response_class=HTMLResponse)
async def admin_upload(request: Request):
    # Cheap early guard: reject an oversized body before parsing it (protects the
    # direct-uvicorn path; nginx also caps via client_max_body_size in prod).
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > MAX_UPLOAD_BYTES + 1024 * 1024:
        return _upload_error("File too large (max 10 MB).", 413)

    form = await request.form()
    token = form.get("token", "")
    upload = form.get("pdf")

    # Token: timing-safe compare; same message whether or not one is configured.
    if not ADMIN_TOKEN or not isinstance(token, str) or not hmac.compare_digest(
        token, ADMIN_TOKEN
    ):
        return _upload_error("Invalid admin token.", 403)

    filename = getattr(upload, "filename", None)
    if not filename or not hasattr(upload, "read"):
        return _upload_error("No file was uploaded.", 400)
    if not filename.lower().endswith(".pdf"):
        return _upload_error("Only .pdf files are accepted.", 400)

    # Read with a hard cap so a lying Content-Length can't blow up memory/disk.
    data = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            return _upload_error("File too large (max 10 MB).", 413)

    if data[:5] != b"%PDF-":
        return _upload_error("That file is not a valid PDF.", 400)

    safe_name = _safe_pdf_name(filename)
    target = (FILES_DIR / safe_name).resolve()
    # Defense in depth: the sanitized name can't contain a separator, but confirm
    # the resolved path still sits directly inside FILES_DIR before writing.
    if target.parent != FILES_DIR:
        return _upload_error("Rejected: unsafe file path.", 400)

    # Atomic write so a partial upload never leaves a corrupt file being served.
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(FILES_DIR), suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, str(target))
        tmp_path = None
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return _upload_error(f"Could not save the file: {e}", 500)

    # Mirror into build/site/files/ so it's served immediately (the next rebuild
    # re-copies static/ -> build/site/ anyway). Best-effort: if it fails, the
    # file is still saved to static/files/ and will appear after a rebuild.
    mirror_note = ""
    try:
        site_files = Path(SITE_DIR).resolve() / "files"
        site_files.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(target), str(site_files / safe_name))
    except Exception as e:
        mirror_note = (f'<p class="hint">Saved, but could not publish to the live '
                       f'site ({_html.escape(str(e))}); it will appear after the '
                       f"next rebuild.</p>")

    href = "/files/" + urllib.parse.quote(safe_name)
    banner = (
        f'<p class="banner ok">Uploaded — now served at '
        f'<a href="{_html.escape(href)}">{_html.escape(href)}</a> '
        f"({len(data) // 1024:,} KB).</p>{mirror_note}"
    )
    return HTMLResponse(_admin_page(banner, status=_admin_status()), status_code=200)


CHAT_UI = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ask CS · Portland State University</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="https://web.cs.pdx.edu/favicon.ico" type="image/vnd.microsoft.icon">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    /* WCAG 2.1 AA: white on --psu-green is 5.6:1, on --psu-green-dark is 8.3:1 */
    --psu-green: #547119;
    --psu-green-dark: #3f5512;
    --psu-green-light: #eaf2d4;
    --ink: #1e2230;
    --ink-soft: #4a5060;
    --ink-muted: #6b7280;
    --bg: #f7f8fb;
    --card: #ffffff;
    --border: #e6e8ee;
    --user-bg: #2d5fa3;       /* PSU blue-ish for user */
    --user-bg-soft: #e7eff9;
    --user-ink: #ffffff;
    --bot-bg: #ffffff;
    --bot-ink: #1e2230;
    --shadow-sm: 0 1px 2px rgba(20,25,40,0.05);
    --shadow-md: 0 4px 12px rgba(20,25,40,0.06), 0 1px 3px rgba(20,25,40,0.04);
  }

  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    color: var(--ink);
    background: var(--bg);
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    display: flex; flex-direction: column;
  }
  a { color: var(--psu-green-dark); text-decoration: none; }
  a:hover { text-decoration: underline; }

  /* Screen-reader-only utility */
  .visually-hidden {
    position: absolute !important;
    width: 1px; height: 1px;
    padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0,0,0,0);
    white-space: nowrap; border: 0;
  }

  /* Visible focus ring (WCAG 2.4.7) */
  :focus-visible {
    outline: 3px solid #2a6cff;
    outline-offset: 2px;
    border-radius: 4px;
  }
  button:focus-visible, a:focus-visible {
    outline: 3px solid #2a6cff;
    outline-offset: 2px;
  }

  /* Skip link (WCAG 2.4.1) */
  .skip-link {
    position: absolute; left: -10000px; top: 8px;
    background: var(--ink); color: #fff !important;
    padding: 10px 16px; border-radius: 6px;
    font-weight: 600; z-index: 1000;
  }
  .skip-link:focus { left: 16px; outline: 3px solid #ffd54f; text-decoration: none; }

  /* Reduced-motion preference (WCAG 2.3.3 / 2.2.2) */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.001ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.001ms !important;
    }
    .top-cta:hover, .suggestion:hover, .composer button:hover, .back-link:hover {
      transform: none !important;
    }
    .typing span { animation: none !important; }
  }

  /* ---------- Top bar ---------- */
  .topbar {
    background: #fff;
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 50;
  }
  .topbar-inner {
    max-width: 1240px; margin: 0 auto;
    padding: 14px 28px;
    display: flex; align-items: center; gap: 20px;
  }
  .brand {
    display: flex; align-items: center; gap: 18px;
    font-weight: 700; color: var(--ink);
    flex-shrink: 0;
  }
  .brand img { height: 76px; width: auto; display: block; }
  .topbar-spacer { flex: 1; }
  .back-link {
    font-size: 14px; font-weight: 600; color: var(--ink-soft);
    padding: 9px 16px; border-radius: 999px;
    border: 1px solid var(--border);
    display: inline-flex; align-items: center; gap: 6px;
    white-space: nowrap;
    transition: all .15s ease;
  }
  .back-link:hover {
    background: var(--bg); text-decoration: none;
    color: var(--psu-green-dark); border-color: var(--psu-green);
  }

  /* ---------- Chat shell ---------- */
  main {
    flex: 1;
    max-width: 900px; width: 100%;
    margin: 0 auto;
    padding: 24px 16px 0;
    display: flex; flex-direction: column;
    min-height: 0;  /* allow inner scroll */
  }
  .intro {
    background: linear-gradient(180deg, #fff 0%, #fafbfd 100%);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px 24px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-sm);
  }
  .intro h1 {
    font-size: 1.4rem; margin: 0 0 6px; letter-spacing: -0.01em;
    display: flex; align-items: center; gap: 10px;
  }
  .intro .badge {
    background: var(--psu-green-light); color: var(--psu-green-dark);
    font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px;
    letter-spacing: .06em; text-transform: uppercase;
  }
  .intro p { margin: 0; color: var(--ink-soft); font-size: 14px; }
  .suggestions {
    display: flex; flex-wrap: wrap; gap: 8px;
    margin-top: 14px;
  }
  .suggestion {
    background: #fff; border: 1px solid var(--border);
    padding: 7px 13px; border-radius: 999px;
    font-size: 13px; color: var(--ink-soft);
    cursor: pointer; transition: all .15s ease;
    font-family: inherit;
  }
  .suggestion:hover { border-color: var(--psu-green); color: var(--psu-green-dark); }

  /* ---------- Message log ---------- */
  #log {
    flex: 1;
    overflow-y: auto;
    padding: 8px 4px 16px;
    display: flex; flex-direction: column; gap: 14px;
    min-height: 200px;
  }
  .row { display: flex; align-items: flex-end; gap: 10px; max-width: 100%; }
  .row.user { justify-content: flex-end; }
  .row.bot  { justify-content: flex-start; }

  .avatar {
    width: 32px; height: 32px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; flex-shrink: 0;
    background: var(--psu-green-light); color: var(--psu-green-dark);
    border: 1px solid var(--border);
    margin-bottom: 4px;
  }
  .row.user .avatar { background: var(--user-bg-soft); color: var(--user-bg); }

  .bubble {
    max-width: min(78%, 680px);
    padding: 12px 16px;
    border-radius: 16px;
    font-size: 15px;
    line-height: 1.55;
    box-shadow: var(--shadow-sm);
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }
  .row.user .bubble {
    background: var(--user-bg);
    color: var(--user-ink);
    border-bottom-right-radius: 4px;
    font-weight: 500;
  }
  .row.user .bubble a { color: #cfe1ff; text-decoration: underline; }
  .row.bot .bubble {
    background: var(--bot-bg);
    color: var(--bot-ink);
    border: 1px solid var(--border);
    border-bottom-left-radius: 4px;
  }

  /* Markdown inside the bot bubble */
  .bubble p { margin: 0 0 .7em; }
  .bubble p:last-child { margin-bottom: 0; }
  .bubble ul, .bubble ol { margin: .3em 0 .7em; padding-left: 1.4em; }
  .bubble li { margin-bottom: .25em; }
  .bubble strong { font-weight: 600; }
  .bubble em { font-style: italic; }
  .bubble code {
    background: #f3f4f7; padding: 2px 6px; border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 92%;
  }
  .bubble a {
    color: var(--psu-green-dark); font-weight: 500;
    text-decoration: underline; text-underline-offset: 2px;
  }
  .bubble a:hover { color: var(--psu-green); }
  .bubble hr { border: 0; border-top: 1px solid var(--border); margin: 12px 0; }

  /* Sources sub-section inside bot bubble */
  .bubble .sources-label {
    display: block;
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .07em;
    color: var(--ink-muted);
    margin-top: 14px; margin-bottom: 6px;
    padding-top: 10px; border-top: 1px dashed var(--border);
  }
  .bubble .sources-list {
    margin: 0; padding-left: 1.2em;
    font-size: 14px;
  }
  .bubble .sources-list li { margin-bottom: 2px; }

  .meta {
    font-size: 11px; color: var(--ink-muted);
    margin: 4px 6px 0;
  }
  .row.user .meta { text-align: right; }

  /* Loading dots */
  .typing { display: inline-flex; align-items: center; gap: 4px; padding: 4px 0; }
  .typing span {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--ink-muted);
    animation: blink 1.2s infinite both;
  }
  .typing span:nth-child(2) { animation-delay: .2s; }
  .typing span:nth-child(3) { animation-delay: .4s; }
  @keyframes blink {
    0%, 60%, 100% { opacity: .25; transform: translateY(0); }
    30% { opacity: 1; transform: translateY(-3px); }
  }

  /* Empty state */
  .empty {
    text-align: center; color: var(--ink-muted);
    padding: 40px 20px; font-size: 14px;
  }

  /* ---------- Composer ---------- */
  .composer-wrap {
    position: sticky; bottom: 0;
    background: linear-gradient(180deg, rgba(247,248,251,0) 0%, var(--bg) 24%);
    padding: 16px 16px 20px;
  }
  .composer {
    max-width: 900px; margin: 0 auto;
    display: flex; gap: 10px; align-items: center;
    background: #fff;
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 8px 8px 8px 16px;
    box-shadow: var(--shadow-md);
    transition: border-color .15s ease, box-shadow .15s ease;
  }
  .composer:focus-within {
    border-color: var(--psu-green);
    box-shadow: 0 0 0 4px rgba(109,141,36,.12), var(--shadow-md);
  }
  .composer input {
    flex: 1; border: 0; outline: 0;
    font: inherit; font-size: 16px;
    padding: 10px 0;
    background: transparent; color: var(--ink);
  }
  .composer input::placeholder { color: var(--ink-muted); }
  .composer button {
    background: var(--psu-green);
    color: #fff; border: 0;
    font-weight: 600; font-size: 15px;
    padding: 11px 18px; border-radius: 10px;
    cursor: pointer;
    transition: background .15s ease, transform .15s ease;
    display: inline-flex; align-items: center; gap: 6px;
    font-family: inherit;
  }
  .composer button:hover { background: var(--psu-green-dark); transform: translateY(-1px); }
  .composer button:disabled { background: var(--ink-muted); cursor: wait; transform: none; }

  .disclaimer {
    max-width: 900px; margin: 8px auto 0;
    font-size: 12px; color: var(--ink-muted);
    text-align: center;
  }

  @media (max-width: 640px) {
    .brand img { height: 60px; }
    .topbar-inner { padding: 12px 16px; gap: 12px; }
    .bubble { max-width: 84%; font-size: 14.5px; }
    .intro { padding: 16px 18px; }
    .intro h1 { font-size: 1.2rem; }
    .back-link .label { display: none; }
    .back-link { padding: 9px 12px; }
  }
</style>
</head>
<body>

<a class="skip-link" href="#main-content">Skip to main content</a>

<header class="topbar">
  <div class="topbar-inner">
    <a href="/" class="brand" aria-label="PSU CS home">
      <img alt="Portland State University Department of Computer Science" src="/images/pdx-cs-logo.png"/>
    </a>
    <span class="topbar-spacer"></span>
    <a class="back-link" href="/" aria-label="Back to site">
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M19 12H5M12 19l-7-7 7-7"/>
      </svg>
      <span class="label">Back to site</span>
    </a>
  </div>
</header>

<main id="main-content">
  <div class="intro">
    <h1><span class="badge" aria-label="AI assistant">AI</span> Ask the CS Assistant</h1>
    <p>I can answer questions about the PSU CS department's programs, courses, faculty, and student resources, drawn directly from the official Google Doc handbook and the department's site content.</p>
    <div class="suggestions" role="group" aria-label="Example questions">
      <button class="suggestion" onclick="askSuggestion('Tell me about your AI degree options')">AI options</button>
      <button class="suggestion" onclick="askSuggestion('Who do I contact for graduate advising?')">Graduate advising</button>
      <button class="suggestion" onclick="askSuggestion('Tell me about the cybersecurity certificate.')">Cybersecurity certificate</button>
      <button class="suggestion" onclick="askSuggestion('What is the Discover CS cohort?')">Discover CS</button>
      <button class="suggestion" onclick="askSuggestion('What internship options exist for graduate students?')">Internships</button>
    </div>
  </div>

  <div id="log" role="log" aria-live="polite" aria-label="Conversation transcript">
    <div class="empty" id="emptyState"><span aria-hidden="true">👋</span> Ask me anything about the PSU CS department. Answers always include citation links you can verify.</div>
  </div>

  <div class="composer-wrap" role="search">
    <form class="composer" id="askForm" onsubmit="return onSubmit(event)" aria-label="Ask a question">
      <label for="askInput" class="visually-hidden">Your question</label>
      <input id="askInput" type="text" placeholder="Ask anything about the CS department..." aria-label="Your question" autofocus autocomplete="off"/>
      <button id="askBtn" type="submit">Send <span aria-hidden="true">→</span></button>
    </form>
    <p class="disclaimer">AI-generated answers may contain errors. Always verify against the linked sources.</p>
  </div>
</main>

<script>
  const log = document.getElementById('log');
  const empty = document.getElementById('emptyState');
  const input = document.getElementById('askInput');
  const btn = document.getElementById('askBtn');

  /* ---------- Tiny safe Markdown renderer ----------
   * Supports: paragraphs, bullet & numbered lists, **bold**, *italic*,
   * `code`, [text](url), and a "Sources:" trailing block which is styled
   * separately so citations are visually grouped.
   */
  function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function renderInline(s) {
    // bold
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // italic (avoid matching ** runs)
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    // inline code
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // links [text](url) -- only allow http(s), mailto, tel, and root-relative URLs
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, url) => {
      const safe = /^(https?:|mailto:|tel:|\/)/i.test(url);
      const target = safe ? url : '#';
      const ext = /^https?:/i.test(url) ? ' target="_blank" rel="noopener"' : '';
      return `<a href="${target}"${ext}>${text}</a>`;
    });
    return s;
  }
  function renderMarkdown(raw) {
    if (!raw) return '';
    // Split off a trailing Sources: section so we can style it separately.
    let main = raw, sources = '';
    const m = raw.match(/^([\s\S]*?)\n+\s*Sources?:\s*\n([\s\S]+)$/);
    if (m) { main = m[1]; sources = m[2]; }

    // Escape everything, then re-introduce markup carefully.
    const blocks = main.trim().split(/\n{2,}/).map(b => b.trim()).filter(Boolean);
    const htmlBlocks = blocks.map(block => {
      const lines = block.split('\n');
      // bullet list?
      if (lines.every(l => /^\s*[-*]\s+/.test(l))) {
        const items = lines.map(l => '<li>' + renderInline(escapeHtml(l.replace(/^\s*[-*]\s+/, ''))) + '</li>').join('');
        return '<ul>' + items + '</ul>';
      }
      // numbered list?
      if (lines.every(l => /^\s*\d+\.\s+/.test(l))) {
        const items = lines.map(l => '<li>' + renderInline(escapeHtml(l.replace(/^\s*\d+\.\s+/, ''))) + '</li>').join('');
        return '<ol>' + items + '</ol>';
      }
      // paragraph (allow soft line breaks)
      return '<p>' + renderInline(escapeHtml(block)).replace(/\n/g, '<br>') + '</p>';
    });

    let html = htmlBlocks.join('');

    if (sources.trim()) {
      const lines = sources.trim().split('\n').map(l => l.trim()).filter(l => /^[-*]\s+/.test(l));
      const items = lines.map(l => {
        const stripped = l.replace(/^[-*]\s+/, '');
        return '<li>' + renderInline(escapeHtml(stripped)) + '</li>';
      }).join('');
      html += '<span class="sources-label">Sources</span><ul class="sources-list">' + items + '</ul>';
    }
    return html;
  }

  function addRow(kind, contentHtml, isLoading) {
    if (empty && empty.parentNode) empty.remove();
    const row = document.createElement('div');
    row.className = 'row ' + kind;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = kind === 'user' ? '🧑' : '🤖';
    avatar.setAttribute('aria-hidden', 'true');  // identity already conveyed by meta label

    const stack = document.createElement('div');
    stack.style.maxWidth = '100%';
    stack.style.display = 'flex';
    stack.style.flexDirection = 'column';
    stack.style.alignItems = kind === 'user' ? 'flex-end' : 'flex-start';

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (isLoading) {
      bubble.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
    } else {
      bubble.innerHTML = contentHtml;
    }

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = kind === 'user' ? 'You' : 'CS Assistant';

    stack.appendChild(bubble);
    stack.appendChild(meta);

    if (kind === 'user') {
      row.appendChild(stack);
      row.appendChild(avatar);
    } else {
      row.appendChild(avatar);
      row.appendChild(stack);
    }

    log.appendChild(row);
    row.scrollIntoView({behavior: 'smooth', block: 'end'});
    return bubble;
  }

  async function ask(q) {
    if (!q) return;
    addRow('user', escapeHtml(q), false);
    const placeholder = addRow('bot', '', true);
    btn.disabled = true;
    input.value = '';
    try {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question: q})
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      placeholder.innerHTML = renderMarkdown(data.answer || '(no answer)');
    } catch (e) {
      placeholder.innerHTML = '<p style="color:#a33">Sorry, something went wrong: ' + escapeHtml(e.message) + '</p>';
    } finally {
      btn.disabled = false;
      input.focus();
    }
  }

  function onSubmit(e) { e.preventDefault(); ask(input.value.trim()); return false; }
  function askSuggestion(q) { input.value = q; ask(q); }
</script>

</body>
</html>
"""


@app.get("/ask/", response_class=HTMLResponse)
def ask_ui():
    return CHAT_UI


# Mount the static site last so it serves /, /<slug>/, and /files/<name>.pdf.
# `cspdx build` copies static/ (incl. files/) into build/site, so this single
# self-contained tree serves both the generated pages and the uploaded PDFs.
if Path(SITE_DIR).is_dir():
    app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="site")
