"""Incremental connector sync runner (build step 4.6 core).

A connector has two halves, deliberately separated so the risky-but-untestable part
is a thin injected seam:

  - FETCH: talk to the provider's API and return a page of raw items + the next
    cursor. This is network + auth (Google), so it's an **injected function**
    (`fetch_fn`) -- real in production, a fixture in tests.
  - INGEST: normalize items into vault notes. This is our already-tested adapter
    (`calendar.sync_events` / `gmail.sync_messages`), which upserts by `source_ref`
    (so re-syncing the same item never duplicates it).

`run_sync` ties them together with the cursor sidecar: start from the saved cursor
(or the beginning for a full resync), page until caught up (empty page or the cursor
stops advancing), ingest each page, and persist the final cursor. Because ingest is
source_ref-deduped, an interrupted or replayed sync is safe and idempotent.

The live Google fetch + OAuth is build step 4.1: implement `fetch_fn` for each
service and plug it into the connectors below. Everything here is offline-tested with
a fake fetch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from ..db import VaultDB, get_db
from . import calendar as calendar_mod
from . import cursors
from . import gmail as gmail_mod

# Safety cap so a misbehaving provider (cursor that never settles) can't page forever.
_MAX_PAGES = 1000


# One page of fetched items plus the cursor to use for the NEXT page. `next_cursor`
# of None (or equal to the current cursor) means "no more / caught up".
@dataclass
class FetchResult:
    items: List[Dict[str, Any]]
    next_cursor: Optional[str] = None


# The shape run_sync needs. Concrete connectors below satisfy it; a test fake does too.
class Connector(Protocol):
    name: str

    def fetch(self, cursor: Optional[str]) -> FetchResult: ...

    def ingest(self, items: List[Dict[str, Any]], database: VaultDB) -> Dict[str, Any]: ...


# Google Calendar connector: fetch is injected (Google API in prod / fixture in tests);
# ingest is the tested calendar adapter.
@dataclass
class CalendarConnector:
    fetch_fn: Callable[[Optional[str]], FetchResult]
    name: str = "google_calendar"

    def fetch(self, cursor: Optional[str]) -> FetchResult:
        return self.fetch_fn(cursor)

    def ingest(self, items: List[Dict[str, Any]], database: VaultDB) -> Dict[str, Any]:
        return calendar_mod.sync_events(items, database=database)


# Gmail connector: injected fetch; ingest is the tested gmail adapter (ephemeral TTL +
# optional label filter carried on the connector).
@dataclass
class GmailConnector:
    fetch_fn: Callable[[Optional[str]], FetchResult]
    ttl_days: Optional[float] = None  # None = per-class triage retention (recommended)
    label_filter: Optional[str] = None
    digest: bool = True               # roll bulk/list mail into a weekly digest note
    name: str = "gmail"

    def fetch(self, cursor: Optional[str]) -> FetchResult:
        return self.fetch_fn(cursor)

    def ingest(self, items: List[Dict[str, Any]], database: VaultDB) -> Dict[str, Any]:
        return gmail_mod.sync_messages(
            items, ttl_days=self.ttl_days, label_filter=self.label_filter,
            digest=self.digest, database=database,
        )


# Sum the integer fields of per-page ingest summaries (created/updated/unchanged/...)
# into a run total.
def _accumulate(total: Dict[str, int], summary: Dict[str, Any]) -> None:
    for key, value in summary.items():
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


# Run one incremental sync for a connector: page from the saved cursor (or from the
# start when full=True) until caught up, ingesting each page and advancing the cursor.
# Persists the final cursor to the sidecar. Returns a report (items fetched, pages,
# final cursor, and the aggregated ingest counts).
def run_sync(
    connector: Connector,
    *,
    database: Optional[VaultDB] = None,
    full: bool = False,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    cursor = None if full else cursors.get_cursor(connector.name)
    totals: Dict[str, int] = {}
    fetched = 0
    pages = 0

    while pages < _MAX_PAGES:
        result = connector.fetch(cursor)

        if result.items:
            _accumulate(totals, connector.ingest(result.items, database))
            fetched += len(result.items)
            pages += 1

        # Stop when the provider signals "no more": empty page, or the cursor didn't
        # advance. Otherwise move to the next page.
        if result.next_cursor is None or result.next_cursor == cursor:
            if result.next_cursor is not None:
                cursor = result.next_cursor
            break
        cursor = result.next_cursor
        if not result.items:
            # Cursor advanced but page was empty -- keep the new cursor and stop.
            break

    saved = cursors.set_cursor(connector.name, cursor, items=fetched)
    return {
        "source": connector.name,
        "fetched": fetched,
        "pages": pages,
        "cursor": cursor,
        "last_sync": saved["last_sync"],
        **totals,
    }
