"""Self-check for hybrid retrieval (build step 6.1). Run:
    python tests/check_vault_hybrid.py

Offline (hash embedder + SQLite + temp VAULT_DIR). Verifies:

  BM25         - a term present scores > 0, absent scores 0
  lexical      - db.lexical_search finds an exact rare token (E1042); non-matches
                 are not returned
  hybrid       - a keyword query's top hit is the exact-match note
  RRF fusion   - a chunk ranked by BOTH retrievers outranks a single-retriever one;
                 active ranks above archived at equal score
  mode switch  - vector / lexical / hybrid all work
  active-first - an archived note still appears but below active matches

No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_hybrid_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.db import get_db, VaultHit  # noqa: E402
from servers.vault.config import ensure_layout  # noqa: E402
from servers.vault import capture as cap  # noqa: E402
from servers.vault import archive  # noqa: E402
from servers.vault.lexical import bm25_scores  # noqa: E402
from servers.vault.search import search, _rrf_fuse  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


def _hit(chunk_id, status="active"):
    return VaultHit(chunk_id=chunk_id, document_id="doc-" + chunk_id, source="s",
                    title="t", heading_path="", page=None, text="", score=0.0, status=status)


# --- BM25 unit -------------------------------------------------------------
print("BM25 unit")
texts = ["the cat sat on the mat", "a dog ran fast", "cats and dogs play"]
scores = bm25_scores("cat", texts)
ok(scores[0] > 0.0, "a doc containing the term scores > 0")
ok(scores[1] == 0.0 and scores[2] == 0.0, "docs without the exact term score 0")
ok(bm25_scores("", texts) == [0.0, 0.0, 0.0], "an empty query scores everything 0")


# --- RRF fusion unit -------------------------------------------------------
print("RRF fusion unit")
fused = _rrf_fuse([_hit("a"), _hit("b")], [_hit("b"), _hit("c")], k=5)
ids = [hit.chunk_id for hit, _ in fused]
ok(ids[0] == "b", "a chunk ranked by BOTH retrievers fuses to the top")
ok(set(ids) == {"a", "b", "c"}, "fusion unions both retrievers' hits")
fused2 = _rrf_fuse([_hit("act", "active")], [_hit("arc", "archived")], k=5)
ids2 = [hit.chunk_id for hit, _ in fused2]
ok(ids2.index("act") < ids2.index("arc"), "active ranks above archived even at equal RRF score")


# --- seed a vault ----------------------------------------------------------
ensure_layout()
database = get_db()
database.migrate()

def add(text, title):
    return cap.capture(text, title=title, category="50-resources", force_new=True, database=database)

add("Photosynthesis converts sunlight into chemical energy inside plant chloroplasts.", "Photosynthesis")
add("Mitochondria generate ATP through the process of cellular respiration.", "Mitochondria")
add("Runbook: error code E1042 means the storage array disk failed; replace the drive.", "Storage runbook")
q1 = add("Quantum entanglement correlates the spin states of two particles.", "Entanglement")
q2 = add("Quantum tunneling lets particles pass through energy barriers.", "Tunneling")


# --- lexical retriever finds an exact rare token ---------------------------
print("lexical retriever")
lex = database.lexical_search("E1042", 10)
ok(len(lex) == 1 and lex[0].title == "Storage runbook",
   "lexical_search returns only the note containing the exact token E1042")
ok(all(h.title != "Photosynthesis" for h in lex), "non-matching notes are not returned lexically")


# --- hybrid puts the exact/best match on top -------------------------------
print("hybrid search")
top = search("E1042 storage array disk failure", database=database)["results"]
ok(top and top[0]["citation"]["title"] == "Storage runbook", "hybrid top hit is the exact-match note")
bio = search("photosynthesis sunlight chloroplasts", database=database)["results"]
ok(bio and bio[0]["citation"]["title"] == "Photosynthesis", "hybrid top hit for a topical query is right")


# --- mode switch -----------------------------------------------------------
print("mode switch")
lex_mode = search("E1042", mode="lexical", database=database)
ok(lex_mode["mode"] == "lexical" and lex_mode["results"][0]["citation"]["title"] == "Storage runbook",
   "lexical-only mode finds the exact token")
vec_mode = search("E1042 storage disk", mode="vector", database=database)
ok(vec_mode["mode"] == "vector" and len(vec_mode["results"]) > 0, "vector-only mode returns results")


# --- active-first ordering (archived still findable, but below active) ------
print("active-first ordering")
archive.archive_documents([q2["id"]], reason="hybrid test", dry_run=False, database=database)
mixed = search("quantum particles entanglement tunneling barriers spin", k=10, database=database)["results"]
titles = [r["citation"]["title"] for r in mixed]
ok("Entanglement" in titles and "Tunneling" in titles, "both the active and archived quantum notes are found")
ok(titles.index("Entanglement") < titles.index("Tunneling"),
   "the active note ranks above the archived one")
tunneling_hit = next(r for r in mixed if r["citation"]["title"] == "Tunneling")
ok(tunneling_hit["citation"]["status"] == "archived", "the archived hit is marked archived")


database.close()
print("\nALL VAULT HYBRID-RETRIEVAL CHECKS PASSED")
