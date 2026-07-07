"""Self-check for self-pruning, Phase 3 batch 1. Run:
    python tests/check_vault_prune.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies the two guarantees
the user asked for first:

  3.A exact dedup     - capture refuses byte-identical content; the indexer skips
                        identical files on disk (no double-counting in search)
  3.B auto-relations  - [[wikilinks]] resolve to real documents; outgoing links
                        and backlinks read back out; title- and slug-form both work
  3.C relations map   - .vault/relations.md is regenerated and human-readable
  3.D rebuild         - dedup + relations survive a full rebuild from the vault
  3.E near-dup        - highly-similar (not identical) docs are flagged for review;
                        unrelated docs are not; the threshold knob works; flag-only

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_prune_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout, paths  # noqa: E402
from servers.vault import capture as cap  # noqa: E402
from servers.vault import relations as rel  # noqa: E402
from servers.vault.index import index, rebuild_index  # noqa: E402
from servers.vault.search import search  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# Write a plain Markdown file (no frontmatter) at a vault-relative path, so the
# indexer must adopt it -- mirrors how a human drops files into the vault.
def write_note(relpath, text):
    destination = paths()["vault"] / relpath
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


# Find a document's id by its vault-relative path.
def doc_id_for(database, relpath):
    for document in database.list_documents():
        if document["path"] == relpath:
            return document["id"]
    return None


ensure_layout()
database = get_db()
database.migrate()


# --- 3.A exact dedup: capture refuses identical content ---------------------
print("3.A exact dedup (capture)")
first = cap.capture("Exact duplicate body for the capture dedup test.", database=database)
ok("error" not in first and first.get("duplicate_of") is None, f"first capture added: {first}")

second = cap.capture("Exact duplicate body for the capture dedup test.", database=database)
ok(second.get("duplicate_of") == first["id"], "identical capture is refused, points at the original")
ok(second["indexed"] == [], "refused capture indexes nothing")
ok(second["path"] == first["path"], "refused capture returns the existing note's path")

inbox_files = list((paths()["vault"] / "00-inbox").glob("*.md"))
ok(len(inbox_files) == 1, f"only one file on disk for the duplicate content: {inbox_files}")


# --- 3.A exact dedup: indexer skips identical files on disk -----------------
print("3.A exact dedup (indexer)")
write_note("50-resources/alpha.md", "Shared reference article about widget calibration.\n")
write_note("50-resources/beta.md", "Shared reference article about widget calibration.\n")
report = index(incremental=True, database=database)

dup_paths = {entry["path"] for entry in report["duplicates"]}
ok("50-resources/beta.md" in dup_paths, f"the second identical file is flagged duplicate: {report['duplicates']}")
ok("50-resources/alpha.md" not in dup_paths, "the first identical file is kept as canonical")

alpha_id = doc_id_for(database, "50-resources/alpha.md")
ok(alpha_id is not None, "the canonical file became a document")
ok(doc_id_for(database, "50-resources/beta.md") is None, "the duplicate file did NOT become a document")

dup_report = rel.find_duplicates(database)
ok(dup_report["count"] == 1, f"find_duplicates reports the one duplicate: {dup_report}")
ok(dup_report["exact"][0]["canonical_path"] == "50-resources/alpha.md",
   "the duplicate names its canonical document")

hits = search("widget calibration reference", k=10, database=database)["results"]
hit_sources = {h["citation"]["source"] for h in hits}
ok("50-resources/beta.md" not in hit_sources, "duplicate content is not double-counted in search")


# --- 3.B auto-relations: wikilinks resolve, outgoing + backlinks ------------
print("3.B auto-relations")
write_note("20-people/jane-doe.md", "# Jane Doe\n\nColleague on the widget program.\n")
write_note("30-projects/widget-launch.md",
           "# Widget launch\n\nKickoff plan. Working with [[jane-doe]] on delivery.\n")
write_note("30-projects/retro.md",
           "# Retro\n\nThanks to [[Jane Doe]] for the assist.\n")  # title-form link
index(incremental=True, database=database)

jane_id = doc_id_for(database, "20-people/jane-doe.md")
launch_id = doc_id_for(database, "30-projects/widget-launch.md")
retro_id = doc_id_for(database, "30-projects/retro.md")
ok(all([jane_id, launch_id, retro_id]), "people + project notes are all indexed")

launch_rel = rel.related(launch_id, database=database)
launch_targets = {link["document_id"] for link in launch_rel["outgoing"] if link["resolved"]}
ok(jane_id in launch_targets, "slug-form [[jane-doe]] resolved to the person document")

retro_rel = rel.related(retro_id, database=database)
retro_targets = {link["document_id"] for link in retro_rel["outgoing"] if link["resolved"]}
ok(jane_id in retro_targets, "title-form [[Jane Doe]] resolved to the same person document")

jane_rel = rel.related(jane_id, database=database)
backlink_ids = {link["document_id"] for link in jane_rel["backlinks"]}
ok({launch_id, retro_id} <= backlink_ids, "both linking notes appear as backlinks on the person")


# --- 3.B dangling links stay visible ---------------------------------------
print("3.B dangling links")
write_note("50-resources/dangle.md", "See [[nonexistent-thing]] for more.\n")
index(incremental=True, database=database)
dangle_id = doc_id_for(database, "50-resources/dangle.md")
dangle_rel = rel.related(dangle_id, database=database)
ok(any(not link["resolved"] for link in dangle_rel["outgoing"]),
   "a link to a missing document is kept and flagged unresolved")


# --- 3.C auditable relations map -------------------------------------------
print("3.C relations map")
written = rel.write_relations_map(database)
map_path = Path(written["path"])
ok(map_path.exists() and map_path == paths()["relations_md"], "relations map written to .vault/relations.md")
map_text = map_path.read_text(encoding="utf-8")
ok("Jane Doe" in map_text, "map names a document by its title")
ok("links to:" in map_text and "linked from:" in map_text, "map shows both directions")
ok(written["documents"] == len(database.list_documents()), "map covers every document")


# --- 3.D rebuild contract: dedup + relations survive a rebuild --------------
print("3.D rebuild")
rebuild_index(database=database)
ok(rel.find_duplicates(database)["count"] == 1, "duplicates are recomputed after a rebuild")
rebuilt_backlinks = {link["document_id"] for link in rel.related(jane_id, database=database)["backlinks"]}
ok({launch_id, retro_id} <= rebuilt_backlinks, "relations are recomputed after a rebuild")

# --- 3.E near-dup detection: flag similar (not identical) docs --------------
print("3.E near-dup detection")
# Two drafts of the same agenda (one has extra words) -> different bodies (not an
# exact dup) but heavy lexical overlap -> high cosine under the hash embedder.
write_note("50-resources/draft-v1.md",
           "Project kickoff agenda: review the budget, assign owners, "
           "set the timeline, and plan the launch.\n")
write_note("50-resources/draft-v2.md",
           "Project kickoff agenda: review the budget, assign owners, "
           "set the timeline, and plan the launch next week.\n")
write_note("50-resources/unrelated.md",
           "Sourdough recipe with rye flour and a long overnight fermentation in the fridge.\n")
index(incremental=True, database=database)

v1_id = doc_id_for(database, "50-resources/draft-v1.md")
v2_id = doc_id_for(database, "50-resources/draft-v2.md")
unrelated_id = doc_id_for(database, "50-resources/unrelated.md")
ok(all([v1_id, v2_id, unrelated_id]), "near-dup test notes are all indexed")

near = rel.find_near_duplicates(database=database)
flagged = {frozenset((entry["a"]["id"], entry["b"]["id"])) for entry in near["near"]}
ok(frozenset((v1_id, v2_id)) in flagged, f"the two near-identical drafts are flagged: {near['near']}")
ok(all(unrelated_id not in pair for pair in flagged), "the unrelated note is not flagged as a near-dup")
ok(all(0.0 <= entry["similarity"] <= 1.0001 for entry in near["near"]), "reported similarities are valid cosines")

# Near-dup is distinct from exact dedup: the byte-identical beta.md never appears
# here (it was recorded in `duplicates`, never became a document/vector).
ok(all("50-resources/beta.md" not in (entry["a"]["path"], entry["b"]["path"]) for entry in near["near"]),
   "exact duplicates are not reported as near-dups")

# The threshold is a real knob: at an exact-only threshold the drafts drop out.
strict = rel.find_near_duplicates(threshold=1.0, database=database)
strict_pairs = {frozenset((entry["a"]["id"], entry["b"]["id"])) for entry in strict["near"]}
ok(frozenset((v1_id, v2_id)) not in strict_pairs, "raising the threshold to 1.0 unflags the near-dup drafts")

# Flag-only: nothing was moved/merged/deleted -- both drafts still exist on disk.
ok((paths()["vault"] / "50-resources/draft-v1.md").exists()
   and (paths()["vault"] / "50-resources/draft-v2.md").exists(),
   "near-dup flagging never moves or deletes the flagged notes")

# Near-dup is recomputed from chunk vectors, so it survives a rebuild too.
rebuild_index(database=database)
near_after = rel.find_near_duplicates(database=database)
flagged_after = {frozenset((entry["a"]["id"], entry["b"]["id"])) for entry in near_after["near"]}
ok(frozenset((v1_id, v2_id)) in flagged_after, "near-dups are recomputed after a rebuild")


database.close()
print("\nALL VAULT PHASE 3 (BATCH 1) CHECKS PASSED")
