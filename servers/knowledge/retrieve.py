"""Retrieval logic. Vector top-k is the DEFAULT path (fast, cheap, reliable).

The graph/synthesis path lives in graph.py and is only reached via the
synthesize tool -- never auto-escalated from a lookup here.

Every result carries citations (source, heading_path, page) so the caller can
tell the user exactly where an answer came from.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .store import Hit, config, get_embedder, get_store, paths

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def search(query: str, k: Optional[int] = None) -> Dict:
    """Vector top-k lookup. Returns ranked chunks with citations.

    This is the default lookup path for "what's the formula", "show me a worked
    example", "what did the notes say about X".

    Returns {"results": [...], "k": int} or {"error", "hint"} on bad input.
    """
    if not query or not query.strip():
        return {"error": "empty query", "hint": "pass a non-empty search string, e.g. \"Snell's law\""}
    cfg = config()
    k = cfg["search_top_k"] if k is None else int(k)
    if k < 1:
        return {"error": f"k must be >= 1 (got {k})", "hint": "use k=8 for a typical lookup"}
    embedder = get_embedder()
    store = get_store()
    if not store.all_records():
        return {"results": [], "k": k,
                "note": "the index is empty; drop notes in data/raw/ and run reindex first"}
    qvec = embedder.embed([query])[0]
    hits: List[Hit] = store.query(qvec, k)
    return {
        "results": [
            {"text": h.text, "score": round(h.score, 4), "citation": h.citation()}
            for h in hits
        ],
        "k": k,
    }


def list_sources() -> List[Dict]:
    """List indexed notes/textbooks with chunk counts."""
    store = get_store()
    counts: Dict[str, int] = {}
    for r in store.all_records():
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    return [{"source": s, "chunks": n} for s, n in sorted(counts.items())]


def _headings_in(corpus_file) -> List[str]:
    out = []
    for line in corpus_file.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _HEADING_RE.match(line)
        if m:
            out.append(m.group(2).strip())
    return out


def get_section(source: str, heading_path: str) -> Dict:
    """Pull a full section verbatim by source + heading_path.

    Reads the canonical corpus Markdown so the section is returned exactly as
    written (not reassembled from chunks). Matches the deepest heading in
    heading_path; returns everything until the next heading of equal/higher level.

    On a miss, the error includes the available sources/headings so the caller
    can correct the call in one step.
    """
    p = paths()
    if not heading_path or not heading_path.strip():
        return {"error": "empty heading_path",
                "hint": "pass a section title or path, e.g. \"Carnot Efficiency\" or \"Thermodynamics > Carnot Efficiency\""}
    corpus_file = p["corpus"] / source
    if not corpus_file.exists():
        available = [s["source"] for s in list_sources()]
        return {"error": f"unknown source {source!r}",
                "hint": "use list_sources() to see valid sources",
                "available_sources": available}

    target = heading_path.split(">")[-1].strip().lower()
    lines = corpus_file.read_text(encoding="utf-8", errors="replace").splitlines()

    start = None
    start_level = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group(2).strip().lower() == target:
            start = i
            start_level = len(m.group(1))
            break
    if start is None:
        return {"error": f"heading {target!r} not found in {source}",
                "hint": "match a heading exactly (case-insensitive); see available_headings",
                "available_headings": _headings_in(corpus_file)}

    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= start_level:
            end = j
            break

    text = "\n".join(lines[start:end]).strip()
    return {
        "source": source,
        "heading_path": heading_path,
        "text": text,
        "chars": len(text),
    }
