"""YAML-frontmatter parsing & serialization (build step 0.2).

A note is a Markdown file with a `---`-delimited YAML frontmatter block on top
and a Markdown body below. We intentionally support only the small YAML subset
our schema uses -- scalars (string / int / bool / null), inline lists
(`[a, b]`), and block lists (`- a` lines) -- so we need no third-party YAML
dependency and the whole pipeline stays offline-testable.

The serializer is canonical: parse -> serialize -> parse is an identity for any
value this module can produce. If you need full YAML later, swap this for PyYAML
behind the same two functions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# The fence that opens and closes the frontmatter block.
DELIMITER = "---"

# Characters that force a string to be quoted on output so it parses back as a
# string (and not as a list, number, bool, or null).
_QUOTE_TRIGGERS = set(":#[]{},")


# Split a full note's text into (frontmatter dict, body string). A note without
# a leading `---` block is treated as all-body with empty frontmatter, so plain
# Markdown files are still valid input.
def split_note(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith(DELIMITER):
        return {}, text

    lines = text.splitlines()
    closing_index = _find_closing_fence(lines)
    if closing_index is None:
        raise ValueError(
            "frontmatter opened with '---' but was never closed. "
            "Add a closing '---' line after the metadata block."
        )

    block_text = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])
    return parse_block(block_text), body


# Find the line index of the closing `---` fence (searching after the opening
# line). Returns None if there isn't one, so the caller can raise a clear error.
def _find_closing_fence(lines: List[str]) -> int | None:
    for index in range(1, len(lines)):
        if lines[index].strip() == DELIMITER:
            return index
    return None


# Parse the inner text of a frontmatter block into a dict. Blank lines and
# comment lines (`# ...`) are ignored.
def parse_block(block_text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    lines = block_text.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue

        index = _parse_one_entry(lines, index, result)
    return result


# Parse a single `key: value` entry starting at `index`, writing it into
# `result`, and return the index of the next unconsumed line. Handles the case
# where an empty value is followed by a block list.
def _parse_one_entry(lines: List[str], index: int, result: Dict[str, Any]) -> int:
    line = lines[index]
    if ":" not in line:
        raise ValueError(
            f"frontmatter line is not 'key: value': {line!r}. "
            "Use 'key: value', or remove the line."
        )

    key, _, raw_value = line.partition(":")
    key = key.strip()
    raw_value = raw_value.strip()

    # An empty value may introduce a block list on the following lines.
    if raw_value == "":
        items, next_index = _read_block_list(lines, index + 1)
        result[key] = items if items is not None else None
        return next_index if items is not None else index + 1

    result[key] = _parse_value(raw_value)
    return index + 1


# Read consecutive `- item` lines as a list. Returns (items, next_index), or
# (None, start) when the next line is not a block-list item.
def _read_block_list(lines: List[str], start: int) -> Tuple[List[Any] | None, int]:
    items: List[Any] = []
    index = start
    while index < len(lines) and lines[index].lstrip().startswith("- "):
        item_text = lines[index].lstrip()[2:].strip()
        items.append(_parse_scalar(item_text))
        index += 1

    return (items, index) if items else (None, start)


# Parse a value that may be an inline list (`[a, b]`) or a scalar.
def _parse_value(raw_value: str) -> Any:
    if raw_value.startswith("[") and raw_value.endswith("]"):
        return _parse_inline_list(raw_value)
    return _parse_scalar(raw_value)


# Parse an inline `[a, b, c]` list into a list of scalars. An empty `[]` is an
# empty list.
def _parse_inline_list(raw_value: str) -> List[Any]:
    inner = raw_value[1:-1].strip()
    if not inner:
        return []
    return [_parse_scalar(part.strip()) for part in inner.split(",")]


# Parse a single scalar token into None / bool / int / str. Quotes are stripped
# from explicitly quoted strings; everything unrecognized stays a string.
def _parse_scalar(token: str) -> Any:
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        return token[1:-1]

    lowered = token.lower()
    if lowered in ("null", "~", ""):
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if _looks_like_int(token):
        return int(token)
    return token


# True when a token is a plain (optionally signed) integer. Avoids treating
# things like "2026-06-29" or "1.5" as ints.
def _looks_like_int(token: str) -> bool:
    candidate = token[1:] if token[:1] in "+-" else token
    return candidate.isdigit()


# Serialize a frontmatter dict into the inner block text (without the `---`
# fences). Keys are written in the order given so callers control field order.
def serialize_block(frontmatter: Dict[str, Any]) -> str:
    lines = [f"{key}: {_serialize_value(value)}" for key, value in frontmatter.items()]
    return "\n".join(lines)


# Serialize one value back to its canonical text form.
def _serialize_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_serialize_scalar(item) for item in value) + "]"
    return _serialize_scalar(value)


# Serialize a scalar, quoting strings only when needed so they round-trip as
# strings rather than being reinterpreted as null/bool/number/list.
def _serialize_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)

    text = str(value)
    if _needs_quoting(text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


# Decide whether a string must be quoted to survive a parse round-trip.
def _needs_quoting(text: str) -> bool:
    if text == "" or text != text.strip():
        return True
    if text.lower() in ("null", "true", "false", "~"):
        return True
    if _looks_like_int(text):
        return True
    return any(char in _QUOTE_TRIGGERS for char in text)


# Assemble a full note file: frontmatter block between `---` fences, then a blank
# line, then the body.
def dump_note(frontmatter: Dict[str, Any], body: str) -> str:
    block = serialize_block(frontmatter)
    body_text = body if body.endswith("\n") or body == "" else body + "\n"
    return f"{DELIMITER}\n{block}\n{DELIMITER}\n\n{body_text}"
