"""Append-only audit log of authenticated activity (build step 2.3).

Every request that reaches the HTTP server is logged as one JSON line under
`.vault/audit.log`: when it happened, the client address, the method/path, and
whether it was authorized. Plain JSONL so you can read or `grep` it directly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from .config import paths


# Path to the audit log file (under the gitignored .vault/ folder).
def audit_path():
    return paths()["system"] / "audit.log"


# Append one event as a JSON line, stamping it with the current UTC time. Never
# raises into the request path -- a failed audit write must not break serving.
def record(event: Dict[str, Any]) -> None:
    line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError:
        pass  # auditing is best-effort; serving continues regardless


# Read the whole audit log back into a list of dicts (for tests and the admin
# CLI). Empty list if there's no log yet.
def read_all() -> List[Dict[str, Any]]:
    path = audit_path()
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
