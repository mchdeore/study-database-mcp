"""Indexer: vault Markdown -> relational index (build steps 0.7 / 0.8).

Walks the vault incrementally (content-hash gated), and for each new/changed note
it: adopts the note (fills frontmatter + a stable id if missing), chunks the body
with the knowledge server's structure-aware chunker, embeds the chunks, extracts
wikilinks as graph edges, and loads rows into the relational index. Deleted notes
are removed from the index.

`rebuild_index` drops the derived tables and replays the whole vault, proving the
truth lives in the vault (the rebuild contract, step 0.9).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the knowledge server's chunker, embedder, config, and manifest so the two
# servers share one battle-tested pipeline.
from ..knowledge.chunk import chunk_markdown
from ..knowledge.store import Manifest, config as knowledge_config, file_hash, get_embedder

from .config import ensure_layout, paths
from .db import VaultDB, get_db
from .note import Note
from .textutil import link_target_keys
from .walk import NOTE_KEY_PREFIX, note_key, note_relpath, scan

# Matches Obsidian-style wikilinks: [[target]] or [[target|alias]].
_WIKILINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")


# Content identity for dedup: the SHA-256 of the note BODY (frontmatter excluded).
# Leading/trailing whitespace is normalized away first because a save/load round
# trip adds a leading newline -- so the capture-time hash (in-memory body) and the
# indexer's hash (reloaded body) agree, and notes differing only in surrounding
# whitespace are treated as identical content.
def body_hash_of(body: str) -> str:
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


# Pull wikilink targets out of a note body and turn them into link dicts. Why:
# these become the graph edges that power graph-aware search later.
def extract_wikilinks(body: str) -> List[Dict[str, Any]]:
    targets = []
    seen = set()
    for match in _WIKILINK_RE.findall(body):
        target = match.strip()
        if target and target not in seen:
            seen.add(target)
            targets.append({"dst_target": target, "rel": "mentions"})
    return targets


# Derive time-stamped events from a note's frontmatter. A note with a `start:`
# (ISO datetime/date, set by the calendar connector or by hand) yields one event
# row so it shows up on a timeline; notes without a start yield none. Because
# this is derived from frontmatter, a rebuild reconstructs events from the vault.
#
# ponytail: one event per note (the calendar case). A note describing several
# distinct events would need a richer frontmatter shape -- add that only if a
# real source needs it.
def extract_events(note: "Note", relpath: str) -> List[Dict[str, Any]]:
    meta = note.frontmatter
    start = meta.get("start")
    if not start:
        return []
    return [{
        "id": f"{meta['id']}#event",
        "document_id": meta["id"],
        "title": meta.get("title", ""),
        "start_at": start,
        "end_at": meta.get("end"),
        "source": relpath,
    }]


# Build the document row dict for the relational index from a note. The category
# falls back to the note's top-level folder when frontmatter doesn't set one.
def build_document(note: Note, relpath: str, content_hash: str, body_hash: str) -> Dict[str, Any]:
    meta = note.frontmatter
    return {
        "id": meta["id"],
        "path": relpath,
        "title": meta.get("title", ""),
        "category": meta.get("category") or relpath.split("/", 1)[0],
        "source": relpath,
        "source_ref": meta.get("source_ref"),
        "content_hash": content_hash,
        "body_hash": body_hash,
        "frontmatter": meta,
        "created": meta.get("created"),
        "updated": meta.get("updated"),
        "status": meta.get("status", "active"),
        "importance": meta.get("importance", 0),
        "pinned": bool(meta.get("pinned", False)),
        "expires": meta.get("expires"),
        "last_access": None,
        "access_count": 0,
        "prune_score": 0.0,
    }


# Convert the chunker's Chunk objects into the chunk-row dicts the DB expects,
# numbering them so order is preserved.
def _chunk_rows(chunks) -> List[Dict[str, Any]]:
    rows = []
    for ordinal, chunk in enumerate(chunks):
        rows.append({
            "chunk_id": chunk.chunk_id,
            "ordinal": ordinal,
            "heading_path": chunk.heading_path,
            "page": chunk.page,
            "text": chunk.text,
            "token_count": chunk.token_estimate,
        })
    return rows


# Index a single note: adopt it (persist a stable id/frontmatter if missing), then
# either record it as an exact duplicate of an existing document, or chunk/embed
# and load documents/chunks/links. Returns {"chunks", "duplicate_of"}.
def index_one(database: VaultDB, embedder, path: Path) -> Dict[str, Any]:
    note = Note.load(path)
    needs_adoption = "id" not in note.frontmatter
    note.ensure_defaults(category=note_relpath(path).split("/", 1)[0])
    if needs_adoption:
        note.save(path)  # write frontmatter + id back so identity is stable

    relpath = note_relpath(path)
    note_id = note.frontmatter["id"]
    body_hash = body_hash_of(note.body)

    # Exact dedup: a different document already has this body -> record + skip.
    existing = database.find_document_by_body_hash(body_hash)
    if existing and existing != note_id:
        database.upsert_duplicate(relpath, existing, body_hash)
        return {"chunks": 0, "duplicate_of": existing}

    # Not a duplicate: clear any stale duplicate record for this path, then index.
    database.delete_duplicate(relpath)
    chunk_count = _load_note(database, embedder, note, relpath, body_hash, path)
    return {"chunks": chunk_count, "duplicate_of": None}


# Chunk, embed, and load a (non-duplicate) note's rows. Returns the chunk count.
def _load_note(database, embedder, note, relpath, body_hash, path) -> int:
    cfg = knowledge_config()
    chunks = chunk_markdown(
        note.body, relpath,
        target_tokens=cfg["chunk_target_tokens"],
        overlap_ratio=cfg["chunk_overlap_ratio"],
    )

    database.upsert_document(build_document(note, relpath, file_hash(path), body_hash))
    vectors = embedder.embed([c.text for c in chunks]) if chunks else []
    database.replace_chunks(note.frontmatter["id"], _chunk_rows(chunks), vectors)
    database.replace_links(note.frontmatter["id"], extract_wikilinks(note.body))
    database.replace_events(note.frontmatter["id"], extract_events(note, relpath))
    return len(chunks)


# Run the incremental indexer over the whole vault. When `incremental` is False,
# every note is reprocessed. Returns a report of what changed.
def index(incremental: bool = True, database: Optional[VaultDB] = None) -> Dict[str, Any]:
    ensure_layout()
    database = database or get_db()
    database.migrate()

    manifest = Manifest(paths()["manifest"])
    plan = scan(manifest, incremental=incremental)
    report = {"indexed": [], "removed": [], "skipped": [], "duplicates": [], "chunks": 0}

    _apply_indexing(database, manifest, plan.to_index, report)
    _apply_removals(database, manifest, plan.removed_keys, report)

    # Resolve wikilink targets to real documents so backlinks/relations work, now
    # that every document in this run is known to the index.
    resolve_links(database)

    # Recompute prune scores (and reapply durable access signals) so ranking is
    # current after every index/rebuild. Imported here to avoid an import cycle.
    from . import prune

    prune.refresh(database)

    manifest.save()
    return report


# Index every queued note, updating the manifest per file so a crash leaves a
# consistent record of what was done. Exact duplicates are recorded but not loaded
# as documents (so search never double-counts identical content).
def _apply_indexing(database, manifest, to_index, report) -> None:
    if not to_index:
        return

    embedder = get_embedder()
    for path in to_index:
        result = index_one(database, embedder, path)
        manifest.update(note_key(path), file_hash(path), chunks=result["chunks"])
        if result["duplicate_of"]:
            report["duplicates"].append(
                {"path": note_relpath(path), "canonical_id": result["duplicate_of"]}
            )
        else:
            report["indexed"].append(note_relpath(path))
            report["chunks"] += result["chunks"]


# Remove documents whose note files were deleted on disk, clearing their manifest
# and any duplicate record too.
def _apply_removals(database, manifest, removed_keys, report) -> None:
    for key in removed_keys:
        source = key[len(NOTE_KEY_PREFIX):]
        document_id = database.document_id_for_source(source)
        if document_id:
            database.delete_document(document_id)
        database.delete_duplicate(source)
        manifest.remove(key)
        report["removed"].append(source)


# Build a {target-text -> document-id} map from all documents and fill in
# links.dst_document. A wikilink may use a filename stem or a title; we register
# every reasonable form so [[Jane Doe]] and [[jane-doe]] both resolve.
def resolve_links(database: VaultDB) -> None:
    slug_to_id: Dict[str, str] = {}
    for document in database.list_documents():
        for key in link_target_keys(document["path"], document.get("title") or ""):
            slug_to_id.setdefault(key, document["id"])
    database.update_link_targets(slug_to_id)


# Rebuild the entire index from the vault: drop derived tables, forget the note
# manifest, and reindex everything. The keystone test (0.9) checks that this
# yields the same search results as the incremental path.
def rebuild_index(database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()
    database.drop_derived()

    manifest = Manifest(paths()["manifest"])
    for key in [k for k in manifest.entries if k.startswith(NOTE_KEY_PREFIX)]:
        manifest.remove(key)
    manifest.save()

    return index(incremental=False, database=database)
