"""Self-check for prune scoring, Phase 3 batch 2. Run:
    python tests/check_vault_prune_scores.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies:

  3.1 access signals  - a search bumps the surfaced note's access_count +
                        last_access (sidecar + DB), and signals survive a rebuild
  3.2 prune_score     - pinned/important notes score high; old-untouched score low
  3.2 config          - changing a weight in .vault/prune.config changes ranking
                        predictably; explain() shows the per-term breakdown

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_score_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db  # noqa: E402
from servers.vault.config import ensure_layout, paths  # noqa: E402
from servers.vault import prune  # noqa: E402
from servers.vault.index import index, rebuild_index  # noqa: E402
from servers.vault.search import search  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


# Write a note WITH full frontmatter so the indexer does NOT adopt it (which would
# overwrite `updated`). Lets the test control recency/importance/pinned exactly.
def write_note(relpath, *, note_id, updated, importance, pinned, body):
    meta = (
        "---\n"
        f"id: {note_id}\n"
        f"title: {note_id}\n"
        f"category: {relpath.split('/', 1)[0]}\n"
        f"created: {updated}\n"
        f"updated: {updated}\n"
        "source: test\n"
        f"importance: {importance}\n"
        f"pinned: {'true' if pinned else 'false'}\n"
        "status: active\n"
        "---\n\n"
        f"{body}\n"
    )
    destination = paths()["vault"] / relpath
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(meta, encoding="utf-8")


def score_of(database, note_id):
    return database.get_document(note_id)["prune_score"]


now = datetime.now(timezone.utc)
recent = now.isoformat(timespec="seconds")
ancient = (now - timedelta(days=900)).isoformat(timespec="seconds")

ensure_layout()
database = get_db()
database.migrate()


# --- 3.1 access signals ----------------------------------------------------
print("3.1 access signals")
write_note("50-resources/widget-guide.md", note_id="DOCWIDGET", updated=recent,
           importance=2, pinned=False, body="Guide to widget calibration and tuning.")
index(incremental=True, database=database)

before = database.get_document("DOCWIDGET")["access_count"]
ok(before == 0, "fresh document starts with access_count 0")

search("widget calibration tuning guide", k=5, database=database)
signals = prune.load_signals()
ok(signals.get("DOCWIDGET", {}).get("access_count", 0) >= 1, "search bumped access_count in the sidecar")
ok(signals["DOCWIDGET"]["last_access"], "search recorded a last_access timestamp")
ok(database.get_document("DOCWIDGET")["access_count"] >= 1, "the DB cache column was bumped too")

rebuild_index(database=database)
ok(prune.load_signals().get("DOCWIDGET", {}).get("access_count", 0) >= 1,
   "signals sidecar survives a rebuild")
ok(database.get_document("DOCWIDGET")["access_count"] >= 1,
   "rebuild reapplies signals to the DB cache")


# --- 3.2 prune_score: pinned / important high, old low ---------------------
print("3.2 prune_score ranking")
write_note("30-projects/pinned-note.md", note_id="DOCPIN", updated=recent,
           importance=2, pinned=True, body="Pinned project note that must never sink.")
write_note("30-projects/important-note.md", note_id="DOCIMP", updated=recent,
           importance=5, pinned=False, body="Important high-value project note.")
write_note("50-resources/recent-plain.md", note_id="DOCNEW", updated=recent,
           importance=0, pinned=False, body="A plain recent resource note.")
write_note("50-resources/old-plain.md", note_id="DOCOLD", updated=ancient,
           importance=0, pinned=False, body="An old untouched resource note.")
index(incremental=True, database=database)

ok(score_of(database, "DOCPIN") > score_of(database, "DOCIMP"),
   "pinned note scores above a merely-important note (default weights)")
ok(score_of(database, "DOCIMP") > score_of(database, "DOCNEW"),
   "higher importance scores above a plain note of the same age")
ok(score_of(database, "DOCNEW") > score_of(database, "DOCOLD"),
   "a recent note scores above an old untouched one")


# --- 3.2 config changes ranking predictably --------------------------------
print("3.2 config tuning")
# Crank importance weight so high that importance dominates the pin bonus.
paths()["prune_config"].write_text("w_importance: 10000.0\n", encoding="utf-8")
config = prune.load_config()
ok(config["w_importance"] == 10000.0, "prune.config override is read")
ok(config["w_pin"] == prune.PRUNE_DEFAULTS["w_pin"], "unspecified weights keep their defaults")

prune.refresh(database)
ok(score_of(database, "DOCIMP") > score_of(database, "DOCPIN"),
   "raising w_importance flips importance above pinned (predictable re-ranking)")


# --- 3.2 explain breakdown -------------------------------------------------
print("3.2 explain")
explained = prune.explain("DOCIMP", database=database)
ok("breakdown" in explained and "importance" in explained["breakdown"], "explain returns a per-term breakdown")
ok(explained["breakdown"]["importance"] == 10000.0 * 5, "importance term reflects weight * value")
ok(abs(sum(explained["breakdown"].values()) - explained["prune_score"]) < 1e-6,
   "breakdown terms sum to the reported prune_score")

database.close()
print("\nALL VAULT PHASE 3 (BATCH 2) CHECKS PASSED")
