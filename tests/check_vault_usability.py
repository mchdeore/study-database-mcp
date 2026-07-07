"""Self-check for usability features (read + discovery). Run:
    python tests/check_vault_usability.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies the ease-of-use
layer that a real LLM/user leans on:

  get_note   - read a whole note by id / vault path / source_ref; clear error on a
               bad ref; long bodies truncate with a message
  timeline   - list events (from note frontmatter) ordered by start; window filter;
               each event resolves back to its note via get_note
  search     - results carry status so archived hits are recognizable
  category   - a category filter matches a folder AND its subcategories (prefix)

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_usability_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout  # noqa: E402
from servers.vault import archive  # noqa: E402
from servers.vault import capture as capture_mod  # noqa: E402
from servers.vault.connectors import calendar as cal  # noqa: E402
from servers.vault.search import search, get_note, timeline  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


ensure_layout()
database = get_db()
database.migrate()

# Seed content: a project note, a long inbox note, and two calendar events.
project = capture_mod.capture(
    "The quantum widget calibration protocol requires careful tuning and alignment.",
    category="30-projects", title="Quantum widget protocol", database=database,
)
capture_mod.capture("Grocery list: milk, eggs, and sourdough bread.",
                    category="00-inbox", database=database)
long_note = capture_mod.capture("padding " * 200, category="00-inbox",
                                title="Long note", database=database)

BUDGET = {
    "id": "evt-budget-1", "summary": "Budget planning meeting", "status": "confirmed",
    "description": "Discuss the annual budget and the quarterly forecast.",
    "start": {"dateTime": "2026-07-10T14:00:00-04:00"},
    "end": {"dateTime": "2026-07-10T15:00:00-04:00"},
}
DENTIST = {
    "id": "evt-dentist-2", "summary": "Dentist appointment", "status": "confirmed",
    "description": "Routine checkup and cleaning.",
    "start": {"dateTime": "2026-07-20T09:00:00-04:00"},
    "end": {"dateTime": "2026-07-20T09:30:00-04:00"},
}
cal.sync_events([BUDGET, DENTIST], database=database)


# --- get_note: read a whole note by several kinds of reference --------------
print("get_note")
by_id = get_note(project["id"], database=database)
ok(by_id.get("id") == project["id"], "get_note resolves a document id")
ok("quantum widget" in by_id["body"], "get_note returns the full note body")
ok(by_id["status"] == "active" and by_id["frontmatter"].get("id") == project["id"],
   "get_note reports status and frontmatter")

by_path = get_note(project["path"], database=database)
ok(by_path.get("id") == project["id"], "get_note resolves a vault path")

by_ref = get_note("gcal://event/evt-budget-1", database=database)
ok(by_ref.get("id") and "Budget planning" in by_ref["body"], "get_note resolves a connector source_ref")

missing = get_note("no-such-note", database=database)
ok("error" in missing and "hint" in missing, "get_note gives a clear error + hint for a bad ref")

truncated = get_note(long_note["id"], database=database, max_chars=100)
ok(truncated.get("truncated") is True and len(truncated["body"]) == 100,
   "get_note truncates a long body and flags it")


# --- timeline: events ordered by start, resolvable, window-filterable -------
print("timeline")
whole = timeline(database=database)
ok(whole["count"] == 2, "timeline lists both events")
ok(whole["events"][0]["title"] == "Budget planning meeting", "timeline is ordered soonest-first")
first_doc = whole["events"][0]["document_id"]
ok(get_note(first_doc, database=database).get("id") == first_doc, "a timeline event resolves via get_note")

window = timeline(start="2026-07-15", end="2026-07-31", database=database)
ok(window["count"] == 1 and window["events"][0]["title"] == "Dentist appointment",
   "timeline filters to a date window")


# --- category filter: prefix-matches subcategories --------------------------
print("category prefix filter")
top = search("Budget planning annual budget forecast meeting", k=10,
             filters={"category": "40-areas"}, database=database)["results"]
ok(any("40-areas/calendar" in hit["citation"]["source"] for hit in top),
   "category='40-areas' matches the '40-areas/calendar' subcategory")

exact = search("Budget planning annual budget forecast", k=10,
               filters={"category": "40-areas/calendar"}, database=database)["results"]
ok(exact, "an exact subcategory filter still returns results")

other = search("Budget planning annual budget", k=10,
               filters={"category": "30-projects"}, database=database)["results"]
ok(all("40-areas" not in hit["citation"]["source"] for hit in other),
   "a different category excludes the calendar notes")


# --- search results carry status (archived hits are recognizable) -----------
print("search status")
active_hits = search("quantum widget calibration protocol tuning", k=5, database=database)["results"]
ok(active_hits and active_hits[0]["citation"]["status"] == "active",
   "search results carry an active status")

archive.archive_documents([project["id"]], reason="usability test", dry_run=False, database=database)
after = search("quantum widget calibration protocol tuning", k=5, database=database)["results"]
archived_hit = next((h for h in after if h["citation"]["document_id"] == project["id"]), None)
ok(archived_hit is not None and archived_hit["citation"]["status"] == "archived",
   "an archived note still surfaces but is marked status=archived")


database.close()
print("\nALL VAULT USABILITY CHECKS PASSED")
