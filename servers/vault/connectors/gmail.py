"""Gmail adapter (build step 4.3; triage refinement).

Gmail is the firehose. Rather than mirror every message into its own note (the
original behavior that dumped ~464 notes, half of them promos/alerts padded with
index-poisoning zero-width characters), this adapter runs each message through the
pure `mail_triage` policy and does one of three things:

  - **keep**   a keeper (starred/important/personal) as its own *clean* note, with a
               triage-derived `importance` and a class-appropriate `expires:` TTL
               (starred keepers get no expiry);
  - **digest** bulk/list mail (newsletters, job alerts) into ONE rolling weekly note
               (`gmail://digest/<year>-W<week>`), so a week of alerts is one skimmable
               note instead of two hundred;
  - **skip**   pure noise (promotions, social, drafts/spam) — counted in the report
               so you still see "118 promotions", but never written to the vault.

Read-only; deduped by `source_ref` (`gmail://msg/<id>` for keepers,
`gmail://digest/<week>` for digests) so re-syncing updates in place. Everything is
a pure transform over already-fetched Gmail message dicts, so it's fully offline
testable; the live fetch + OAuth is a separate thin layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..config import paths
from ..db import VaultDB, get_db
from ..note import Note
from . import base
from . import mail_triage

# Ephemeral category for ingested mail. The TTL (not the folder) is what makes it
# ephemeral; the folder just keeps it grouped and scannable.
GMAIL_CATEGORY = "50-resources/mail"

# The `source` recorded on every mail note's frontmatter.
SOURCE = "google"

# Legacy uniform TTL. Retention is now per-class (see mail_triage); this remains as
# an optional override callers may pass to force one TTL on every kept/digest note.
DEFAULT_TTL_DAYS = 30

# How many keepers plan_messages lists inline before truncating (token budget).
_PLAN_KEEPER_CAP = 50


# Case-insensitive lookup of a header value from a Gmail payload's header list.
def _header(headers: List[Dict[str, Any]], name: str) -> str:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "") or ""
    return ""


# The message's content, cleaned: a caller-decoded `body_text` if present, else the
# API `snippet`, with zero-width padding + boilerplate stripped (see mail_triage).
def _content(message: Dict[str, Any]) -> str:
    return mail_triage.clean_snippet(message.get("body_text") or message.get("snippet") or "")


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


# Read the body of an existing note by source_ref (for merging into a digest), or ""
# if there's no such note yet / its file is missing.
def _existing_body(database: VaultDB, source_ref: str) -> str:
    document_id = database.find_document_by_source_ref(source_ref)
    if not document_id:
        return ""
    try:
        relpath = database.get_document(document_id)["path"]
        return Note.load(paths()["vault"] / relpath).body
    except (FileNotFoundError, KeyError, TypeError):
        return ""


# Classify a batch WITHOUT touching the vault: a token-cheap triage preview an LLM
# (or the preview_gmail tool) can read to decide what to do. Returns per-class and
# per-action counts, the keepers (capped) with their computed importance, and the
# weekly digest buckets that a real sync would build.
def plan_messages(
    messages: List[Dict[str, Any]],
    *,
    label_filter: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    classes: Dict[str, int] = {}
    actions = {"keep": 0, "digest": 0, "skip": 0}
    keepers: List[Dict[str, Any]] = []
    digest_weeks: Dict[str, int] = {}
    considered = 0

    for message in messages:
        if not message.get("id"):
            continue
        if label_filter and label_filter not in (message.get("labelIds") or []):
            continue
        considered += 1

        classification = mail_triage.classify(message)
        classes[classification.klass] = classes.get(classification.klass, 0) + 1
        actions[classification.action] += 1

        if classification.action == "keep" and len(keepers) < _PLAN_KEEPER_CAP:
            keepers.append({
                "subject": mail_triage.clean_snippet(
                    mail_triage.header_value(message, "Subject")) or "(no subject)",
                "from": mail_triage.clean_snippet(mail_triage.header_value(message, "From")),
                "date": mail_triage.message_datetime(message, fallback=now).date().isoformat(),
                "class": classification.klass,
                "importance": classification.importance,
            })
        elif classification.action == "digest":
            week = mail_triage.digest_key(
                mail_triage.message_datetime(message, fallback=now)).rsplit("/", 1)[-1]
            digest_weeks[week] = digest_weeks.get(week, 0) + 1

    return {
        "count": len(messages),
        "considered": considered,
        "classes": dict(sorted(classes.items())),
        "actions": actions,
        "keepers": keepers,
        "keepers_truncated": actions["keep"] > len(keepers),
        "digest_weeks": dict(sorted(digest_weeks.items())),
    }


# Sync a batch of Gmail message resources into the vault under the triage policy.
# `messages` is already-fetched API data, so this is fully testable offline.
#   - label_filter: if set, only messages carrying that Gmail label are considered.
#   - ttl_days:     None (default) uses the per-class retention policy; a number
#                   forces that uniform TTL on every kept/digest note (tests use this).
#   - digest:       when False, bulk mail is skipped instead of rolled into a digest.
# Returns per-action counts plus a per-class breakdown and the digest notes touched.
def sync_messages(
    messages: List[Dict[str, Any]],
    *,
    ttl_days: Optional[float] = None,
    category: str = GMAIL_CATEGORY,
    label_filter: Optional[str] = None,
    digest: bool = True,
    now: Optional[datetime] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    now = now or datetime.now(timezone.utc)
    summary: Dict[str, Any] = {
        "count": len(messages), "created": 0, "updated": 0, "unchanged": 0,
        "skipped": 0, "kept": 0, "digested": 0, "classes": {}, "digests": [],
    }
    # source_ref -> {"title": str, "items": {message_id: line}}
    buckets: Dict[str, Dict[str, Any]] = {}

    for message in messages:
        if not message.get("id"):
            summary["skipped"] += 1
            continue
        if label_filter and label_filter not in (message.get("labelIds") or []):
            summary["skipped"] += 1
            continue

        classification = mail_triage.classify(message)
        summary["classes"][classification.klass] = summary["classes"].get(classification.klass, 0) + 1

        if classification.action == "skip" or (classification.action == "digest" and not digest):
            summary["skipped"] += 1
            continue

        if classification.action == "digest":
            when = mail_triage.message_datetime(message, fallback=now)
            ref = mail_triage.digest_key(when)
            bucket = buckets.setdefault(ref, {"title": mail_triage.digest_title(when), "items": {}})
            bucket["items"][message["id"]] = mail_triage.digest_line(message, when)
            summary["digested"] += 1
            continue

        # keep -> one clean, importance-scored note.
        fields = message_to_fields(message)
        effective_ttl = classification.ttl_days if ttl_days is None else ttl_days
        extra: Dict[str, Any] = {
            "tags": ["mail", classification.klass],
            "importance": classification.importance,
        }
        if effective_ttl is not None:
            extra["expires"] = _expires(now, effective_ttl)
        outcome = base.upsert_note(
            source=SOURCE, source_ref=fields["source_ref"], title=fields["title"],
            body=fields["body"], category=category, extra=extra, database=database,
        )
        summary[outcome["action"]] = summary.get(outcome["action"], 0) + 1
        summary["kept"] += 1

    _flush_digests(buckets, summary, category=category, ttl_days=ttl_days, now=now, database=database)
    return summary


# Merge each week's freshly-collected rows into its existing digest note (if any)
# and upsert it. A week that gained no new rows is left untouched (no needless
# rewrite/re-embed). Old weeks aren't in `buckets`, so they simply age out via TTL.
def _flush_digests(
    buckets: Dict[str, Dict[str, Any]],
    summary: Dict[str, Any],
    *,
    category: str,
    ttl_days: Optional[float],
    now: datetime,
    database: VaultDB,
) -> None:
    bulk_ttl = mail_triage.BULK_TTL_DAYS if ttl_days is None else ttl_days
    for source_ref, bucket in buckets.items():
        existing = mail_triage.parse_digest_items(_existing_body(database, source_ref))
        merged = {**existing, **bucket["items"]}
        if merged == existing:  # nothing new this run -> don't churn the note/index
            summary["digests"].append({"source_ref": source_ref, "items": len(merged),
                                        "action": "unchanged"})
            continue

        body = mail_triage.render_digest(bucket["title"], merged, source_ref)
        extra: Dict[str, Any] = {"tags": ["mail", "digest"], "importance": 1}
        if bulk_ttl is not None:
            extra["expires"] = _expires(now, bulk_ttl)
        outcome = base.upsert_note(
            source=SOURCE, source_ref=source_ref, title=bucket["title"], body=body,
            category=category, extra=extra, database=database,
        )
        summary[outcome["action"]] = summary.get(outcome["action"], 0) + 1
        summary["digests"].append({"source_ref": source_ref, "items": len(merged),
                                   "action": outcome["action"]})
