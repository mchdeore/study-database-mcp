"""Google Calendar adapter (build step 4.2).

Transforms Google Calendar API event resources into vault notes: one note per
event, carrying `start`/`end` in frontmatter so the indexer derives an `events`
row (and a rebuild reconstructs it from the vault). Events dedup by `source_ref`
= `gcal://event/<id>`, so re-syncing updates the same note in place.

The transform (`event_to_fields`) is pure and offline-testable with fixture
dicts; the live Google fetch + OAuth is a separate thin layer (added with the
sync CLI). Read-only: this never writes back to Google.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..db import VaultDB, get_db
from . import base

# Where calendar notes live. An ongoing life area, its own subfolder so it stays
# scannable and can be pruned/capped as a group.
CALENDAR_CATEGORY = "40-areas/calendar"

# The `source` recorded on every calendar note's frontmatter.
SOURCE = "google"

# Detects an ALL-CAPS "PREFIX:" at the start of an event title -- the owner's
# Google-Calendar convention for separating categories (e.g. "SCHOOL: Midterm",
# "FINANCE: Rent due", "GYM: Legs"). The captured group becomes a tag + a `group`
# field so the dashboard/timeline can split events by category. The title is kept
# intact (prefix included) so nothing the owner typed is lost.
_GROUP_RE = re.compile(r"^\s*([A-Z][A-Z0-9 &/]{1,23}):\s*\S")


# Extract the ALL-CAPS group label from a summary, or None if there isn't one.
def _group_of(summary: str) -> Optional[str]:
    match = _GROUP_RE.match(summary or "")
    return match.group(1).strip() if match else None


# Pull an ISO timestamp out of a Calendar start/end node, which is either
# {"dateTime": "...T..."} (timed) or {"date": "YYYY-MM-DD"} (all-day). None if
# neither is present.
def _when(node: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    return node.get("dateTime") or node.get("date")


# Render the human-readable note body. The trailing `source_ref` comment keeps
# each event's body unique (two same-titled meetings won't collide under the
# indexer's exact body-hash dedup) and makes the origin auditable by eye.
def _render_body(
    title: str, start: Optional[str], end: Optional[str],
    location: str, description: str, html_link: str, source_ref: str,
) -> str:
    when = start or "(no start)"
    if end:
        when = f"{when} → {end}"

    lines = [f"# {title}", "", f"- **When:** {when}"]
    if location:
        lines.append(f"- **Where:** {location}")
    lines.append("")
    if description:
        lines.extend([description, ""])
    if html_link:
        lines.extend([f"[Open in Google Calendar]({html_link})", ""])
    lines.append(f"<!-- source_ref: {source_ref} -->")
    return "\n".join(lines) + "\n"


# Transform one Google Calendar event resource into the note fields the connector
# needs. Raises if the event has no id (the id is the dedup key).
def event_to_fields(event: Dict[str, Any]) -> Dict[str, Any]:
    event_id = event.get("id")
    if not event_id:
        raise ValueError("calendar event has no 'id'; cannot form a stable source_ref.")

    title = (event.get("summary") or "").strip() or "(untitled event)"
    start = _when(event.get("start"))
    end = _when(event.get("end"))
    location = (event.get("location") or "").strip()
    description = (event.get("description") or "").strip()
    html_link = event.get("htmlLink") or ""
    source_ref = f"gcal://event/{event_id}"

    return {
        "title": title,
        "start": start,
        "end": end,
        "location": location,
        "source_ref": source_ref,
        "status": event.get("status"),
        "group": _group_of(event.get("summary") or ""),
        "body": _render_body(title, start, end, location, description, html_link, source_ref),
    }


# Sync a batch of Google Calendar event resources into the vault. Cancelled events
# and events with no start are skipped; everything else is upserted by source_ref.
# Returns per-action counts. `events` is already-fetched API data (a list of dicts),
# so this is fully testable offline with fixtures.
def sync_events(
    events: List[Dict[str, Any]],
    *,
    category: str = CALENDAR_CATEGORY,
    skip_cancelled: bool = True,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    summary = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "count": len(events)}

    for event in events:
        if not event.get("id") or (skip_cancelled and event.get("status") == "cancelled"):
            summary["skipped"] += 1
            continue

        fields = event_to_fields(event)
        if not fields["start"]:
            summary["skipped"] += 1  # not a timeline event without a start
            continue

        extra: Dict[str, Any] = {"start": fields["start"], "end": fields["end"], "tags": ["calendar"]}
        if fields["location"]:
            extra["location"] = fields["location"]
        if fields.get("group"):
            extra["group"] = fields["group"]
            extra["tags"] = ["calendar", fields["group"].lower()]

        outcome = base.upsert_note(
            source=SOURCE, source_ref=fields["source_ref"], title=fields["title"],
            body=fields["body"], category=category, extra=extra, database=database,
        )
        summary[outcome["action"]] = summary.get(outcome["action"], 0) + 1

    return summary
