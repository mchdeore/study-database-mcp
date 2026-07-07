"""Postgres + pgvector implementation of the vault index (build step 0.5).

Opt-in backend for scale (VAULT_DB=postgres), requiring the `store-postgres`
extra (psycopg + pgvector) and a reachable Postgres with the `vector` extension.
The offline test suite does not exercise this path; it skips cleanly when the
driver isn't installed or the server isn't reachable. Same contract as the
SQLite backend.

Note: for v1 simplicity the embedding column is an unconstrained `vector` and
search is an exact cosine scan (`<=>`). Adding a fixed dimension + an HNSW/IVFFlat
index is a later performance step once the embedder is pinned.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import numpy as np

from .config import config
from .db import VaultDB, VaultHit, unit_mean

DERIVED_TABLES = ["documents", "chunks", "entities", "links", "events", "duplicates"]

_SCHEMA = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        path TEXT,
        title TEXT,
        category TEXT,
        source TEXT UNIQUE,
        source_ref TEXT,
        content_hash TEXT,
        body_hash TEXT,
        frontmatter JSONB,
        created TIMESTAMPTZ,
        updated TIMESTAMPTZ,
        status TEXT,
        importance INT,
        pinned BOOLEAN,
        expires TIMESTAMPTZ,
        last_access TIMESTAMPTZ,
        access_count INT,
        prune_score REAL
    )""",
    """CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
        ordinal INT,
        heading_path TEXT,
        page INT,
        text TEXT,
        token_count INT,
        embedding vector
    )""",
    """CREATE TABLE IF NOT EXISTS entities (
        id TEXT PRIMARY KEY, type TEXT, name TEXT, canonical_id TEXT, note_id TEXT
    )""",
    "CREATE TABLE IF NOT EXISTS links (src_document TEXT, dst_target TEXT, dst_document TEXT, rel TEXT)",
    """CREATE TABLE IF NOT EXISTS duplicates (
        path TEXT PRIMARY KEY, canonical_id TEXT, body_hash TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, document_id TEXT, title TEXT,
        start_at TIMESTAMPTZ, end_at TIMESTAMPTZ, source TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tombstones (
        id TEXT PRIMARY KEY, document_id TEXT, action TEXT, reason TEXT,
        prev_path TEXT, payload JSONB, at TIMESTAMPTZ, batch TEXT
    )""",
]

_EXPECTED_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)",
    "CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category)",
    "CREATE INDEX IF NOT EXISTS idx_documents_prune_score ON documents(prune_score)",
    "CREATE INDEX IF NOT EXISTS idx_documents_body_hash ON documents(body_hash)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)",
    "CREATE INDEX IF NOT EXISTS idx_links_src ON links(src_document)",
    "CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst_document)",
]

_DOCUMENT_COLUMNS = [
    "id", "path", "title", "category", "source", "source_ref", "content_hash",
    "body_hash", "frontmatter", "created", "updated", "status", "importance",
    "pinned", "expires", "last_access", "access_count", "prune_score",
]


class PostgresVaultDB(VaultDB):
    """Postgres-backed relational index with pgvector search."""

    # Connect using POSTGRES_DSN and register the pgvector adapters. Raises a
    # clear error if the extra isn't installed or no DSN was configured.
    def __init__(self, dsn: Optional[str] = None):
        dsn = dsn if dsn is not None else config()["postgres_dsn"]
        if not dsn:
            raise ValueError(
                "VAULT_DB=postgres but POSTGRES_DSN is empty. "
                "Set POSTGRES_DSN, e.g. postgresql://user:pass@localhost:5432/vault"
            )
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except Exception as error:  # noqa: BLE001
            raise RuntimeError(
                "Postgres backend needs the 'store-postgres' extra. "
                "Install it: pip install -e \".[store-postgres]\""
            ) from error

        self.connection = psycopg.connect(dsn, autocommit=True)
        register_vector(self.connection)

    # Create the vector extension, all tables, and indexes. Idempotent.
    def migrate(self) -> None:
        with self.connection.cursor() as cursor:
            for statement in _SCHEMA:
                cursor.execute(statement)
            for statement in _EXPECTED_INDEXES:
                cursor.execute(statement)
            # Forward-migrate a pre-archival DB to add tombstones.batch.
            cursor.execute("ALTER TABLE tombstones ADD COLUMN IF NOT EXISTS batch TEXT")

    # Backend name + row counts.
    def health(self) -> Dict[str, Any]:
        return {"backend": "postgres", "counts": self.counts()}

    # Insert or update one document row (upsert on the id primary key).
    def upsert_document(self, document: Dict[str, Any]) -> None:
        values = [self._document_value(document, column) for column in _DOCUMENT_COLUMNS]
        placeholders = ", ".join("%s" for _ in _DOCUMENT_COLUMNS)
        columns = ", ".join(_DOCUMENT_COLUMNS)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _DOCUMENT_COLUMNS if c != "id")
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO documents ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET {updates}",
                values,
            )

    # Extract a column value, JSON-encoding frontmatter for the JSONB column.
    def _document_value(self, document: Dict[str, Any], column: str) -> Any:
        if column == "frontmatter":
            return json.dumps(document.get("frontmatter", {}), sort_keys=True)
        return document.get(column)

    # Replace all chunks for a document with their embeddings.
    def replace_chunks(
        self, document_id: str, chunks: List[Dict[str, Any]], vectors: np.ndarray
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
            for index, chunk in enumerate(chunks):
                cursor.execute(
                    """INSERT INTO chunks
                       (chunk_id, document_id, ordinal, heading_path, page, text, token_count, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        chunk["chunk_id"], document_id, chunk.get("ordinal", 0),
                        chunk.get("heading_path", ""), chunk.get("page"),
                        chunk.get("text", ""), chunk.get("token_count", 0),
                        np.asarray(vectors[index], dtype=np.float32),
                    ),
                )

    # Replace all outgoing links for a document. dst_document is filled later by
    # update_link_targets once all documents are known.
    def replace_links(self, document_id: str, links: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM links WHERE src_document = %s", (document_id,))
            for link in links:
                cursor.execute(
                    "INSERT INTO links (src_document, dst_target, dst_document, rel) "
                    "VALUES (%s, %s, %s, %s)",
                    (document_id, link["dst_target"], link.get("dst_document"),
                     link.get("rel", "mentions")),
                )

    # Replace all events for a document (delete then insert the derived rows).
    def replace_events(self, document_id: str, events: List[Dict[str, Any]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM events WHERE document_id = %s", (document_id,))
            for event in events:
                cursor.execute(
                    """INSERT INTO events (id, document_id, title, start_at, end_at, source)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id) DO UPDATE SET
                         document_id = EXCLUDED.document_id, title = EXCLUDED.title,
                         start_at = EXCLUDED.start_at, end_at = EXCLUDED.end_at,
                         source = EXCLUDED.source""",
                    (event["id"], document_id, event.get("title", ""),
                     event.get("start_at"), event.get("end_at"), event.get("source", "")),
                )

    # Delete a document; chunks cascade via the foreign key, links + events by hand.
    def delete_document(self, document_id: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM links WHERE src_document = %s", (document_id,))
            cursor.execute("DELETE FROM events WHERE document_id = %s", (document_id,))
            cursor.execute("DELETE FROM documents WHERE id = %s", (document_id,))

    # Find a document id by source path.
    def document_id_for_source(self, source: str) -> Optional[str]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT id FROM documents WHERE source = %s", (source,))
            row = cursor.fetchone()
        return row[0] if row else None

    # Fetch one document as a dict keyed by column name.
    def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM documents WHERE id = %s", (document_id,))
            row = cursor.fetchone()
            if not row:
                return None
            columns = [description[0] for description in cursor.description]
        return dict(zip(columns, row))

    # Row counts per table.
    def counts(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        with self.connection.cursor() as cursor:
            for table in DERIVED_TABLES + ["tombstones"]:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                result[table] = int(cursor.fetchone()[0])
        return result

    # Top-k cosine search via the pgvector `<=>` distance operator. Archived notes
    # stay searchable but rank below active ones (3.6): a fixed penalty is added to
    # their distance for ORDER BY only; the reported score is the true cosine.
    def search(
        self, query_vector: np.ndarray, k: int, filters: Optional[Dict[str, Any]] = None
    ) -> List[VaultHit]:
        clauses, values = self._filter_clauses(filters or {})
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        query = np.asarray(query_vector, dtype=np.float32).ravel()
        sql = (
            "SELECT c.chunk_id, c.document_id, c.heading_path, c.page, c.text, "
            "d.source, d.title, d.status, (c.embedding <=> %s) AS distance "
            "FROM chunks c JOIN documents d ON c.document_id = d.id"
            f"{where} ORDER BY (c.embedding <=> %s) "
            "+ CASE WHEN d.status = 'archived' THEN 2.0 ELSE 0 END LIMIT %s"
        )
        params = [query] + values + [query, max(1, k)]
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return [self._row_to_hit(row) for row in rows]

    # Lexical (BM25-like) search via Postgres full-text search. Untested offline
    # (needs a live Postgres; SQLite proves the hybrid contract). Mirrors the
    # SQLite lexical_search contract: top-k chunks matching the query terms.
    def lexical_search(
        self, query: str, k: int, filters: Optional[Dict[str, Any]] = None
    ) -> List[VaultHit]:
        clauses, values = self._filter_clauses(filters or {})
        where = (" AND " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT c.chunk_id, c.document_id, c.heading_path, c.page, c.text, "
            "d.source, d.title, d.status, "
            "ts_rank(to_tsvector('english', c.text), websearch_to_tsquery('english', %s)) AS rank "
            "FROM chunks c JOIN documents d ON c.document_id = d.id "
            "WHERE to_tsvector('english', c.text) @@ websearch_to_tsquery('english', %s)"
            f"{where} ORDER BY rank DESC LIMIT %s"
        )
        params = [query, query] + values + [max(1, k)]
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        hits: List[VaultHit] = []
        for row in rows:
            chunk_id, document_id, heading_path, page, text, source, title, status, rank = row
            hits.append(VaultHit(
                chunk_id=chunk_id, document_id=document_id, source=source, title=title,
                heading_path=heading_path, page=page, text=text, score=float(rank), status=status,
            ))
        return hits

    # Build WHERE clauses for category / source / status filters. `category`
    # matches the exact category OR any subcategory beneath it.
    def _filter_clauses(self, filters: Dict[str, Any]) -> tuple[List[str], List[Any]]:
        column_for = {"category": "d.category", "source": "d.source", "status": "d.status"}
        clauses: List[str] = []
        values: List[Any] = []
        for key, column in column_for.items():
            if filters.get(key) is None:
                continue
            if key == "category":
                clauses.append(f"({column} = %s OR {column} LIKE %s)")
                values.append(filters[key])
                values.append(filters[key].rstrip("/") + "/%")
            else:
                clauses.append(f"{column} = %s")
                values.append(filters[key])
        return clauses, values

    # Convert a result tuple into a VaultHit (cosine score = 1 - distance).
    def _row_to_hit(self, row: tuple) -> VaultHit:
        chunk_id, document_id, heading_path, page, text, source, title, status, distance = row
        return VaultHit(
            chunk_id=chunk_id,
            document_id=document_id,
            source=source,
            title=title,
            heading_path=heading_path,
            page=page,
            text=text,
            score=1.0 - float(distance),
            status=status,
        )

    # List all documents as dicts (ordered by path).
    def list_documents(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM documents ORDER BY path")
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    # List all events (ordered by start time) as dicts.
    def list_events(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM events ORDER BY start_at")
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    # Per-document centroid embedding (L2-normalized mean of its chunk vectors),
    # for near-duplicate detection. pgvector returns each embedding as a numpy
    # array (register_vector); documents with no chunks are omitted.
    def document_vectors(self) -> Dict[str, np.ndarray]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT document_id, embedding FROM chunks")
            rows = cursor.fetchall()
        grouped: Dict[str, List[np.ndarray]] = {}
        for document_id, embedding in rows:
            grouped.setdefault(document_id, []).append(np.asarray(embedding, dtype=np.float32))
        return {doc_id: unit_mean(vectors) for doc_id, vectors in grouped.items()}

    # Find an existing document with this exact body hash, or None.
    def find_document_by_body_hash(self, body_hash: str) -> Optional[str]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT id FROM documents WHERE body_hash = %s LIMIT 1", (body_hash,))
            row = cursor.fetchone()
        return row[0] if row else None

    # Find an existing document by its external source_ref, or None.
    def find_document_by_source_ref(self, source_ref: str) -> Optional[str]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT id FROM documents WHERE source_ref = %s LIMIT 1", (source_ref,))
            row = cursor.fetchone()
        return row[0] if row else None

    # Outgoing links of a document.
    def outgoing_links(self, document_id: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT dst_target, dst_document, rel FROM links WHERE src_document = %s",
                (document_id,),
            )
            rows = cursor.fetchall()
        return [{"dst_target": r[0], "dst_document": r[1], "rel": r[2]} for r in rows]

    # Backlinks: documents whose links resolve to this document.
    def backlinks(self, document_id: str) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT src_document, rel FROM links WHERE dst_document = %s", (document_id,)
            )
            rows = cursor.fetchall()
        return [{"src_document": r[0], "rel": r[1]} for r in rows]

    # Fill links.dst_document from a {slug -> document_id} map.
    def update_link_targets(self, slug_to_id: Dict[str, str]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE links SET dst_document = NULL")
            for slug, document_id in slug_to_id.items():
                cursor.execute(
                    "UPDATE links SET dst_document = %s WHERE dst_target = %s", (document_id, slug)
                )

    # Record an exact-duplicate file (keyed by its path).
    def upsert_duplicate(self, path: str, canonical_id: str, body_hash: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO duplicates (path, canonical_id, body_hash) VALUES (%s, %s, %s) "
                "ON CONFLICT (path) DO UPDATE SET canonical_id = EXCLUDED.canonical_id, "
                "body_hash = EXCLUDED.body_hash",
                (path, canonical_id, body_hash),
            )

    # List recorded exact duplicates.
    def list_duplicates(self) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT path, canonical_id, body_hash FROM duplicates ORDER BY path")
            rows = cursor.fetchall()
        return [{"path": r[0], "canonical_id": r[1], "body_hash": r[2]} for r in rows]

    # Remove a duplicate record by path (no-op if absent).
    def delete_duplicate(self, path: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM duplicates WHERE path = %s", (path,))

    # Write access signals onto a document row (cache of the signals sidecar).
    def set_signals(self, document_id: str, last_access: str, access_count: int) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE documents SET last_access = %s, access_count = %s WHERE id = %s",
                (last_access, access_count, document_id),
            )

    # Write the recomputed prune_score onto a document row.
    def set_prune_score(self, document_id: str, prune_score: float) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE documents SET prune_score = %s WHERE id = %s", (prune_score, document_id)
            )

    # Drop and recreate the derived tables, preserving tombstones.
    def drop_derived(self) -> None:
        with self.connection.cursor() as cursor:
            for table in DERIVED_TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        self.migrate()

    # Record an archival/deletion tombstone (payload into the JSONB column).
    def record_tombstone(self, tombstone: Dict[str, Any]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO tombstones
                   (id, document_id, action, reason, prev_path, payload, at, batch)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                     document_id = EXCLUDED.document_id, action = EXCLUDED.action,
                     reason = EXCLUDED.reason, prev_path = EXCLUDED.prev_path,
                     payload = EXCLUDED.payload, at = EXCLUDED.at, batch = EXCLUDED.batch""",
                (
                    tombstone["id"], tombstone.get("document_id"), tombstone.get("action"),
                    tombstone.get("reason"), tombstone.get("prev_path"),
                    json.dumps(tombstone.get("payload", {}), sort_keys=True),
                    tombstone.get("at"), tombstone.get("batch"),
                ),
            )

    # List tombstones (ordered by time), optionally only one batch.
    def list_tombstones(self, batch: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            if batch:
                cursor.execute("SELECT * FROM tombstones WHERE batch = %s ORDER BY at", (batch,))
            else:
                cursor.execute("SELECT * FROM tombstones ORDER BY at")
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()

        result = []
        for row in rows:
            entry = dict(zip(columns, row))
            payload = entry.get("payload")
            if isinstance(payload, str):
                try:
                    entry["payload"] = json.loads(payload)
                except (json.JSONDecodeError, TypeError):
                    entry["payload"] = {}
            elif payload is None:
                entry["payload"] = {}
            result.append(entry)
        return result

    # Remove a tombstone by id (used on restore).
    def delete_tombstone(self, tombstone_id: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM tombstones WHERE id = %s", (tombstone_id,))

    # Close the connection.
    def close(self) -> None:
        self.connection.close()
