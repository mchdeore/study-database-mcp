"""SQLite implementation of the vault relational index (build steps 0.5 / 0.6).

Zero-ops and stdlib-only: the schema lives in one SQLite file under `.vault/`,
embeddings are stored as float32 blobs, and vector search is a brute-force cosine
scan in numpy. For a personal corpus (a few thousand to tens of thousands of
chunks) this is fast and needs no server. Scale up later with VAULT_DB=postgres.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .config import paths
from .db import VaultDB, VaultHit, unit_mean

# Derived tables are rebuildable from the vault; `tombstones` is an audit log and
# is deliberately NOT in this list so it survives a rebuild.
DERIVED_TABLES = ["documents", "chunks", "entities", "links", "events", "duplicates"]

# How far archived notes are pushed down in search ranking. Cosine lives in
# [-1, 1], so subtracting 2.0 guarantees any archived hit ranks below any active
# one while archived notes remain findable (3.6: "still searchable, ranked below").
_ARCHIVED_RANK_PENALTY = 2.0

# Index name -> the CREATE statement, so tests can assert the exact set exists.
EXPECTED_INDEXES = {
    "idx_documents_status": "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)",
    "idx_documents_category": "CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category)",
    "idx_documents_prune_score": "CREATE INDEX IF NOT EXISTS idx_documents_prune_score ON documents(prune_score)",
    "idx_documents_body_hash": "CREATE INDEX IF NOT EXISTS idx_documents_body_hash ON documents(body_hash)",
    "idx_chunks_document": "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)",
    "idx_links_src": "CREATE INDEX IF NOT EXISTS idx_links_src ON links(src_document)",
    "idx_links_dst": "CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst_document)",
}

# CREATE TABLE statements for the full schema (see docs/vision/05-data-model.md).
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        path TEXT,
        title TEXT,
        category TEXT,
        source TEXT UNIQUE,
        source_ref TEXT,
        content_hash TEXT,
        body_hash TEXT,
        frontmatter TEXT,
        created TEXT,
        updated TEXT,
        status TEXT,
        importance INTEGER,
        pinned INTEGER,
        expires TEXT,
        last_access TEXT,
        access_count INTEGER,
        prune_score REAL
    )""",
    """CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        document_id TEXT,
        ordinal INTEGER,
        heading_path TEXT,
        page INTEGER,
        text TEXT,
        token_count INTEGER,
        embedding BLOB,
        dim INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS entities (
        id TEXT PRIMARY KEY,
        type TEXT,
        name TEXT,
        canonical_id TEXT,
        note_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS links (
        src_document TEXT,
        dst_target TEXT,
        dst_document TEXT,
        rel TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS duplicates (
        path TEXT PRIMARY KEY,
        canonical_id TEXT,
        body_hash TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        document_id TEXT,
        title TEXT,
        start_at TEXT,
        end_at TEXT,
        source TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tombstones (
        id TEXT PRIMARY KEY,
        document_id TEXT,
        action TEXT,
        reason TEXT,
        prev_path TEXT,
        payload TEXT,
        at TEXT,
        batch TEXT
    )""",
]

# The document columns in a fixed order, reused by INSERT to keep it readable.
_DOCUMENT_COLUMNS = [
    "id", "path", "title", "category", "source", "source_ref", "content_hash",
    "body_hash", "frontmatter", "created", "updated", "status", "importance",
    "pinned", "expires", "last_access", "access_count", "prune_score",
]


class SQLiteVaultDB(VaultDB):
    """The default, offline relational index."""

    # Open (and create the parent folder for) the SQLite database file. An
    # explicit path can be passed for tests; otherwise it comes from config.
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path is not None else paths()["db_sqlite"]
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.row_factory = sqlite3.Row

    # Create every table and index if missing. Idempotent.
    def migrate(self) -> None:
        cursor = self.connection.cursor()
        for statement in _SCHEMA:
            cursor.execute(statement)
        for statement in EXPECTED_INDEXES.values():
            cursor.execute(statement)
        # Lightweight forward-migration: add the tombstones.batch column to a DB
        # created before archival existed (CREATE TABLE IF NOT EXISTS won't alter
        # an existing table). Safe + idempotent.
        columns = {row["name"] for row in cursor.execute("PRAGMA table_info(tombstones)")}
        if "batch" not in columns:
            cursor.execute("ALTER TABLE tombstones ADD COLUMN batch TEXT")
        self.connection.commit()

    # Backend name + row counts, for the status tool and tests.
    def health(self) -> Dict[str, Any]:
        return {"backend": "sqlite", "path": str(self.db_path), "counts": self.counts()}

    # Insert or replace one document row (keyed by its stable id).
    def upsert_document(self, document: Dict[str, Any]) -> None:
        values = [self._document_value(document, column) for column in _DOCUMENT_COLUMNS]
        placeholders = ", ".join("?" for _ in _DOCUMENT_COLUMNS)
        columns = ", ".join(_DOCUMENT_COLUMNS)
        self.connection.execute(
            f"INSERT OR REPLACE INTO documents ({columns}) VALUES ({placeholders})", values
        )
        self.connection.commit()

    # Extract one column's value from a document dict, serializing frontmatter to
    # JSON and coercing the boolean `pinned` to an integer for SQLite.
    def _document_value(self, document: Dict[str, Any], column: str) -> Any:
        if column == "frontmatter":
            return json.dumps(document.get("frontmatter", {}), sort_keys=True)
        if column == "pinned":
            return 1 if document.get("pinned") else 0
        return document.get(column)

    # Delete then re-insert all chunks for a document with their embeddings.
    def replace_chunks(
        self, document_id: str, chunks: List[Dict[str, Any]], vectors: np.ndarray
    ) -> None:
        self.connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        rows = [self._chunk_row(document_id, chunks[i], vectors[i]) for i in range(len(chunks))]
        if rows:
            self.connection.executemany(
                """INSERT OR REPLACE INTO chunks
                   (chunk_id, document_id, ordinal, heading_path, page, text, token_count, embedding, dim)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        self.connection.commit()

    # Build one chunk's row tuple, encoding the embedding as float32 bytes.
    def _chunk_row(self, document_id: str, chunk: Dict[str, Any], vector: np.ndarray) -> tuple:
        embedding = np.asarray(vector, dtype=np.float32)
        return (
            chunk["chunk_id"],
            document_id,
            chunk.get("ordinal", 0),
            chunk.get("heading_path", ""),
            chunk.get("page"),
            chunk.get("text", ""),
            chunk.get("token_count", 0),
            embedding.tobytes(),
            int(embedding.shape[0]),
        )

    # Replace all outgoing links for a document. dst_document is left NULL here and
    # filled later by update_link_targets once all documents are known.
    def replace_links(self, document_id: str, links: List[Dict[str, Any]]) -> None:
        self.connection.execute("DELETE FROM links WHERE src_document = ?", (document_id,))
        rows = [
            (document_id, link["dst_target"], link.get("dst_document"), link.get("rel", "mentions"))
            for link in links
        ]
        if rows:
            self.connection.executemany(
                "INSERT INTO links (src_document, dst_target, dst_document, rel) VALUES (?, ?, ?, ?)",
                rows,
            )
        self.connection.commit()

    # Replace all events for a document (delete then insert the derived rows).
    def replace_events(self, document_id: str, events: List[Dict[str, Any]]) -> None:
        self.connection.execute("DELETE FROM events WHERE document_id = ?", (document_id,))
        rows = [
            (event["id"], document_id, event.get("title", ""),
             event.get("start_at"), event.get("end_at"), event.get("source", ""))
            for event in events
        ]
        if rows:
            self.connection.executemany(
                "INSERT OR REPLACE INTO events (id, document_id, title, start_at, end_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        self.connection.commit()

    # Remove a document and all of its dependent rows.
    def delete_document(self, document_id: str) -> None:
        self.connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        self.connection.execute("DELETE FROM links WHERE src_document = ?", (document_id,))
        self.connection.execute("DELETE FROM events WHERE document_id = ?", (document_id,))
        self.connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self.connection.commit()

    # Find a document id by its source path.
    def document_id_for_source(self, source: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT id FROM documents WHERE source = ?", (source,)
        ).fetchone()
        return row["id"] if row else None

    # Fetch one document as a plain dict, or None.
    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return dict(row) if row else None

    # Row counts for each table.
    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for table in DERIVED_TABLES + ["tombstones"]:
            row = self.connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            result[table] = int(row["n"])
        return result

    # Brute-force cosine search: load candidate chunk rows (optionally filtered),
    # score them against the query vector, and return the top k as VaultHits.
    # Archived notes stay searchable but are ranked below active ones (3.6): a
    # fixed penalty is applied for ordering only; the reported score is the true
    # cosine so callers still see real similarity.
    def search(
        self, query_vector: np.ndarray, k: int, filters: Optional[Dict[str, Any]] = None
    ) -> List[VaultHit]:
        rows = self._candidate_rows(filters or {})
        if not rows:
            return []

        matrix, query = self._build_matrix(rows, query_vector)
        similarities = matrix @ query
        penalty = np.array(
            [_ARCHIVED_RANK_PENALTY if row["status"] == "archived" else 0.0 for row in rows],
            dtype=np.float32,
        )
        ranked = similarities - penalty
        top_indexes = np.argsort(-ranked)[: max(1, k)]
        return [self._row_to_hit(rows[i], float(similarities[i])) for i in top_indexes]

    # Lexical (BM25) search over the same candidate chunks the vector search uses.
    # Pure-Python BM25 (servers/vault/lexical.py) -- no FTS table, no schema change.
    # Returns only chunks that contain at least one query term, best first.
    def lexical_search(
        self, query: str, k: int, filters: Optional[Dict[str, Any]] = None
    ) -> List[VaultHit]:
        from .lexical import bm25_scores

        rows = self._candidate_rows(filters or {})
        if not rows:
            return []
        scores = bm25_scores(query, [row["text"] for row in rows])
        ranked = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)

        hits: List[VaultHit] = []
        for index in ranked:
            if scores[index] <= 0.0:
                break  # sorted desc -> everything after is a non-match
            hits.append(self._row_to_hit(rows[index], float(scores[index])))
            if len(hits) >= max(1, k):
                break
        return hits

    # Load chunk rows joined to their document, applying optional equality filters
    # on category / source / status.
    def _candidate_rows(self, filters: Dict[str, Any]) -> List[sqlite3.Row]:
        sql = (
            "SELECT c.chunk_id, c.document_id, c.heading_path, c.page, c.text, "
            "c.embedding, c.dim, d.source, d.title, d.category, d.status "
            "FROM chunks c JOIN documents d ON c.document_id = d.id"
        )
        clauses, values = self._filter_clauses(filters)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return self.connection.execute(sql, values).fetchall()

    # Turn a filter dict into SQL WHERE clauses + bound values. The `category`
    # filter matches the exact category OR any subcategory beneath it (so
    # category="40-areas" also catches "40-areas/calendar").
    def _filter_clauses(self, filters: Dict[str, Any]) -> tuple[List[str], List[Any]]:
        column_for = {"category": "d.category", "source": "d.source", "status": "d.status"}
        clauses: List[str] = []
        values: List[Any] = []
        for key, column in column_for.items():
            if filters.get(key) is None:
                continue
            if key == "category":
                clauses.append(f"({column} = ? OR {column} LIKE ?)")
                values.append(filters[key])
                values.append(filters[key].rstrip("/") + "/%")
            else:
                clauses.append(f"{column} = ?")
                values.append(filters[key])
        return clauses, values

    # Stack the candidate embeddings into a matrix and L2-normalize the query so
    # the dot product is a cosine similarity.
    def _build_matrix(self, rows: List[sqlite3.Row], query_vector: np.ndarray):
        vectors = [np.frombuffer(row["embedding"], dtype=np.float32) for row in rows]
        matrix = np.vstack(vectors)
        query = np.asarray(query_vector, dtype=np.float32).ravel()
        norm = np.linalg.norm(query)
        if norm:
            query = query / norm
        return matrix, query

    # Convert a result row + score into a VaultHit.
    def _row_to_hit(self, row: sqlite3.Row, score: float) -> VaultHit:
        return VaultHit(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source=row["source"],
            title=row["title"],
            heading_path=row["heading_path"],
            page=row["page"],
            text=row["text"],
            score=score,
            status=row["status"],
        )

    # List all documents as dicts (ordered by path for stable output).
    def list_documents(self) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM documents ORDER BY path"
        ).fetchall()
        return [dict(row) for row in rows]

    # List all events (ordered by start time) as dicts.
    def list_events(self) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM events ORDER BY start_at"
        ).fetchall()
        return [dict(row) for row in rows]

    # Per-document centroid embedding (L2-normalized mean of its chunk vectors),
    # for near-duplicate detection. Documents with no chunks are omitted.
    def document_vectors(self) -> Dict[str, np.ndarray]:
        grouped: Dict[str, List[np.ndarray]] = {}
        for row in self.connection.execute("SELECT document_id, embedding FROM chunks"):
            grouped.setdefault(row["document_id"], []).append(
                np.frombuffer(row["embedding"], dtype=np.float32)
            )
        return {doc_id: unit_mean(vectors) for doc_id, vectors in grouped.items()}

    # Find an existing document with this exact body hash, or None.
    def find_document_by_body_hash(self, body_hash: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT id FROM documents WHERE body_hash = ? LIMIT 1", (body_hash,)
        ).fetchone()
        return row["id"] if row else None

    # Find an existing document by its external source_ref, or None.
    def find_document_by_source_ref(self, source_ref: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT id FROM documents WHERE source_ref = ? LIMIT 1", (source_ref,)
        ).fetchone()
        return row["id"] if row else None

    # Outgoing links of a document.
    def outgoing_links(self, document_id: str) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT dst_target, dst_document, rel FROM links WHERE src_document = ?",
            (document_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # Backlinks: documents whose links resolve to this document.
    def backlinks(self, document_id: str) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT src_document, rel FROM links WHERE dst_document = ?", (document_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    # Fill links.dst_document from a {slug -> document_id} map. Clears any stale
    # resolution first so renamed/removed targets don't keep an old id.
    def update_link_targets(self, slug_to_id: Dict[str, str]) -> None:
        self.connection.execute("UPDATE links SET dst_document = NULL")
        for slug, document_id in slug_to_id.items():
            self.connection.execute(
                "UPDATE links SET dst_document = ? WHERE dst_target = ?", (document_id, slug)
            )
        self.connection.commit()

    # Record an exact-duplicate file (keyed by its path).
    def upsert_duplicate(self, path: str, canonical_id: str, body_hash: str) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO duplicates (path, canonical_id, body_hash) VALUES (?, ?, ?)",
            (path, canonical_id, body_hash),
        )
        self.connection.commit()

    # List recorded exact duplicates.
    def list_duplicates(self) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT path, canonical_id, body_hash FROM duplicates ORDER BY path"
        ).fetchall()
        return [dict(row) for row in rows]

    # Remove a duplicate record by path (no-op if absent).
    def delete_duplicate(self, path: str) -> None:
        self.connection.execute("DELETE FROM duplicates WHERE path = ?", (path,))
        self.connection.commit()

    # Write access signals onto a document row (cache of the signals sidecar).
    def set_signals(self, document_id: str, last_access: str, access_count: int) -> None:
        self.connection.execute(
            "UPDATE documents SET last_access = ?, access_count = ? WHERE id = ?",
            (last_access, access_count, document_id),
        )
        self.connection.commit()

    # Write the recomputed prune_score onto a document row.
    def set_prune_score(self, document_id: str, prune_score: float) -> None:
        self.connection.execute(
            "UPDATE documents SET prune_score = ? WHERE id = ?", (prune_score, document_id)
        )
        self.connection.commit()

    # Drop and recreate the derived tables, preserving tombstones.
    def drop_derived(self) -> None:
        for table in DERIVED_TABLES:
            self.connection.execute(f"DROP TABLE IF EXISTS {table}")
        self.connection.commit()
        self.migrate()

    # Record an archival/deletion tombstone (payload stored as a JSON string).
    def record_tombstone(self, tombstone: Dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO tombstones
               (id, document_id, action, reason, prev_path, payload, at, batch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tombstone["id"], tombstone.get("document_id"), tombstone.get("action"),
                tombstone.get("reason"), tombstone.get("prev_path"),
                json.dumps(tombstone.get("payload", {}), sort_keys=True),
                tombstone.get("at"), tombstone.get("batch"),
            ),
        )
        self.connection.commit()

    # List tombstones (ordered by time), optionally only one batch. payload is
    # parsed back into a dict.
    def list_tombstones(self, batch: Optional[str] = None) -> List[Dict[str, Any]]:
        if batch:
            rows = self.connection.execute(
                "SELECT * FROM tombstones WHERE batch = ? ORDER BY at", (batch,)
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM tombstones ORDER BY at").fetchall()

        result = []
        for row in rows:
            entry = dict(row)
            try:
                entry["payload"] = json.loads(entry["payload"]) if entry.get("payload") else {}
            except (json.JSONDecodeError, TypeError):
                entry["payload"] = {}
            result.append(entry)
        return result

    # Remove a tombstone by id (used on restore).
    def delete_tombstone(self, tombstone_id: str) -> None:
        self.connection.execute("DELETE FROM tombstones WHERE id = ?", (tombstone_id,))
        self.connection.commit()

    # Close the connection.
    def close(self) -> None:
        self.connection.close()
