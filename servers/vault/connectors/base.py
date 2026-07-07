"""Connector write primitive: upsert a note keyed by `source_ref` (build step 4.5).

This is the dedup guarantee for connectors: the FIRST time an external item is
seen it becomes a new note; every later sync finds the existing note (by its
`source_ref`) and updates it IN PLACE -- same file, same stable id -- so a vault
never accumulates duplicate copies of the same calendar event or email.

An unchanged re-sync is a no-op (returns action="unchanged") so periodic syncs
don't needlessly rewrite files or re-embed chunks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..config import ensure_layout, paths
from ..db import VaultDB, get_db
from ..index import index
from ..note import Note
from ..textutil import slugify
from ..walk import note_relpath


# Pick a unique filename in `folder` for `slug`, disambiguating a collision with a
# short suffix from the note id (mirrors capture's behavior).
def _unique_path(folder: Path, slug: str, note_id: str) -> Path:
    candidate = folder / f"{slug}.md"
    if not candidate.exists():
        return candidate
    return folder / f"{slug}-{note_id[-6:].lower()}.md"


# Upsert a note for an external item. If a document already carries this
# `source_ref`, update it in place; otherwise create a new note in `category`.
# `extra` sets/overrides frontmatter keys (e.g. start/end/expires/tags). Returns
# {action: created|updated|unchanged, id, path, indexed}.
def upsert_note(
    *,
    source: str,
    source_ref: str,
    title: str,
    body: str,
    category: str,
    extra: Optional[Dict[str, Any]] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    if not source_ref:
        raise ValueError("source_ref is required for a connector upsert (it is the dedup key).")

    ensure_layout()
    database = database or get_db()
    database.migrate()
    extra = extra or {}

    existing_id = database.find_document_by_source_ref(source_ref)
    if existing_id:
        return _update_in_place(database, existing_id, title, body, extra)
    return _create(database, source, source_ref, title, body, category, extra)


# Update the existing note for a source_ref. Short-circuits to a no-op when the
# incoming title/body/extra already match what's on disk (so a periodic re-sync of
# unchanged items doesn't rewrite files or force a re-embed).
def _update_in_place(
    database: VaultDB, document_id: str, title: str, body: str, extra: Dict[str, Any]
) -> Dict[str, Any]:
    document = database.get_document(document_id)
    relpath = document["path"]
    path = paths()["vault"] / relpath
    note = Note.load(path)

    unchanged = (
        note.body.strip() == body.strip()
        and note.frontmatter.get("title") == title
        and all(note.frontmatter.get(key) == value for key, value in extra.items())
    )
    if unchanged:
        return {"action": "unchanged", "id": document_id, "path": relpath, "indexed": []}

    note.body = body
    note.frontmatter["title"] = title
    for key, value in extra.items():
        note.frontmatter[key] = value
    note.save(path)
    indexed = index(incremental=True, database=database)["indexed"]
    return {"action": "updated", "id": document_id, "path": relpath, "indexed": indexed}


# Create a fresh note for a never-before-seen source_ref.
def _create(
    database: VaultDB,
    source: str,
    source_ref: str,
    title: str,
    body: str,
    category: str,
    extra: Dict[str, Any],
) -> Dict[str, Any]:
    note = Note(body=body)
    note.ensure_defaults(source=source, category=category)
    note.frontmatter["title"] = title
    note.frontmatter["source_ref"] = source_ref
    for key, value in extra.items():
        note.frontmatter[key] = value

    folder = paths()["vault"] / category
    destination = _unique_path(folder, slugify(title) or "note", note.frontmatter["id"])
    note.save(destination)
    indexed = index(incremental=True, database=database)["indexed"]
    return {"action": "created", "id": note.frontmatter["id"],
            "path": note_relpath(destination), "indexed": indexed}
