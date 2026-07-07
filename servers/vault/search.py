"""Vault search over the relational index (build step 6.1: hybrid retrieval).

Hybrid search: a **vector** retriever (embedding cosine, per backend) and a
**lexical** retriever (BM25, `lexical.py`) are run in parallel and fused with
**Reciprocal Rank Fusion** — so semantic matches and exact-term matches (names,
codes, error strings) both surface, and chunks ranked high by both win. Active
notes always rank above archived. `mode` can force `vector` or `lexical` only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..knowledge.store import get_embedder

from .config import paths
from .db import VaultDB, VaultHit, get_db
from .note import Note

# Default number of results when the caller doesn't specify k.
DEFAULT_TOP_K = 8

# Reciprocal Rank Fusion constant (the standard k≈60): dampens the influence of
# any single retriever's exact ranks so the two lists combine smoothly.
RRF_K = 60

# Cap the note body get_note returns so a huge note can't blow the context window.
CHARACTER_LIMIT = 25000

# Default number of events timeline() returns when the caller doesn't specify.
DEFAULT_TIMELINE_LIMIT = 50

# Compact-mode snippet length (chars): enough to judge relevance and often to answer
# outright, without shipping the whole chunk body to the client on every hit.
SNIPPET_CHARS = 300

# Soft cap on a search response's textual size (chars). Compact hits are small, so
# this only trims pathological result sets; the first hit is always kept.
SEARCH_CHARACTER_BUDGET = 6000


# Trim chunk text to a bounded snippet on a word boundary (compact mode): enough to
# judge relevance and often to answer, without shipping the whole chunk every time.
def _snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit].rsplit(" ", 1)[0].rstrip()
    return (cut or collapsed[:limit]) + "…"


# Shape fused hits into response items under a size budget. compact (default) =
# snippet + citation (cheap); full = also inline the chunk text. The first hit is
# always kept; extra hits are dropped (truncated=True) once the budget is exceeded.
def _format_results(fused: List[tuple], *, detail: str, max_chars: int) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    used = 0
    truncated = False
    for hit, score in fused:
        snippet = _snippet(hit.text)
        item: Dict[str, Any] = {"snippet": snippet, "score": round(score, 6), "citation": hit.citation()}
        if detail == "full":
            item["text"] = hit.text
        size = len(snippet) + (len(hit.text) if detail == "full" else 0) + 160  # +citation est.
        if items and used + size > max_chars:
            truncated = True
            break
        items.append(item)
        used += size
    message = (f"showing {len(items)} of {len(fused)} hits to fit a ~{max_chars}-char budget; "
               "narrow with filters (category/source) or read a specific note with get_note.")
    return {"items": items, "truncated": truncated, "message": message}


# Search the vault and return ranked hits with citations. `filters` may carry
# category / source / status to scope the search. `detail`:
#   compact (default) -> a short snippet per hit (token-cheap; pair with get_note),
#   full              -> also inline each chunk's full text.
# The response is held under `max_chars` so a large k can't blow the context window.
def search(
    query: str,
    k: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
    database: Optional[VaultDB] = None,
    record_access: bool = True,
    mode: str = "hybrid",
    detail: str = "compact",
    max_chars: int = SEARCH_CHARACTER_BUDGET,
) -> Dict[str, Any]:
    if not query or not query.strip():
        return {"error": "empty query", "hint": "pass a non-empty search string, e.g. 'budget'"}

    top_k = DEFAULT_TOP_K if k is None else int(k)
    if top_k < 1:
        return {"error": f"k must be >= 1 (got {top_k})", "hint": "use k=8 for a typical lookup"}
    if mode not in ("hybrid", "vector", "lexical"):
        mode = "hybrid"
    if detail not in ("compact", "full"):
        detail = "compact"

    database = database or get_db()
    # Pull a wider pool from each retriever than we return, so fusion has room to
    # re-rank (a chunk ranked high by BOTH retrievers should be able to climb).
    pool = max(top_k * 4, 20)

    vector_hits: List[VaultHit] = []
    lexical_hits: List[VaultHit] = []
    if mode in ("hybrid", "vector"):
        query_vector = get_embedder().embed([query])[0]
        vector_hits = database.search(query_vector, pool, filters)
    if mode in ("hybrid", "lexical"):
        lexical_hits = database.lexical_search(query, pool, filters)

    fused = _rrf_fuse(vector_hits, lexical_hits, top_k)

    # Usage signal: surfacing a note counts as accessing it (feeds prune_score).
    # Imported lazily so search has no hard dependency on the pruning module.
    if record_access and fused:
        from . import prune

        prune.record_access([hit.document_id for hit, _ in fused], database=database)

    formatted = _format_results(fused, detail=detail, max_chars=max_chars)
    response: Dict[str, Any] = {
        "results": formatted["items"],
        "k": top_k,
        "returned": len(formatted["items"]),
        "mode": mode,
        "detail": detail,
    }
    if formatted["truncated"]:
        response["has_more"] = True
        response["truncation_message"] = formatted["message"]
    return response


# Fuse two ranked hit lists with Reciprocal Rank Fusion: each list contributes
# 1/(RRF_K + rank) to a chunk's score, so a chunk ranked high by BOTH retrievers
# outranks one found by only one. Active notes are kept strictly above archived
# (the pruning down-rank guarantee), then ordered by fused score. Returns the
# top-k as [(hit, fused_score)].
def _rrf_fuse(
    vector_hits: List[VaultHit], lexical_hits: List[VaultHit], k: int, rrf_k: int = RRF_K
) -> List[tuple]:
    fused: Dict[str, list] = {}
    for ranked_list in (vector_hits, lexical_hits):
        for rank, hit in enumerate(ranked_list):
            entry = fused.setdefault(hit.chunk_id, [0.0, hit])
            entry[0] += 1.0 / (rrf_k + rank + 1)

    ordered = sorted(
        fused.values(),
        key=lambda pair: (pair[1].status != "archived", pair[0]),
        reverse=True,
    )
    return [(hit, score) for score, hit in ordered[: max(1, k)]]


# Resolve a caller-supplied reference to a document row. Accepts (in order) a
# document id, an external source_ref (e.g. "gcal://event/abc"), or a vault path
# ("00-inbox/foo.md"). Returns the document dict or None.
def resolve_document(database: VaultDB, ref: str) -> Optional[Dict[str, Any]]:
    document = database.get_document(ref)
    if document:
        return document

    by_source_ref = database.find_document_by_source_ref(ref)
    if by_source_ref:
        return database.get_document(by_source_ref)

    by_path = database.document_id_for_source(ref)
    if by_path:
        return database.get_document(by_path)

    return None


# Read a full note (frontmatter + body) by id / source_ref / path. This is the
# read that pairs with search: search returns chunks + a citation.document_id;
# get_note turns that id into the whole note so the caller can answer accurately.
# A very long body is truncated (with a message) so it can't overflow the context.
# Reading a note counts as an access (feeds prune scoring) unless disabled.
def get_note(
    ref: str,
    database: Optional[VaultDB] = None,
    max_chars: int = CHARACTER_LIMIT,
    record_access: bool = True,
) -> Dict[str, Any]:
    if not ref or not ref.strip():
        return {"error": "empty note reference",
                "hint": "pass a document id from a search citation, a vault path, or a source_ref."}

    database = database or get_db()
    document = resolve_document(database, ref.strip())
    if document is None:
        return {"error": f"no note found for {ref!r}.",
                "hint": "pass a document id (from search citations), a vault path like "
                        "'00-inbox/foo.md', or a source_ref like 'gcal://event/…'."}

    note_path = paths()["vault"] / document["path"]
    if not note_path.exists():
        return {"error": f"note file is missing at {document['path']} (the index may be stale).",
                "hint": "run reindex to reconcile the index with the vault."}

    note = Note.load(note_path)
    body = note.body
    truncated = len(body) > max_chars
    if truncated:
        body = body[:max_chars]

    if record_access:
        from . import prune
        prune.record_access([document["id"]], database=database)

    result: Dict[str, Any] = {
        "id": document["id"],
        "path": document["path"],
        "title": note.frontmatter.get("title", ""),
        "status": document.get("status"),
        "frontmatter": note.frontmatter,
        "body": body,
    }
    if truncated:
        result["truncated"] = True
        result["truncation_message"] = (
            f"body truncated to {max_chars} characters; open {document['path']} in the "
            "vault for the full text."
        )
    return result


# List time-stamped events (derived from note frontmatter `start:`), ordered by
# start time, optionally bounded by ISO `start`/`end`. Powers "what's on my
# calendar" style questions; each event points back to its note via document_id.
def timeline(
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: int = DEFAULT_TIMELINE_LIMIT,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    from . import prune  # parse_iso: robust across date-only and tz-aware timestamps

    lower = prune.parse_iso(start) if start else None
    upper = prune.parse_iso(end) if end else None

    matched: List[tuple] = []
    for event in database.list_events():
        when = prune.parse_iso(event.get("start_at"))
        if when is None:
            continue
        if lower and when < lower:
            continue
        if upper and when > upper:
            continue
        matched.append((when, event))

    matched.sort(key=lambda pair: pair[0])
    limited = matched[: max(1, int(limit))]
    return {
        "events": [
            {
                "title": event.get("title", ""),
                "start_at": event.get("start_at"),
                "end_at": event.get("end_at"),
                "document_id": event.get("document_id"),
                "source": event.get("source"),
            }
            for _, event in limited
        ],
        "count": len(limited),
        "total": len(matched),
    }
