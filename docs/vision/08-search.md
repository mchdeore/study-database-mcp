# 08 — Search (hybrid: vector + graph)

Goal: the most relevant, *cited* context for an LLM, time-efficiently. We fuse
three signals the system already has the pieces for.

## The three signals

1. **Lexical (full-text).** Exact terms, names, IDs, rare tokens. Postgres
   `tsvector` or SQLite FTS5. Cheap, great for "find the note that says X".
2. **Vector (semantic).** Meaning-based recall via embeddings (reuses the
   existing chunker + pluggable vector store). Great for "stuff about Y" when you
   don't know the words used.
3. **Graph.** Wikilinks + typed links + shared entities. Expands from the best
   hits to closely related notes (the person, the parent project) the keyword/
   vector pass missed. Reuses the existing light concept graph.

## The pipeline (DECISION)

```
query
  ├─▶ lexical top-k ─┐
  ├─▶ vector top-k ──┼─▶ fuse (Reciprocal Rank Fusion) ─▶ graph expand 1 hop
  │                  │                                        │
  └──────────────────┘                                        ▼
                                          re-rank + recency/importance boost
                                                             │
                                                             ▼
                                        return ranked chunks WITH citations
```

- **RRF** to fuse lexical + vector (robust, no tuning of score scales).
- **Graph expansion**: pull neighbors of the top fused hits (capped) so related
  context comes along. This is the "graphical hybrid" you wanted.
- **Boosts**: nudge by `importance`, `pinned`, and recency so live, valued notes
  win ties. Archived notes are included but down-weighted.
- **Citations always**: every returned chunk carries `source`, `heading_path`,
  `page`, `chunk_id` (already implemented). The LLM must cite.

## Tools exposed to the LLM

- `search(query, k, filters)` — the default hybrid path. Filters: category,
  source, date range, tags, status.
- `get_note(id)` / `get_section(id, heading)` — verbatim retrieval for quoting.
- `synthesize(question)` — multi-note, graph-aware answer with citations (the
  existing synthesize path, generalized).
- `timeline(range)` — uses the `events` table for "what happened / what's next".
- `related(id)` — graph neighbors of a note.

## Time-efficiency notes

- Lexical + vector are both sub-millisecond-to-low-ms at personal scale (the perf
  test already shows <1 ms vector search on ~560 chunks).
- Graph expansion is one bounded hop, not a full traversal.
- The expensive option (full GraphRAG with per-chunk LLM extraction) stays
  **gated off**, exactly as today, and is enabled per-need only.

## Re-ranking (optional, later)

A cross-encoder re-ranker (local model, runs on your spare GPU VRAM) can sharpen
the top results. It adds latency and a model download, so it's an opt-in quality
lever, not v1.

## Agentic RAG via a cheap server-side LLM (DECISION: later lever)

A big token-saver for your *main* models: let the cheap server-side LLM
(**DeepSeek**) act as a **retrieval agent** that runs entirely on the server.

```
main model asks: context_pack("kitchen reno budget + next step")
        │
        ▼
server-side DeepSeek loop:  search → read chunks → maybe search again →
                            select + summarize the relevant bits
        │
        ▼
returns a small, CITED "context pack" (not raw dumps) to the main model
```

- **Why:** your expensive main model receives a tight, relevant, cited pack
  instead of paging through many raw chunks — far fewer input tokens and less
  context bloat across your sessions.
- Exposed as a tool, e.g. `context_pack(question, budget_tokens)`.
- It's paid LLM use, so it runs under the same budget cap (see `10-cost.md`) and
  is a phase-5 add-on once plain hybrid search is solid (`12` Q14).
