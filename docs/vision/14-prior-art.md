# 14 — Prior art & inspiration

What we studied, what's worth taking, and how we adapt it **our way** (vault-first:
Markdown is truth, the DB is a derived index, everything reversible + local-first).
We take *ideas and shapes*, not code — most of these are different stacks. Each row
lands in a concrete plan step (see `13-build-plan.md`).

## Guiding filter

For every borrowed idea we ask: does it serve a **whole-life manager I can rely
on** (the north-star tests in `01-vision.md`), and can it be done **vault-first,
reversible, local-first, and offline-testable**? If not, it's dropped or deferred.

## Reference repositories

### 1. Mintable (`kevinschaich/mintable`) — personal finance automation
- **What it is:** TS CLI that aggregates bank balances/transactions via Plaid /
  Teller / finicity **or a local CSV import**, and writes them to a spreadsheet for
  budgeting. `setup` wizard + `fetch` command; "no ads, no data collection."
- **Worth taking:**
  - The **`setup` wizard + `fetch`** loop — matches our connector setup-wizard +
    sync pattern exactly (reuse for Phase 4 and budgeting).
  - **Pluggable account sources** (Plaid/Teller/**CSV**) → our pluggable adapter
    model. The **CSV/local-first path** is the important one for us: bank sync works
    with zero cloud.
  - Transaction **categorization + budgets**; balances over time.
- **How we adapt (vault-first):** transactions become Markdown notes (deduped by
  `source_ref`) in a finance area **plus** derived `accounts`/`transactions` tables;
  budgets are config; reports are computed views / digest notes. Plaid is
  cloud-optional and behind encrypted creds; CSV import is the default.
- **Lands in:** **Phase 8 — Budgeting tracker** (was backlog B2).

### 2. YACS (`YACS-RCOS/yacs`) — course scheduler (archived)
- **What it is:** a real multi-service web app — monorepo with a `core` API, a `web`
  client, a `users` (auth) service, a `notifications` service, and a **published
  `yacs-api-client` package**, behind nginx.
- **Worth taking:**
  - **A shared, typed API-client package** so multiple front-ends (web **and** iOS)
    talk to one authenticated API without re-implementing it.
  - **A notifications service** as its own concern (reminders, digests).
  - Scheduler/timetable UX (filter a catalog, build a schedule) — inspiration for the
    web app's **timeline/planner** view over our `events`.
- **How we adapt:** we already have the single authenticated HTTP MCP (Phase 2); the
  web app is a thin client over it, and we ship a small typed client package both the
  web app and iOS reuse. Notifications become our reminders/notifier.
- **Lands in:** **Phase 7 (notifications)** and **Phase 9 — Web app** (was B3).

### 3. AppointmentScheduler (`slabiak/AppointmentScheduler`) — Spring Boot booking app
- **What it is:** schedule appointments between providers/customers with **email
  reminders**, cancellation, **provider working-plans with breaks**, invoicing, roles.
- **Worth taking:**
  - **Reminders + notifications** tied to time-stamped items (we have `events`; add
    `remind_at` and a notifier).
  - A **working-hours / availability** model — useful for "when am I free?" planning
    over the calendar.
  - **Cancellation/state transitions** with an audit trail — mirrors our tombstone
    ethos.
- **How we adapt:** no bookings/providers (single-owner), but the *reminder +
  working-hours + state-transition* concepts feed a lightweight scheduling layer over
  events. Invoicing overlaps the budgeting module.
- **Lands in:** **Phase 7 — Scheduling & reminders.**

### 4. datadam_mcp (`KennethLeeJE8/datadam_mcp`) — personal-data MCP
- **What it is:** an MCP server (Supabase/Postgres) exposing personal data over
  **stdio + streamable HTTP**, framed as a **"persistent memory layer decoupled from
  the AI tool"** shared by many clients. No auth yet (OAuth planned).
- **Worth taking:**
  - **Validation of our whole thesis** (persistent, portable memory across AI
    clients) — and confirmation that our extras (bearer auth + encrypted secrets +
    the Markdown vault as truth) are the right differentiators (their stated gap is
    auth; ours is already built).
  - **Typed structured records + CRUD tools** (profile, preferences, contacts) as a
    complement to freeform notes.
  - MCP Inspector config + one-command deploy (Render) — nice-to-have DX.
- **How we adapt:** add a small **structured-records** layer (frontmatter-typed notes
  + the relational tables we already have) with explicit CRUD tools; harden
  **multi-client concurrency** since we already serve stdio + HTTP.
- **Lands in:** **Phase 12 — Structured records & multi-client memory.**

### 5. AI-Second-Brain / "StreamBrain" (`balabhadra3141/AI-Second-Brain`)
- **What it is:** a Next.js + shadcn/ui + Tailwind web "second brain" with
  chat-over-notes and streaming responses (plus `AGENTS.md`/`CLAUDE.md` agent guides).
- **Worth taking:**
  - A concrete, modern **web UI stack** (Next.js + shadcn/Tailwind) and the
    **chat-over-your-vault + streaming** UX for the web app.
  - Treating agent guidance as first-class docs (we already do this with steering +
    skills).
- **How we adapt:** our web app (Phase 9) is a client over the authenticated MCP; this
  is the reference for its stack and chat/streaming UX. Retrieval stays server-side
  (our hybrid RAG), not reimplemented in the browser.
- **Lands in:** **Phase 9 — Web app.**

## Mature-database techniques (from the retrieval/pruning research)

Full analysis in the session notes; the durable takeaways and where they land:

| Technique | Mature precedent | Our gap | Lands in |
|-----------|------------------|---------|----------|
| **Hybrid retrieval (lexical + vector, RRF)** | Elasticsearch, pgvector, Azure, Solr | search is vector-only (hybrid was "a later phase" in `search.py`) | **6.1** |
| **Frequency decay / aging in eviction score** | W-TinyLFU (Caffeine), Redis LFU | `access_count` never decays → latent LFU-pollution | **6.2** |
| **Dedup precision via lexical signature** (SimHash/MinHash + Jaccard) | AltaVista, LLM-dataset dedup | near-dup uses a single embedding-cosine threshold (false-merge risk, D20) | **6.3** |
| **ANN index (HNSW)** | pgvector 0.7+, FAISS, Milvus | brute-force / exact scan (fine now; D2/D7) | **6.4** |
| **Cross-encoder rerank** | standard RAG | none (was roadmap 5.7) | **6.5** |
| **Lifecycle delete phase w/ grace** | Elasticsearch ILM `min_age`, Cassandra `gc_grace` | archived→delete is manual | **6.6** |
| **Downsampling / rollups of old data** | Prometheus/Thanos | — (already planned) | **5.4** compaction |

## Design principles carried into every borrowed feature

1. **Vault-first.** Anything ingested (transactions, reminders, records) is a
   Markdown note; relational tables are derived and rebuildable.
2. **Connectors are adapters** that upsert by `source_ref` (Plaid/CSV like
   Calendar/Gmail) — never live pass-throughs.
3. **Reversible + audited** (tombstones, dry-run) for any state change.
4. **Local-first, cloud-optional** (CSV before Plaid; local embeddings; budget-capped
   LLM).
5. **One authenticated door**; web/iOS/automation are thin clients over it, sharing
   one typed API client.
6. **Every step ships an offline self-check** (the house style).
