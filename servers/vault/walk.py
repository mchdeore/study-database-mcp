"""Incremental vault walker (build step 0.4).

Enumerates the Markdown notes in the vault and, using the content-hash manifest
borrowed from the knowledge server, reports exactly which notes are new/changed
(need indexing) and which were deleted (need removing). This is the incremental
gate: an unchanged note is never re-processed.

The `.vault/` system folder is skipped so generated machine files never get
treated as notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Set

# Reuse the knowledge server's proven manifest + hashing so the two servers share
# one incremental mechanism.
from ..knowledge.store import Manifest, file_hash

from .config import SYSTEM_FOLDER, paths

# Manifest keys for notes are namespaced so vault entries never collide with the
# knowledge server's raw:/corpus: entries if a manifest is ever shared.
NOTE_KEY_PREFIX = "note:"


# The relative POSIX path of a note within the vault, used as its stable manifest
# key and as the `source` shown in citations.
def note_relpath(path: Path) -> str:
    return path.relative_to(paths()["vault"]).as_posix()


# The manifest key for a note path.
def note_key(path: Path) -> str:
    return f"{NOTE_KEY_PREFIX}{note_relpath(path)}"


# Every Markdown note currently in the vault, excluding the system folder. Sorted
# for deterministic processing (which keeps rebuilds reproducible).
def iter_note_paths() -> List[Path]:
    vault_root = paths()["vault"]
    if not vault_root.exists():
        return []

    notes = []
    for path in sorted(vault_root.rglob("*.md")):
        if SYSTEM_FOLDER in path.relative_to(vault_root).parts:
            continue
        if path.is_file():
            notes.append(path)
    return notes


@dataclass
class VaultScan:
    """The result of comparing the vault against the manifest."""

    to_index: List[Path]  # new or changed notes that must be (re)indexed
    present_keys: Set[str]  # manifest keys for all notes that currently exist
    removed_keys: List[str]  # manifest keys for notes that were deleted on disk


# Compare the vault on disk against the manifest and decide what work is needed.
# When `incremental` is False, every existing note is queued for indexing (used
# by rebuild). Deleted notes are detected by manifest keys with no file.
def scan(manifest: Manifest, incremental: bool = True) -> VaultScan:
    to_index: List[Path] = []
    present_keys: Set[str] = set()

    for path in iter_note_paths():
        key = note_key(path)
        present_keys.add(key)
        digest = file_hash(path)
        if incremental and not manifest.is_changed(key, digest):
            continue
        to_index.append(path)

    removed_keys = [
        key
        for key in manifest.entries
        if key.startswith(NOTE_KEY_PREFIX) and key not in present_keys
    ]
    return VaultScan(to_index=to_index, present_keys=present_keys, removed_keys=removed_keys)
