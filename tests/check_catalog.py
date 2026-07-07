"""Self-check for the SCHOOL catalog. Run: python tests/check_catalog.py

Builds a fake SCHOOL tree in an isolated temp dir (no network, no API key, no
PDF deps -- uses .md files so title extraction is exercised via the filename
fallback) and verifies:
  - only document files are catalogued; venvs / hidden files / wrong types skipped
  - course + subpath are derived from the folder layout
  - byte-identical copies become duplicate records, NOT extra entries
  - descriptive names + heuristic doc types are generated
  - a second scan is incremental (everything skipped, nothing re-added)
  - editing a file in place refreshes its entry (no orphan, no new row)
  - list_courses / find_documents / stats / duplicates queries work
  - rename is reversible: apply renames files on disk, undo restores them, and a
    rescan after rename stays incremental (no new entries)

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="study_catalog_test_")
_SCHOOL = Path(_TMP) / "SCHOOL"
os.environ["DATA_DIR"] = str(Path(_TMP) / "data")
os.environ["SCHOOL_DIR"] = str(_SCHOOL)

from servers.knowledge import catalog  # noqa: E402


def ok(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


def write(rel, text):
    p = _SCHOOL / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# --- build a fake SCHOOL tree ----------------------------------------------
INTRO = "# Intro lecture\n\nWelcome to the course. " * 5
write("MATH225/lecture-1-intro.md", INTRO)
write("MATH225/quizzes/Quiz1_Solutions.md", "# Quiz 1 Solutions\n\nanswers " * 5)
write("PHYS234/griffiths-notes.md", "# Reading notes\n\nquantum stuff " * 5)
write("PHYS234/copy-of-intro.md", INTRO)            # byte-identical duplicate
write("MATH225/.venv/site-packages/junk.md", "should be skipped")  # excluded dir
write("MATH225/.DS_Store", "junk")                  # hidden file
write("PHYS234/data.csv", "1,2,3")                  # wrong extension

# --- 1. first scan ----------------------------------------------------------
print("scan (initial)")
r = catalog.scan()
ok("error" not in r, f"scan succeeded ({r.get('error')})")
ok(len(r["new"]) == 3, f"3 unique documents catalogued (got {len(r['new'])})")
ok(len(r["duplicates"]) == 1, f"1 duplicate copy recorded (got {len(r['duplicates'])})")
ok(_SCHOOL / "MATH225/.venv/site-packages/junk.md" not in
   [Path(p) for p in r["new"]], "venv contents not catalogued")

# --- 2. dedup + skip rules verified at the DB level ------------------------
print("dedup & exclusion")
docs = catalog.list_documents()
ok(len(docs) == 3, f"exactly 3 document rows (got {len(docs)})")
paths_str = " ".join(d["current_path"] for d in docs)
ok(".venv" not in paths_str, "no venv path entered")
ok("data.csv" not in paths_str, "non-document extension skipped")
dups = catalog.duplicates()
ok(len(dups) == 1 and "copy-of-intro.md" in dups[0]["duplicate"],
   "duplicate copy points at the redundant file, canonical kept")

# --- 3. descriptive names + doc types --------------------------------------
print("naming & typing")
by_name = {d["descriptive_name"]: d for d in docs}
sol = [d for d in docs if d["doc_type"] == "solutions"]
ok(len(sol) == 1, "Quiz1_Solutions classified as 'solutions'")
ok(all(d["descriptive_name"].startswith(d["course"] + " \u2014 ") for d in docs),
   "every descriptive name is prefixed with its course code")
ok(any(d["doc_type"] == "slides" for d in docs), "intro lecture classified as 'slides'")

# --- 4. course / subpath layout --------------------------------------------
print("course & subpath")
courses = {c["course"]: c["documents"] for c in catalog.list_courses()}
ok(courses.get("MATH225") == 2, f"MATH225 has 2 docs (got {courses.get('MATH225')})")
ok(courses.get("PHYS234") == 1, f"PHYS234 has 1 doc (got {courses.get('PHYS234')})")

# --- 5. incremental rescan --------------------------------------------------
print("incremental rescan")
r2 = catalog.scan()
ok(len(r2["new"]) == 0, "second scan adds nothing new")
ok(r2["skipped"] >= 3, f"unchanged files skipped without rehash (got {r2['skipped']})")
ok(len(catalog.list_documents()) == 3, "still 3 rows after rescan")

# --- 6. edit in place refreshes the entry (no orphan) ----------------------
print("edit in place")
hash_before = next(d for d in catalog.list_documents()
                   if "griffiths-notes" in d["current_path"])
write("PHYS234/griffiths-notes.md", "# Reading notes REVISED\n\nmore content here " * 8)
r3 = catalog.scan()
ok(r3["updated"] >= 1, "edited file reported as updated")
ok(len(catalog.list_documents()) == 3, "edit did not create an orphan row")

# --- 7. search queries ------------------------------------------------------
print("queries")
hits = catalog.find_documents("intro")
ok(any("intro" in h["current_path"].lower() for h in hits), "find_documents finds the intro lecture")
ok(all(h["course"] == "MATH225" for h in catalog.find_documents("intro", course="MATH225")),
   "course filter restricts results")
s = catalog.stats()
ok(s["total_documents"] == 3 and s["duplicate_copies"] == 1,
   "stats report 3 documents and 1 duplicate copy")
ok(isinstance(catalog.possible_duplicates(), list), "possible_duplicates returns a list")

# --- 8. rename round-trip (reversible) -------------------------------------
print("rename + undo")
plan = catalog.plan_renames()
ok(len(plan) == 3, f"3 files planned for rename (got {len(plan)})")
done = catalog.apply_renames()
ok(len(done) == 3, "3 files renamed on disk")
for item in done:
    ok(Path(item["to"]).exists() and not Path(item["from"]).exists(),
       f"file moved to descriptive name: {Path(item['to']).name}")
r4 = catalog.scan()
ok(len(r4["new"]) == 0, "rescan after rename adds nothing (paths tracked)")
reverted = catalog.undo_renames()
ok(len(reverted) == 3, "undo restored all 3 original filenames")
for item in done:
    ok(Path(item["from"]).exists() and not Path(item["to"]).exists(),
       f"original filename restored: {Path(item['from']).name}")

print("\nAll catalog self-checks passed.")
