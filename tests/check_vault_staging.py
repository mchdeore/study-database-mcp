"""Self-check for the staging importer. Run:
    python tests/check_vault_staging.py

Offline (hash embedder + SQLite + temp VAULT_DIR + temp STAGING_DIR). Verifies:

  import       - content files upsert into the vault at their declared `category`
  skip rules   - README.md and `_`-prefixed (_digest/_template) files are ignored
  no source_ref- a content file without source_ref is REPORTED, not imported/guessed
  events       - a dated item (`start`) yields an events row (shows on the timeline)
  search       - imported notes are searchable
  idempotent   - re-import unchanged = no-op; editing a file updates it IN PLACE
                 (same id, no duplicate)
  dry-run      - previews without writing anything

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_staging_test_")
os.environ["VAULT_DIR"] = str(Path(_TMP) / "vault")
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout, paths  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault.search import search  # noqa: E402
from servers.vault.staging import import_staging  # noqa: E402

STG = Path(_TMP) / "incoming"


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


def write(relpath, content):
    path = STG / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


HW1 = """---
title: "MATH101 HW1"
category: 40-areas/school/math101
course: MATH101
type: assignment
source: school
source_ref: school://math101/assignment/hw1
created: "2026-01-10T00:00:00-05:00"
start: "2026-01-17T23:59:00-05:00"
tags: [homework, limits]
importance: 3
status: active
---

# HW1
## Problems
Do problems 1-5 on limits and derivatives from chapter 1.
"""

SYLLABUS = """---
title: "MATH101 Syllabus"
category: 40-areas/school/math101
course: MATH101
type: syllabus
source: school
source_ref: school://math101/syllabus/outline
created: "2026-01-05T00:00:00-05:00"
tags: [syllabus]
importance: 4
status: active
---

# MATH101 Syllabus
## Grading
Homework 20%, midterm 30%, final 50%. Weekly Friday quizzes on calculus.
"""

NOREF = """---
title: "No ref note"
category: 40-areas/school/math101
type: note
source: school
status: active
---

# No ref
This file has no source_ref and must be reported, not imported.
"""

ensure_layout()
database = get_db()
database.migrate()

write("school/math101/assignment-hw1.md", HW1)
write("school/math101/syllabus.md", SYLLABUS)
write("school/math101/noref.md", NOREF)
write("school/README.md", "# instructions\nNot imported.\n")
write("school/math101/_digest.md", "<!-- digest -->\n# Digest\nScratch summary, not imported.\n")


# --- import ----------------------------------------------------------------
print("import")
report = import_staging("school", root=STG, database=database)
ok(report["counts"]["created"] == 2, "the two content files were imported (created)")
ok(report["counts"]["skipped"] == 1, "exactly one file was skipped (the no-source_ref file)")
ok(report["skipped"][0]["path"].endswith("noref.md"), "the skipped file is the no-source_ref one")
ok(len(database.list_documents()) == 2, "README and _digest were NOT imported")

hw_id = database.find_document_by_source_ref("school://math101/assignment/hw1")
ok(hw_id is not None, "the assignment resolves by its source_ref")
hw = database.get_document(hw_id)
ok(hw["category"] == "40-areas/school/math101", "it landed in its declared category")
ok(hw["path"].startswith("40-areas/school/math101/"), "the vault path is under that category")
note = Note.load(paths()["vault"] / hw["path"])
ok(note.frontmatter.get("course") == "MATH101" and note.frontmatter.get("type") == "assignment",
   "all staged frontmatter was carried through")


# --- events + search -------------------------------------------------------
print("events + search")
events = database.list_events()
ok(len(events) == 1 and events[0]["document_id"] == hw_id, "the dated assignment produced one event")
ok(events[0]["start_at"] == "2026-01-17T23:59:00-05:00", "the due date became the event start")

results = search("MATH101 grading homework midterm final quizzes", k=5, database=database)["results"]
ok(any(r["citation"]["document_id"] == database.find_document_by_source_ref("school://math101/syllabus/outline")
       for r in results), "the imported syllabus is searchable")


# --- idempotent re-import --------------------------------------------------
print("idempotent re-import")
again = import_staging("school", root=STG, database=database)
ok(again["counts"]["unchanged"] == 2 and again["counts"]["created"] == 0, "re-import of unchanged files is a no-op")
ok(len(database.list_documents()) == 2, "no duplicates created on re-import")

write("school/math101/assignment-hw1.md", HW1.replace("problems 1-5", "problems 1-8"))
edited = import_staging("school", root=STG, database=database)
ok(edited["counts"]["updated"] == 1, "an edited file updates in place")
ok(database.find_document_by_source_ref("school://math101/assignment/hw1") == hw_id, "same stable id after edit")
ok(len(database.list_documents()) == 2, "still no duplicate after the edit")


# --- dry-run writes nothing ------------------------------------------------
print("dry-run")
write("school/math101/quiz-q1.md", HW1.replace("assignment/hw1", "quiz/q1")
      .replace("MATH101 HW1", "MATH101 Quiz 1"))
preview = import_staging("school", root=STG, dry_run=True, database=database)
ok("school://math101/quiz/q1" in preview["created"], "dry-run lists the new file as would-create")
ok(database.find_document_by_source_ref("school://math101/quiz/q1") is None, "dry-run wrote nothing")
applied = import_staging("school", root=STG, database=database)
ok(applied["counts"]["created"] == 1 and
   database.find_document_by_source_ref("school://math101/quiz/q1") is not None,
   "a real import then creates it")


database.close()
print("\nALL VAULT STAGING-IMPORT CHECKS PASSED")
