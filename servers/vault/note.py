"""The Note model: load/save Markdown + frontmatter, stable ids (build step 0.3).

A Note couples a frontmatter dict with a Markdown body. Identity is the `id`
field in frontmatter (a ULID), NOT the filename -- so files can be renamed or
moved freely without breaking links in the derived index. Saving a note that
lacks an id assigns one and fills sensible defaults.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from . import frontmatter as fm

# Canonical order frontmatter fields are written in, so every note looks the same
# and diffs stay clean.
FIELD_ORDER = [
    "id",
    "title",
    "category",
    "created",
    "updated",
    "source",
    "source_ref",
    "tags",
    "people",
    "importance",
    "pinned",
    "expires",
    "status",
]

# Default importance for a fresh note (0-5 scale; pruning can adjust later).
DEFAULT_IMPORTANCE = 2

# Crockford base32 alphabet for ULIDs (no I, L, O, U to avoid ambiguity).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_TIME_CHARS = 10
_ULID_RANDOM_CHARS = 16


# Current UTC time as an ISO-8601 string with timezone, used for created/updated.
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Generate a ULID: 48 bits of millisecond timestamp + 80 bits of randomness,
# Crockford-base32 encoded. Lexicographically sortable by creation time, which
# is handy for stable ordering. Why ULID over uuid4: time-sortable + compact.
def new_id() -> str:
    milliseconds = int(time.time() * 1000)
    randomness = secrets.randbits(80)
    value = (milliseconds << 80) | randomness

    digits = []
    for _ in range(_ULID_TIME_CHARS + _ULID_RANDOM_CHARS):
        digits.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(digits))


@dataclass
class Note:
    """A vault note: frontmatter metadata plus a Markdown body."""

    frontmatter: Dict[str, Any] = field(default_factory=dict)
    body: str = ""
    path: Path | None = None

    # Load a note from disk, splitting frontmatter from body. The path is kept so
    # the note knows where it came from (used by the indexer for the source ref).
    @classmethod
    def load(cls, path: Path) -> "Note":
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        parsed_frontmatter, body = fm.split_note(text)
        return cls(frontmatter=parsed_frontmatter, body=body, path=Path(path))

    # Fill any missing required frontmatter with defaults and assign an id if the
    # note doesn't have one yet. Returns self so it can be chained.
    def ensure_defaults(self, *, source: str = "capture", category: str = "00-inbox") -> "Note":
        meta = self.frontmatter
        meta.setdefault("id", new_id())
        meta.setdefault("title", self._fallback_title())
        meta.setdefault("category", category)
        timestamp = now_iso()
        meta.setdefault("created", timestamp)
        meta["updated"] = meta.get("updated", timestamp)
        meta.setdefault("source", source)
        meta.setdefault("source_ref", None)
        meta.setdefault("tags", [])
        meta.setdefault("people", [])
        meta.setdefault("importance", DEFAULT_IMPORTANCE)
        meta.setdefault("pinned", False)
        meta.setdefault("expires", None)
        meta.setdefault("status", "active")
        return self

    # Derive a human title from the first Markdown heading or the filename, used
    # only when the note has no explicit title.
    def _fallback_title(self) -> str:
        for line in self.body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        if self.path is not None:
            return self.path.stem.replace("-", " ").strip()
        return "Untitled"

    # Serialize the note (frontmatter in canonical field order, then body) and
    # write it to `target` (or its own path). Bumps `updated`. Creates parent
    # folders as needed.
    def save(self, target: Path | None = None) -> Path:
        destination = Path(target) if target is not None else self.path
        if destination is None:
            raise ValueError(
                "cannot save a note with no path. Pass save(target=...) or set note.path."
            )

        self.ensure_defaults()
        self.frontmatter["updated"] = now_iso()
        ordered = self._ordered_frontmatter()

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(fm.dump_note(ordered, self.body), encoding="utf-8")
        self.path = destination
        return destination

    # Return the frontmatter as a new dict with known fields first (in
    # FIELD_ORDER) and any extra custom fields appended in their existing order.
    def _ordered_frontmatter(self) -> Dict[str, Any]:
        ordered: Dict[str, Any] = {}
        for key in FIELD_ORDER:
            if key in self.frontmatter:
                ordered[key] = self.frontmatter[key]
        for key, value in self.frontmatter.items():
            if key not in ordered:
                ordered[key] = value
        return ordered

    # Convenience accessor for the note's stable id (or None before defaults).
    @property
    def id(self) -> str | None:
        return self.frontmatter.get("id")
