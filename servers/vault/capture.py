"""Low-friction write tools for the LLM (build steps 1.1-1.5).

These are the "drop a thought and find it later" path. Each tool writes a
well-formed note into the vault (correct frontmatter via the Note model) and then
runs an incremental index so the new note is immediately searchable. Because the
vault is the source of truth, every write is just a Markdown file you can audit.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..knowledge.store import get_embedder
from .config import ensure_layout, paths
from .db import VaultDB, get_db
from .index import body_hash_of, index
from .note import Note
from .relations import NEAR_DUP_THRESHOLD
from .search import resolve_document
from .textutil import slugify
from .walk import note_relpath

# How many characters of a note's first line to show as an inbox summary.
_SUMMARY_CHARS = 160


# Pick a unique file path in `folder` for `slug`, appending a short suffix from
# the note id if a file with that name already exists (so two captures with the
# same title don't collide).
def _unique_path(folder: Path, slug: str, note_id: str) -> Path:
    candidate = folder / f"{slug}.md"
    if not candidate.exists():
        return candidate
    return folder / f"{slug}-{note_id[-6:].lower()}.md"


# Index the single just-written note and report whether it was indexed. Keeps
# capture latency to one note's worth of work (the manifest skips everything else).
def _index_after_write(database: Optional[VaultDB]) -> List[str]:
    return index(incremental=True, database=database)["indexed"]


# Capture free text as a new note. `category` is a vault-relative folder (default
# the inbox); `tags`/`title` are optional.
#
# Smart dedup: byte-identical content is always refused (points at the original).
# Beyond that, if a highly-similar note already exists, `on_similar` decides:
#   - "warn"   (default): still create, but include `similar_to` in the result so
#              the caller can choose to consolidate instead.
#   - "skip":  don't create; return the match (no duplicate is made).
#   - "append": consolidate -- append this text into the matching note.
# `force_new=True` bypasses the similarity check and always creates a new note.
# Returns the note id, path, and what was indexed.
def capture(
    text: str,
    category: str = "00-inbox",
    tags: Optional[List[str]] = None,
    title: Optional[str] = None,
    source: str = "capture",
    on_similar: str = "warn",
    similarity_threshold: float = NEAR_DUP_THRESHOLD,
    force_new: bool = False,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    if not text or not text.strip():
        return {"error": "empty capture text", "hint": "pass the note content as `text`."}

    ensure_layout()
    database = database or get_db()
    database.migrate()

    note = Note(body=_body_with_title(title, text))
    note.ensure_defaults(source=source, category=category)
    if title:
        note.frontmatter["title"] = title
    if tags:
        note.frontmatter["tags"] = list(tags)

    # Exact dedup: refuse content byte-for-byte identical to an existing note.
    duplicate = _existing_duplicate(database, note.body)
    if duplicate is not None:
        return duplicate

    # Smart near-dedup: find the closest existing note and act per `on_similar`.
    similar: Optional[Dict[str, Any]] = None
    if not force_new and on_similar in ("warn", "skip", "append"):
        match = _best_similar(database, note.body, similarity_threshold)
        if match is not None:
            match_id, score = match
            existing = database.get_document(match_id)
            if on_similar == "skip":
                return {
                    "created": False,
                    "similar_to": _match_info(existing, score),
                    "note": "a very similar note already exists; not added (on_similar='skip').",
                    "hint": "append to it with append_to_note('<id>', text), or force a "
                            "separate note with force_new=True.",
                }
            if on_similar == "append":
                appended = append_to_note(match_id, text, database=database)
                appended["consolidated_into"] = match_id
                appended["similarity"] = round(float(score), 4)
                return appended
            similar = _match_info(existing, score)  # "warn": fall through and create

    folder = paths()["vault"] / category
    destination = _unique_path(folder, slugify(title or text), note.frontmatter["id"])
    note.save(destination)

    result: Dict[str, Any] = {
        "id": note.frontmatter["id"],
        "path": note_relpath(destination),
        "indexed": _index_after_write(database),
    }
    if similar is not None:
        result["similar_to"] = similar
        result["hint"] = ("a similar note exists; created a separate note anyway. To consolidate "
                          "instead, use append_to_note or capture(..., on_similar='append').")
    return result


# The closest existing document to `text` by cosine of the whole-text embedding
# against each document's centroid, or None if nothing meets `threshold`. Reuses
# the embeddings already in the index (document_vectors) -- no new storage.
# ponytail: O(n) scan + one extra embed of the new text; fine at personal scale.
def _best_similar(
    database: VaultDB, text: str, threshold: float
) -> Optional[tuple]:
    vectors = database.document_vectors()
    if not vectors:
        return None

    query = np.asarray(get_embedder().embed([text])[0], dtype=np.float32).ravel()
    norm = float(np.linalg.norm(query))
    if not norm:
        return None
    query = query / norm

    best_id, best_score = None, -1.0
    for document_id, centroid in vectors.items():
        score = float(np.dot(query, centroid))
        if score > best_score:
            best_id, best_score = document_id, score

    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None


# Compact description of a matched note, for the `similar_to` payload.
def _match_info(document: Optional[Dict[str, Any]], score: float) -> Dict[str, Any]:
    if not document:
        return {"similarity": round(float(score), 4)}
    return {
        "id": document.get("id"),
        "title": (document.get("title") or "").strip() or document.get("path"),
        "path": document.get("path"),
        "similarity": round(float(score), 4),
    }


# Append text to an existing note (found by document id / vault path / source_ref)
# and re-index it. This is the consolidation primitive: instead of creating a
# near-duplicate, add the new content to the note it belongs with.
def append_to_note(ref: str, text: str, database: Optional[VaultDB] = None) -> Dict[str, Any]:
    if not text or not text.strip():
        return {"error": "empty text", "hint": "pass the content to append as `text`."}

    ensure_layout()
    database = database or get_db()
    database.migrate()

    document = resolve_document(database, (ref or "").strip())
    if document is None:
        return {"error": f"no note found for {ref!r}.",
                "hint": "pass a document id (from search), a vault path, or a source_ref."}

    note_path = paths()["vault"] / document["path"]
    if not note_path.exists():
        return {"error": f"note file is missing at {document['path']}.",
                "hint": "run reindex to reconcile the index with the vault."}

    note = Note.load(note_path)
    note.body = note.body.rstrip("\n") + "\n\n" + text.strip() + "\n"
    note.save(note_path)
    return {"id": document["id"], "path": document["path"], "appended": True,
            "indexed": _index_after_write(database)}


# If an existing document already has this exact body, return a "not added"
# result pointing at it; otherwise None. This is the capture-time dedup guard.
def _existing_duplicate(database: VaultDB, body: str) -> Optional[Dict[str, Any]]:
    existing_id = database.find_document_by_body_hash(body_hash_of(body))
    if not existing_id:
        return None
    existing = database.get_document(existing_id)
    return {
        "duplicate_of": existing_id,
        "id": existing_id,
        "path": existing["path"] if existing else None,
        "indexed": [],
        "note": "identical content already exists; not added.",
    }


# Build a note body, prepending an H1 title heading when a title is given (so the
# Markdown reads well and the chunker sees the heading).
def _body_with_title(title: Optional[str], text: str) -> str:
    if title:
        return f"# {title}\n\n{text.strip()}\n"
    return f"{text.strip()}\n"


# A titled note: thin convenience over capture with an explicit title + body.
def quick_note(
    title: str,
    body: str,
    category: str = "00-inbox",
    tags: Optional[List[str]] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    if not title or not title.strip():
        return {"error": "empty title", "hint": "pass a non-empty `title`."}
    return capture(body or "", category=category, tags=tags, title=title.strip(),
                   source="capture", database=database)


# Append a timestamped entry to today's daily journal note, creating the dated
# file (under 10-journal/<year>/) if it doesn't exist yet.
def append_to_journal(
    text: str,
    when: Optional[datetime] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    if not text or not text.strip():
        return {"error": "empty journal text", "hint": "pass the entry content as `text`."}

    ensure_layout()
    moment = when or datetime.now().astimezone()
    note, destination = _load_or_create_daily(moment)

    note.body = note.body.rstrip("\n") + f"\n\n## {moment.strftime('%H:%M')}\n\n{text.strip()}\n"
    note.save(destination)
    return {"id": note.frontmatter["id"], "path": note_relpath(destination),
            "indexed": _index_after_write(database)}


# Load today's journal note if it exists, or build a fresh one with a date H1.
def _load_or_create_daily(moment: datetime) -> tuple[Note, Path]:
    date_text = moment.strftime("%Y-%m-%d")
    destination = paths()["10-journal"] / moment.strftime("%Y") / f"{date_text}.md"

    if destination.exists():
        return Note.load(destination), destination

    note = Note(body=f"# {date_text}\n")
    note.ensure_defaults(source="journal", category="10-journal")
    note.frontmatter["title"] = date_text
    return note, destination


# List the unfiled notes sitting in the inbox so a human (or the regroup job) can
# triage them. Returns a short summary per note.
def list_inbox() -> List[Dict[str, Any]]:
    inbox = paths()["00-inbox"]
    if not inbox.exists():
        return []

    entries = []
    for path in sorted(inbox.glob("*.md")):
        note = Note.load(path)
        entries.append({
            "id": note.frontmatter.get("id"),
            "title": note.frontmatter.get("title", path.stem),
            "path": note_relpath(path),
            "created": note.frontmatter.get("created"),
            "summary": _summary(note.body),
        })
    return entries


# First meaningful line of a note body (skipping headings/blanks), truncated.
def _summary(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:_SUMMARY_CHARS]
    return ""
