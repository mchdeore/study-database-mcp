"""Per-source connector sync cursors (build step 4.4).

A durable sidecar (`.vault/cursors.json`) recording, per connector source, the last
checkpoint (an opaque cursor string the provider understands -- a Google Calendar
syncToken, a Gmail historyId, a max-seen date, ...), when it last synced, and how
many items that sync pulled. This is what makes syncs INCREMENTAL and RESUMABLE:
the next sync passes the saved cursor so the source only returns items after it, and
an interrupted sync just re-runs from the last saved cursor.

Mirrors the prune signals sidecar: plain auditable JSON under `.vault/`, independent
of the derived DB (so it survives a rebuild). The cursor value is opaque here -- only
the provider's fetch function interprets it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..config import paths
from ..note import now_iso


# Load the cursor sidecar: {source: {"cursor": str|None, "last_sync": iso, "items": int}}.
# A missing or corrupt file is treated as "no cursors yet" (empty dict).
def load_cursors() -> Dict[str, Dict[str, Any]]:
    cursors_path = paths()["cursors_json"]
    if not cursors_path.exists():
        return {}
    try:
        return json.loads(cursors_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# Persist the cursor sidecar (pretty-printed + sorted so it's auditable by eye).
def save_cursors(cursors: Dict[str, Dict[str, Any]]) -> None:
    cursors_path = paths()["cursors_json"]
    cursors_path.parent.mkdir(parents=True, exist_ok=True)
    cursors_path.write_text(json.dumps(cursors, indent=2, sort_keys=True), encoding="utf-8")


# The saved cursor for a source, or None if it has never synced (a first/full sync).
def get_cursor(source: str) -> Optional[str]:
    return (load_cursors().get(source) or {}).get("cursor")


# Record the checkpoint after a sync: store the new cursor, stamp last_sync=now, and
# note how many items that run pulled. Returns the updated entry.
def set_cursor(source: str, cursor: Optional[str], *, items: int = 0) -> Dict[str, Any]:
    cursors = load_cursors()
    entry = {"cursor": cursor, "last_sync": now_iso(), "items": int(items)}
    cursors[source] = entry
    save_cursors(cursors)
    return entry
