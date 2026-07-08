# Life MCP

A self-hosted, local-first personal knowledge system you reach through Claude /
Cowork (over [MCP](https://modelcontextprotocol.io)) or by reading plain Markdown
files directly. No web app, no cloud backend, no account, nothing to host.

Three parts:

- **Markdown vault** — the source of truth. Every note is a plain `.md` file with
  YAML frontmatter you can open, edit, grep, and back up yourself.
- **The Librarian** — a knowledge server that indexes the vault (plus a catalog of
  your SCHOOL documents) and answers with hybrid search (vector + lexical) and
  citations, handing what it finds to the LLM that queries it.
- **Google Calendar + Gmail connectors** — read-only ingestion that turns your
  calendar (the source of truth for scheduling, tasks, and reminders) and mail
  into vault notes, deduped by `source_ref` so re-syncs update in place.

Everything is served over **MCP stdio**: the servers run locally as subprocesses
of Claude Desktop / Cowork. There is deliberately no HTTP server, no Docker, and
no separate app.

## Layout

```
servers/vault/               Markdown vault back end + its MCP server
                             (capture · search_vault · timeline · sync_google)
servers/vault/connectors/    Google Calendar + Gmail (OAuth + read-only fetch → notes)
servers/knowledge/           the Librarian: chunk · embed · store · retrieve · catalog · server
scripts/                     reindex · catalog · vault_sync · vault_index · vault_import · vault_schedule
vault/                       YOUR Markdown vault (gitignored; the source of truth)
data/                        generated index + SCHOOL catalog (gitignored)
tests/                       offline self-checks (no API key or network needed)
```

## Setup

Requires Python 3.10+ (built/tested on 3.12).

```bash
python3.12 -m venv .venv && source .venv/bin/activate

# Core (vault + Librarian; runs fully offline with the numpy store + hash embedder):
pip install -e ".[knowledge]"

# Recommended — real local semantic search (free, private, no cloud round-trip):
pip install -e ".[knowledge,embeddings-local]"

# Google Calendar + Gmail connectors:
pip install -e ".[connectors-google]"

# Optional — PDF→Markdown for school docs, and encrypted secrets at rest:
pip install -e ".[pdf-pymupdf,crypto]"

cp .env.example .env    # defaults are sane; keep EMBEDDING_PROVIDER=local
```

## The vault + the Librarian

The vault is a plain Markdown tree (`00-inbox`, `10-journal`, `20-people`,
`30-projects`, `40-areas`, `50-resources`, `60-sources`, `90-archive`). A derived
**SQLite** index (`.vault/index.db`) powers retrieval; it's rebuildable from the
Markdown at any time, so the files always win.

Vault MCP tools: `search_vault` (hybrid vector + BM25 with citations), `get_note`,
`timeline`, `capture` / `quick_note` / `append_to_journal`, `list_inbox`,
`related`, `find_duplicates`, `prune_expired`, `reindex`, `set_credential`.

Librarian (knowledge) MCP tools: `search_notes`, `get_section`, `synthesize`,
`related_concepts`, plus the SCHOOL catalog: `list_courses`, `find_documents`,
`catalog_stats`. Build the catalog once with `python scripts/catalog.py`.

Search is hybrid: a vector retriever (embedding cosine) and a lexical retriever
(BM25) fused with Reciprocal Rank Fusion, so both semantic matches and exact terms
(names, course codes) surface. Real semantics require a real embedder — set
`EMBEDDING_PROVIDER=local` and install `embeddings-local`, then reindex.

## Google Calendar + Gmail (read-only)

The connectors ingest your calendar and mail as vault notes. Least-privilege,
read-only scopes only. One-time setup:

1. Create an OAuth **Desktop app** client in Google Cloud Console (APIs & Services
   → Credentials); enable the Calendar API and Gmail API. For personal use set the
   OAuth consent screen to **Internal** (or add yourself as a Test user) to avoid
   the verification wall.
2. Store the client id/secret with the `set_credential` MCP tool
   (`google_oauth_client_id`, `google_oauth_client_secret`).
3. Grant consent: `python scripts/vault_sync.py --setup` (opens a browser once).
4. Sync: `python scripts/vault_sync.py --calendar --gmail` (or the `sync_google`
   tool). Check `google_auth_status` / `--status` any time.

Calendar events land in `40-areas/calendar/`; Gmail lands in an ephemeral
`50-resources/mail/` with a TTL (unpinned mail auto-archives). Gmail is a
firehose — narrow `GOOGLE_GMAIL_QUERY` (e.g. `is:important`, `newer_than:7d`).

## Register with Claude Desktop / Cowork

See `claude_desktop_config.example.json` (Desktop) and `cowork_config.example.json`
(Cowork). Use the venv's Python and absolute paths, e.g.:

```json
{
  "mcpServers": {
    "knowledge": { "command": "/abs/.venv/bin/python", "args": ["/abs/servers/knowledge/server.py", "--stdio"] },
    "vault":     { "command": "/abs/.venv/bin/python", "args": ["/abs/servers/vault/server.py", "--stdio"] }
  }
}
```

## Tests

Offline self-checks (no deps beyond the core install, no network):

```bash
.venv/bin/python tests/check_vault.py            # vault CRUD, index, search contract
.venv/bin/python tests/check_knowledge.py        # chunker, incremental, citations, graph
.venv/bin/python tests/check_catalog.py          # SCHOOL catalog: dedup, naming, rescan
.venv/bin/python tests/check_vault_google.py     # Google OAuth + live-fetch wiring (mocked)
.venv/bin/python tests/check_vault_connectors.py # connector sync/cursor contract
.venv/bin/python tests/check_vault_hybrid.py     # hybrid vector + lexical retrieval
```

## Scaling later (not needed now)

Postgres + pgvector (and the Docker deployment) were removed to keep this lean and
local. SQLite already stores chunk embeddings and does brute-force cosine + BM25
hybrid search, which is plenty at personal scale. If a vault ever grows into the
hundreds of thousands of chunks, pgvector is the documented upgrade path (add a
backend, reindex) — see `docs/vision/03-architecture.md`.
