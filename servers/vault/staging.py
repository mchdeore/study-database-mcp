"""Staging importer: turn `data/incoming/<area>/` drops into live vault notes.

An external agent stages Markdown files (with the frontmatter contract in
`data/incoming/*/README.md`). This importer reads each **content** file and
upserts it into the vault **by its `source_ref`** — reusing the connector write
primitive, so:

  - it lands in the note's declared `category` (e.g. `40-areas/school/phys234`),
  - it gets a stable id and re-imports UPDATE the same note (never duplicate),
  - an unchanged re-import is a no-op.

Instruction/scratch files are skipped (`README.md`, `_template.md`, `_digest.md`,
anything starting with `_` or `.`, and non-`.md`). A content file missing a
`source_ref` is **reported, not guessed** — nothing is invented.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import frontmatter as fm
from .db import VaultDB, get_db
from .connectors import base

# Frontmatter keys the importer sets explicitly or lets the Note model manage —
# everything else is carried through into the vault note as-is.
_MANAGED_KEYS = {"title", "source", "source_ref", "id", "updated"}


# Root of the staging areas: repo `data/incoming/` by default, overridable with
# the STAGING_DIR env var (expands a leading ~).
def staging_root() -> Path:
    configured = os.environ.get("STAGING_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "incoming"


# True for files that are instructions/scratch, not content to import.
def _is_skippable(name: str) -> bool:
    return (
        not name.endswith(".md")
        or name == "README.md"
        or name.startswith("_")   # _template.md, _digest.md, ...
        or name.startswith(".")
    )


# Yield the content files under the staging root (or one area subfolder), sorted
# for deterministic processing, skipping instruction/scratch files.
def _iter_files(root: Path, area: Optional[str]) -> List[Path]:
    base_dir = root / area if area else root
    if not base_dir.exists():
        return []
    return [p for p in sorted(base_dir.rglob("*.md")) if p.is_file() and not _is_skippable(p.name)]


# Import staged files into the vault. `area` limits to one subfolder (e.g. "school").
# dry_run previews (no writes): it still reports which files would be created vs
# updated (by checking source_ref) and which would be skipped. Returns a report.
def import_staging(
    area: Optional[str] = None,
    *,
    root: Optional[Path] = None,
    dry_run: bool = False,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    root = Path(root) if root is not None else staging_root()
    database = database or get_db()
    database.migrate()

    report: Dict[str, Any] = {
        "root": str(root), "area": area or "all", "dry_run": dry_run,
        "created": [], "updated": [], "unchanged": [], "skipped": [], "errors": [],
    }

    for path in _iter_files(root, area):
        rel = str(path.relative_to(root))
        try:
            frontmatter, body = fm.split_note(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as error:  # noqa: BLE001 -- report a bad file, don't abort the batch
            report["errors"].append({"path": rel, "error": str(error)})
            continue

        source_ref = frontmatter.get("source_ref")
        if not source_ref:
            report["skipped"].append({"path": rel, "reason": "no source_ref (not imported; nothing guessed)"})
            continue

        if dry_run:
            exists = database.find_document_by_source_ref(source_ref)
            report["updated" if exists else "created"].append(source_ref)
            continue

        extra = {key: value for key, value in frontmatter.items() if key not in _MANAGED_KEYS}
        outcome = base.upsert_note(
            source=frontmatter.get("source", "file"),
            source_ref=source_ref,
            title=frontmatter.get("title") or path.stem,
            body=body,
            category=frontmatter.get("category") or "00-inbox",
            extra=extra,
            database=database,
        )
        report.setdefault(outcome.get("action", "created"), []).append(source_ref)

    report["counts"] = {
        key: len(report[key]) for key in ("created", "updated", "unchanged", "skipped", "errors")
    }
    return report
