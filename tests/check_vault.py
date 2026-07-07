"""Self-check for the vault server, Phase 0. Run: python tests/check_vault.py

Verifies the Phase 0 acceptance criteria with zero network access (offline hash
embedder + SQLite backend, isolated temp VAULT_DIR):

  0.1 config & paths        - taxonomy folders are created
  0.2 frontmatter           - parse/serialize round-trip + clear errors
  0.3 note model            - load/save, stable id
  0.4 walker (incremental)  - new/changed/deleted detection via the indexer
  0.5 db layer + 0.6 schema - migrate is idempotent, expected indexes exist
  0.7 indexer               - documents/chunks/links loaded; incremental
  0.8 rebuild-index         - runs clean
  0.9 rebuild contract      - incremental DB and a fresh rebuild give identical
                              search results (the keystone)

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Fully-offline environment BEFORE importing modules that read config at runtime.
_TMP = tempfile.mkdtemp(prefix="vault_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault import config as cfg  # noqa: E402
from servers.vault import frontmatter as fm  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault.db import get_db  # noqa: E402
from servers.vault.db_sqlite import EXPECTED_INDEXES  # noqa: E402
from servers.vault.index import index, rebuild_index, extract_wikilinks  # noqa: E402
from servers.vault.search import search  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# --- 0.1 config & paths ----------------------------------------------------
print("0.1 config & paths")
resolved = cfg.ensure_layout()
for folder in cfg.TAXONOMY_FOLDERS:
    ok(resolved[folder].is_dir(), f"taxonomy folder exists: {folder}")
ok(resolved["system"].is_dir(), ".vault system folder exists")
ok(resolved["db_sqlite"].parent == resolved["system"], "db lives under .vault/")


# --- 0.2 frontmatter -------------------------------------------------------
print("0.2 frontmatter")
sample = {
    "id": "01ABC",
    "title": "Kitchen reno: budget",   # has a colon -> must be quoted
    "tags": ["home", "money"],
    "importance": 3,
    "pinned": False,
    "expires": None,
    "count": 0,
}
roundtrip = fm.parse_block(fm.serialize_block(sample))
ok(roundtrip == sample, f"frontmatter round-trips exactly: {roundtrip}")

meta, body = fm.split_note(fm.dump_note(sample, "# Body\n\ntext"))
ok(meta == sample, "split_note recovers frontmatter from a full note")
ok("Body" in body, "split_note recovers the body")

try:
    fm.split_note("---\nid: x\n# never closed\n")
    raise AssertionError("unterminated frontmatter should raise")
except ValueError:
    print("  ok: unterminated frontmatter raises a clear error")

try:
    fm.parse_block("this line has no colon")
    raise AssertionError("a non 'key: value' line should raise")
except ValueError:
    print("  ok: malformed frontmatter line raises a clear error")


# --- 0.3 note model --------------------------------------------------------
print("0.3 note model")
note_path = resolved["30-projects"] / "kitchen-reno-budget.md"
Note(body="# Kitchen reno budget\n\nContractor quote and budget for the kitchen.\n").save(note_path)
reloaded = Note.load(note_path)
ok(reloaded.id is not None and len(reloaded.id) == 26, "note got a 26-char ULID id")
ok("Kitchen reno" in reloaded.frontmatter["title"], "title derived from first heading")
first_id = reloaded.id
reloaded.save(note_path)
ok(Note.load(note_path).id == first_id, "id is stable across re-saves")

ok(extract_wikilinks("see [[jane-doe]] and [[Project X|alias]]") ==
   [{"dst_target": "jane-doe", "rel": "mentions"},
    {"dst_target": "Project X", "rel": "mentions"}],
   "wikilink extraction handles plain + aliased links")


# --- 0.5 db layer + 0.6 schema --------------------------------------------
print("0.5/0.6 db layer + schema")
database = get_db()
database.migrate()
database.migrate()  # idempotent
index_names = {
    row["name"]
    for row in database.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
}
for expected in EXPECTED_INDEXES:
    ok(expected in index_names, f"index exists: {expected}")
ok(set(database.counts()) >= {"documents", "chunks", "links", "events", "tombstones"},
   "all tables countable")


# --- author a few more notes -----------------------------------------------
Note(body="# Jane Doe\n\nJane is the contractor. Linked from [[kitchen-reno-budget]].\n").save(
    resolved["20-people"] / "jane-doe.md"
)
Note(body="# Snell's Law\n\nRefraction follows n1 sin(theta1) = n2 sin(theta2).\n").save(
    resolved["50-resources"] / "snell-law.md"
)


# --- 0.7 indexer + 0.4 walker (incremental) --------------------------------
print("0.7 indexer + 0.4 incremental")
report1 = index(incremental=True, database=database)
ok(len(report1["indexed"]) == 3, f"first index processes all 3 notes: {report1['indexed']}")
ok(report1["chunks"] >= 3, "chunks were produced")
ok(database.counts()["documents"] == 3, "three documents in the index")
ok(database.counts()["links"] >= 1, "wikilink edges were loaded")

report2 = index(incremental=True, database=database)
ok(report2["indexed"] == [], f"no-change run indexes nothing: {report2['indexed']}")

# Change ONE note -> only it re-indexes.
changed = Note.load(resolved["50-resources"] / "snell-law.md")
changed.body += "\nUpdated: also the critical angle for total internal reflection.\n"
changed.save()
report3 = index(incremental=True, database=database)
ok(report3["indexed"] == ["50-resources/snell-law.md"],
   f"changing one note re-indexes only it: {report3['indexed']}")

# Add ONE note -> only it indexes.
Note(body="# Optics notes\n\nLenses and focal length basics.\n").save(
    resolved["50-resources"] / "optics.md"
)
report4 = index(incremental=True, database=database)
ok(report4["indexed"] == ["50-resources/optics.md"],
   f"adding one note indexes only it: {report4['indexed']}")

# Delete ONE note -> it is removed from the index.
(resolved["50-resources"] / "optics.md").unlink()
report5 = index(incremental=True, database=database)
ok(report5["removed"] == ["50-resources/optics.md"],
   f"deleting a note removes it: {report5['removed']}")
ok(database.counts()["documents"] == 3, "document count back to three after removal")


# --- search returns citations ----------------------------------------------
print("search + citations")
hits = search("kitchen renovation budget", k=5, database=database)["results"]
ok(len(hits) > 0, "search returns results")
citation = hits[0]["citation"]
ok({"source", "title", "heading_path", "page", "chunk_id", "document_id"} <= set(citation),
   "result carries a full citation")
ok(any(h["citation"]["source"] == "30-projects/kitchen-reno-budget.md" for h in hits),
   "the kitchen note surfaces for its query")


# --- 0.9 rebuild contract (the keystone) -----------------------------------
print("0.8 rebuild + 0.9 rebuild contract")
queries = ["kitchen renovation budget", "Jane contractor", "Snell refraction"]


def top_chunk_ids(db):
    snapshot = {}
    for query in queries:
        results = search(query, k=5, database=db)["results"]
        snapshot[query] = [r["citation"]["chunk_id"] for r in results]
    return snapshot


before = top_chunk_ids(database)
rebuild_report = rebuild_index(database=database)
ok(len(rebuild_report["indexed"]) == 3, f"rebuild replays all notes: {rebuild_report['indexed']}")
after = top_chunk_ids(database)
ok(before == after, "rebuild yields identical search results (truth lives in the vault)")

database.close()
print("\nALL VAULT PHASE 0 CHECKS PASSED")
