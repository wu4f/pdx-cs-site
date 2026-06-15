"""Build metadata: when the last build ran and the per-document revisions /
modified-times it was built from.

Used to (a) skip a rebuild when no source document has changed and (b) show
"last build" + "last changed" timestamps on the /admin page. Stored as a
small JSON file next to sections.json; it is runtime state (gitignored).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_META_PATH = "build/build_meta.json"


def load_meta(path: str = DEFAULT_META_PATH) -> dict:
    """Return the stored build metadata, or {} if absent/unreadable."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_meta(docs: list[dict], path: str = DEFAULT_META_PATH) -> dict:
    """Stamp 'built_at' = now (UTC) and persist the per-doc states."""
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "docs": docs,
    }
    Path(path).write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def unique_docs(docs_cfg: list[dict]) -> list[dict]:
    """Dedupe config docs by id (the handbook appears twice, split two ways).
    First occurrence wins for the fallback name."""
    seen: dict[str, dict] = {}
    for d in docs_cfg:
        seen.setdefault(d["id"], d)
    return list(seen.values())


def current_doc_states(creds, docs_cfg: list[dict]) -> list[dict]:
    """Fetch revisionId + modifiedTime for each unique doc id in the config."""
    from .sources import gdocs
    states: list[dict] = []
    for d in unique_docs(docs_cfg):
        doc_id = d["id"]
        revision = gdocs.get_revision(creds, doc_id)
        modified_time, drive_name = "", ""
        try:
            modified_time, drive_name = gdocs.get_modified_time(creds, doc_id)
        except Exception as e:  # Drive API hiccup shouldn't abort the build
            print(f"  ! could not read modifiedTime for {doc_id}: {e}")
        states.append(
            {
                "id": doc_id,
                "name": drive_name or d.get("name", ""),
                "revision": revision,
                "modified_time": modified_time,
            }
        )
    return states


def _fingerprint(d: dict) -> tuple[str, str]:
    return (d.get("revision", ""), d.get("modified_time", ""))


def changed_ids(prev_meta: dict, current_states: list[dict]) -> list[str]:
    """Return the ids that changed since prev_meta (or are new).

    Compares both revisionId and Drive modifiedTime: some docs (e.g. tabbed
    ones) don't expose a revisionId, so modifiedTime is the reliable signal.
    A doc counts as changed if either value differs -- erring toward rebuild.
    """
    prev = {d["id"]: _fingerprint(d) for d in (prev_meta.get("docs") or [])}
    return [s["id"] for s in current_states if prev.get(s["id"]) != _fingerprint(s)]
