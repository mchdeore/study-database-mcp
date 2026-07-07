"""Gmail adapter (build step 4.3).

Gmail is the firehose, so ingested mail lands in an *ephemeral* category with a
short default `expires:` TTL. That plugs straight into the Phase 3 TTL policy
(`archive.run_ttl`): unpromoted mail auto-archives once its TTL passes, while a
message you pin or edit survives. Read-only; deduped by `source_ref =
gmail://msg/<id>` so re-syncing updates the same note.

`message_to_fields` is a pure transform over an already-fetched Gmail message
dict (headers + snippet/body), so it's fully offline-testable; the live Gmail
fetch + OAuth + MIME decoding is a separate thin layer added with the sync CLI.
Scope tightly at fetch time (a label/query filter); `sync_messages` also accepts
a `label_filter` so only wanted mail is ingested.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..db import VaultDB, get_db
from . import base

# Ephemeral category for ingested mail. The TTL (not the folder) is what makes it
# ephemeral; the folder just keeps it grouped and scannable.
GMAIL_CATEGORY = "50-resources/mail"

# The `source` recorded on every mail note's frontmatter.
SOURCE = "google"

# Default time-to-live for an ingested message before the TTL policy archives it.
DEFAULT_TTL_DAYS = 30


# Case-insensitive lookup of a header value from a Gmail payload's header list.
def _header(headers: List[Dict[str, Any]], name: str) -> str:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "") or ""
    return ""


# The message's content: a caller-decoded `body_text` if present, else the API
# `snippet` (always available, no MIME decoding needed).
def _content(message: Dict[str, Any]) -> str:
    return (message.get("body_text") or message.get("snippet") or "").strip()


# Render the human-readable note body. The trailing source_ref comment keeps each
# message's body unique (avoids the indexer's exact body-hash dedup) and is auditable.
def _render_body(
    subject: str, sender: str, date: str, labels: List[str], content: str, source_ref: str
) -> str:
    lines = [f"# {subject}", ""]
    if sender:
        lines.append(f"- **From:** {sender}")
    if date:
        lines.append(f"- **Date:** {date}")
    if labels:
        lines.append(f"- **Labels:** {', '.join(labels)}")
    lines.append("")
    if content:
        lines.extend([content, ""])
    lines.append(f"<!-- source_ref: {source_ref} -->")
    return "\n".join(lines) + "\n"


# The `expires:` timestamp for a freshly-ingested message: now + ttl_days.
def _expires(now: datetime, ttl_days: float) -> str:
    return (now + timedelta(days=ttl_days)).isoformat(timespec="seconds")


# Transform one Gmail message resource into the note fields the connector needs.
# Accepts either the nested `payload.headers` shape or a flat `headers` list.
# Raises if the message has no id (the id is the dedup key).
def message_to_fields(message: Dict[str, Any]) -> Dict[str, Any]:
    message_id = message.get("id")
    if not message_id:
        raise ValueError("gmail message has no 'id'; cannot form a stable source_ref.")

    headers = (message.get("payload") or {}).get("headers") or message.get("headers") or []
    subject = _header(headers, "Subject").strip() or "(no subject)"
    sender = _header(headers, "From").strip()
    date = _header(headers, "Date").strip()
    labels = list(message.get("labelIds") or [])
    source_ref = f"gmail://msg/{message_id}"

    return {
        "title": subject,
        "sender": sender,
        "date": date,
        "labels": labels,
        "source_ref": source_ref,
        "body": _render_body(subject, sender, date, labels, _content(message), source_ref),
    }


# Sync a batch of Gmail message resources into the vault. Messages are upserted by
# source_ref with an ephemeral `expires:` (now + ttl_days). `label_filter`, if set,
# ingests only messages carrying that Gmail label (mirrors a server-side query).
# `messages` is already-fetched API data, so this is fully testable offline.
def sync_messages(
    messages: List[Dict[str, Any]],
    *,
    ttl_days: float = DEFAULT_TTL_DAYS,
    category: str = GMAIL_CATEGORY,
    label_filter: Optional[str] = None,
    now: Optional[datetime] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    now = now or datetime.now(timezone.utc)
    expires = _expires(now, ttl_days)
    summary = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "count": len(messages)}

    for message in messages:
        if not message.get("id"):
            summary["skipped"] += 1
            continue
        if label_filter and label_filter not in (message.get("labelIds") or []):
            summary["skipped"] += 1
            continue

        fields = message_to_fields(message)
        outcome = base.upsert_note(
            source=SOURCE, source_ref=fields["source_ref"], title=fields["title"],
            body=fields["body"], category=category,
            extra={"expires": expires, "tags": ["mail"]}, database=database,
        )
        summary[outcome["action"]] = summary.get(outcome["action"], 0) + 1

    return summary
