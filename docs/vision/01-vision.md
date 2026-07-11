# 01 — Vision

## What it is

A self-hosted personal knowledge operating system. It takes everything that
matters in your life — notes, documents, emails, calendar, contacts, Notion
pages, web clippings, random captures — and turns it into:

1. A **Markdown vault** you own and can read by hand (the truth).
2. A **self-pruning relational index** over that vault for fast, structured,
   hybrid (vector + graph) search.
3. A **password-protected MCP API** so any LLM client, on any device, can query
   and update your context.

Think of it as your own private, queryable memory that an AI can use as working
context — without handing your life to a third-party cloud.

## Who it's for

You, the single owner-operator. Single-tenant by design. No multi-user accounts,
no sharing model in v1. (This simplifies auth, pruning, and the data model a lot.)

## Why build it (the problem)

- Your knowledge is scattered across Google, Notion, files, and your head.
- Cloud "AI memory" products own your data and lock it in.
- A raw Obsidian vault is auditable but has no real search, no pruning, no API,
  and no way for an LLM to reliably read/write it remotely.
- You want an LLM to have *durable, private, current* context about your life —
  and you want to be able to open a folder and see exactly what it knows.

## What "done" feels like (the experience)

- You drop a PDF, a screenshot, or paste a thought. Seconds later it's filed in
  the right place in the vault with sensible frontmatter, and it's searchable.
- You ask your AI client (connected over the API from your phone): *"What did I
  decide about the kitchen reno budget, and what's the next step?"* — it answers
  with citations to the exact notes.
- Your Google Calendar and Notion changes show up in the vault automatically as
  Markdown, deduped against what's already there.
- Six months later, stale, low-value clutter has been quietly archived and the
  vault still feels clean — and you can see *exactly* what was pruned and undo it.
- You can `git log` the vault and read your life's changelog. You can also delete
  the entire database and rebuild it from the vault in one command.

## The north-star tests

We're succeeding if all of these stay true:

- **Audit test:** A stranger (or future you) can understand the vault by reading
  files, with no app running.
- **Rebuild test:** `rebuild-index` reconstructs the full DB from the vault with
  identical search results.
- **Pruning-trust test:** You can always answer "why was this archived?" and undo
  it.
- **Anywhere test:** You can query and capture from your phone over the
  authenticated API, securely, without exposing the box to the open internet.
- **Cost test:** Monthly spend is known, bounded, and **capped (~$10/mo target as of
  2026-07-10 — quality-first, not $0; see `10-cost.md` + the README CURRENT TRUTH banner).**

## Relationship to the current project

The current repo (`study-database-mcp`) already has the hard parts of the index:
structure-aware chunking, incremental embedding with a manifest, a pluggable
vector store, a light concept graph, and a SQLite catalog. This project
**generalizes** that pipeline from "course notes" to "everything," adds the
relational + self-pruning layer, the connectors, and the authenticated remote
API. The study tools become one *category* inside the larger vault.

**The project is renamed to `life-vault-mcp`** to reflect the broader scope; the
deterministic `calculator` server stays bundled as a separate, optional door.
