"""Header-only mail triage: classify · clean · digest (pure, offline-testable).

The Gmail connector's original sin was treating every message equally: a
`newer_than:30d` sync dumped ~464 messages into ~464 vault notes, ~half of them
promotions and LinkedIn job alerts, many padded with zero-width characters that
poison the vector index. This module is the data-reduction brain that fixes that,
and it is deliberately a *pure* transform over already-fetched Gmail message dicts
(no network, no DB) so the whole policy is verified by an offline self-check.

Design (grounded in how real triage tools work):
  - **Header-only classification** (SaneBox model): decide from Gmail `labelIds`
    (CATEGORY_*/IMPORTANT/STARRED — Gmail already classifies server-side, for free)
    plus the RFC list headers (`List-Unsubscribe`/`List-Id`) and the sender. We
    never need the message body, which also keeps this cheap and private.
  - **Three outcomes** (HEY's Imbox / The Feed / screened-out): KEEP a keeper as its
    own clean note, DIGEST bulk/list mail into ONE rolling weekly note, or SKIP pure
    noise (promotions/social/drafts) — counted for the report, but never written.
  - **Tag, don't flood** (notmuch/afew): every message gets a class + an importance
    + a retention, so search ranks real mail above noise and TTL expires the rest.

Nothing here writes to the vault; `gmail.py` maps these decisions onto notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

# --- Retention policy (days of TTL before the archival policy sweeps a note) ---
# Keepers get long or no expiry; bulk/digest is short-lived; skipped classes are
# never written. Tunable in one place so the policy is auditable at a glance.
STARRED_TTL_DAYS: Optional[float] = None   # user starred it -> never auto-archive
IMPORTANT_TTL_DAYS: float = 180            # Gmail thinks it matters -> keep a while
PERSONAL_TTL_DAYS: float = 90              # primary/personal mail -> a quarter
BULK_TTL_DAYS: float = 30                  # newsletters/alerts digest -> a month

# Importance (0-5, matching note.py's scale; keepers 3-4, noise 0-1).
_IMPORTANCE = {
    "starred": 4, "important": 3, "personal": 3,
    "bulk": 1, "promotion": 0, "social": 0, "excluded": 0,
}

# Gmail system labels we key off. CATEGORY_* are Gmail's own inbox tabs.
_L_STARRED = "STARRED"
_L_IMPORTANT = "IMPORTANT"
_L_PROMOTIONS = "CATEGORY_PROMOTIONS"
_L_SOCIAL = "CATEGORY_SOCIAL"
_L_UPDATES = "CATEGORY_UPDATES"
_L_FORUMS = "CATEGORY_FORUMS"
# Labels that mean "this isn't real inbox mail" — never ingest these.
_EXCLUDED_LABELS = frozenset({"DRAFT", "SPAM", "TRASH", "CHAT", "SENT"})

# Invisible/zero-width codepoints senders stuff into preheader text to pad the
# Gmail snippet (e.g. the "͏ ͏ ͏" runs seen in LinkedIn alerts). They carry no
# meaning and wreck tokenization/embeddings, so we strip them from any kept note.
_ZERO_WIDTH = str.maketrans({codepoint: None for codepoint in (
    0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x034F, 0x061C,
    0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E, 0x2028, 0x2029,
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069,
)})

# Boilerplate fragments that add no signal to a stored note.
_BOILERPLATE = re.compile(
    r"(view (this )?(email |message )?in (your )?browser|"
    r"trouble (viewing|reading) this email|"
    r"can'?t see (this|the) (email|images)|"
    r"add us to your address book|"
    r"unsubscribe|manage (your )?preferences)",
    re.IGNORECASE,
)

# Cap a stored snippet — Gmail snippets are ~200 chars, but be defensive.
_MAX_SNIPPET = 500


@dataclass(frozen=True)
class Classification:
    """The triage decision for one message (pure function of its headers)."""

    klass: str                 # starred|important|personal|bulk|promotion|social|excluded
    action: str                # keep | digest | skip
    importance: int            # 0-5 (feeds note ranking / prune scoring)
    ttl_days: Optional[float]  # None = keeper (no expiry); else days until TTL
    reason: str                # short human explanation (for the preview/report)


# --- header access (handles both nested payload.headers and a flat headers list) ---
def _headers_of(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (message.get("payload") or {}).get("headers") or message.get("headers") or []


def header_value(message: Dict[str, Any], name: str) -> str:
    """Case-insensitive fetch of a single header value ('' if absent)."""
    lowered = name.lower()
    for header in _headers_of(message):
        if str(header.get("name", "")).lower() == lowered:
            return header.get("value", "") or ""
    return ""


def _labels(message: Dict[str, Any]) -> set:
    return set(message.get("labelIds") or [])


def is_list_mail(message: Dict[str, Any]) -> bool:
    """True if the message looks like bulk/list mail (a mailing list, newsletter,
    or automated alert). Uses the RFC list headers (the same signal mail providers
    and notmuch/afew use) plus Gmail's Updates/Forums tabs."""
    if header_value(message, "List-Unsubscribe") or header_value(message, "List-Id"):
        return True
    labels = _labels(message)
    return bool(labels & {_L_UPDATES, _L_FORUMS})


# --- classification --------------------------------------------------------
# Precedence (first match wins), chosen so explicit user intent beats heuristics
# and cheap "never ingest" signals short-circuit first:
#   excluded (draft/spam/trash/chat/sent)  -> skip
#   STARRED (user keep)                     -> keep, importance 4, no expiry
#   CATEGORY_PROMOTIONS                     -> skip (pure marketing)
#   CATEGORY_SOCIAL                         -> skip (social network noise)
#   list mail (List-* / Updates / Forums)   -> digest, importance 1
#   IMPORTANT (Gmail's marker)              -> keep, importance 3, long TTL
#   otherwise (primary/personal)            -> keep, importance 3, medium TTL
def classify(message: Dict[str, Any]) -> Classification:
    """Classify one already-fetched Gmail message dict from its headers/labels."""
    labels = _labels(message)

    if labels & _EXCLUDED_LABELS:
        which = ", ".join(sorted(labels & _EXCLUDED_LABELS))
        return Classification("excluded", "skip", _IMPORTANCE["excluded"], None,
                              f"not inbox mail ({which})")

    if _L_STARRED in labels:
        return Classification("starred", "keep", _IMPORTANCE["starred"], STARRED_TTL_DAYS,
                              "starred by you (keeper, never auto-archives)")

    if _L_PROMOTIONS in labels:
        return Classification("promotion", "skip", _IMPORTANCE["promotion"], None,
                              "Gmail Promotions (marketing)")

    if _L_SOCIAL in labels:
        return Classification("social", "skip", _IMPORTANCE["social"], None,
                              "Gmail Social (social-network notification)")

    if is_list_mail(message):
        return Classification("bulk", "digest", _IMPORTANCE["bulk"], BULK_TTL_DAYS,
                              "bulk/list mail (newsletter or automated alert) -> weekly digest")

    if _L_IMPORTANT in labels:
        return Classification("important", "keep", _IMPORTANCE["important"], IMPORTANT_TTL_DAYS,
                              "Gmail marked important")

    return Classification("personal", "keep", _IMPORTANCE["personal"], PERSONAL_TTL_DAYS,
                          "primary/personal mail")


# --- snippet cleaning ------------------------------------------------------
def clean_snippet(text: str) -> str:
    """Strip zero-width padding + obvious boilerplate and collapse whitespace, so a
    kept note carries clean, indexable content instead of tracking cruft."""
    if not text:
        return ""
    text = text.translate(_ZERO_WIDTH)
    # Drop boilerplate fragments, then squeeze runs of whitespace to single spaces.
    text = _BOILERPLATE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > _MAX_SNIPPET:
        text = text[:_MAX_SNIPPET].rstrip() + "…"
    return text


# --- message date resolution (for digest bucketing + lines) ----------------
def message_datetime(message: Dict[str, Any], *, fallback: Optional[datetime] = None) -> datetime:
    """Best-effort UTC datetime for a message: Gmail `internalDate` (epoch ms) if
    present, else the parsed `Date:` header, else `fallback`/now. Always tz-aware."""
    internal = message.get("internalDate")
    if internal:
        try:
            return datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    raw_date = header_value(message, "Date")
    if raw_date:
        try:
            parsed = parsedate_to_datetime(raw_date)
            if parsed is not None:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return (fallback or datetime.now(timezone.utc)).astimezone(timezone.utc)


# --- weekly digest (one rolling note per ISO week) -------------------------
# Keying by ISO week gives a stable, human-meaningful bucket ("mail from the week
# of ..."). List-Id could sub-split it later, but one digest/week keeps the vault
# tiny and is exactly the rollup a human would skim.
def digest_key(when: datetime) -> str:
    """Stable digest id for a datetime's ISO week, e.g. gmail://digest/2026-W28."""
    iso_year, iso_week, _ = when.isocalendar()
    return f"gmail://digest/{iso_year}-W{iso_week:02d}"


def digest_title(when: datetime) -> str:
    iso_year, iso_week, _ = when.isocalendar()
    return f"Mail digest — {iso_year} week {iso_week:02d}"


def digest_line(message: Dict[str, Any], when: datetime) -> str:
    """One stable, parseable digest row, carrying the message id in a marker comment
    so re-syncs dedup and merge cleanly. Format:
        - YYYY-MM-DD · Sender · Subject <!-- id:<msgid> -->
    Sorting lines lexically therefore sorts by date."""
    date_str = when.date().isoformat()
    subject = clean_snippet(header_value(message, "Subject")) or "(no subject)"
    sender = clean_snippet(header_value(message, "From")) or "(unknown sender)"
    message_id = message.get("id", "")
    return f"- {date_str} · {sender} · {subject} <!-- id:{message_id} -->"


_ID_MARKER = re.compile(r"<!--\s*id:([^\s]+?)\s*-->")


def parse_digest_items(body: str) -> Dict[str, str]:
    """Recover the {message_id: line} map from an existing digest note body, so a
    later sync can merge new rows in without duplicating or losing the old ones."""
    items: Dict[str, str] = {}
    for line in (body or "").splitlines():
        match = _ID_MARKER.search(line)
        if match and line.lstrip().startswith("-"):
            items[match.group(1)] = line.rstrip()
    return items


def render_digest(title: str, items: Dict[str, str], source_ref: str) -> str:
    """Render the digest note body: heading, a count, newest-first rows, and the
    source_ref marker (keeps the body unique + auditable, like the other adapters)."""
    rows = sorted(items.values(), reverse=True)  # lines start with the date -> newest first
    lines = [f"# {title}", "", f"{len(rows)} message(s) rolled up from bulk/list mail.", ""]
    lines.extend(rows)
    lines.extend(["", f"<!-- source_ref: {source_ref} -->"])
    return "\n".join(lines) + "\n"
