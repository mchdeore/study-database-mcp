# 02 — Scope

Scope is defined per phase so we ship something usable early and add ambition
later. Phases are detailed in `11-roadmap.md`; this file is the in/out list.

## In scope (the system we are building)

- **Ingestion** of local files (PDF, docx, pptx, md, txt, images via OCR later)
  into a normalized Markdown vault.
- **Connectors** that pull from Google and Notion and write Markdown into the
  vault (read-mostly; see `07-connectors-credentials.md`).
- **A Markdown vault** with an intuitive, auditable, categorical folder taxonomy.
- **A relational index** (tables for documents, chunks, entities, links, events)
  with **vector search** built in.
- **Self-pruning**: dedup, decay/TTL, importance scoring, compaction, archival,
  tombstones — all reversible and logged.
- **Hybrid search**: lexical + vector + graph traversal, fused, with citations.
- **Capture tools for the LLM**: low-friction `capture`, `quick_note`,
  `append_to_journal`, plus auto-categorization.
- **A password-protected MCP API** served over HTTP, reachable remotely and
  securely (Tailscale and/or token + TLS).
- **First-run setup**: a wizard that collects credentials/secrets and stores them
  safely.
- **Operability**: `rebuild-index`, backups, logs, a status/health tool.

## Out of scope (v1) — non-goals

- **Multi-user / sharing / collaboration.** Single owner only.
- **A custom GUI.** The "UI" is your file browser/Obsidian + your AI client.
  (A tiny read-only status page is allowed; a full web app is not.)
- **Writing back to Google/Notion as the system of record.** Connectors are
  read-mostly into the vault. (Limited write-back, e.g. create a calendar event,
  may come later and is explicitly gated.)
- **Real-time streaming sync.** Connectors run on a schedule/poll, not a live
  push pipeline.
- **Mobile app.** You reach it through an existing MCP client over the API.
- **Fine-grained ACLs / encryption-at-rest of individual notes.** Disk-level
  encryption + auth at the door is the v1 security model.
- **Automatic irreversible deletion.** Pruning archives; hard-delete is opt-in.

## Explicit constraints / requirements

- Must run on your own hardware at home.
- Must be queryable from anywhere, behind authentication.
- The vault must remain human-auditable plain Markdown at all times.
- The database must be fully rebuildable from the vault.
- Cloud API usage must be optional and cost-bounded.

## Success criteria (acceptance, high level)

1. Drop-a-file → searchable-with-citation in one pass, no manual filing required.
2. A connector sync writes deduped Markdown into the vault.
3. `rebuild-index` reproduces search results from the vault alone.
4. A pruning run archives stale notes and you can list/undo exactly what changed.
5. A remote MCP client authenticates and runs search + capture end to end.
