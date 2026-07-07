# 05 — Data model

The vault is truth; these tables are the **derived index**. They can be dropped
and rebuilt from the vault. Shown as conceptual schema (Postgres flavor; the same
shape works on SQLite). Types simplified for clarity.

## Core tables

### `documents` — one row per note
```
id            text primary key      -- the frontmatter id (stable)
path          text                  -- current path in the vault
title         text
category      text                  -- e.g. projects/kitchen-reno
source        text                  -- capture | file | google | notion | webclip
source_ref    text                  -- external id for dedup / re-sync
content_hash  text                  -- skip re-index when unchanged
frontmatter   jsonb                 -- full frontmatter, flexible
created       timestamptz
updated       timestamptz
status        text                  -- active | archived | tombstoned
-- pruning signals:
importance    int                   -- 0-5
pinned        bool
expires       timestamptz null
last_access   timestamptz           -- updated whenever a search surfaces/opens it
access_count  int
prune_score   real                  -- computed; see 06-self-pruning.md
```

### `chunks` — one row per searchable chunk (reuses existing chunker)
```
chunk_id      text primary key      -- stable, e.g. "<doc>#<n>"
document_id   text references documents(id)
ordinal       int
heading_path  text                  -- for citations
page          int null
text          text
embedding     vector(N)             -- pgvector column (or sqlite-vec)
token_count   int
```

### `entities` — people/places/things extracted or declared
```
id            text primary key
type          text                  -- person | place | org | concept | event
name          text
canonical_id  text null             -- merges aliases ("Jane" -> jane-doe)
note_id       text null             -- the note that *is* this entity, if any
```

### `links` — the graph edges
```
src_document  text references documents(id)
dst_target    text                  -- document id or entity id
rel           text                  -- mentions | people | related | parent ...
```

### `events` — anything with a time (calendar, deadlines, journal moments)
```
id            text primary key
document_id   text references documents(id)
title         text
start_at      timestamptz
end_at        timestamptz null
source        text
```

### `tombstones` — the deletion/prune audit log (reversibility)
```
id            text primary key
document_id   text
action        text                  -- archived | deleted
reason        text                  -- which policy fired, with the score
prev_path     text                  -- where it was, so undo can restore
payload       jsonb                 -- snapshot of frontmatter at the time
at            timestamptz
```

## Indexes that matter

- Vector index on `chunks.embedding` (HNSW/IVFFlat for pgvector; sqlite-vec ANN).
- Full-text index on `chunks.text` (Postgres `tsvector` / SQLite FTS5) for the
  lexical half of hybrid search.
- B-tree on `documents.prune_score`, `documents.status`, `documents.category`.

## Why a relational layer at all (vs. files only)

- **Structured queries**: "events next week", "all notes about Jane created since
  March", "projects with status=active and no update in 60 days (prune candidates)".
- **Pruning needs counters** (`access_count`, `last_access`, `prune_score`) that
  don't belong in hand-edited frontmatter.
- **Hybrid search** needs vectors + full-text + joins in one place.

## The rebuild contract

`rebuild-index`:
1. Drop all derived tables (keep `tombstones` — it's an audit log, optionally
   also derivable from the vault's tombstone file).
2. Walk the vault, parse frontmatter + body, chunk, embed, extract.
3. Load rows. End state must be identical to an incremental-updated DB.

If a counter like `access_count` only lives in the DB, it's persisted into the
note's frontmatter periodically (or into `.vault/`) so a rebuild doesn't lose
pruning history. **DECISION:** persist pruning signals back to frontmatter so the
vault remains the complete source of truth.
