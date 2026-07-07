# 03 — Architecture

## The mental model

```
            ┌─────────────────────────────────────────────────────────┐
 inputs     │  files drop · Google · Notion · LLM captures · web clips │
            └───────────────┬─────────────────────────────────────────┘
                            │  (adapters normalize everything to Markdown)
                            ▼
            ┌─────────────────────────────────────────────────────────┐
 TRUTH      │            THE MARKDOWN VAULT  (plain files, git)         │
            │   frontmatter + body · wikilinks · categorical folders    │
            └───────────────┬─────────────────────────────────────────┘
                            │  (indexer: chunk → embed → extract → load)
                            ▼
            ┌─────────────────────────────────────────────────────────┐
 INDEX      │     RELATIONAL DB  (documents, chunks, vectors,           │
 (derived)  │     entities, links, events)  +  concept graph            │
            └───────────────┬─────────────────────────────────────────┘
                            │
                            ▼
            ┌─────────────────────────────────────────────────────────┐
 SERVE      │   MCP server (HTTP, authenticated)  →  search / capture / │
            │   synthesize / prune / admin tools  →  any LLM client     │
            └─────────────────────────────────────────────────────────┘
                            ▲
 MAINTAIN   │  scheduler: connector sync · reindex · prune · backup     │
```

## Layers

### 1. Adapters (input → Markdown)
Each input type has an adapter whose only job is to produce a well-formed
Markdown note (frontmatter + body) and drop it in the vault inbox. File adapter
reuses the existing PDF→Markdown pipeline. Google/Notion adapters call their APIs
and template the result. The LLM `capture` tool is just another adapter.

**Why:** one normalization point means search, pruning, and the data model never
need to know where something came from.

### 2. The vault (truth)
Plain Markdown on disk, organized by the taxonomy in `04-vault-structure.md`,
versioned with git. This is the only thing that must be backed up to be safe.

### 3. The indexer (vault → DB)
Walks the vault incrementally (reusing the existing content-hash manifest so only
changed files are reprocessed). For each note it: chunks (structure-aware), embeds
(vector), extracts entities/links/dates, and loads rows into the relational DB.

**Key property:** idempotent and rebuildable. `rebuild-index` drops the DB and
replays the whole vault to the same end state.

### 4. The relational index (derived)
Stores documents, chunks + their vectors, extracted entities, the link graph, and
time-stamped events. Holds the metadata that powers self-pruning (scores, access
counts, TTLs). See `05-data-model.md`. This is *disposable* — truth lives in the
vault.

### 5. The MCP server (serve)
A single authenticated HTTP MCP endpoint exposing read tools (search, get,
synthesize) and write tools (capture, quick_note) plus admin tools (prune,
rebuild, status). See `09-hosting-auth.md`.

### 6. The scheduler (maintain)
A background loop / cron that runs connector syncs, incremental reindex, the
pruning policy, and backups. Everything it does is also runnable by hand as a CLI
command (so it's auditable and debuggable).

## Data flow examples

- **Drop a PDF:** file adapter → Markdown in `00-inbox/` → indexer chunks+embeds
  → searchable. A later pass may auto-file it into a category.
- **Ask a question (remote):** client → authenticated MCP → hybrid search
  (lexical+vector) → graph expansion → ranked chunks with citations → LLM answers.
- **Nightly:** scheduler → Google/Notion sync (dedup) → reindex changed files →
  run pruning policy → write backup → log a summary note to the journal.

## DECISION: relational store

**Default recommendation: PostgreSQL + `pgvector`.** It gives real SQL, robust
full-text search, JSONB for flexible frontmatter, and first-class vector search
in one engine, and it scales well past a personal corpus. Run it in Docker on the
home box.

**Alternative kept on the table: SQLite + `sqlite-vec` + FTS5.** Zero-ops,
single-file, matches the current catalog's philosophy, perfect if the corpus
stays modest. The data-access layer will be written behind an interface so this
is swappable. (This is a fork — see `12-open-questions.md`.)

## DECISION: keep "two servers" or merge?

The current repo has separate `calculator` and `knowledge` servers. Keep the
**calculator** as-is (independent, no state). Grow **knowledge** into the life
vault server. The calculator stays a separate, optional door.
