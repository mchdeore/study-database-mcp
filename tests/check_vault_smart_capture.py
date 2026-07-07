"""Self-check for smart capture + note consolidation. Run:
    python tests/check_vault_smart_capture.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies the "don't keep two
copies of the same thing" behavior:

  exact dup   - byte-identical content is refused (points at the original)
  warn        - a near-identical capture still creates but flags `similar_to`
  skip        - on_similar='skip' does NOT create a duplicate; returns the match
  append      - on_similar='append' consolidates into the matching note
  force_new   - force_new=True always makes a separate note
  unrelated   - an unrelated capture is created with no similar flag
  append_to_note - explicit consolidation into a note by id; clear error on bad ref

Thresholds are passed explicitly so the check is deterministic regardless of the
embedder's absolute similarity scale. No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_smartcap_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault.config import paths  # noqa: E402
from servers.vault import capture as cap  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


database = get_db()
database.migrate()


def doc_count():
    return len(database.list_documents())


def body_of(document_id):
    return Note.load(paths()["vault"] / database.get_document(document_id)["path"]).body


MITO = "The mitochondria is the powerhouse of the cell and produces ATP via cellular respiration."

# --- create the first note --------------------------------------------------
print("create + exact dup")
first = cap.capture(MITO, category="50-resources", title="Mitochondria", database=database)
ok("error" not in first and first.get("id"), "first note is created")
mito_id = first["id"]
ok(doc_count() == 1, "one note exists")

# Byte-identical re-capture is refused and points at the original.
dup = cap.capture(MITO, category="50-resources", title="Mitochondria", database=database)
ok(dup.get("duplicate_of") == mito_id, "byte-identical content is refused (duplicate_of the original)")
ok(doc_count() == 1, "exact duplicate added no note")

# --- warn (default): near-identical still creates but is flagged -----------
print("on_similar='warn'")
warn = cap.capture(MITO + " It is a double-membrane organelle.", category="50-resources",
                   title="Mito facts", similarity_threshold=0.5, database=database)
ok(warn.get("id"), "warn created a note")
ok(warn.get("similar_to", {}).get("id") == mito_id, "warn flags the similar existing note")
ok(doc_count() == 2, "warn created a separate note (2 total)")

# --- skip: do not create a duplicate ---------------------------------------
print("on_similar='skip'")
skip = cap.capture(MITO + " extra tail.", category="50-resources", title="Mito again",
                   on_similar="skip", similarity_threshold=0.5, database=database)
ok(skip.get("created") is False, "skip did not create a note")
ok(skip.get("similar_to", {}).get("id") in (mito_id, warn["id"]), "skip returns the matched note")
ok(doc_count() == 2, "skip added no note")

# --- append: consolidate into the matching note ----------------------------
print("on_similar='append'")
appended = cap.capture(
    "Mitochondria produce ATP through cellular respiration; it is the powerhouse of the cell.",
    category="50-resources", on_similar="append", similarity_threshold=0.3, database=database,
)
ok(appended.get("appended") is True, "append consolidated into an existing note")
ok(doc_count() == 2, "append created no new note")
ok("through cellular respiration" in body_of(appended["id"]), "the appended text is in the matched note")

# --- force_new: always a separate note -------------------------------------
print("force_new=True")
forced = cap.capture(MITO + " (duplicate on purpose)", category="50-resources",
                     title="Forced copy", force_new=True, similarity_threshold=0.5, database=database)
ok(forced.get("id") and "similar_to" not in forced, "force_new creates without a similar flag")
ok(doc_count() == 3, "force_new added a new note")

# --- unrelated: created cleanly, no similar flag ---------------------------
print("unrelated")
groceries = cap.capture("Grocery list: milk, eggs, and sourdough bread for the week.",
                        category="00-inbox", similarity_threshold=0.5, database=database)
ok(groceries.get("id") and "similar_to" not in groceries, "an unrelated note is created with no similar flag")
ok(doc_count() == 4, "unrelated note added")

# --- append_to_note: explicit consolidation --------------------------------
print("append_to_note")
res = cap.append_to_note(mito_id, "First described by Richard Altmann in 1890.", database=database)
ok(res.get("appended") is True, "append_to_note appends to the target note")
ok("Altmann" in body_of(mito_id), "the appended text is present in the note")
ok(cap.append_to_note("no-such-note", "x", database=database).get("error"),
   "append_to_note errors clearly on an unknown ref")


database.close()
print("\nALL VAULT SMART-CAPTURE CHECKS PASSED")
