# 11 — Roadmap

Build order chosen so you have a useful, auditable system as early as possible,
then add reach and polish. Each phase ends with something you actually use.

## Phase 0 — Foundations (reuse + reshape)
- Generalize the `knowledge` server from "course notes" to "vault".
- Define the vault taxonomy (`04`) and the frontmatter schema (`05`) in code.
- Pick and wire the relational store behind a clean interface (Postgres+pgvector
  default; SQLite path kept). Keep the existing chunker/embedder/store.
- `rebuild-index` command + the rebuild test (vault → DB → same search results).
- **Done when:** you drop files into `vault/00-inbox/` and hybrid search returns
  cited results, fully offline.

## Phase 1 — Capture & the LLM write path
- MCP write tools: `capture`, `quick_note`, `append_to_journal`, `set_credential`,
  `missing_credentials`.
- Auto-frontmatter (id, timestamps, source) on capture; inbox triage.
- **Done when:** an LLM client can file a thought and find it later, with a clean
  note in the vault.

## Phase 2 — Remote, authenticated serving
- Streamable HTTP transport + bearer-token auth + audit log.
- Docker Compose deploy; Tailscale setup doc.
- Secrets: encrypted store + first-run unlock.
- **Done when:** you query and capture from your phone over the authenticated API.

## Phase 3 — Self-pruning
- Pruning signals in the DB + persisted to frontmatter.
- Dedup, TTL, decay-archival policies + `prune --dry-run`, `--undo`,
  `explain-prune`, tombstone log.
- Scheduler (cron/loop) running reindex + prune + backup, reporting to the journal.
- **Done when:** stale notes archive automatically and you can audit/undo every
  change.

## Phase 4 — Connectors
- Google **Calendar + Gmail** (one Google OAuth). Gmail behind a short ephemeral
  TTL + label/query filter; Calendar → `events`. Contacts/Drive/Notion later.
- Setup wizard for OAuth/tokens; incremental cursors.
- **Done when:** your calendar/contacts/Notion appear as deduped Markdown and are
  searchable alongside everything else.

## Phase 5 — Intelligence & polish (opt-in, DeepSeek, cost-capped)
- Periodic **regroup** batch job: propose inbox → category moves (dry-run/approve).
- Compaction/summarization pruning.
- **Agentic RAG**: `context_pack` tool — DeepSeek does server-side multi-step
  retrieval and returns a tight cited pack, cutting main-model token use (Q14).
- OCR ingestion (local OCR first; hosted vision-OCR for hard scans).
- Optional: local cross-encoder re-ranker on spare GPU VRAM.

## Cross-cutting (every phase)
- Keep the offline self-check tests green (extend them per feature).
- Keep `rebuild-index` correct as the schema grows.
- Keep the vault human-readable — review real notes by eye each phase.

## Suggested first concrete step
Phase 0: add a `vault` config + taxonomy, point the existing ingest/index
pipeline at it, and prove the rebuild contract. Everything else builds on that.

## Owner priority & backlog (set 2026-07-02)

**Priority:** the **standard RAG database** comes first and must be runnable —
capture/drop Markdown → index → vector search with citations → `get_note`. That
core is done and verified; keep it solid before expanding. Then connectors
(Phase 4) and intelligence (Phase 5), then the backlog below.

**Backlog (net-new scope, not scheduled, not implemented):**

- **B1 — Keyboard & mouse automations:** desktop input automation (global hotkeys
  / macros) to capture to the vault and trigger search/prune without the chat UI.
- **B2 — Budgeting tracker:** structured finance module (accounts / transactions /
  budgets) layered on the vault, with categorization and periodic reports; reuses
  the relational + events layer.
- **B3 — Web app:** browser front-end over the authenticated MCP/HTTP API (search,
  capture, timeline, prune-review).
- **B4 — iOS access point:** mobile capture + query over the authenticated API
  (Shortcuts / PWA / thin app), reachable over Tailscale.

Each is promoted to a full phase (steps + a self-check) when scheduled. See
`13-build-plan.md` → "Owner backlog" for the tracking table.
