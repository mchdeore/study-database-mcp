# 07 — Connectors & credentials

Connectors are **adapters that write Markdown into the vault**. They are not live
pass-throughs the LLM queries directly. This keeps one search path, uniform
pruning, and an offline-readable vault.

## The connector model

```
external API ──pull──▶ adapter ──template──▶ Markdown note(s) ──▶ vault inbox/category
                          │
                          └─ dedup via source_ref so re-syncs update, not duplicate
```

- Each synced note carries `source` and `source_ref` (e.g. `notion://page/abc`)
  so the next sync **updates** the same note instead of creating a new one.
- Sync is **read-mostly** in v1. Write-back (e.g. create a calendar event) is a
  separate, explicitly gated capability for later.
- Sync runs on a schedule and on demand (`sync google`, `sync notion`).

## Google (DECISION: Calendar + Gmail first; per-service, least scope, opt-in)

OAuth 2.0 "installed app" / device flow. You authorize once in the setup wizard;
we store the refresh token (encrypted). **First two services to build:**

- **Calendar** → `events` table + journal-style notes. High value, low volume.
  Read-only scope.
- **Gmail** → notes per thread/label. This is the **firehose**, so by default it
  lands in an *ephemeral* category with a **short TTL** (auto-archives unless you
  promote a keeper). Scope tightly (read-only; consider label/query filters so we
  only ingest what matters, e.g. starred/important, not every newsletter).

Later, each independently toggleable:

- **Contacts (People)** → `20-people/` notes (great for linking).
- **Drive / Docs** → export selected folders to Markdown into `50-resources/`.
- **Photos** → metadata + captions later (image understanding is a phase-3 cost).

Use the **narrowest OAuth scopes** that work (read-only where possible). Each
service is off until you turn it on. Gmail's volume makes its TTL + filtering the
most important knob to get right.

Calendar (single source) → use **Google Calendar** above rather than a separate
Notion calendar, since the Google OAuth is already set up for Gmail. One login
covers both. (If you'd rather keep your calendar in Notion, swap this for the
Notion adapter below — same `events` output, just a different source.)

## Notion (DEFERRED to a later phase)

Not in v1. When added: a Notion internal integration + token; you share specific
pages/databases with it; the adapter exports them to Markdown into the vault,
deduped by Notion page id, re-syncing in place. Kept out of v1 to avoid a second
auth/connector surface while Google already covers calendar + mail.

## Reusing existing MCP servers vs. building adapters

You mentioned wanting to plug in existing Google/Notion MCP servers with your
creds. Two ways, and we can do both:

- **Adapter (recommended for ingestion):** our own thin pull → Markdown, so the
  data lands in the vault and is pruned/searched like everything else.
- **Proxy (optional):** the life server can also *expose* selected tools from an
  upstream Google/Notion MCP behind our single authenticated door, for live
  actions (e.g. "create a calendar event") without ingesting. This is the "give
  it my connectors and certain tools" part. Gated and off by default.

(Which upstream MCP servers, and exactly which of their tools to expose, is a fork
— see `12-open-questions.md`.)

## Credentials & secrets (DECISION)

- **First-run setup wizard** (`setup` CLI / first-call MCP tool): walks you
  through each credential, opens the OAuth consent, and stores results.
- **Storage:** secrets live in a single gitignored, **encrypted** file
  (`.secrets/secrets.enc`, encrypted with `age` or libsodium using a master key
  from your OS keychain), *not* in plaintext `.env`. The `.env` keeps only
  non-secret config. Rationale: the vault and repo can be backed up / git-synced
  without leaking tokens.
- **LLM ease-of-input:** the MCP exposes a guarded `set_credential(name, value)`
  tool and a `missing_credentials()` tool so, on first use, the assistant can
  prompt you for exactly what's missing and store it — no hand-editing files.
  This tool is only available locally / to the authenticated owner.
- **Master password / first-run unlock:** on boot the server asks for the master
  key once (env var, keychain, or prompt) to decrypt secrets. This is *separate*
  from the API auth that protects remote access (see `09-hosting-auth.md`).

## Idempotency & rate limits

Every connector tracks a per-source cursor/checkpoint in `.vault/` so syncs are
incremental and resumable, and respects API rate limits with backoff. A failed
sync never corrupts the vault — it writes nothing until a note is fully formed.
