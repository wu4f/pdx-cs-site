"""Google Docs auth + fetch helpers.

Supports two auth modes:
- 'oauth': InstalledAppFlow with credentials.json + token.json (dev)
- 'service_account': service_account.json (Cloud Run / CI)

Lifted and refactored from the original gdoc2site.py.
"""
from __future__ import annotations
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import time
import requests
from typing import Optional

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/docs.readonly",
]


def get_creds(
    mode: str = "oauth",
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    service_account_path: str = "service_account.json",
):
    """Return Google API credentials.

    mode='service_account' is recommended for Cloud Run; share the docs
    with the service account's email address (Viewer access is enough).
    """
    if mode == "service_account":
        sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", service_account_path)
        return service_account.Credentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )

    # OAuth (dev / interactive)
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return creds


def docs_service(creds):
    return build("docs", "v1", credentials=creds)


def drive_service(creds):
    return build("drive", "v3", credentials=creds)


def get_modified_time(creds, doc_id: str) -> tuple[str, str]:
    """Return (modifiedTime, name) for a doc from the Drive API.

    modifiedTime is an RFC-3339 timestamp (e.g. '2026-06-13T10:23:45.000Z').
    Used to show on the admin page when each source doc last changed.
    """
    f = (
        drive_service(creds)
        .files()
        .get(fileId=doc_id, fields="modifiedTime,name", supportsAllDrives=True)
        .execute()
    )
    return f.get("modifiedTime", ""), f.get("name", "")


def get_doc(creds, doc_id: str) -> dict:
    """Fetch a Google Doc, including tab content if present."""
    return (
        docs_service(creds)
        .documents()
        .get(documentId=doc_id, includeTabsContent=True)
        .execute()
    )


def get_revision(creds, doc_id: str) -> str:
    """Return the doc's revisionId, or '' if the API doesn't provide one."""
    doc = (
        docs_service(creds)
        .documents()
        .get(documentId=doc_id, fields="revisionId")
        .execute()
    )
    if "revisionId" in doc:
        return doc["revisionId"]
    # Some docs don't return revisionId under a `fields` filter; fall back.
    doc = docs_service(creds).documents().get(documentId=doc_id).execute()
    return doc.get("revisionId", "")


_EXPORT_TIMEOUT = 60  # seconds before a frozen request is killed and retried
_EXPORT_RETRIES = 7

# The export endpoint is not the Docs API and carries its own, much tighter,
# undocumented quota: two full builds back to back are enough to start drawing
# 429s, and the block clears in minutes rather than seconds. So a throttle backs
# off from 5 s while a dropped connection or a 5xx retries from 1 s — a 1 s
# ladder gives up after half a minute of total waiting and fails the build for
# something that would have cleared on its own.
_THROTTLE_STATUS = {408, 429}
_RETRYABLE_STATUS = _THROTTLE_STATUS | {500, 502, 503, 504}
_TRANSIENT_BASE = 1.0    # 1 s, 2 s, 4 s, 8 s ...
_THROTTLE_BASE = 5.0     # 5 s, 10 s, 20 s, 40 s, 80 s, 120 s ...
_BACKOFF_CAP = 120.0
_RETRY_AFTER_CAP = 300.0  # don't stall a build for an hour on the server's say-so


def _export_error(r: requests.Response, url: str) -> requests.HTTPError:
    """An HTTPError that names the status, reason, and URL.

    `requests.HTTPError(response=r)` carries the response but stringifies to the
    empty string, so a failed build ended in a bare `requests.exceptions.HTTPError`
    with no way to tell a rate limit from an expired token or a deleted doc.
    """
    msg = f"HTTP {r.status_code} {r.reason} from {url}"
    body = " ".join(r.text.split())[:200]
    return requests.HTTPError(f"{msg} — {body}" if body else msg, response=r)


def _retry_after(r: requests.Response) -> Optional[float]:
    """Seconds the response's Retry-After header asks for, if it has one.

    The header is either a delay in seconds or an HTTP date; both forms appear
    in the wild, so handle each and fall back to the caller's back-off ladder
    when it's missing or unparseable.
    """
    value = (r.headers.get("Retry-After") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def export_tab_html(creds, doc_id: str, tab_id: Optional[str] = None) -> str:
    """Download a tab (or whole doc) as HTML via the docs export endpoint.

    Retries throttles (429), timeouts, and 5xx with exponential back-off,
    honouring Retry-After when the server sends one. A status that waiting
    cannot fix — 401, 403, 404 — fails immediately rather than burning the
    ladder on a request that will never succeed.
    """
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=html&id={doc_id}"
    if tab_id:
        url += f"&tab={tab_id}"
    headers = {"Authorization": f"Bearer {creds.token}"}
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(_EXPORT_RETRIES):
        base, requested, what = _TRANSIENT_BASE, None, ""
        try:
            r = requests.get(url, headers=headers, timeout=_EXPORT_TIMEOUT)
            if r.status_code == 200:
                return r.text
            last_exc = _export_error(r, url)
            if r.status_code not in _RETRYABLE_STATUS:
                break
            if r.status_code in _THROTTLE_STATUS:
                base = _THROTTLE_BASE
            requested = _retry_after(r)
            what = f"HTTP {r.status_code}"
        except requests.exceptions.Timeout as e:
            last_exc, what = e, "timed out"
        except requests.exceptions.RequestException as e:
            last_exc, what = e, type(e).__name__

        if attempt == _EXPORT_RETRIES - 1:
            break  # nothing left to wait for
        delay = (
            min(requested, _RETRY_AFTER_CAP)
            if requested is not None
            else min(base * 2 ** attempt, _BACKOFF_CAP)
        )
        print(
            f"  [warn] tab export {what} (attempt {attempt + 1}/{_EXPORT_RETRIES}), "
            f"retrying in {delay:.0f}s..."
        )
        time.sleep(delay)
    raise last_exc
