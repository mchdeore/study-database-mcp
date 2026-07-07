"""Self-building, auditable document relations (build steps 3.B-3.C).

Two of the self-pruning guarantees the user cares about live here:

  1. Relations create themselves. Wikilinks ([[target]]) in note bodies are
     resolved to real documents during indexing; this module reads those edges
     back out as outgoing links and backlinks so the graph is navigable without
     anyone maintaining it by hand.

  2. Auditing is easy. `write_relations_map()` regenerates a single human-readable
     Markdown file (.vault/relations.md) listing every document with its outgoing
     links and backlinks, so you can eyeball the whole graph in one place.

This module only READS the relational index; the actual link resolution happens in
index.resolve_links(). Nothing here mutates the vault except the generated map file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .config import paths
from .db import VaultDB, get_db

# Cosine-similarity threshold at/above which two documents are flagged as possible
# near-duplicates. Tuned for real (semantic) embedders; the offline hash embedder,
# being lexical, also clears it for near-identical text. Overridable per call.
NEAR_DUP_THRESHOLD = 0.9


# Short label for a document (title if present, else its path) used everywhere we
# render a relation so the auditor sees something meaningful, never just an id.
def _label(document: Optional[Dict[str, Any]]) -> str:
    if not document:
        return "(unknown)"
    title = (document.get("title") or "").strip()
    return title or document.get("path") or document.get("id") or "(unknown)"


# Resolve one outgoing link row into a display dict. An unresolved link (target
# text that matches no document) is kept and flagged so dangling links are visible.
def _describe_outgoing(database: VaultDB, link: Dict[str, Any]) -> Dict[str, Any]:
    target_id = link.get("dst_document")
    target_doc = database.get_document(target_id) if target_id else None
    return {
        "target": link.get("dst_target"),
        "rel": link.get("rel"),
        "document_id": target_id,
        "title": _label(target_doc) if target_id else None,
        "path": target_doc["path"] if target_doc else None,
        "resolved": target_id is not None,
    }


# Resolve one backlink row (a document that links here) into a display dict.
def _describe_backlink(database: VaultDB, link: Dict[str, Any]) -> Dict[str, Any]:
    source_id = link.get("src_document")
    source_doc = database.get_document(source_id)
    return {
        "document_id": source_id,
        "rel": link.get("rel"),
        "title": _label(source_doc),
        "path": source_doc["path"] if source_doc else None,
    }


# All relations for one document: its outgoing links and its backlinks. Returns an
# error dict if the id is unknown so callers (and the LLM) get a clear message.
def related(note_id: str, database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    document = database.get_document(note_id)
    if document is None:
        return {
            "error": f"no document with id {note_id!r}.",
            "hint": "pass an id from search results or vault_status.",
        }

    outgoing = [_describe_outgoing(database, link) for link in database.outgoing_links(note_id)]
    incoming = [_describe_backlink(database, link) for link in database.backlinks(note_id)]
    return {
        "id": note_id,
        "title": _label(document),
        "path": document.get("path"),
        "outgoing": outgoing,
        "backlinks": incoming,
    }


# Report exact duplicates the indexer recorded: files whose content is identical to
# an already-indexed document (and therefore were not added again). Each entry
# names the duplicate file and the canonical document it matched.
def find_duplicates(database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    entries: List[Dict[str, Any]] = []
    for duplicate in database.list_duplicates():
        canonical = database.get_document(duplicate["canonical_id"])
        entries.append({
            "path": duplicate["path"],
            "canonical_id": duplicate["canonical_id"],
            "canonical_title": _label(canonical),
            "canonical_path": canonical["path"] if canonical else None,
        })
    return {"exact": entries, "count": len(entries)}


# Compact {id, title, path} for one document, used in near-dup report entries.
def _describe_doc(database: VaultDB, document_id: str) -> Dict[str, Any]:
    document = database.get_document(document_id)
    return {
        "id": document_id,
        "title": _label(document),
        "path": document["path"] if document else None,
    }


# Flag pairs of documents whose content is highly similar -- possible duplicates
# for a human to review. It NEVER merges or deletes (per the self-pruning spec:
# "never auto-merge bodies"); it only reports. Similarity is the cosine between
# each document's centroid embedding (the mean of its chunk vectors), so it reuses
# the embeddings already in the index -- no new storage, no schema change.
#
# Exact (byte-identical) duplicates never appear here: identical files are
# recorded in the `duplicates` table and never become a second document, so
# `document_vectors()` returns at most one vector per distinct body.
#
# ponytail: O(n^2) pairwise comparison (one vectorized n x n matmul). Fine at
# personal scale (a few thousand notes). Upgrade path if a vault grows huge:
# block by category, or use an ANN index over the centroids (see deferred D2).
def find_near_duplicates(
    threshold: float = NEAR_DUP_THRESHOLD,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    vectors = database.document_vectors()
    doc_ids = list(vectors)
    if len(doc_ids) < 2:
        return {"near": [], "count": 0, "threshold": threshold}

    # Centroids are unit vectors, so the gram matrix is pairwise cosine similarity.
    matrix = np.vstack([vectors[doc_id] for doc_id in doc_ids])
    similarities = matrix @ matrix.T

    pairs: List[tuple] = []
    for i in range(len(doc_ids)):
        for j in range(i + 1, len(doc_ids)):
            score = float(similarities[i, j])
            if score >= threshold:
                pairs.append((score, doc_ids[i], doc_ids[j]))

    pairs.sort(key=lambda pair: pair[0], reverse=True)  # most similar first
    near = [
        {
            "similarity": round(score, 4),
            "a": _describe_doc(database, a),
            "b": _describe_doc(database, b),
        }
        for score, a, b in pairs
    ]
    return {"near": near, "count": len(near), "threshold": threshold}


# Render the outgoing-links section for one document in the relations map.
def _render_outgoing(outgoing: List[Dict[str, Any]]) -> List[str]:
    if not outgoing:
        return ["  - links to: (none)"]
    lines = []
    for link in outgoing:
        if link["resolved"]:
            lines.append(f"  - links to: {link['title']} ({link['path']})")
        else:
            lines.append(f"  - links to: {link['target']} (unresolved)")
    return lines


# Render the backlinks section for one document in the relations map.
def _render_backlinks(backlinks: List[Dict[str, Any]]) -> List[str]:
    if not backlinks:
        return ["  - linked from: (none)"]
    return [f"  - linked from: {link['title']} ({link['path']})" for link in backlinks]


# Regenerate the auditable relations map at .vault/relations.md. This is a
# generated, read-only view of the whole link graph -- one block per document with
# its outgoing links and backlinks -- so you can audit relations in one file.
# Returns {"path", "documents"}.
def write_relations_map(database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    documents = database.list_documents()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Relations map (generated -- do not edit)",
        "",
        f"Generated: {stamp}",
        f"Documents: {len(documents)}",
        "",
    ]

    # One block per document, ordered by path (list_documents sorts by path).
    for document in documents:
        relation = related(document["id"], database=database)
        lines.append(f"## {relation['title']}")
        lines.append(f"  - path: {document['path']}")
        lines.append(f"  - id: {document['id']}")
        lines.extend(_render_outgoing(relation["outgoing"]))
        lines.extend(_render_backlinks(relation["backlinks"]))
        lines.append("")

    destination: Path = paths()["relations_md"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(destination), "documents": len(documents)}
