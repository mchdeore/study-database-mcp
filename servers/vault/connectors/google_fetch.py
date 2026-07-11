"""Live Google fetch layer for the Calendar + Gmail connectors (build step 4.1 —
fetch half).

`connectors/sync.py` deliberately split each connector into FETCH (network + auth,
injected) and INGEST (the tested adapter). This module implements the FETCH half:
a `fetch_fn(cursor) -> FetchResult` per service, talking to Google's REST APIs with
a bearer token from `google_auth.get_access_token`.

Why thin HTTP instead of `google-api-python-client`: the two list/get calls we need
are trivial REST, the heavy client library buys us nothing here, and a plain
`http_get` seam is directly mockable — so the whole paging/cursor contract is
covered by an offline self-check with zero network (`tests/check_vault_google.py`).

Cursor semantics (opaque to the runner, interpreted here):
  - Calendar: the cursor is a **syncToken**. First run (no cursor) does a bounded
    initial pull (`timeMin = now - GOOGLE_CALENDAR_SYNC_FROM_DAYS`); Google returns a
    `nextSyncToken` we persist. Later runs pass it and receive only changes. An
    expired syncToken (HTTP 410) transparently falls back to a full resync.
  - Gmail: the cursor is an **epoch-seconds watermark**. We list message ids with
    the configured query plus `after:<cursor>`, then fetch each id's metadata.
    Dedup by `source_ref` makes any small overlap harmless.

ponytail: Gmail incremental uses Gmail search `after:` (coarse, ~day granularity),
not the History API. Ceiling: a re-synced day re-touches a few notes (cheap, dedup).
Upgrade path: switch the Gmail cursor to a `historyId` + `users.history.list` if the
volume ever makes the overlap matter.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import calendar as calendar_mod
from . import gmail as gmail_mod
from . import google_auth
from .sync import CalendarConnector, FetchResult, GmailConnector

# API roots.
_CAL_BASE = "https://www.googleapis.com/calendar/v3"
_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"

# Page sizes (Google caps: 2500 for calendar events, 500 for gmail list).
_CAL_PAGE = 250
_GMAIL_PAGE = 100

# Safety cap on internal (within-one-fetch) paging, mirroring sync._MAX_PAGES.
_MAX_INTERNAL_PAGES = 1000


# Raised when a Calendar syncToken has expired (HTTP 410); the caller retries with
# a full resync. Kept module-local so callers don't depend on requests' exceptions.
class SyncTokenExpired(Exception):
    pass


# The default HTTP GET seam: a single authenticated JSON GET against a Google API.
# Isolated (and injectable) so the fetch logic is testable without the network.
# Raises SyncTokenExpired on 410 so Calendar can fall back to a full resync.
def _http_get(url: str, token: str, params: Dict[str, Any]) -> Dict[str, Any]:
    import requests  # lazy: only needed for real calls (ships with the google extra)

    response = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    if response.status_code == 410:
        raise SyncTokenExpired(response.text)
    response.raise_for_status()
    return response.json()


# --- Calendar --------------------------------------------------------------

def _calendar_sync_from_days() -> int:
    try:
        return int(os.environ.get("GOOGLE_CALENDAR_SYNC_FROM_DAYS", "30"))
    except ValueError:
        return 30


# How far into the FUTURE the initial calendar pull reaches. Bounds open-ended
# recurring events (e.g. a daily reminder, a yearly birthday) so they can't
# expand years/decades ahead and flood the vault. Incremental syncs (syncToken)
# are unaffected.
def _calendar_sync_to_days() -> int:
    try:
        return int(os.environ.get("GOOGLE_CALENDAR_SYNC_TO_DAYS", "365"))
    except ValueError:
        return 365


# Page through calendar events for one sync. With a syncToken (cursor) Google
# returns only changes since last time; without one we bound the initial pull to
# a window [now - SYNC_FROM_DAYS, now + SYNC_TO_DAYS] so open-ended recurring
# events can't expand years into the future and pollute the vault.
def _calendar_pull(
    token: str, sync_token: Optional[str], calendar_id: str, http_get: Callable[..., Dict[str, Any]]
) -> FetchResult:
    items: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    url = f"{_CAL_BASE}/calendars/{calendar_id}/events"

    for _ in range(_MAX_INTERNAL_PAGES):
        params: Dict[str, Any] = {"maxResults": _CAL_PAGE, "singleEvents": True, "showDeleted": True}
        if sync_token:
            params["syncToken"] = sync_token
        else:
            since = datetime.now(timezone.utc) - timedelta(days=_calendar_sync_from_days())
            until = datetime.now(timezone.utc) + timedelta(days=_calendar_sync_to_days())
            params["timeMin"] = since.isoformat(timespec="seconds").replace("+00:00", "Z")
            params["timeMax"] = until.isoformat(timespec="seconds").replace("+00:00", "Z")
            params["orderBy"] = "startTime"
        if page_token:
            params["pageToken"] = page_token

        data = http_get(url, token, params)
        items.extend(data.get("items", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            # Last page carries the token to use next time.
            return FetchResult(items=items, next_cursor=data.get("nextSyncToken") or sync_token)

    return FetchResult(items=items, next_cursor=sync_token)


# Calendar fetch_fn: signature `(cursor) -> FetchResult`, matching what run_sync
# expects. `token_provider` / `http_get` are injectable for offline tests.
def calendar_fetch_fn(
    cursor: Optional[str],
    *,
    calendar_id: str = "primary",
    token_provider: Callable[[], str] = google_auth.get_access_token,
    http_get: Callable[..., Dict[str, Any]] = _http_get,
) -> FetchResult:
    token = token_provider()
    try:
        return _calendar_pull(token, cursor, calendar_id, http_get)
    except SyncTokenExpired:
        # Google invalidated the syncToken — start over with a bounded full pull.
        return _calendar_pull(token, None, calendar_id, http_get)


# --- Gmail -----------------------------------------------------------------

def _gmail_query() -> str:
    # Default bounds the FIRST sync AND drops the two biggest noise buckets
    # (Promotions + Social) server-side, for free, before we fetch anything — the
    # cheapest possible data reduction. The ingest-time triage policy still runs on
    # whatever remains. Narrow further (e.g. "is:important", "is:starred") via the
    # env knob; the triage policy classifies whatever the query lets through.
    return os.environ.get(
        "GOOGLE_GMAIL_QUERY", "-category:promotions -category:social newer_than:30d"
    ).strip()


# List message ids matching the query (+ after: watermark), paging internally.
def _gmail_list_ids(
    token: str, query: str, http_get: Callable[..., Dict[str, Any]]
) -> List[str]:
    ids: List[str] = []
    page_token: Optional[str] = None
    url = f"{_GMAIL_BASE}/users/me/messages"

    for _ in range(_MAX_INTERNAL_PAGES):
        params: Dict[str, Any] = {"maxResults": _GMAIL_PAGE}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token

        data = http_get(url, token, params)
        ids.extend(message["id"] for message in data.get("messages", []) if message.get("id"))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return ids


# Gmail fetch_fn: `(cursor) -> FetchResult`. cursor is an epoch-seconds watermark;
# we list matching ids since it, then fetch each message's metadata (headers +
# snippet + labelIds + internalDate — enough for the triage policy; no MIME body
# decode). We request the RFC list headers (List-Unsubscribe/List-Id) too so the
# classifier can spot bulk/newsletter mail the way mail providers do.
_METADATA_HEADERS = ["Subject", "From", "Date", "List-Unsubscribe", "List-Id"]


def gmail_fetch_fn(
    cursor: Optional[str],
    *,
    query: Optional[str] = None,
    token_provider: Callable[[], str] = google_auth.get_access_token,
    http_get: Callable[..., Dict[str, Any]] = _http_get,
    now: Optional[Callable[[], int]] = None,
) -> FetchResult:
    token = token_provider()
    now_epoch = (now or (lambda: int(time.time())))()

    effective_query = _gmail_query() if query is None else query
    if cursor:
        effective_query = f"{effective_query} after:{cursor}".strip()

    ids = _gmail_list_ids(token, effective_query, http_get)

    messages: List[Dict[str, Any]] = []
    for message_id in ids:
        messages.append(http_get(
            f"{_GMAIL_BASE}/users/me/messages/{message_id}",
            token,
            {"format": "metadata", "metadataHeaders": _METADATA_HEADERS},
        ))

    # Advance the watermark to now. run_sync's empty-page guard stops the loop on the
    # follow-up call (which returns no messages), so we never page forever.
    return FetchResult(items=messages, next_cursor=str(now_epoch))


# --- Live connector builders ----------------------------------------------
# Construct the sync.py connectors wired to the LIVE fetch functions above. These
# are what the CLI / MCP tools use; tests build the same connectors with a fake
# fetch_fn instead.

def live_calendar_connector(*, calendar_id: str = "primary") -> CalendarConnector:
    return CalendarConnector(fetch_fn=lambda cursor: calendar_fetch_fn(cursor, calendar_id=calendar_id))


def live_gmail_connector(
    *,
    ttl_days: Optional[float] = None,
    label_filter: Optional[str] = None,
    digest: bool = True,
    query: Optional[str] = None,
) -> GmailConnector:
    return GmailConnector(
        fetch_fn=lambda cursor: gmail_fetch_fn(cursor, query=query),
        ttl_days=ttl_days,
        label_filter=label_filter,
        digest=digest,
    )


# Live, read-only triage preview: fetch a batch of message metadata for `query`
# (defaults to the configured GOOGLE_GMAIL_QUERY; ignores the saved cursor so it's
# a fresh look) and return the classify-only plan (`gmail.plan_messages`). Writes
# nothing — this backs the `preview_gmail` MCP tool so an LLM can see the shape of
# the inbox (per-class counts, keepers) in one cheap call before syncing.
def live_gmail_preview(*, query: Optional[str] = None) -> Dict[str, Any]:
    result = gmail_fetch_fn(None, query=query)
    plan = gmail_mod.plan_messages(result.items)
    plan["query"] = _gmail_query() if query is None else query
    return plan
