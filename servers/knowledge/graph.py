"""Concept graph: the cheap "light graph" that captures most of GraphRAG's
synthesis value at near-zero cost (no LLM calls to build).

Structure (networkx DiGraph, persisted as JSON node-link):
  - Heading nodes from each chunk's heading_path (Topic > Subtopic > ...),
    linked by containment edges (parent -> child).
  - Chunk nodes, linked from their deepest heading (heading -> chunk).
  - Cross-reference edges: a chunk that names a concept defined under a
    different heading links to that concept (lexical match on heading titles).

The full Microsoft GraphRAG layer is gated behind ENABLE_GRAPHRAG (default off);
run_graphrag() is a stub hook documenting the cost, not built on the first pass.

ponytail: cross-ref detection is an O(chunks x concepts) substring scan. Fine for
a personal corpus; upgrade to an inverted index if the corpus grows large.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .store import config, get_store, paths

_GRAPH_FILE = "concept_graph.json"
_MIN_CONCEPT_LEN = 4  # ignore very short heading titles as cross-ref triggers


def _graph_path() -> Path:
    return paths()["graph"] / _GRAPH_FILE


def _segments(heading_path: str) -> List[str]:
    return [s.strip() for s in heading_path.split(">") if s.strip()]


def build_graph() -> Dict:
    """Build the concept graph from indexed chunks and persist it. Returns stats."""
    import networkx as nx

    store = get_store()
    records = store.all_records()
    g = nx.DiGraph()

    # glossary: lowercased heading title -> set of heading-path node ids
    glossary: Dict[str, set] = {}

    # Pass 1: hierarchy + chunk nodes.
    for r in records:
        segs = _segments(r.get("heading_path", ""))
        prev = None
        path_acc: List[str] = []
        for seg in segs:
            path_acc.append(seg)
            node_id = " > ".join(path_acc)
            if not g.has_node(node_id):
                g.add_node(node_id, kind="concept", title=seg, source=r.get("source"))
            if prev is not None:
                g.add_edge(prev, node_id, kind="contains")
            glossary.setdefault(seg.lower(), set()).add(node_id)
            prev = node_id
        # chunk node under deepest heading
        cid = r["chunk_id"]
        g.add_node(cid, kind="chunk", source=r.get("source"),
                   heading_path=r.get("heading_path", ""), page=r.get("page"))
        if prev is not None:
            g.add_edge(prev, cid, kind="contains")

    # Pass 2: cross-references (chunk mentions a concept defined elsewhere).
    concepts = [(title, ids) for title, ids in glossary.items() if len(title) >= _MIN_CONCEPT_LEN]
    patterns = {title: re.compile(rf"\b{re.escape(title)}\b", re.IGNORECASE) for title, _ in concepts}
    xref_count = 0
    for r in records:
        text = r.get("text", "")
        own = set(s.lower() for s in _segments(r.get("heading_path", "")))
        for title, ids in concepts:
            if title in own:
                continue
            if patterns[title].search(text):
                for target in ids:
                    g.add_edge(r["chunk_id"], target, kind="mentions")
                    xref_count += 1

    p = _graph_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(nx.node_link_data(g, edges="links")))
    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "concepts": sum(1 for _, d in g.nodes(data=True) if d.get("kind") == "concept"),
        "cross_references": xref_count,
    }


def _load_graph():
    import networkx as nx

    p = _graph_path()
    if not p.exists():
        return None
    return nx.node_link_graph(json.loads(p.read_text()), edges="links")


def related_concepts(concept: str) -> Dict:
    """Neighbors of a concept in the graph: parents, sub-concepts, cross-links.

    For "how does X connect to Y" style questions.
    """
    g = _load_graph()
    if g is None:
        return {"error": "concept graph not built yet; run reindex first"}

    cl = concept.strip().lower()
    matches = [n for n, d in g.nodes(data=True)
               if d.get("kind") == "concept" and (d.get("title", "").lower() == cl or cl in n.lower())]
    if not matches:
        return {"error": f"concept {concept!r} not found", "available": _sample_concepts(g)}

    node = matches[0]
    parents = [u for u, _, d in g.in_edges(node, data=True) if d.get("kind") == "contains"]
    children = [v for _, v, d in g.out_edges(node, data=True)
                if d.get("kind") == "contains" and g.nodes[v].get("kind") == "concept"]
    chunk_children = [v for _, v, d in g.out_edges(node, data=True)
                      if g.nodes[v].get("kind") == "chunk"]
    # chunks elsewhere that mention this concept
    mentioned_by = [u for u, _, d in g.in_edges(node, data=True) if d.get("kind") == "mentions"]
    related = sorted({g.nodes[c].get("heading_path", "") for c in mentioned_by if g.nodes[c].get("heading_path")})

    return {
        "concept": node,
        "parent": parents[0] if parents else None,
        "sub_concepts": sorted(children),
        "direct_chunks": len(chunk_children),
        "referenced_by_topics": related,
    }


def _sample_concepts(g, n: int = 20) -> List[str]:
    return sorted([nd for nd, d in g.nodes(data=True) if d.get("kind") == "concept"])[:n]


def synthesize(query: str, max_chunks: int = 12) -> Dict:
    """Cross-topic synthesis: the explicit global/slow path.

    Seeds with a vector search, then traverses the concept graph to gather
    related chunks across *other* topics (siblings + cross-referenced concepts),
    returning the assembled multi-topic context with citations. The caller
    (Claude) writes the synthesis from this gathered context.

    Only reachable as its own tool -- never auto-triggered by a lookup.
    """
    from .retrieve import search

    cfg = config()
    if cfg["enable_graphrag"]:
        try:
            return run_graphrag(query)
        except NotImplementedError as e:
            # fall through to the light-graph path with a note
            note = str(e)
        else:  # pragma: no cover
            pass
    else:
        note = None

    g = _load_graph()
    seeds = search(query, k=cfg["search_top_k"]).get("results", [])
    if g is None:
        return {"mode": "synthesis", "note": "concept graph not built; returning vector seeds only",
                "context": seeds, "citations": [s["citation"] for s in seeds]}

    # Collect chunk ids from seeds + their graph neighborhood.
    gathered: Dict[str, dict] = {}
    records_by_id = {r["chunk_id"]: r for r in get_store().all_records()}

    def add_chunk(cid: str, why: str):
        if cid in gathered or cid not in records_by_id:
            return
        r = records_by_id[cid]
        gathered[cid] = {
            "text": r["text"],
            "why": why,
            "citation": {"source": r["source"], "heading_path": r.get("heading_path", ""),
                         "page": r.get("page"), "chunk_id": cid},
        }

    for s in seeds:
        cid = s["citation"]["chunk_id"]
        add_chunk(cid, "vector match")
        if not g.has_node(cid):
            continue
        # concepts this chunk mentions -> pull their chunks (cross-topic links)
        for _, concept, d in g.out_edges(cid, data=True):
            if d.get("kind") == "mentions":
                for _, sib, dd in g.out_edges(concept, data=True):
                    if dd.get("kind") == "contains" and g.nodes[sib].get("kind") == "chunk":
                        add_chunk(sib, f"related via concept '{g.nodes[concept].get('title')}'")
        # sibling chunks under the same parent heading
        for parent, _, d in g.in_edges(cid, data=True):
            if d.get("kind") == "contains":
                for _, sib, dd in g.out_edges(parent, data=True):
                    if dd.get("kind") == "contains" and g.nodes[sib].get("kind") == "chunk":
                        add_chunk(sib, "same section")
        if len(gathered) >= max_chunks:
            break

    items = list(gathered.values())[:max_chunks]
    return {
        "mode": "synthesis",
        "note": note or "light concept-graph traversal (GraphRAG disabled)",
        "context": items,
        "citations": [it["citation"] for it in items],
    }


# --- Deferred full GraphRAG hook (opt-in, off by default) ------------------
def run_graphrag(query: str) -> Dict:
    """Hook for the full Microsoft GraphRAG layer over a corpus subset.

    Deliberately NOT implemented on the first pass. Enabling it (ENABLE_GRAPHRAG=true)
    costs per-chunk LLM calls at index time and per-query map-reduce tokens, and
    makes note edits expensive to reindex -- which is why it is off by default and
    never the primary path. Wire a real implementation here per-subject only if
    cross-cutting synthesis questions prove frequent.
    """
    raise NotImplementedError(
        "Full GraphRAG is gated off. Set ENABLE_GRAPHRAG=true and implement run_graphrag() "
        "to enable it; the light concept graph handles synthesis by default."
    )
