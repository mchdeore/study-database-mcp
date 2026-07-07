"""Small text helpers shared across the vault package (slugs, link targets).

Kept in its own module so both the capture path and the indexer can use slugify
without creating an import cycle (capture depends on index).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Set

# Cap a generated filename slug so paths stay sane.
_MAX_SLUG_WORDS = 8


# Turn arbitrary text into a safe, hyphenated, lowercase slug. Falls back to
# "note" so we never produce an empty slug.
def slugify(text: str) -> str:
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    words = cleaned.split("-")[:_MAX_SLUG_WORDS]
    slug = "-".join(word for word in words if word)
    return slug or "note"


# The set of strings a wikilink might use to refer to a document: its filename
# stem, its title, and the slug forms of both. Used to resolve [[target]] links
# to real documents regardless of whether the author typed the slug or the title.
def link_target_keys(path: str, title: str) -> Set[str]:
    stem = Path(path).stem
    keys = {stem, slugify(stem)}
    if title:
        keys.add(title)
        keys.add(slugify(title))
    return {key for key in keys if key}
