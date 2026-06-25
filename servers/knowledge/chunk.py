"""Structure-aware Markdown chunker.

Splitting mid-derivation destroys retrieval quality for physics/math, so this
splitter is heading-aware and treats LaTeX blocks and fenced code as atomic:

- Split on heading boundaries (`#`..`######`); a heading's intro prose stays
  with it (the heading starts a new chunk).
- NEVER split inside a fenced code block (``` or ~~~) or a display-math block
  ($$...$$, \\[...\\], or \\begin{env}...\\end{env} for align/equation/etc.).
- Target ~500-800 tokens with ~10% overlap; oversized chunks are allowed rather
  than breaking an equation/derivation.
- Per-chunk metadata: source, heading_path ("Topic > Subtopic"), page, chunk_id.

Token counting is a chars/4 heuristic (ponytail: avoids a tiktoken dependency;
chunk sizing doesn't need exact token counts). Upgrade path: swap _est_tokens
for a real tokenizer if a downstream embedder has a hard token limit.

Page tracking: ingest.py injects `<!-- page: N -->` markers when converting
PDFs; the chunker reads them to attach a `page` to each chunk. Plain .md notes
have no markers and get page=None.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_PAGE_RE = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_MATH_ENV_BEGIN = re.compile(r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?|cases|array|matrix|bmatrix|pmatrix)\}")


@dataclass
class Chunk:
    text: str
    source: str
    heading_path: str
    chunk_id: str
    page: Optional[int] = None
    token_estimate: int = 0
    headings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _est_tokens(text: str) -> int:
    # ponytail: chars/4 approximation, good enough for sizing decisions.
    return max(1, round(len(text) / 4))


# --- Block model -----------------------------------------------------------
@dataclass
class _Block:
    kind: str  # "heading" | "math" | "code" | "text"
    text: str
    level: int = 0          # heading level (1-6) when kind == "heading"
    page: Optional[int] = None


def _split_blocks(md: str) -> List[_Block]:
    """Split markdown into atomic blocks, tracking page markers.

    Protected blocks (code fences, display math) are emitted whole, so a blank
    line inside them never causes a split downstream.
    """
    lines = md.splitlines()
    blocks: List[_Block] = []
    i = 0
    page: Optional[int] = None
    n = len(lines)

    def flush_text(buf: List[str]):
        if buf and any(s.strip() for s in buf):
            blocks.append(_Block("text", "\n".join(buf).strip("\n"), page=page))

    text_buf: List[str] = []
    while i < n:
        line = lines[i]

        # Page marker -> update current page, drop the marker line.
        m = _PAGE_RE.match(line)
        if m:
            flush_text(text_buf)
            text_buf = []
            page = int(m.group(1))
            i += 1
            continue

        # Heading.
        hm = _HEADING_RE.match(line)
        if hm:
            flush_text(text_buf)
            text_buf = []
            blocks.append(_Block("heading", line.strip(), level=len(hm.group(1)), page=page))
            i += 1
            continue

        # Fenced code block: consume until matching closing fence.
        fm = _FENCE_RE.match(line)
        if fm:
            flush_text(text_buf)
            text_buf = []
            fence = fm.group(1)
            buf = [line]
            i += 1
            while i < n and not lines[i].lstrip().startswith(fence):
                buf.append(lines[i])
                i += 1
            if i < n:  # include closing fence
                buf.append(lines[i])
                i += 1
            blocks.append(_Block("code", "\n".join(buf), page=page))
            continue

        # Display math: $$...$$ or \[...\] or \begin{env}...\end{env}.
        stripped = line.strip()
        start_delim = None
        if stripped.startswith("$$"):
            start_delim = "$$"
        elif stripped.startswith("\\["):
            start_delim = "\\]"
        elif _MATH_ENV_BEGIN.search(line):
            env = _MATH_ENV_BEGIN.search(line).group(1)
            start_delim = f"\\end{{{env}}}"

        if start_delim is not None:
            flush_text(text_buf)
            text_buf = []
            buf = [line]
            # single-line $$ ... $$ ?
            closed = False
            if start_delim == "$$" and stripped.count("$$") >= 2:
                closed = True
            i += 1
            while not closed and i < n:
                buf.append(lines[i])
                if start_delim in lines[i]:
                    closed = True
                    i += 1
                    break
                i += 1
            blocks.append(_Block("math", "\n".join(buf), page=page))
            continue

        text_buf.append(line)
        i += 1

    flush_text(text_buf)
    return blocks


def _heading_path(stack: List[str]) -> str:
    return " > ".join(stack)


def chunk_markdown(
    md: str,
    source: str,
    target_tokens: int = 650,
    overlap_ratio: float = 0.10,
) -> List[Chunk]:
    """Chunk a Markdown document into structure-aware, citation-tagged chunks.

    Args:
        md: the Markdown text.
        source: source identifier (e.g. corpus filename) for citations.
        target_tokens: soft target chunk size; equations/code can push past it.
        overlap_ratio: fraction of the previous chunk carried into the next.

    Returns:
        list[Chunk] with stable chunk_ids "<source>#<ordinal>".
    """
    blocks = _split_blocks(md)
    chunks: List[Chunk] = []
    heading_stack: List[str] = []
    ordinal = 0

    # Accumulator for the current chunk.
    cur_blocks: List[_Block] = []
    cur_tokens = 0
    cur_heading_path = ""
    cur_headings: List[str] = []

    def first_page(bs: List[_Block]) -> Optional[int]:
        for b in bs:
            if b.page is not None:
                return b.page
        return None

    def flush():
        nonlocal ordinal, cur_blocks, cur_tokens
        if not cur_blocks:
            return
        text = "\n\n".join(b.text for b in cur_blocks).strip()
        if not text:
            cur_blocks = []
            cur_tokens = 0
            return
        chunks.append(
            Chunk(
                text=text,
                source=source,
                heading_path=cur_heading_path,
                chunk_id=f"{source}#{ordinal}",
                page=first_page(cur_blocks),
                token_estimate=_est_tokens(text),
                headings=list(cur_headings),
            )
        )
        ordinal += 1
        # Build overlap tail for the next chunk (trailing non-protected text).
        overlap_budget = int(target_tokens * overlap_ratio)
        tail: List[_Block] = []
        acc = 0
        for b in reversed(cur_blocks):
            if b.kind in ("code", "math"):
                break  # don't duplicate whole equations/code as overlap
            t = _est_tokens(b.text)
            if acc + t > overlap_budget and tail:
                break
            tail.insert(0, b)
            acc += t
            if acc >= overlap_budget:
                break
        cur_blocks = list(tail)
        cur_tokens = acc

    for b in blocks:
        if b.kind == "heading":
            # New heading starts a new chunk; keep its intro prose with it.
            flush()
            cur_blocks = []
            cur_tokens = 0
            # Update heading stack by level.
            level = b.level
            while heading_stack and len(heading_stack) >= level:
                heading_stack.pop()
            title = b.text.lstrip("#").strip()
            heading_stack.append(title)
            cur_heading_path = _heading_path(heading_stack)
            cur_headings = list(heading_stack)
            cur_blocks.append(b)
            cur_tokens += _est_tokens(b.text)
            continue

        btok = _est_tokens(b.text)
        # If adding this block overflows and we already have content, flush first.
        # Protected blocks are never split; if a single one is oversized it just
        # becomes its own (allowed) large chunk.
        if cur_tokens and cur_tokens + btok > target_tokens:
            flush()
        cur_blocks.append(b)
        cur_tokens += btok

    flush()
    return chunks
