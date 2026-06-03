"""Shared admin-token loading + chat-reload helper.

The chat server caches sections in memory and in a Gemini context cache, so
it must be told to reload after `cspdx build` rewrites sections.json. A shared
secret guards the /admin/reload endpoint; both the server and the build read
it from $ADMIN_TOKEN, falling back to a gitignored .admin_token file so the
secret only has to live in one place.
"""
from __future__ import annotations
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_FILE = ".admin_token"
DEFAULT_RELOAD_URL = "http://127.0.0.1:8080/admin/reload"


def load_admin_token(token_file: str = TOKEN_FILE) -> str:
    """Return the admin token: $ADMIN_TOKEN, else the .admin_token file,
    else "" (an empty token disables the /admin/reload endpoint)."""
    env = os.getenv("ADMIN_TOKEN")
    if env and env.strip():
        return env.strip()
    p = Path(token_file)
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def reload_chat(
    reload_url: str | None = None,
    token: str | None = None,
    timeout: float = 5.0,
) -> tuple[bool, str]:
    """POST to the server's /admin/reload so it re-reads sections.json.

    Best-effort: returns (ok, message) and never raises, so a missing token or
    an offline server degrades to a warning rather than failing the build.
    """
    url = reload_url or os.getenv("CSPDX_RELOAD_URL") or DEFAULT_RELOAD_URL
    tok = token if token is not None else load_admin_token()
    if not tok:
        return False, (
            "no admin token set (set $ADMIN_TOKEN or create .admin_token); "
            "skipping chat reload"
        )
    full = f"{url}?{urllib.parse.urlencode({'token': tok})}"
    req = urllib.request.Request(full, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace").strip()
            return True, f"chat reloaded ({resp.status}) {body}"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()
        return False, f"chat reload rejected ({e.code}) {detail}"
    except Exception as e:  # connection refused, timeout, bad URL, ...
        return False, f"chat reload skipped (server unreachable?): {e}"
