# Life Vault MCP — Vision & Scope

> # ⭐ CURRENT TRUTH (2026-07-10) — read this first
>
> There have been **two scope phases**, and this banner is the authoritative record
> of which is current:
>
> **Phase A — "lean local MCP" (the code as-built):** a Markdown vault + Google
> Calendar + a knowledge Librarian, served over **MCP stdio**, SQLite backend, local
> embeddings, ~$0/mo. Docs 01–14 describe this. Several things were descoped here
> (calculator/study-math server, HTTP dashboard + bearer auth, Docker, Postgres+pgvector).
>
> **Phase B — "the super-tool" (current direction, `15-database-ai-architecture.md`):**
> the user has **deliberately re-expanded** the vision. This is now the target. Where B
> conflicts with A, **B wins and A is history.** Key decisions locked 2026-07-10:
>
> - **Two-model MCP:** a frontier model (Opus) sends the backend a JSON query spec; a
>   cheap backend model on a **Mac mini** does graph-RAG + returns cited payloads.
> - **Quality-first embeddings (NOT local-only, NOT $0):** OpenAI `text-embedding-3-large`
>   for **all** content (user is not privacy-constrained). **~$10/mo budget cap**
>   (embeddings + LLM). See updated `10-cost.md`.
> - **Primary use case = exam prep:** "help me study for the test in course X, it covers
>   concept Z — go." Retrieval is optimized around this (`test → covers → topic →
>   definition/formula/problem`). See doc 15 §3a.
> - **Remote access is back on the table:** hosted on the Mac mini, reached via
>   **Tailscale + bearer token** (the `09-hosting-auth.md` design — no longer "stdio only").
> - **New capabilities:** queueable **automations** (registered scripts), a backend
>   **proposing-librarian** agent, identity/**version chains** (`supersedes`/`might_copy`),
>   finance via **Plaid** (no credential auto-login), a reflective health/mood journal.
> - **Priority #1 is retrieval quality** — and the design is settled in
>   **`16-retrieval-and-tagging.md`**: density lives in **intrinsic open-vocabulary
>   multi-field chunk tags** + embeddings, with an **emergent, self-maintaining concept
>   index** (synonyms merged by geometry + learned from usage). Relations stay **minimal**.
>   No fixed glossary, no blanket chunk-to-chunk semantic graph. Everything else stays
>   simple until a real need forces more.
>
> The **code today is still Phase A**; Phase B is the roadmap, not yet built. Tags in
> doc 15 mark `[exists]` vs `[new]`. When in doubt, doc 15 + this banner are the truth.

This folder is the historical **vision** for turning the original study-database
MCP into a self-hosted personal knowledge system. It ingests files and connected
accounts, parses them into an auditable Markdown vault, and indexes that vault in
a self-pruning SQLite database with hybrid (vector + lexical) search.

## The one-paragraph pitch

A private, self-hosted "second brain" you fully own. Everything you feed it —
dropped files, Google data, Notion pages, quick captures — is normalized into a
human-readable Markdown vault (Obsidian-style) that **you** can open and audit.
A relational database indexes that vault for fast hybrid search and keeps itself
tidy by pruning, deduplicating, and compacting old material on a policy you
control. An LLM talks to it through MCP tools over an authenticated API, so any
AI client (Claude, etc.) can read and write your life-context from any device.

## Core principles (read these first)

1. **The Markdown vault is the source of truth.** The database is a *derived
   index* that can be deleted and rebuilt from the vault at any time. If the two
   ever disagree, the vault wins.
2. **Auditable by a human.** Plain files, intuitive folders, readable
   frontmatter. You can browse it in Finder or Obsidian without the app running.
3. **Reversible by default.** Nothing is hard-deleted silently. Pruning moves to
   `archive/`, then to a tombstone log, then (optionally, after a grace period)
   deletes. Every step is logged.
4. **Local-first, cloud-optional.** It runs entirely on your hardware. Cloud APIs
   (embeddings, LLM) are opt-in and cost-scoped (see `10-cost.md`).
5. **One server, one door.** A single authenticated endpoint. Connectors are
   *writers into the vault*, not separate live integrations the LLM juggles.

## How to read this folder

| File | What it answers |
|------|-----------------|
| `01-vision.md` | What this is, who it's for, what "done" feels like |
| `02-scope.md` | In scope / out of scope / non-goals, by phase |
| `03-architecture.md` | The layers and how data flows through them |
| `04-vault-structure.md` | The on-disk folder taxonomy you'll audit |
| `05-data-model.md` | Frontmatter schema, entities, relational tables |
| `06-self-pruning.md` | The self-pruning / dedup / compaction policy |
| `07-connectors-credentials.md` | Google, Notion, files; the credential flow |
| `08-search.md` | Hybrid embedding + graph retrieval |
| `09-hosting-auth.md` | Self-hosting, password protection, remote access |
| `10-cost.md` | Pricing model and budget guardrails |
| `11-roadmap.md` | Build order, milestones, what to do first |
| `12-open-questions.md` | Decisions log + remaining forks |
| `13-build-plan.md` | **Step-by-step build tracker** (start here to build) |
| `14-prior-art.md` | Reference projects studied + what we borrow, mapped to plan steps |
| `15-database-ai-architecture.md` | **Two-model MCP retrieval + backend-AI, feature superset, identity/version chains** (2026-07-10 design session; re-expands scope) |
| `16-retrieval-and-tagging.md` | **THE retrieval design** — open-vocab multi-field chunk tagging + emergent self-maintaining concept index + minimal relations (2026-07-10; authoritative for retrieval, supersedes doc 15 §3 there) |

## Decisions locked (2026-06-29)

- **Name:** rename/fork to **`life-vault-mcp`** (calculator stays a separate
  optional server).
- **Store:** PostgreSQL + pgvector (swappable interface).
- **Remote access:** Tailscale + bearer token (no public exposure).
- **Embeddings:** local (free). **Server-side LLM:** cloud, budget-capped.
- **First connectors:** Google Calendar + Gmail (one Google OAuth; Gmail
  short-TTL/ephemeral), ingest-to-vault only (no live-tool proxy yet). Notion
  deferred.
- **Cloud LLM:** DeepSeek (cheap) for batch categorize/compact/regroup + agentic
  RAG later; local OCR first. Budget-capped.
- **Taxonomy:** static folders + periodic cheap "regroup" batch job (no per-note
  LLM cost).
- **Backups:** local folder on the server box (put it on a 2nd physical disk).
- **Hardware:** NVIDIA box, ~14GB VRAM across 2 GPUs (plenty; heavy LLM is cloud).

Full table + still-open items (repo rename migration, agentic-RAG sign-off) in
`12-open-questions.md`.

## Status

Draft v0.1. Defaults/recommendations are marked **DECISION** with rationale.
Change any default freely — that's what this folder is for.
