"""Self-check for the token-efficient search contract. Run:
    python tests/check_vault_efficiency.py

The "handshake" the MCP client and server share for retrieval is: search returns
CHEAP compact hits (snippet + citation) by default; you fetch a full note only when
you need it. This verifies that contract holds and actually shrinks the payload.

  compact default - hits carry a bounded snippet + citation.document_id, NOT the
                    full chunk text (so a search is cheap on tokens)
  full opt-in     - detail="full" inlines the chunk text for when you need it
  smaller         - the compact response is materially smaller than the full one
  budget          - a size budget trims extra hits (first hit always kept) and
                    flags has_more, so a big k can't blow the context window

Offline (hash embedder + SQLite + temp VAULT_DIR). No test framework -- just asserts.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="vault_eff_test_")
os.environ["VAULT_DIR"] = _TMP
os.environ["VAULT_DB"] = "sqlite"
os.environ["EMBEDDING_PROVIDER"] = "hash"

from servers.vault.config import ensure_layout  # noqa: E402
from servers.vault.db import get_db  # noqa: E402
from servers.vault import capture as capture_mod  # noqa: E402
from servers.vault.search import search, SNIPPET_CHARS  # noqa: E402


def ok(condition, message):
    assert condition, message
    print(f"  ok: {message}")


ensure_layout()
database = get_db()
database.migrate()

# Five distinct notes (not near-dupes) that all match a rare term, each body > the
# snippet length so truncation is genuinely exercised.
for i in range(5):
    details = " ".join(f"Point {i}-{j}: the quokka's habitat, diet, and behavior number {i*10+j}."
                       for j in range(12))
    capture_mod.quick_note(f"Quokka dossier {i}", f"Entry {i} on the quokka marsupial. {details}",
                           category="50-resources")

QUERY = "quokka marsupial habitat diet"

# --- compact is the default and is cheap -----------------------------------
print("compact default")
compact = search(QUERY, k=5)
ok(compact["detail"] == "compact", "the response reports detail=compact")
ok(len(compact["results"]) >= 2, "compact returns multiple hits")
hit = compact["results"][0]
ok("snippet" in hit and "text" not in hit, "a compact hit has a snippet and NOT the full chunk text")
ok(len(hit["snippet"]) <= SNIPPET_CHARS + 1, f"the snippet is bounded to ~{SNIPPET_CHARS} chars")
ok(bool(hit["citation"].get("document_id")), "the citation carries document_id for the get_note stage")

# --- full is opt-in --------------------------------------------------------
print("full opt-in")
full = search(QUERY, k=5, detail="full")
ok(all("text" in r for r in full["results"]), "detail=full inlines each chunk's text")
ok(len(full["results"][0]["text"]) >= len(hit["snippet"]), "the full text is at least as long as the snippet")

# --- compact really is smaller (the whole point) ---------------------------
print("compact is smaller")
ok(len(json.dumps(compact)) < len(json.dumps(full)),
   "the compact response is materially smaller than the full response")

# --- size budget trims extra hits, keeps the first, flags has_more ---------
print("budget")
tiny = search(QUERY, k=5, max_chars=50)
ok(len(tiny["results"]) >= 1, "at least one hit is returned even under a tiny budget")
ok(tiny.get("has_more") is True, "a tiny budget flags has_more")
ok("truncation_message" in tiny, "the trim carries an actionable message")
ok(len(tiny["results"]) < len(compact["results"]), "the tiny budget returned fewer hits than the full compact set")

database.close()
print("\nALL VAULT SEARCH EFFICIENCY CHECKS PASSED")
