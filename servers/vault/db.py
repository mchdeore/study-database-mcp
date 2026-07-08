"""Relational index interface & backend factory (build steps 0.5 / 0.6).

The vault is the source of truth; this relational layer is a DERIVED index that
can be dropped and rebuilt from the vault. Two interchangeable backends sit
behind one interface:

  - SQLite (default): zero-ops, single file, brute-force cosine in numpy. Runs
    fully offline -- this is what the tests use.
  - Postgres + pgvector (opt-in via VAULT_DB=postgres): real SQL + ANN vector
    index for scale.

Pick the backend with `get_db()`, which reads VAULT_DB from config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from .config import config


# Mean of a list of vectors, L2-normalized to unit length so a plain dot product
# between two results is their cosine similarity. Shared by both backends'
# `document_vectors()`. A zero vector (no/empty chunks) is returned unchanged.
def unit_mean(vectors: List[np.ndarray]) -> np.ndarray:
    centroid = np.mean(np.vstack(vectors), axis=0)
    norm = np.linalg.norm(centroid)
    return centroid / norm if norm else centroid


@dataclass
class VaultHit:
    """One search result: the chunk text plus everything needed to cite it."""

    chunk_id: str
    document_id: str
    source: str  # the note's path within the vault
    title: str
    heading_path: str
    page: Optional[int]
    text: str
    score: float
    status: str = "active"  # active | archived -- lets callers spot stale hits

    # The citation payload returned to callers (mirrors the knowledge server's
    # shape so existing clients understand it).
    def citation(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "title": self.title,
            "heading_path": self.heading_path,
            "page": self.page,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "status": self.status,
        }


class VaultDB(ABC):
    """The relational index contract both backends implement."""

    # Create all tables and indexes if absent. Must be idempotent (safe to run
    # repeatedly, e.g. on every boot).
    @abstractmethod
    def migrate(self) -> None: ...

    # A quick liveness + shape report: backend name and row counts.
    @abstractmethod
    def health(self) -> Dict[str, Any]: ...

    # Insert or update one document row from a document dict.
    @abstractmethod
    def upsert_document(self, document: Dict[str, Any]) -> None: ...

    # Replace ALL chunks for a document: delete existing, insert the given chunk
    # dicts with their (already-normalized) embedding vectors.
    @abstractmethod
    def replace_chunks(
        self, document_id: str, chunks: List[Dict[str, Any]], vectors: np.ndarray
    ) -> None: ...

    # Replace ALL outgoing links for a document.
    @abstractmethod
    def replace_links(self, document_id: str, links: List[Dict[str, Any]]) -> None: ...

    # Replace ALL time-stamped events for a document. Each event dict carries:
    # {id, document_id, title, start_at, end_at, source}. Events are DERIVED from
    # note frontmatter at index time (a note with a `start:`), so a rebuild
    # reconstructs them from the vault -- the DB stays disposable.
    @abstractmethod
    def replace_events(self, document_id: str, events: List[Dict[str, Any]]) -> None: ...

    # Delete a document and its dependent rows (chunks, links) by document id.
    @abstractmethod
    def delete_document(self, document_id: str) -> None: ...

    # Look up a document id by its source path, or None. Used when a note is
    # deleted on disk and must be removed from the index.
    @abstractmethod
    def document_id_for_source(self, source: str) -> Optional[str]: ...

    # Fetch one document row as a dict, or None.
    @abstractmethod
    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]: ...

    # List all documents as dicts (id, path, title, frontmatter, ...). Used by
    # relation resolution and the auditable relations map.
    @abstractmethod
    def list_documents(self) -> List[Dict[str, Any]]: ...

    # List all events (ordered by start_at) as dicts, for timeline/reporting.
    @abstractmethod
    def list_events(self) -> List[Dict[str, Any]]: ...

    # Find an existing document whose body hash matches (exact-duplicate detection),
    # or None. Used to refuse/record identical documents.
    @abstractmethod
    def find_document_by_body_hash(self, body_hash: str) -> Optional[str]: ...

    # Find a document id by its external `source_ref` (e.g. "gcal://event/abc"),
    # or None. Connectors use this to re-sync an item into the SAME note instead
    # of creating a duplicate on every sync.
    @abstractmethod
    def find_document_by_source_ref(self, source_ref: str) -> Optional[str]: ...

    # Outgoing links of a document: list of {dst_target, dst_document, rel}.
    @abstractmethod
    def outgoing_links(self, document_id: str) -> List[Dict[str, Any]]: ...

    # Backlinks to a document: list of {src_document, rel} (documents linking here).
    @abstractmethod
    def backlinks(self, document_id: str) -> List[Dict[str, Any]]: ...

    # Resolve wikilink targets to document ids: given a {slug -> document_id} map,
    # fill links.dst_document wherever the target text matches a known slug.
    @abstractmethod
    def update_link_targets(self, slug_to_id: Dict[str, str]) -> None: ...

    # Record a file as an exact duplicate of a canonical document (so it isn't
    # indexed twice). Keyed by the duplicate's path.
    @abstractmethod
    def upsert_duplicate(self, path: str, canonical_id: str, body_hash: str) -> None: ...

    # List recorded exact duplicates: [{path, canonical_id, body_hash}].
    @abstractmethod
    def list_duplicates(self) -> List[Dict[str, Any]]: ...

    # Remove a duplicate record by path (no-op if absent).
    @abstractmethod
    def delete_duplicate(self, path: str) -> None: ...

    # Write access signals onto a document row (cache of the signals sidecar).
    @abstractmethod
    def set_signals(self, document_id: str, last_access: str, access_count: int) -> None: ...

    # Write the recomputed prune_score onto a document row.
    @abstractmethod
    def set_prune_score(self, document_id: str, prune_score: float) -> None: ...

    # Row counts per table, for tests and the health report.
    @abstractmethod
    def counts(self) -> Dict[str, int]: ...

    # Vector search: return the top-k chunks for a normalized query vector,
    # optionally filtered (category / source / status).
    @abstractmethod
    def search(
        self, query_vector: np.ndarray, k: int, filters: Optional[Dict[str, Any]] = None
    ) -> List[VaultHit]: ...

    # Lexical (BM25) search: return the top-k chunks matching the query TERMS,
    # optionally filtered. The lexical half of hybrid retrieval -- complements
    # vector search on exact terms (names, codes, error strings). Only chunks
    # containing at least one query term are returned.
    @abstractmethod
    def lexical_search(
        self, query: str, k: int, filters: Optional[Dict[str, Any]] = None
    ) -> List[VaultHit]: ...

    # Per-document embedding: {document_id -> L2-normalized centroid of its chunk
    # vectors}. Powers near-duplicate detection (pairwise cosine). Documents with
    # no chunks are omitted. Use `unit_mean()` to build each centroid.
    @abstractmethod
    def document_vectors(self) -> Dict[str, np.ndarray]: ...

    # Drop the derived tables (documents, chunks, entities, links, events) and
    # recreate empty ones. The tombstones audit log is intentionally preserved.
    @abstractmethod
    def drop_derived(self) -> None: ...

    # Record an archival/deletion tombstone (the reversible prune log). The dict
    # carries: id, document_id, action, reason, prev_path, payload (frontmatter
    # snapshot), at (iso), batch (the prune-run id, for batch undo).
    @abstractmethod
    def record_tombstone(self, tombstone: Dict[str, Any]) -> None: ...

    # List tombstones (newest-ordering by `at`), optionally only those from one
    # prune `batch`. `payload` is returned parsed back into a dict.
    @abstractmethod
    def list_tombstones(self, batch: Optional[str] = None) -> List[Dict[str, Any]]: ...

    # Remove a tombstone by id (used when a note is restored from it).
    @abstractmethod
    def delete_tombstone(self, tombstone_id: str) -> None: ...

    # Release any open connection/handles.
    @abstractmethod
    def close(self) -> None: ...


# Build the backend. SQLite is the single backend: zero-ops, stdlib, and it
# already stores chunk embeddings (float32 blobs) for brute-force cosine + BM25
# hybrid search at personal scale. Postgres+pgvector was removed as app/deploy
# surface; it's the documented upgrade path if a vault ever outgrows brute-force
# search (see docs/vision/03-architecture.md).
def get_db() -> VaultDB:
    from .db_sqlite import SQLiteVaultDB

    return SQLiteVaultDB()
