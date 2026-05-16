"""Google Docs auth + fetch helpers.

Supports two auth modes:
- 'oauth': InstalledAppFlow with credentials.json + token.json (dev)
- 'service_account': service_account.json (Cloud Run / CI)

Lifted and refactored from the original gdoc2site.py.
"""
from __future__ import annotations
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


def export_tab_html(creds, doc_id: str, tab_id: Optional[str] = None) -> str:
    """Download a tab (or whole doc) as HTML via the docs export endpoint."""
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=html&id={doc_id}"
    if tab_id:
        url += f"&tab={tab_id}"
    headers = {"Authorization": f"Bearer {creds.token}"}
    for _ in range(10):
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return r.text
        time.sleep(1)
    r.raise_for_status()
    return r.text
