"""Self-check for trustworthy archival, Phase 3 batch 3. Run:
    python tests/check_vault_archive.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies the archival
lifecycle end to end -- the "prune like an archivist, not a shredder" guarantees:

  3.4 TTL policy    - notes past `expires:` are selected; future/none are not
  3.5 decay policy  - low-score AND idle notes are archived; recent or high-score
                      notes survive; both gates must hold; pinned is protected
  3.8 dry-run       - a dry-run previews the plan and changes NOTHING on disk
  3.6 mechanism     - apply moves the note to 90-archive/, flips status=archived,
                      and archived notes stay searchable but rank below active
  3.7 tombstones    - a tombstone row + .vault/tombstones.md record the archival
  3.7 undo          - restore by note id AND by whole prune batch works
  hard rule         - a pinned note is NEVER auto-archived, even when expired
  rebuild           - archived status + tombstones survive a full rebuild

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_archive_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout, paths  # noqa: E402
from servers.vault import archive as arch  # noqa: E402
from servers.vault.index import index, rebuild_index  # noqa: E402
from servers.vault.note import Note  # noqa: E402
from servers.vault.search import search  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# Write a note through the Note model so frontmatter (expires/pinned/status)
# round-trips through the project's own serializer. `extra` overrides frontmatter.
def write_note(relpath, body, **extra):
    destination = paths()["vault"] / relpath
    note = Note(body=body)
    note.ensure_defaults(category=relpath.split("/", 1)[0])
    for key, value in extra.items():
        note.frontmatter[key] = value
    note.save(destination)
    return destination


def doc_id_for(database, relpath):
    for document in database.list_documents():
        if document["path"] == relpath:
            return document["id"]
    return None


def status_of(database, document_id):
    document = database.get_document(document_id)
    return document["status"] if document else None


PAST = "2020-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"

ensure_layout()
database = get_db()
database.migrate()

# A past-expiry note, a future-expiry note, and a no-expiry note.
write_note("50-resources/old-promo.md",
           "# Old promo\n\nExpired promotional flyer about a seasonal sale.\n",
           title="Old promo", expires=PAST)
write_note("50-resources/upcoming.md",
           "# Upcoming\n\nPlanning note about a future promotional sale event.\n",
           title="Upcoming", expires=FUTURE)
write_note("30-projects/evergreen.md",
           "# Evergreen\n\nA project note with no expiry at all.\n",
           title="Evergreen")
# A pinned note that is ALSO expired -- the hard rule must protect it.
write_note("40-areas/pinned-expired.md",
           "# Pinned expired\n\nImportant standing reference, pinned though expired.\n",
           title="Pinned expired", expires=PAST, pinned=True)
index(incremental=True, database=database)

old_id = doc_id_for(database, "50-resources/old-promo.md")
upcoming_id = doc_id_for(database, "50-resources/upcoming.md")
evergreen_id = doc_id_for(database, "30-projects/evergreen.md")
pinned_id = doc_id_for(database, "40-areas/pinned-expired.md")
ok(all([old_id, upcoming_id, evergreen_id, pinned_id]), "all four test notes indexed")


# --- 3.4 + 3.8 dry-run: preview only, nothing moves ------------------------
print("3.4/3.8 TTL dry-run")
preview = arch.run_ttl(dry_run=True, database=database)
would_ids = {entry["id"] for entry in preview["would_archive"]}
ok(preview["dry_run"] is True and preview["batch"] is None, "dry-run is flagged and assigns no batch")
ok(old_id in would_ids, "the expired note is in the dry-run plan")
ok(upcoming_id not in would_ids and evergreen_id not in would_ids,
   "future-expiry and no-expiry notes are NOT in the plan")
ok(pinned_id not in would_ids, "the pinned-but-expired note is NOT in the plan (hard rule)")
ok((paths()["vault"] / "50-resources/old-promo.md").exists(), "dry-run left the note where it was")
ok(status_of(database, old_id) == "active", "dry-run did not change status")
ok(database.list_tombstones() == [], "dry-run recorded no tombstone")


# --- 3.6 mechanism: apply the TTL policy for real --------------------------
print("3.6 archive mechanism")
applied = arch.run_ttl(dry_run=False, database=database)
ok(applied["dry_run"] is False and applied["batch"], "apply reports a batch id")
ok(applied["count"] == 1 and applied["archived"][0]["id"] == old_id, "exactly the expired note was archived")

ok(not (paths()["vault"] / "50-resources/old-promo.md").exists(), "the original file is gone from its folder")
ok((paths()["vault"] / "90-archive/50-resources/old-promo.md").exists(),
   "the note now lives under 90-archive/ at the same sub-path")
ok(status_of(database, old_id) == "archived", "the document's status flipped to archived")
ok(status_of(database, pinned_id) == "active", "the pinned note stayed active (never archived)")


# --- 3.6 down-rank: archived stays searchable but below active -------------
print("3.6 search down-rank")
# 'upcoming' (active) and 'old-promo' (archived) both mention a promotional sale.
results = search("promotional sale", k=10, database=database)["results"]
sources = [hit["citation"]["source"] for hit in results]
ok("90-archive/50-resources/old-promo.md" in sources, "archived note is still findable in search")
ok("50-resources/upcoming.md" in sources, "the active note is found too")
ok(sources.index("50-resources/upcoming.md") < sources.index("90-archive/50-resources/old-promo.md"),
   "the active note ranks ABOVE the archived one")


# --- 3.7 tombstone + audit map ---------------------------------------------
print("3.7 tombstone + map")
tombstones = database.list_tombstones()
ok(len(tombstones) == 1, "one tombstone was recorded")
tomb = tombstones[0]
ok(tomb["document_id"] == old_id and tomb["action"] == "archived", "tombstone names the doc and action")
ok(tomb["prev_path"] == "50-resources/old-promo.md", "tombstone remembers the original path")
ok(tomb["batch"] == applied["batch"], "tombstone carries the prune batch id")
ok(tomb["payload"].get("status") == "active", "tombstone snapshot has the pre-archival status")

map_path = paths()["tombstones_md"]
ok(map_path.exists(), "tombstones.md was generated")
map_text = map_path.read_text(encoding="utf-8")
ok("50-resources/old-promo.md" in map_text and "90-archive/" in map_text,
   "the map shows where the note was and where it went")


# --- 3.7 undo: restore one note by id --------------------------------------
print("3.7 restore by id")
restored = arch.restore(note_id=old_id, database=database)
ok(restored["count"] == 1, "restore reports one note restored")
ok((paths()["vault"] / "50-resources/old-promo.md").exists(), "the note is back at its original path")
ok(not (paths()["vault"] / "90-archive/50-resources/old-promo.md").exists(), "the archived copy is gone")
ok(status_of(database, old_id) == "active", "status is active again")
ok(database.list_tombstones() == [], "the tombstone was cleared on restore")


# --- 3.7 undo: restore a whole prune batch ---------------------------------
print("3.7 restore by batch")
# Add a second expired note, then archive the batch (old-promo is expired again).
write_note("60-sources/clip.md", "# Clip\n\nAn old web clip that has expired.\n",
           title="Clip", expires=PAST)
index(incremental=True, database=database)
clip_id = doc_id_for(database, "60-sources/clip.md")

batch_run = arch.run_ttl(dry_run=False, database=database)
ok(batch_run["count"] == 2, "both expired notes archived in one batch")
ok(status_of(database, old_id) == "archived" and status_of(database, clip_id) == "archived",
   "both notes are archived")
batch_id = batch_run["batch"]
ok(len(database.list_tombstones(batch=batch_id)) == 2, "both tombstones share the batch id")

undo = arch.restore(batch=batch_id, database=database)
ok(undo["count"] == 2, "restoring the batch brings back both notes")
ok(status_of(database, old_id) == "active" and status_of(database, clip_id) == "active",
   "both notes are active again")
ok(database.list_tombstones() == [], "the batch's tombstones were cleared")


# --- rebuild: archived status + tombstones survive a rebuild ---------------
print("rebuild durability")
# Archive one note, then rebuild the derived index from the vault.
arch.run_ttl(dry_run=False, database=database)
ok(status_of(database, old_id) == "archived", "note archived before rebuild")
tombs_before = len(database.list_tombstones())
rebuild_index(database=database)
ok(status_of(database, old_id) == "archived", "archived status survives a rebuild (it's in frontmatter)")
ok(len(database.list_tombstones()) == tombs_before, "tombstones survive a rebuild (audit log is preserved)")


# --- 3.5 decay archival: low-score AND idle archives; recent/high survives --
print("3.5 decay archival")
from datetime import datetime, timezone, timedelta  # noqa: E402

# A low-importance note (low prune_score) and a high-importance note (high score),
# both freshly written so they start recently-touched.
write_note("50-resources/stale-low.md",
           "# Stale low\n\nA low-importance note nobody references or opens.\n",
           title="Stale low", importance=0)
write_note("40-areas/keeper-high.md",
           "# Keeper high\n\nAn important standing reference in an active area.\n",
           title="Keeper high", importance=5)
index(incremental=True, database=database)
stale_id = doc_id_for(database, "50-resources/stale-low.md")
keeper_id = doc_id_for(database, "40-areas/keeper-high.md")
ok(stale_id and keeper_id, "decay test notes indexed")

recent_now = datetime.now(timezone.utc)
future_now = recent_now + timedelta(days=120)

# Idle GATE: fresh notes (idle ~0) are never decayed, even the low-scoring one.
fresh = arch.run_decay(dry_run=True, now=recent_now, threshold=3.0, min_idle_days=30, database=database)
fresh_ids = {entry["id"] for entry in fresh["would_archive"]}
ok(stale_id not in fresh_ids and keeper_id not in fresh_ids,
   "recently-touched notes are NOT decayed (idle gate holds even for a low score)")

# SCORE gate: far in the future both are idle, but only the low-score note decays.
aged = arch.run_decay(dry_run=True, now=future_now, threshold=3.0, min_idle_days=30, database=database)
aged_ids = {entry["id"] for entry in aged["would_archive"]}
ok(stale_id in aged_ids, "the idle low-score note is a decay candidate")
ok(keeper_id not in aged_ids, "the idle high-score note is protected by the score threshold")
ok((paths()["vault"] / "50-resources/stale-low.md").exists(), "decay dry-run moved nothing on disk")

# APPLY: the low note archives (reversible tombstone); the keeper is untouched.
decayed = arch.run_decay(dry_run=False, now=future_now, threshold=3.0, min_idle_days=30, database=database)
ok(decayed["policy"] == "decay" and decayed["count"] == 1, "decay archived exactly one note")
ok(status_of(database, stale_id) == "archived", "the stale low-score note is archived")
ok(status_of(database, keeper_id) == "active", "the high-score keeper stays active")
ok((paths()["vault"] / "90-archive/50-resources/stale-low.md").exists(),
   "the decayed note moved under 90-archive/ at the same sub-path")
ok((paths()["vault"] / "40-areas/keeper-high.md").exists(), "the keeper is still in place")
decay_tomb = [t for t in database.list_tombstones() if t["document_id"] == stale_id]
ok(decay_tomb and decay_tomb[0]["reason"].startswith("decay:"),
   "the tombstone records the decay policy as the reason")

# REVERSIBLE like every prune: restoring brings the decayed note back.
arch.restore(note_id=stale_id, database=database)
ok(status_of(database, stale_id) == "active", "the decayed note restores cleanly")
ok((paths()["vault"] / "50-resources/stale-low.md").exists(), "restored to its original path")


database.close()
print("\nALL VAULT PHASE 3 (BATCH 3) CHECKS PASSED")
