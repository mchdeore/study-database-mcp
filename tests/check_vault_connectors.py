"""Self-check for connectors, Phase 4 batch 1. Run:
    python tests/check_vault_connectors.py

Offline (hash embedder + SQLite + temp VAULT_DIR), fixture event dicts -- no live
OAuth or network. Verifies:

  4.2 transform    - a Google Calendar event dict maps to the right note fields
  4.2 sync         - events become notes under the calendar category + `events` rows
  4.2 events       - the derived events table carries start/end and the doc id
  4.3 gmail        - a keeper maps to a clean, importance-scored note in the mail
                     category with a class-based TTL; label filtering excludes
                     non-matching mail; an expired mail note is archived by the TTL
                     policy; the triage policy skips promotions and rolls bulk/list
                     mail into a weekly digest (per-class counts in the report)
  4.5 dedup        - re-syncing the SAME event updates its note in place (no dup);
                     an unchanged re-sync is a no-op
  skip rules       - cancelled events and events with no start are skipped
  search           - a synced event is findable
  rebuild          - events + source_ref survive a full rebuild (derived from vault)

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_conn_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout, paths  # noqa: E402
from servers.vault.index import rebuild_index  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault.search import search  # noqa: E402
from servers.vault.connectors import calendar as cal  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# --- fixture Google Calendar event resources -------------------------------
STANDUP = {
    "id": "evt-standup-001",
    "summary": "Team standup",
    "description": "Daily sync on progress and blockers.",
    "location": "Zoom",
    "start": {"dateTime": "2026-07-01T09:30:00-04:00"},
    "end": {"dateTime": "2026-07-01T09:45:00-04:00"},
    "htmlLink": "https://calendar.google.com/event?eid=abc",
    "status": "confirmed",
}
REVIEW = {
    "id": "evt-review-002",
    "summary": "Design review",
    "description": "Review the new architecture proposal.",
    "start": {"date": "2026-07-04"},  # all-day
    "end": {"date": "2026-07-05"},
    "htmlLink": "https://calendar.google.com/event?eid=def",
    "status": "confirmed",
}
CANCELLED = {"id": "evt-x", "summary": "Scrapped", "status": "cancelled",
             "start": {"dateTime": "2026-07-02T10:00:00-04:00"}}
NO_START = {"id": "evt-y", "summary": "Someday maybe", "status": "confirmed"}

STANDUP_REF = "gcal://event/evt-standup-001"
REVIEW_REF = "gcal://event/evt-review-002"

ensure_layout()
database = get_db()
database.migrate()


# --- 4.2 transform ---------------------------------------------------------
print("4.2 transform")
fields = cal.event_to_fields(STANDUP)
ok(fields["title"] == "Team standup", "summary maps to title")
ok(fields["start"] == "2026-07-01T09:30:00-04:00", "timed start extracted from dateTime")
ok(fields["end"] == "2026-07-01T09:45:00-04:00", "timed end extracted")
ok(fields["source_ref"] == STANDUP_REF, "source_ref is gcal://event/<id>")
ok("Team standup" in fields["body"] and STANDUP_REF in fields["body"],
   "body carries the title and a source_ref marker (keeps bodies unique)")
ok(cal.event_to_fields(REVIEW)["start"] == "2026-07-04", "all-day start extracted from date")


# --- 4.2 sync: events -> notes + events rows -------------------------------
print("4.2 sync")
summary = cal.sync_events([STANDUP, REVIEW, CANCELLED, NO_START], database=database)
ok(summary["created"] == 2, "two real events created as notes")
ok(summary["skipped"] == 2, "cancelled event and no-start event were skipped")
ok(len(database.list_documents()) == 2, "exactly two calendar notes exist")

standup_id = database.find_document_by_source_ref(STANDUP_REF)
review_id = database.find_document_by_source_ref(REVIEW_REF)
ok(standup_id and review_id and standup_id != review_id, "each event resolves to its own document")

standup_doc = database.get_document(standup_id)
ok(standup_doc["path"].startswith("40-areas/calendar/"), "calendar notes land in the calendar category")
standup_note = Note.load(paths()["vault"] / standup_doc["path"])
ok(standup_note.frontmatter["source"] == "google", "note frontmatter records google as the origin")
ok(standup_note.frontmatter["source_ref"] == STANDUP_REF, "note frontmatter carries the source_ref")

events = database.list_events()
ok(len(events) == 2, "the derived events table has one row per event")
standup_event = next(e for e in events if e["document_id"] == standup_id)
ok(standup_event["start_at"] == "2026-07-01T09:30:00-04:00", "event row carries the start time")
ok(standup_event["end_at"] == "2026-07-01T09:45:00-04:00", "event row carries the end time")
ok(standup_event["id"] == f"{standup_id}#event", "event id is derived from the document id")


# --- search: a synced event is findable ------------------------------------
print("search")
results = search("Team standup daily sync progress blockers Zoom", k=5, database=database)["results"]
sources = [hit["citation"]["source"] for hit in results]
ok(standup_doc["path"] in sources, "the synced standup event is findable via search")


# --- 4.5 dedup: unchanged re-sync is a no-op -------------------------------
print("4.5 dedup (unchanged)")
again = cal.sync_events([STANDUP, REVIEW], database=database)
ok(again["unchanged"] == 2 and again["created"] == 0, "re-syncing identical events changes nothing")
ok(len(database.list_documents()) == 2, "no duplicate notes created on re-sync")


# --- 4.5 dedup: a changed event updates the SAME note in place -------------
print("4.5 dedup (update in place)")
moved = dict(STANDUP)
moved["summary"] = "Team standup (moved)"
moved["start"] = {"dateTime": "2026-07-01T10:00:00-04:00"}
moved["end"] = {"dateTime": "2026-07-01T10:15:00-04:00"}

changed = cal.sync_events([moved], database=database)
ok(changed["updated"] == 1, "the changed event reports an in-place update")
ok(len(database.list_documents()) == 2, "still exactly two notes (no duplicate)")
ok(database.find_document_by_source_ref(STANDUP_REF) == standup_id, "the note keeps its stable id")
ok(database.get_document(standup_id)["title"] == "Team standup (moved)", "the title was updated")

updated_event = next(e for e in database.list_events() if e["document_id"] == standup_id)
ok(updated_event["start_at"] == "2026-07-01T10:00:00-04:00", "the event's start time was updated")


# --- rebuild: events + source_ref survive (derived from the vault) ---------
print("rebuild durability")
rebuild_index(database=database)
ok(len(database.list_events()) == 2, "events survive a rebuild (re-derived from frontmatter)")
ok(database.find_document_by_source_ref(STANDUP_REF) == standup_id,
   "source_ref still resolves to the same note after rebuild")
rebuilt_event = next(e for e in database.list_events() if e["document_id"] == standup_id)
ok(rebuilt_event["start_at"] == "2026-07-01T10:00:00-04:00", "the updated start time survived the rebuild")


# --- 4.3 Gmail adapter (ephemeral) -----------------------------------------
from datetime import datetime, timezone  # noqa: E402
from servers.vault.connectors import gmail  # noqa: E402
from servers.vault import archive  # noqa: E402

IMPORTANT_MAIL = {
    "id": "msg-100",
    "threadId": "t-1",
    "labelIds": ["INBOX", "IMPORTANT"],
    "snippet": "Your order shipped and will arrive Tuesday.",
    "payload": {"headers": [
        {"name": "Subject", "value": "Your order shipped"},
        {"name": "From", "value": "Store <no-reply@store.com>"},
        {"name": "Date", "value": "Mon, 30 Jun 2026 12:00:00 -0400"},
    ]},
}
NEWSLETTER = {
    "id": "msg-200",
    "labelIds": ["INBOX"],
    "snippet": "This week in tech.",
    "payload": {"headers": [{"name": "Subject", "value": "Weekly newsletter"}]},
}
MAIL_REF = "gmail://msg/msg-100"

print("4.3 gmail transform")
gfields = gmail.message_to_fields(IMPORTANT_MAIL)
ok(gfields["title"] == "Your order shipped", "subject maps to title")
ok(gfields["source_ref"] == MAIL_REF, "source_ref is gmail://msg/<id>")
ok("shipped" in gfields["body"] and MAIL_REF in gfields["body"], "body carries content + source_ref marker")

print("4.3 gmail sync + label filter")
mail_summary = gmail.sync_messages(
    [IMPORTANT_MAIL, NEWSLETTER], label_filter="IMPORTANT",
    now=datetime(2026, 6, 30, tzinfo=timezone.utc), database=database,
)
ok(mail_summary["created"] == 1, "only the IMPORTANT-labeled message was ingested")
ok(mail_summary["skipped"] == 1, "the unlabeled newsletter was filtered out")

mail_id = database.find_document_by_source_ref(MAIL_REF)
ok(mail_id is not None, "the mail note exists")
mail_doc = database.get_document(mail_id)
ok(mail_doc["path"].startswith("50-resources/mail/"), "mail lands in the ephemeral mail category")
ok(mail_doc["expires"], "the mail note carries an expiry (ephemeral TTL)")

print("4.3 gmail ephemeral TTL feeds the existing archival policy")
# Re-sync the same message with an expiry already in the past, then run TTL.
gmail.sync_messages([IMPORTANT_MAIL], ttl_days=30,
                    now=datetime(2020, 1, 1, tzinfo=timezone.utc), database=database)
ok(database.get_document(mail_id)["status"] == "active", "mail is active before pruning")
archive.run_ttl(dry_run=False, database=database)
ok(database.get_document(mail_id)["status"] == "archived",
   "an expired mail note is archived by the TTL policy (ephemeral by default)")

# --- 4.3 gmail triage policy (skip noise, digest bulk, per-class report) ----
print("4.3 gmail triage policy")
PROMO = {"id": "msg-promo", "labelIds": ["INBOX", "CATEGORY_PROMOTIONS"],
         "snippet": "25% off flights", "payload": {"headers": [
             {"name": "Subject", "value": "Discount Tuesday"}]}}
ALERT = {"id": "msg-alert", "labelIds": ["INBOX", "CATEGORY_UPDATES"],
         "snippet": "Clio Data Scientist role", "payload": {"headers": [
             {"name": "Subject", "value": "Data Scientist at Clio"},
             {"name": "From", "value": "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"},
             {"name": "List-Unsubscribe", "value": "<https://linkedin.com/unsub>"}]}}
triage = gmail.sync_messages([PROMO, ALERT], now=datetime(2026, 7, 8, tzinfo=timezone.utc),
                             database=database)
ok(triage["classes"].get("promotion") == 1 and triage["classes"].get("bulk") == 1,
   "sync_messages reports a per-class breakdown (promotion + bulk)")
ok(database.find_document_by_source_ref("gmail://msg/msg-promo") is None,
   "the promotion is skipped -- never written to the vault")
ok(triage["digested"] == 1 and len(triage["digests"]) == 1,
   "the bulk job alert is rolled into a weekly digest, not its own note")
ok(database.find_document_by_source_ref("gmail://digest/2026-W28") is not None,
   "the weekly digest note exists (bulk mail aggregated)")


database.close()
print("\nALL VAULT PHASE 4 (BATCH 1: CALENDAR + GMAIL) CHECKS PASSED")
