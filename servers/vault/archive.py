"""Archival mechanism + TTL/expiry policy + tombstones/undo (build steps 3.4, 3.6, 3.7, 3.8-dry-run).

Archiving is the trustworthy half of self-pruning: a note is never silently
deleted. It is MOVED to `90-archive/` (preserving its sub-path), its frontmatter
`status` flips to "archived", and a tombstone row + a line in `.vault/tombstones.md`
record exactly what moved, from where, and why -- so any archival is auditable and
fully reversible. Search still finds archived notes but ranks them below active
ones (the down-rank lives in the DB layer).

Separation of concerns:
  - POLICIES decide *which* notes to archive. This module ships the TTL/expiry
    policy (`run_ttl`): active, non-pinned notes whose `expires:` date has passed.
    Decay-by-score is a later step that will reuse `archive_documents()`.
  - The MECHANISM (`archive_documents`) does the move + tombstone, and `restore`
    undoes it (by note id or by whole prune batch).

Trust guarantees honored here:
  - Dry-run: every entry point previews without touching disk when dry_run=True.
  - Never touch pinned: a hard rule, checked independently of any score/weight.
  - Reversible: `restore` moves the file back to its prev_path and clears the
    tombstone; a `batch` id ties one prune run together for whole-run undo.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ARCHIVE_FOLDER, paths
from .db import VaultDB, get_db
from .index import index
from .note import Note, new_id, now_iso
from .prune import parse_iso, _now, days_since_touch, load_config, load_signals


# Short label for a document/tombstone (title if present, else its path/id).
def _label(document: Optional[Dict[str, Any]]) -> str:
    if not document:
        return "(unknown)"
    title = (document.get("title") or "").strip()
    return title or document.get("path") or document.get("id") or "(unknown)"


# The archive destination for a note currently at `relpath`: same sub-path under
# 90-archive/ so restoring is a trivial move back and the origin is self-evident.
def _archive_relpath(relpath: str) -> str:
    return f"{ARCHIVE_FOLDER}/{relpath}"


# -- selection (the TTL policy) ---------------------------------------------

# Active, non-pinned documents whose `expires:` date is at or before `now`. Pinned
# notes are skipped as a HARD rule (never a weight), per the self-pruning spec.
def _expired_documents(database: VaultDB, now: datetime) -> List[Dict[str, Any]]:
    due = []
    for document in database.list_documents():
        if document.get("status") != "active" or document.get("pinned"):
            continue
        expires = parse_iso(document.get("expires"))
        if expires is not None and expires <= now:
            due.append(document)
    return due


# -- mechanism --------------------------------------------------------------

# Archive a set of documents by id: move each note file to 90-archive/, flip its
# status, and record a tombstone. Skips anything not currently active or pinned
# (defense in depth over the policy). With dry_run=True it changes NOTHING and
# returns the plan. Returns {dry_run, batch, archived|would_archive, count}.
def archive_documents(
    document_ids: List[str],
    reason: str,
    *,
    dry_run: bool = False,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    planned: List[Dict[str, Any]] = []
    for document_id in document_ids:
        document = database.get_document(document_id)
        if not document or document.get("status") != "active" or document.get("pinned"):
            continue
        relpath = document["path"]
        planned.append({
            "id": document_id,
            "title": _label(document),
            "prev_path": relpath,
            "new_path": _archive_relpath(relpath),
            "reason": reason,
        })

    if dry_run:
        return {"dry_run": True, "batch": None, "would_archive": planned, "count": len(planned)}

    batch = new_id()
    for item in planned:
        _archive_one(database, item, batch, reason)

    if planned:
        # One reindex reconciles all the moves (old paths removed, new archived
        # paths added) and refreshes prune scores; then regenerate the audit map.
        index(incremental=True, database=database)
        write_tombstones_map(database)

    return {"dry_run": False, "batch": batch, "archived": planned, "count": len(planned)}


# Move one note into the archive, flip its status, and record its tombstone. The
# frontmatter snapshot is taken BEFORE the flip so restore has the original state.
def _archive_one(database: VaultDB, item: Dict[str, Any], batch: str, reason: str) -> None:
    vault = paths()["vault"]
    source = vault / item["prev_path"]
    note = Note.load(source)
    payload = dict(note.frontmatter)  # snapshot original frontmatter

    note.frontmatter["status"] = "archived"
    destination = vault / item["new_path"]
    note.save(destination)          # writes the archived copy (bumps updated)
    if source.resolve() != destination.resolve():
        source.unlink(missing_ok=True)  # remove the original

    database.record_tombstone({
        "id": new_id(),
        "document_id": item["id"],
        "action": "archived",
        "reason": reason,
        "prev_path": item["prev_path"],
        "payload": payload,
        "at": now_iso(),
        "batch": batch,
    })


# -- policy entry point -----------------------------------------------------

# Run the TTL/expiry policy: archive every active, non-pinned note past its
# `expires:` date. dry_run=True previews only. `now` is injectable for tests.
def run_ttl(
    *,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()
    moment = now or _now()
    due = _expired_documents(database, moment)
    result = archive_documents(
        [document["id"] for document in due],
        reason="ttl: past expires date",
        dry_run=dry_run,
        database=database,
    )
    result["policy"] = "ttl"
    return result


# -- decay selection (the low-value-and-idle policy) ------------------------

# Active, non-pinned documents whose prune_score is at/below `threshold` AND that
# have gone untouched for at least `min_idle_days`. Both gates must hold: a
# low-scoring note that was just used is NOT decayed, and an old note that still
# scores well (pinned/important/hub) is NOT decayed. Pinned notes are skipped as a
# HARD rule (never a weight), per the self-pruning spec.
def _decayed_documents(
    database: VaultDB, now: datetime, threshold: float, min_idle_days: float
) -> List[Dict[str, Any]]:
    signals = load_signals()
    due = []
    for document in database.list_documents():
        if document.get("status") != "active" or document.get("pinned"):
            continue
        score = float(document.get("prune_score") or 0.0)
        if score > threshold:
            continue
        if days_since_touch(document, signals.get(document["id"]), now) < min_idle_days:
            continue
        due.append(document)
    return due


# Run the decay policy: archive every active, non-pinned note that is both
# low-value (prune_score <= threshold) and idle (untouched >= min_idle_days).
# Thresholds default to the tunable weights in .vault/prune.config but can be
# overridden per call (used by tests). dry_run=True previews only. `now` is
# injectable so a test can age the vault without waiting.
def run_decay(
    *,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    threshold: Optional[float] = None,
    min_idle_days: Optional[float] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    config = load_config()
    threshold = config["decay_score_threshold"] if threshold is None else float(threshold)
    min_idle_days = config["decay_min_idle_days"] if min_idle_days is None else float(min_idle_days)
    moment = now or _now()

    due = _decayed_documents(database, moment, threshold, min_idle_days)
    result = archive_documents(
        [document["id"] for document in due],
        reason=f"decay: score<={threshold:g} and idle>={min_idle_days:g}d",
        dry_run=dry_run,
        database=database,
    )
    result["policy"] = "decay"
    return result


# -- undo / restore ---------------------------------------------------------

# Restore archived note(s) from their tombstones: move each file back to its
# prev_path, set status back to active, reindex, and clear the tombstone. Select
# by a single `note_id`, by a whole prune `batch`, or (note_id within batch) both.
# Returns {restored, count} or an error dict when nothing matches.
def restore(
    *,
    note_id: Optional[str] = None,
    batch: Optional[str] = None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()

    tombstones = database.list_tombstones(batch=batch)
    if note_id:
        tombstones = [t for t in tombstones if t.get("document_id") == note_id]

    if not tombstones:
        return {
            "error": "no matching tombstone to restore.",
            "hint": "pass a note_id from list_tombstones, or a prune batch id.",
        }

    restored: List[Dict[str, Any]] = []
    for tombstone in tombstones:
        outcome = _restore_one(tombstone)
        if outcome.get("error"):
            outcome["document_id"] = tombstone.get("document_id")
            restored.append(outcome)
            continue
        database.delete_tombstone(tombstone["id"])
        restored.append(outcome)

    index(incremental=True, database=database)
    write_tombstones_map(database)
    return {"restored": restored, "count": sum(1 for r in restored if not r.get("error"))}


# Move one archived note back to its prev_path and mark it active. Returns a
# per-note result (with an error if the archived file is missing).
def _restore_one(tombstone: Dict[str, Any]) -> Dict[str, Any]:
    vault = paths()["vault"]
    prev_path = tombstone["prev_path"]
    archived = vault / _archive_relpath(prev_path)

    if not archived.exists():
        return {
            "error": f"archived file not found at {_archive_relpath(prev_path)}; "
                     "it may have been moved or already restored.",
            "prev_path": prev_path,
        }

    note = Note.load(archived)
    note.frontmatter["status"] = "active"
    destination = vault / prev_path
    note.save(destination)
    if archived.resolve() != destination.resolve():
        archived.unlink(missing_ok=True)

    return {"document_id": tombstone.get("document_id"), "restored_to": prev_path}


# -- audit map --------------------------------------------------------------

# Read the tombstone log for callers/tools (payload parsed). Thin passthrough so
# the server layer never imports the DB module directly for this.
def list_tombstones(database: Optional[VaultDB] = None) -> List[Dict[str, Any]]:
    database = database or get_db()
    database.migrate()
    return database.list_tombstones()


# Regenerate the human-readable prune log at .vault/tombstones.md: one block per
# tombstone (what, from where, to where, when, why, which batch) so you can audit
# every archival by eye. Returns {"path", "entries"}.
def write_tombstones_map(database: Optional[VaultDB] = None) -> Dict[str, Any]:
    database = database or get_db()
    tombstones = database.list_tombstones()
    stamp = now_iso()

    lines = [
        "# Tombstones (generated -- the reversible prune/archival log)",
        "",
        f"Generated: {stamp}",
        f"Entries: {len(tombstones)}",
        "",
    ]
    for tombstone in tombstones:
        prev_path = tombstone.get("prev_path", "")
        lines.append(f"## {tombstone.get('action', 'archived')}: {prev_path}")
        lines.append(f"  - document_id: {tombstone.get('document_id')}")
        lines.append(f"  - reason: {tombstone.get('reason')}")
        lines.append(f"  - at: {tombstone.get('at')}")
        lines.append(f"  - batch: {tombstone.get('batch')}")
        if tombstone.get("action") == "archived":
            lines.append(f"  - now at: {_archive_relpath(prev_path)}")
        lines.append("")

    destination: Path = paths()["tombstones_md"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(destination), "entries": len(tombstones)}
