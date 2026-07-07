"""Self-check for connector cursors + the incremental sync runner, Phase 4 batch 2.
Run:  python tests/check_vault_sync.py

Offline (hash embedder + SQLite + temp VAULT_DIR), fixture "fetch" -- no network.
Verifies the FETCH/INGEST separation and the cursor loop:

  4.4 cursors   - set/get round-trip; survives reload; unknown source -> None
  4.6 first sync- pages through ALL new items in one run, ingests, saves the cursor
  4.6 caught up - a re-sync with the saved cursor fetches nothing
  4.6 incremental- a newly-appeared page is picked up from the saved cursor
  4.6 full/idempotent - full=True replays from the start; source_ref dedup means
                        NO duplicate notes are created

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_sync_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout  # noqa: E402
from servers.vault.connectors import cursors  # noqa: E402
from servers.vault.connectors.sync import FetchResult, CalendarConnector, run_sync  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


def event(event_id, summary, day):
    return {
        "id": event_id, "summary": summary, "status": "confirmed",
        "start": {"dateTime": f"2026-07-{day:02d}T09:00:00-04:00"},
        "end": {"dateTime": f"2026-07-{day:02d}T10:00:00-04:00"},
    }


ensure_layout()
database = get_db()
database.migrate()


# --- 4.4 cursor sidecar round-trip -----------------------------------------
print("4.4 cursors")
ok(cursors.get_cursor("nope") is None, "unknown source has no cursor")
cursors.set_cursor("demo", "tok-1", items=5)
ok(cursors.get_cursor("demo") == "tok-1", "set then get returns the cursor")
ok(cursors.load_cursors()["demo"]["items"] == 5, "the item count is recorded")
ok(cursors.load_cursors()["demo"]["last_sync"], "a last_sync timestamp is stamped")


# --- fake provider: pages keyed by cursor (mutable so we can add data later) --
A, B, C, D = event("ev-a", "Alpha meeting", 10), event("ev-b", "Beta review", 11), \
             event("ev-c", "Gamma sync", 12), event("ev-d", "Delta planning", 13)

pages = {
    None: FetchResult(items=[A, B], next_cursor="cur-1"),
    "cur-1": FetchResult(items=[C], next_cursor="cur-2"),
    "cur-2": FetchResult(items=[], next_cursor="cur-2"),  # caught up
}


def fake_fetch(cursor):
    return pages.get(cursor, FetchResult(items=[], next_cursor=cursor))


connector = CalendarConnector(fetch_fn=fake_fetch)


# --- 4.6 first sync pages through everything --------------------------------
print("4.6 first sync (pages through all new items)")
first = run_sync(connector, database=database)
ok(first["fetched"] == 3, "first sync fetched all 3 items across pages")
ok(first["pages"] == 2, "it paged twice (two non-empty pages)")
ok(first["created"] == 3, "all 3 became notes")
ok(first["cursor"] == "cur-2", "the final cursor was saved")
ok(cursors.get_cursor("google_calendar") == "cur-2", "the cursor persisted to the sidecar")
ok(len(database.list_documents()) == 3, "three calendar notes exist")


# --- 4.6 caught up: nothing new ---------------------------------------------
print("4.6 caught up")
again = run_sync(connector, database=database)
ok(again["fetched"] == 0, "a re-sync from the saved cursor fetches nothing")
ok(len(database.list_documents()) == 3, "no new notes")


# --- 4.6 incremental: a new page appears ------------------------------------
print("4.6 incremental pickup")
pages["cur-2"] = FetchResult(items=[D], next_cursor="cur-3")
pages["cur-3"] = FetchResult(items=[], next_cursor="cur-3")
incremental = run_sync(connector, database=database)
ok(incremental["fetched"] == 1 and incremental["created"] == 1, "only the new item was pulled + created")
ok(incremental["cursor"] == "cur-3", "cursor advanced")
ok(len(database.list_documents()) == 4, "now four notes")


# --- 4.6 full resync is idempotent (source_ref dedup) -----------------------
print("4.6 full resync (idempotent)")
full = run_sync(connector, database=database, full=True)
ok(full["fetched"] == 4, "full resync replayed all 4 items from the start")
ok(full.get("created", 0) == 0 and full.get("unchanged", 0) == 4, "replayed items were unchanged, not re-created")
ok(len(database.list_documents()) == 4, "still four notes -- no duplicates from a full replay")


database.close()
print("\nALL VAULT PHASE 4 (BATCH 2: SYNC) CHECKS PASSED")
