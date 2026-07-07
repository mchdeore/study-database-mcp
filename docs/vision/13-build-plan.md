# 13 — Build plan & progress tracker

The single place to track building `life-vault-mcp`. Work top-to-bottom. Each step
is small enough to build and test in one sitting, and has a concrete test so
"done" is unambiguous.

## How to use this file

- Update the **Status** of a step as you go: ☐ todo · ◐ in-progress · ☑ done · ✗ blocked.
- When a step is done, fill its **Notes** line with *how* it was implemented (one
  or two sentences: key files, approach, decisions). This is the "how it was
  built" overview for quick iteration later.
- Keep steps independently testable. Don't start the next phase until the current
  phase's steps pass their tests.
- Tests prefer the existing offline pattern (plain `assert` self-checks, no
  network) where possible, so the suite stays runnable anywhere.

## ⚠️ Build discipline (READ EVERY TIME — non-negotiable)

To avoid a giant, painful refactor later, we integrate and verify continuously:

1. **Test at least every 4 steps.** After completing any ≤4 steps, STOP and run
   the **full vault self-check suite** (not just the new test). Do not start the
   next batch until everything is green. End-of-phase is always a checkpoint too.
2. **Never build on red.** A failing or skipped check blocks new work until it's
   fixed or explicitly logged as deferred (below) with a reason it's safe to defer.
3. **Keep the rebuild contract sacred.** Any schema/index change must keep
   `tests/check_vault.py` (0.9) green — the vault stays the source of truth.
4. **No silent scope creep in the index.** If you change the DB schema, update
   `rebuild-index`, the migration, and the contract test in the same batch.
5. **Defer, don't ignore.** If something is a real but *later* problem (perf,
   a backend we can't test offline, a nice-to-have), add it to the **Deferred
   issues log** below instead of half-fixing it now. Come back to it on schedule.
6. **Surface honestly.** Anything skipped, mocked, or untested-offline gets called
   out in the step Notes AND, if it needs follow-up, logged as a deferred issue.

Run the whole suite (do this at every checkpoint):

```bash
.venv/bin/python tests/check_vault.py          # Phase 0: vault + index + rebuild
.venv/bin/python tests/check_vault_capture.py  # Phase 1: write path
.venv/bin/python tests/check_vault_serve.py    # Phase 2: auth/crypto/HTTP
.venv/bin/python tests/check_vault_prune.py         # Phase 3: relations + dedup (batch 1)
.venv/bin/python tests/check_vault_prune_scores.py  # Phase 3: signals + prune_score (batch 2)
.venv/bin/python tests/check_vault_archive.py       # Phase 3: TTL/decay/archive/tombstones (batch 3)
.venv/bin/python tests/check_vault_scheduler.py     # Phase 3: backup + scheduler tick (batch 4)
.venv/bin/python tests/check_vault_connectors.py    # Phase 4: calendar adapter + source_ref dedup + events
.venv/bin/python tests/check_vault_usability.py     # RAG usability: get_note / timeline / status / category
.venv/bin/python tests/check_vault_smart_capture.py # smart capture (near-dup) + append_to_note consolidation
.venv/bin/python tests/check_vault_sync.py          # Phase 4: connector cursors + incremental sync runner
.venv/bin/python tests/check_vault_web.py           # Phase 9: read-only web dashboard (Starlette TestClient)
.venv/bin/python tests/check_vault_staging.py       # staging importer: data/incoming/<area> -> vault
.venv/bin/python tests/check_vault_hybrid.py        # Phase 6.1: hybrid retrieval (BM25 + vector + RRF)
# (add each new phase's check here as it lands)
.venv/bin/python tests/check_knowledge.py      # existing knowledge server (regression)
```

## Deferred issues log (come back to these)

Real-but-later items. Each has an owner-decision or a phase to revisit. Don't let
this list rot — review it at every phase checkpoint.

| # | Issue | Why deferred | Revisit at |
|---|-------|--------------|-----------|
| D1 | Postgres/pgvector backend is **not exercised by the offline suite** | needs a live Postgres; SQLite proves the contract | when first deploying with `VAULT_DB=postgres` (add a gated integration test) |
| D2 | SQLite vector search is **brute-force cosine** (loads all embeddings) | fine to ~tens of thousands of chunks | when a real vault gets large → `sqlite-vec` or move to Postgres |
| D3 | Write tools run a **full incremental index** after each write | manifest makes it cheap now | if capture latency grows → single-note index path |
| D4 | Docker Compose + Tailscale are **authored, not live-tested** here (no Docker in dev env) | environment limitation | first real deploy on the home box |
| D5 | HTTP live smoke asserts the **401 (deny) path** only, not a full authed MCP round-trip | proving the gate is the priority; full handshake is flaky in CI | Phase 4+ when a client integration test is worthwhile |
| D6 | Indexer **adopts dropped `.md` files by rewriting frontmatter** (edits files on first index) | needed for stable ids | revisit if a read-only ingest mode is ever wanted |
| D7 | pgvector column is **unconstrained `vector`** with exact scan (no ANN index) | dim depends on the chosen embedder | when embedder is pinned → fixed dim + HNSW/IVFFlat |
| D8 | Incremental **removal of a canonical doc orphans its duplicates** (they aren't re-promoted until a rebuild); deleting a duplicate file does clear its row | edge case; rebuild fixes it | Phase 3 scheduler (re-promote on canonical delete) |
| D9 | **Empty-body notes all share one body hash** → could be flagged as duplicates of each other | rare; capture rejects empty text | when templates/empty stubs become real → skip dedup for empty bodies |
| D10 | Relation **link resolution is global on every index run** (`UPDATE links` over all rows) | trivial cost at personal scale | if it gets slow → resolve only touched targets |
| D11 | **Persisted** similarity ("related-by-embedding") edges not built — the link graph stays wikilink-only. (Near-dup *flagging* is done — 3.3 `find_near_duplicates` computes cosine on demand, no stored edges.) | the on-demand report covers the 3.3 need; storing edges adds rebuild burden for no current gain | when graph-aware search wants similarity edges → persist them + cover in the rebuild test |
| D12 | Access signals persist to `.vault/signals.json`, **not note frontmatter** (deviation from 3.1 spec) | frontmatter writes force re-embeds / churn | revisit only if a frontmatter-native signal is ever wanted |
| D13 | `record_access` + `prune.refresh` **write per-document on every search/index** (one UPDATE each) | trivial at personal scale | if it gets slow → batch writes / debounce access bumps |
| D14 | Backup **`pg_dump` path is authored but untested offline** (SQLite online-backup path is tested) | needs a live Postgres | first `VAULT_DB=postgres` deploy (gate with D1's integration test) |
| D15 | DB **snapshot filenames are per-second** (`index-YYYYMMDD-HHMMSS.db`) → two backups in the same second overwrite | not a real scheduler cadence | if sub-second backups ever happen → add a counter/microseconds |
| D16 | Backup only guards **secrets it knows to gitignore**; a secret already committed in a pre-existing vault repo isn't retroactively purged | our repos are git-init'd by the backup (secrets ignored from commit #1) | if adopting an existing repo → run `git rm --cached` on secrets once |
| D17 | Scheduler's **journal summary lands in the NEXT tick's commit** (backup runs just before the journal write) | intentional, so the summary can name this tick's commit | only if same-tick commit of the summary is ever wanted |
| D18 | Indexer derives **one event per note** (from a single `start:`); a note describing several distinct events isn't modeled | the connector case (calendar) is 1:1; richer shapes add complexity for no current source | when a source needs multi-event notes → list-of-events frontmatter + `replace_events` many |
| D19 | Connector adapters (calendar) are **fed already-fetched event dicts**; the live Google fetch + OAuth is not built/tested yet | offline testability; the transform+load is the risky part and is covered | 4.1 (OAuth wizard) + 4.6 (sync tools/CLI) wire the real fetch |
| D20 | Smart-capture near-dup uses **whole-note centroid cosine at a fixed threshold** (0.9 default); short texts can score high on the lexical hash embedder | default mode is `warn` (never silently merges — safe); `skip`/`append` are opt-in | tune threshold per real embedder; consider per-category thresholds / a re-ranker |

## Status dashboard

| Phase | Title | Steps | Done |
|------:|-------|------:|-----:|
| 0 | Foundations (vault + DB index + rebuild) | 9 | 9 ✅ |
| 1 | Capture & LLM write path | 6 | 6 ✅ |
| 2 | Remote, authenticated serving | 7 | 7 ✅ |
| 3 | Self-pruning | 11 | 11 ✅ |
| 4 | Connectors (Google Calendar + Gmail) | 6 | 6 ◐ (4.2–4.6 ✅; 4.1 code-complete + offline-tested, live consent pending owner Google creds) |
| 5 | Intelligence (DeepSeek, capped) | 7 | 0 |
| 6 | Retrieval & matching quality (hybrid · decay · dedup · ANN · rerank) | 6 | 1 (6.1 ✅; 6.2–6.6 todo) |
| 7 | Scheduling & reminders | 4 | 0 |
| 8 | Budgeting tracker *(was B2)* | 6 | 0 |
| 9 | Web app *(was B3)* | 5 | ◐ 9.2 basic server-rendered dashboard (skeleton) |
| 10 | iOS access point *(was B4)* | 3 | 0 |
| 11 | Input automation *(was B1)* | 3 | 0 |
| 12 | Structured records & multi-client memory | 3 | 0 |

Also shipped (cross-cutting, logged below): a **RAG-usability** batch (`get_note`,
`timeline`, status-in-results, category-prefix filter) and **smart capture**
(near-dup dedup + `append_to_note`).

Update the **Done** column as steps complete. Phases 6–12 were derived from the
prior-art study — see **`14-prior-art.md`** for what each borrows and why.

### Owner priority (set 2026-07-02, extended 2026-07-03)

1. **Standard RAG database FIRST — and it must actually run.** Core loop = capture →
   index → vector search with citations → `get_note`. **Done, verified, installed.**
2. **Then** finish connectors (Phase 4) and intelligence (Phase 5).
3. **Then Phase 6 (retrieval & matching quality)** — the mature-DB improvements that
   make the core trustworthy at scale before piling features on it.
4. **Then the life-manager surfaces** (Phases 7–12): scheduling/reminders, budgeting,
   web app, iOS, input automation, structured records.

### Design principles carried into every future phase (6–12)

Borrowed features are re-expressed **our way**, never copied wholesale:

- **Vault-first:** ingested data (transactions, reminders, records) is a Markdown
  note; relational tables stay derived + rebuildable.
- **Connectors are adapters** that upsert by `source_ref` (Plaid/CSV like
  Calendar/Gmail); **local-first, cloud-optional** (CSV before Plaid).
- **Reversible + audited** (tombstones, dry-run) for every state change.
- **One authenticated door**; web/iOS/automation are thin clients sharing one typed
  API client (YACS pattern).
- **Every step ships an offline self-check** (or a clearly-labelled gated one).

*(Backlog B1–B4 are now scheduled as Phases 8/9/10/11/12; nothing there is built yet
— do not start until Phases 4–6 are locked in.)*

---

## Phase 0 — Foundations

Goal: drop files into a `vault/`, index them into the relational DB, search with
citations, and rebuild the DB from the vault to the same result. Reuses the
existing chunker, embedder, vector store, and manifest.

### ☑ 0.1 — Vault config & paths
- **Scope:** Add `VAULT_DIR` config + a `paths()` helper for the taxonomy folders
  (`00-inbox`, `10-journal`, …, `.vault/`). Create folders on first run.
- **Test:** calling `paths()` returns existing dirs in a temp `VAULT_DIR`.
- **Status:** done
- **Notes:** `servers/vault/config.py`. `TAXONOMY_FOLDERS` constant drives folder
  creation; `ensure_layout()` is idempotent; DB/manifest/tombstones live under
  `.vault/`. `VAULT_DIR` overridable (expands `~`); `.env` auto-loaded.

### ☑ 0.2 — Frontmatter schema & parser
- **Scope:** Define the YAML frontmatter schema (`05-data-model.md`) and a
  parse/serialize round-trip.
- **Test:** parse → serialize → parse is identity; missing optional fields get
  defaults; a malformed file returns a clear error (what + how to fix).
- **Status:** done
- **Notes:** `servers/vault/frontmatter.py` — dependency-free YAML *subset*
  (scalars/null/bool/int, inline + block lists). Canonical serializer quotes only
  when needed so round-trip is exact. Unterminated block / non-`key: value` lines
  raise actionable `ValueError`s.

### ☑ 0.3 — Note model (load/save)
- **Scope:** A `Note` type wrapping frontmatter + body; load/save; stable `id`.
- **Test:** create a note, save, reload, fields match; id is stable across saves.
- **Status:** done
- **Notes:** `servers/vault/note.py`. ULID ids (time-sortable, 26-char Crockford
  base32). `ensure_defaults()` fills schema; `FIELD_ORDER` keeps frontmatter
  canonical; `save()` bumps `updated`.

### ☑ 0.4 — Vault walker (incremental)
- **Scope:** Walk `VAULT_DIR` for `.md`, skip `.vault/`; reuse the content-hash
  manifest so only changed files reprocess.
- **Test:** new/changed/deleted detection (exercised via the indexer below).
- **Status:** done
- **Notes:** `servers/vault/walk.py` reuses `knowledge.store.Manifest` +
  `file_hash`. `scan()` returns `to_index / present_keys / removed_keys`; keys
  namespaced `note:`.

### ☑ 0.5 — DB access layer (interface + SQLite + Postgres)
- **Scope:** Storage interface + two impls. Connection from config. Health check.
- **Test:** SQLite backend passes the contract offline; Postgres skips cleanly
  when its driver/DB is absent.
- **Status:** done
- **Notes:** `db.py` (ABC `VaultDB`, `VaultHit`, `get_db()`), `db_sqlite.py`
  (default; embeddings as float32 blobs, brute-force numpy cosine), `db_postgres.py`
  (pgvector, opt-in via `VAULT_DB=postgres` + `store-postgres` extra; untested
  offline by design). **Note:** SQLite uses brute-force cosine, not `sqlite-vec`,
  to stay stdlib-only — swap in later if scale demands.

### ☑ 0.6 — Schema & migrations
- **Scope:** Tables `documents, chunks, entities, links, events, tombstones` +
  indexes. Idempotent.
- **Test:** migrate twice → no error; expected indexes exist.
- **Status:** done
- **Notes:** Schema in each backend module; `EXPECTED_INDEXES` asserted by the
  test. FTS/lexical index deferred to the hybrid-search phase (P0 is vector-only).

### ☑ 0.7 — Indexer (vault → DB)
- **Scope:** Per changed note: chunk → embed → extract wikilinks → upsert
  documents/chunks/links. Incremental.
- **Test:** counts match; re-index unchanged → nothing; change/add/delete one →
  only it.
- **Status:** done
- **Notes:** `servers/vault/index.py`. Reuses `knowledge.chunk.chunk_markdown` +
  `get_embedder`. **Adopts** dropped notes (writes frontmatter + id back) so
  identity is stable. Dates→`events` deferred to the connector phase.

### ☑ 0.8 — `rebuild-index` command
- **Scope:** Drop derived tables (keep `tombstones`) and replay the vault.
- **Test:** rebuild on a populated vault runs clean and stays queryable.
- **Status:** done
- **Notes:** `rebuild_index()` in `index.py`; CLI at `scripts/vault_index.py`
  (`--rebuild`, `--full`, `--status`); console script `life-vault-index`.

### ☑ 0.9 — Rebuild contract test (the keystone)
- **Scope:** Incremental-built DB and a fresh rebuild give identical search
  results for a fixed query set.
- **Test:** top-k `chunk_id`s identical across both, for several queries.
- **Status:** done
- **Notes:** `tests/check_vault.py` (offline: hash embedder + SQLite + temp
  vault). All Phase 0 asserts pass; existing `check_knowledge.py` still green.

---

## Phase 1 — Capture & LLM write path

Goal: an LLM client can file a note and find it later; first-run credential prompts.

### ☑ 1.1 — `capture(text, category?, tags?)` tool
- **Scope:** Write a well-formed note to `00-inbox/` (or given category) with
  auto frontmatter, then index it.
- **Test:** capture → note exists, parses, searchable.
- **Status:** done
- **Notes:** `servers/vault/capture.py`. Slugified unique filenames (id-suffix on
  collision); indexes only the new note (manifest skips the rest); empty text →
  clear error.

### ☑ 1.2 — `quick_note(title, body)` tool
- **Scope:** Thin convenience over `capture` for a titled note.
- **Test:** title becomes the H1; frontmatter title set.
- **Status:** done
- **Notes:** Wraps `capture` with `# <title>` body prefix; respects `category`.

### ☑ 1.3 — `append_to_journal(text)` tool
- **Scope:** Append a timestamped entry to today's daily note
  (`10-journal/<year>/<date>.md`), creating it if absent.
- **Test:** two appends → same dated file, both timestamped.
- **Status:** done
- **Notes:** `append_to_journal(text, when=...)`; loads-or-creates the dated note,
  appends `## HH:MM` sections, reindexes.

### ☑ 1.4 — Auto-frontmatter & defaults
- **Scope:** Consistent default frontmatter across all write tools.
- **Test:** notes from each write tool have a complete, valid frontmatter block.
- **Status:** done
- **Notes:** Centralized in `Note.ensure_defaults()` (Phase 0); write tools set
  source/category/tags through it.

### ☑ 1.5 — Inbox triage listing
- **Scope:** `list_inbox()` returns unfiled notes with summaries.
- **Test:** captured inbox notes listed; filed ones not.
- **Status:** done
- **Notes:** Reads `00-inbox/`; returns id/title/path/created/summary (first
  non-heading line).

### ☑ 1.6 — Credential tools (guarded)
- **Scope:** `missing_credentials()` + `set_credential(name, value)`; owner-only;
  secrets store.
- **Test:** missing→set→drops out of missing; non-owner refused; empty errors.
- **Status:** done
- **Notes:** `servers/vault/secrets.py`. JSON store under `.vault/` (chmod 600),
  env fallback on read, `CREDENTIAL_REGISTRY` for first-run prompts,
  `require_owner()` guard. **Plaintext for now — encrypted at rest in Phase 2.4.**

### Phase 1 server
- **Notes:** `servers/vault/server.py` (FastMCP, stdio) exposes vault_status,
  search_vault, capture, quick_note, append_to_journal, list_inbox, reindex,
  rebuild_index, missing_credentials, set_credential. Console script `life-vault`.
  Self-check: `tests/check_vault_capture.py` (all green).

---

## Phase 2 — Remote, authenticated serving

Goal: query + capture from your phone over an authenticated endpoint; secrets safe.

### ☑ 2.1 — Streamable HTTP transport
- **Scope:** Serve the vault server over HTTP (keep stdio for local).
- **Test:** a real server boots and answers over HTTP (live smoke).
- **Status:** done
- **Notes:** `server.py --http` (host/port via flags or `VAULT_HOST/PORT`); wraps
  `FastMCP.streamable_http_app()` and runs uvicorn. Endpoint `/mcp`.

### ☑ 2.2 — Bearer-token auth
- **Scope:** Require a token on every request; store hashed; constant-time compare.
- **Test:** no/wrong token → 401; correct token → passes; rotation kills old.
- **Status:** done
- **Notes:** `servers/vault/auth.py`. SHA-256 stored (raw token never on disk),
  `hmac.compare_digest`, `BearerAuthMiddleware` (ASGI) gates all HTTP. Deny-by-
  default when no token configured.

### ☑ 2.3 — Audit log
- **Scope:** Log who/when/path + authorized for each request.
- **Test:** authorized + unauthorized attempts both recorded with timestamps.
- **Status:** done
- **Notes:** `servers/vault/audit.py` → `.vault/audit.log` (JSONL). Best-effort
  writes never break the request path. `vault_admin --audit N` tails it.

### ☑ 2.4 — Encrypted secrets + boot unlock
- **Scope:** Encrypted secrets file + master key; decrypt on boot; refuse on bad key.
- **Test:** ciphertext on disk; wrong key → unlock raises (server refuses start);
  right key → readable.
- **Status:** done
- **Notes:** `crypto.py` (scrypt KDF + Fernet), `credentials.py` writes
  `secrets.enc` when `VAULT_MASTER_KEY` is set (migrates/deletes plaintext). **Module
  renamed `secrets.py`→`credentials.py`** to avoid shadowing stdlib `secrets` when
  run as a script.

### ☑ 2.5 — Docker Compose deploy
- **Scope:** Compose with Postgres+pgvector + the server.
- **Test:** `docker compose up -d` runs it (manual; image/compose authored).
- **Status:** done
- **Notes:** `Dockerfile`, `docker-compose.yml` (pgvector/pgvector:pg16 + vault),
  `.dockerignore`. Binds to `127.0.0.1` by default; secrets via `VAULT_MASTER_KEY`.

### ☑ 2.6 — Tailscale binding + doc
- **Scope:** Bind to tailnet; document join + connect.
- **Test:** runbook (manual verification on real devices).
- **Status:** done
- **Notes:** `docs/DEPLOY.md` — full runbook (config → up → token → index →
  Tailscale `serve`/bind → connect → ops). `VAULT_HOST` controls bind address.

### ☑ 2.7 — Token rotation CLI
- **Scope:** Rotate the bearer token; old stops working.
- **Test:** rotate → old rejected, new accepted (in Phase 2 self-check).
- **Status:** done
- **Notes:** `scripts/vault_admin.py` (`--rotate-token`, `--set-token`, `--check`,
  `--status`, `--audit N`). Token shown once; only hash stored.

### Phase 2 self-check
- **Notes:** `tests/check_vault_serve.py` — crypto round-trip, encrypted-at-rest +
  bad-key refusal, hash/verify/rotate, ASGI gate (401/pass), audit, and a live
  HTTP 401 smoke. All green. Phase 0/1 still green after the module rename.

---

## Phase 3 — Self-pruning

Goal: stale notes archive automatically; every change is explainable and reversible.

> User priority (stated): (1) can't add identical documents, (2) relations build
> themselves so auditing is easy. Done first as **batch 1** (3.0 + 3.3-exact).

### ☑ 3.0 — Auto-relations + auditable map  *(DONE — batch 1)*
- **Scope:** Resolve `[[wikilinks]]` (slug- or title-form) to real documents at
  index time (`links.dst_document`); read back `outgoing`/`backlinks` via
  `relations.related()`; regenerate a human-readable `.vault/relations.md`
  (`write_relations_map`). New MCP tools: `related`, `find_duplicates`,
  `write_relations_map`. Dangling links are kept + flagged unresolved.
- **Test:** `tests/check_vault_prune.py` (3.B/3.C) — slug + title links resolve,
  backlinks appear, map covers every doc, relations survive a rebuild.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** Similarity/“related-by-embedding” edges deferred (see 3.3 near-dup).

### ☑ 3.1 — Pruning signals  *(DONE — batch 2)*
- **Scope:** Maintain `last_access`, `access_count`; bump when search surfaces a
  note. Persist to a durable sidecar `.vault/signals.json` (NOT frontmatter — see
  deviation below) + cache on document columns; reapply on every index/rebuild.
- **Test:** `tests/check_vault_prune_scores.py` (3.1) — a search bumps the sidecar
  + DB; signals survive a rebuild and are reapplied to the DB cache.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** DEVIATION from the original "persist to frontmatter" plan: writing
  `last_access` into a note's frontmatter changes its content hash → forces a
  needless re-embed (and risks a churn loop). Sidecar avoids this while staying
  auditable + rebuild-durable. See D12.

### ☑ 3.2 — `prune_score` + config  *(DONE — batch 2)*
- **Scope:** `prune_score = w_recency·recency + w_usage·usage + w_importance·imp
  + w_links·incoming + w_pin·pinned − w_age·staleness`. Weights load from
  `.vault/prune.config` (flat `key: value`, defaults in code). Recomputed at the
  end of every index/rebuild and after each access bump. `explain()` returns the
  per-term breakdown. Tools: `explain_prune`, `recompute_scores`, `init_prune_config`.
- **Test:** `tests/check_vault_prune_scores.py` (3.2) — pinned/important high,
  old-untouched low; raising `w_importance` predictably flips ranking; breakdown
  sums to the score.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** Config file is `.vault/prune.config` (flat) not `.yaml` — no YAML dep.
  Pin is a big weight now; the HARD "never prune pinned" rule lands with archival.

### ☑ 3.3 — Dedup (exact ✅ / near ✅)
- **Scope:** Exact by body hash (`documents.body_hash`, whitespace-normalized):
  capture refuses identical content; the indexer records identical files in a
  `duplicates` table instead of creating a 2nd document (no search double-count).
  Near-dup by embedding cosine → flag only (no auto-merge).
- **Test:** `tests/check_vault_prune.py` (3.A exact, 3.E near) — capture refuses a
  byte-identical note; a duplicate file on disk is recorded, not indexed; survives
  rebuild. Near: two near-identical drafts are flagged, an unrelated note is not,
  the threshold knob unflags at 1.0, nothing is moved/deleted, survives rebuild.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** Near-dup is a **report-only** tool (`find_near_duplicates(threshold=0.9)`):
  cosine between document *centroids* (L2-normalized mean of a doc's chunk vectors)
  via a new `VaultDB.document_vectors()` (+ shared `unit_mean()`), reusing the
  embeddings already in the index — **no schema change**, so the rebuild contract is
  untouched. Flag-only per the spec; exact dups can't appear (they never become a
  2nd document). `duplicates` is a derived table (dropped/rebuilt). Empty-body notes
  all hash equal → could false-dup (minor; see D9). O(n²) pairwise scan (ponytail
  ceiling noted in code; upgrade path = block-by-category / ANN, see D2).

### ☑ 3.4 — TTL / expiry archival
- **Scope:** Notes past `expires:` auto-archive on a prune run.
- **Test:** a note with a past expiry archives; one with future/none doesn't.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** `archive.run_ttl(dry_run=)` selects active, non-pinned notes whose
  `expires:` is at/before now and archives them via the shared `archive_documents`
  mechanism. Pinned-but-expired is protected by the HARD rule (checked
  independently of any weight). `tests/check_vault_archive.py`.

### ☑ 3.5 — Decay archival
- **Scope:** Notes below score threshold AND untouched N days archive (config).
- **Test:** synthetic old/low note archives; recent/high note survives.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** `archive.run_decay(dry_run=, now=, threshold=, min_idle_days=)`.
  BOTH gates must hold: `prune_score <= decay_score_threshold` AND idle
  `>= decay_min_idle_days` (idle = `prune.days_since_touch`, the later of `updated`
  / `last_access`). Weights live in `.vault/prune.config` (conservative defaults
  `decay_score_threshold: 0.0`, `decay_min_idle_days: 90.0`); reuses
  `archive_documents` so dry-run/tombstone/undo come for free. Pinned protected by
  the hard rule. `now` is injectable so the test ages the vault without waiting.
  MCP tool `prune_decayed`.

### ☑ 3.6 — Archive move & status transitions
- **Scope:** Move to `90-archive/`, set `status=archived`, down-rank in search.
- **Test:** archived note moves on disk, still searchable, ranked below active.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** `archive_documents` moves the note under `90-archive/<same sub-path>`,
  flips frontmatter `status=archived`, and reconciles the index with one
  incremental reindex. Search down-rank is a fixed penalty in the backend
  (`_ARCHIVED_RANK_PENALTY`) so archived notes stay findable but below active.

### ☑ 3.7 — Tombstones + undo/restore
- **Scope:** `tombstones` rows + `.vault/tombstones.md`; `restore <id>` and
  `prune --undo <batch>`.
- **Test:** archive a batch → undo restores all to prior paths/status; restore one
  works from archive and from tombstone.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** Each archival records a tombstone (doc id, action, reason, prev_path,
  frontmatter snapshot, `at`, `batch`) and regenerates the auditable
  `.vault/tombstones.md`. `archive.restore(note_id=, batch=)` moves the file back,
  sets `status=active`, and clears the tombstone; a `batch` id ties one prune run
  together for whole-run undo. Tombstones survive `rebuild-index` (not a derived
  table). Tools: `restore_note`, `list_tombstones`.

### ☑ 3.8 — `prune --dry-run` + `explain-prune`
- **Scope:** Dry-run prints every would-be change (note, action, score, policy);
  `explain-prune <id>` shows the score breakdown.
- **Test:** dry-run changes nothing on disk; explain returns each weight's
  contribution.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** Every policy entry point (`run_ttl`, `run_decay`) and
  `archive_documents` take `dry_run=True` (default for the MCP tools) → returns the
  `would_archive` plan (id, title, prev/new path, reason, policy) and touches
  nothing. `prune.explain(note_id)` returns the per-term breakdown that sums to the
  score. Tools: `prune_expired`, `prune_decayed`, `explain_prune`.

### ☑ 3.9 — Scheduler loop
- **Scope:** Periodic job: reindex → prune (dry-run or apply per config) → backup;
  writes a summary to the journal. Also runnable by hand.
- **Test:** one scheduled tick performs the steps and logs a summary note.
- **Status:** DONE. SQLite tested; Postgres mirrored (untested → D1).
- **Notes:** `servers/vault/scheduler.py` — `run_once()` sequences the existing,
  individually-tested ops (index → `run_ttl` → `run_decay` → `backup.run_backup`)
  and appends a one-line summary to today's journal via `capture.append_to_journal`.
  Pruning is DRY-RUN unless `apply=True` or `prune_apply>0` in `.vault/prune.config`
  (safe default). `now` is threaded into the policies so tests age the vault. `loop()`
  is the unattended cron form (a bad tick is logged, loop continues). CLI
  `scripts/vault_schedule.py` (`--once/--apply/--backup/--loop --interval`), console
  script `life-vault-schedule`; MCP tools `run_maintenance`, `backup_vault`. The
  summary note lands in the NEXT tick's commit (backup runs just before it) — an
  intentional lag so the summary can name this tick's commit.

### ☑ 3.10 — Backup job (local)
- **Scope:** `git commit` the vault + `pg_dump`/SQLite copy to a local backup
  folder (ideally a 2nd disk).
- **Test:** a run produces a new git commit and a dated DB dump file.
- **Status:** DONE (SQLite path tested; `pg_dump` path authored, untested → D14).
- **Notes:** `servers/vault/backup.py` — `git_commit_vault` inits the repo on first
  run, stages, and commits with per-commit identity flags (never touches global git
  config); no changes → no empty commit. `snapshot_db` writes a dated file to
  `config.backup_dir()` (a sibling of the vault via `VAULT_BACKUP_DIR`, so it isn't
  committed back in): SQLite uses the online-backup API (consistent copy), Postgres
  shells to `pg_dump`. **Security:** `_ensure_gitignore` guarantees `.vault/secrets.*`
  (and the rebuildable `index.db`/`manifest.json` + churny `audit.log`) are excluded
  before every commit, so secrets are never committed — asserted by the test.

---

## Phase 4 — Connectors (Google Calendar + Gmail)

Goal: calendar + mail appear as deduped Markdown, searchable with everything else.

### ◐ 4.1 — Google OAuth setup wizard
- **Scope:** Wizard runs the OAuth flow, stores the encrypted refresh token,
  least scopes (read-only).
- **Test:** wizard yields a usable token; `missing_credentials` clears Google.
  (Manual/mocked auth in the offline test.)
- **Status:** CODE-COMPLETE + offline-tested. Live consent pending the owner's
  Google Cloud OAuth credentials (a manual GCP step + one browser consent — can't be
  automated here). The whole seam is built and injected so it's verifiable offline.
- **Notes:** `servers/vault/connectors/google_auth.py` — read-only scopes
  (`calendar.readonly` + `gmail.readonly`), `_client_config()` assembled from the
  stored client id/secret (no client_secret.json on disk), `run_consent()`
  (`InstalledAppFlow.run_local_server`, `access_type=offline` + `prompt=consent`, so
  Google returns a refresh token; stored encrypted via the credential store),
  `get_access_token()` (google-auth skew-aware refresh), `status()`/`has_consent()`.
  `servers/vault/connectors/google_fetch.py` — the `fetch_fn(cursor) -> FetchResult`
  per service over thin REST (an injectable `http_get` seam, not the heavy
  google-api client): `calendar_fetch_fn` (syncToken cursor, internal pageToken
  paging, 410 → bounded full resync) + `gmail_fetch_fn` (query + `after:<epoch>`
  watermark, list ids → metadata get) + `live_{calendar,gmail}_connector` builders.
  All google libs are lazy-imported so the modules (and the offline suite) import
  without the extra. Wired into `scripts/vault_sync.py` (`--setup` consent;
  `--calendar/--gmail` run live, guarded with an actionable missing-creds message)
  and three MCP tools (`google_auth_status`, `setup_google`, `sync_google`). New dep
  extra `connectors-google = [google-auth-oauthlib]` (pulls google-auth + requests).
  Offline self-check `tests/check_vault_google.py`: read-only scopes, credential-chain
  readiness, calendar paging + syncToken cursor + 410 fallback, gmail list/get +
  `after:` watermark, and `run_sync` driven by the injected fetch (notes created,
  cursor persisted, re-sync is a clean no-op). **Remaining (owner):** create a GCP
  "Desktop app" OAuth client, enable the Calendar + Gmail APIs, `set_credential` the
  id/secret, then run `setup_google` once.

### ☑ 4.2 — Calendar adapter
- **Scope:** Pull events → `events` rows + journal-style notes; `source=google`,
  `source_ref=event id`.
- **Test:** a mocked event set produces notes + `events` rows; `timeline()`
  returns them.
- **Status:** DONE (offline, fixture events). SQLite tested; Postgres mirrored (D1).
- **Notes:** `servers/vault/connectors/calendar.py` — pure `event_to_fields`
  (Google event dict → title/start/end/location/body, `source_ref=gcal://event/<id>`,
  handles timed `dateTime` and all-day `date`) + `sync_events` (upserts each via the
  connector base; skips cancelled + no-start). Notes land in `40-areas/calendar/`
  with `start`/`end` in frontmatter; the **indexer derives an `events` row** from any
  note with a `start:` (`index.extract_events` → `VaultDB.replace_events`), so events
  are reconstructed from the vault on rebuild (not stored authoritatively in the DB).
  Added `VaultDB.list_events` (the `timeline()` primitive; an MCP timeline tool comes
  with the sync surface in 4.6). Live Google fetch/OAuth is the only untested part
  (deferred to 4.1). `tests/check_vault_connectors.py`.

### ☑ 4.3 — Gmail adapter (ephemeral)
- **Scope:** Pull filtered mail (label/query) → notes in an ephemeral category
  with a short default `expires:` TTL.
- **Test:** mocked messages → notes with TTL set; filter excludes non-matching
  mail.
- **Status:** DONE (offline, fixture messages). SQLite tested; Postgres mirrored (D1).
- **Notes:** `servers/vault/connectors/gmail.py` — pure `message_to_fields`
  (headers→subject/from/date, `snippet` or a decoded `body_text`,
  `source_ref=gmail://msg/<id>`) + `sync_messages` (upserts via the base with
  `expires = now + ttl_days`, default 30d, into `50-resources/mail/`; `label_filter`
  ingests only matching mail). The TTL is what makes it ephemeral: the test proves
  an expired mail note is archived by the **existing** Phase 3 `archive.run_ttl`
  policy — no new pruning code. Live Gmail fetch/OAuth + MIME decode deferred (4.1/4.6).

### ☑ 4.4 — Connector cursors
- **Scope:** Per-source checkpoint in `.vault/` for incremental, resumable sync;
  rate-limit backoff.
- **Test:** second sync only fetches items after the cursor; interrupted sync
  resumes without dupes.
- **Status:** DONE (offline, fake provider). Rate-limit backoff lives with the live
  fetch (4.1).
- **Notes:** `connectors/cursors.py` — durable `.vault/cursors.json` sidecar
  (`get_cursor`/`set_cursor`, records cursor + last_sync + item count), mirroring the
  prune signals sidecar (survives a DB rebuild). Cursor value is opaque (syncToken /
  historyId / date) — only the provider's fetch interprets it.

### ☑ 4.5 — Re-sync dedup by `source_ref`
- **Scope:** Re-syncing an existing item updates the same note, never duplicates.
- **Test:** changing a source item updates its note in place; count unchanged.
- **Status:** DONE. SQLite tested; Postgres mirrored (D1).
- **Notes:** `servers/vault/connectors/base.py::upsert_note` is the shared connector
  write primitive: look up an existing document by `source_ref`
  (`VaultDB.find_document_by_source_ref`, new on both backends) → update it IN PLACE
  (same file, same stable id) or create a fresh note. An unchanged re-sync is a
  **no-op** (`action="unchanged"`; compares title/body/extra) so periodic syncs don't
  rewrite files or re-embed. Built ahead of 4.1/4.6 because both adapters depend on it.

### ◐ 4.6 — `sync` tools/CLI
- **Scope:** `sync google` (calendar+gmail) on demand + via scheduler.
- **Test:** on-demand sync runs both adapters and reports counts.
- **Status:** CORE DONE (runner + cursors + surfaces, offline-tested with a fake
  provider); the live Google fetch is 4.1.
- **Notes:** `connectors/sync.py` — `run_sync(connector, full=)` separates **FETCH**
  (injected `fetch_fn`, mockable) from **INGEST** (the tested `calendar`/`gmail`
  adapters), pages from the saved cursor until caught up, advances the cursor, and is
  idempotent on full replay (source_ref dedup → no dupes). `CalendarConnector` /
  `GmailConnector` wrap the adapters. Surfaces: `sync_status` MCP tool + `scripts/
  vault_sync.py` (`--status` works; `--calendar/--gmail` report they need 4.1) +
  `life-vault-sync`. `tests/check_vault_sync.py`. **Remaining:** plug a real
  `fetch_fn` (Google API) once 4.1 lands.

---

## Phase 5 — Intelligence (DeepSeek, budget-capped)

Goal: cheap server-side LLM tidies and (later) packs context; spend is bounded.

### ☐ 5.1 — Budget cap & spend tracking
- **Scope:** `.vault/budget.config.yaml` monthly cap; per-call estimated-cost log;
  refuse paid calls past cap with a clear error; confirm-before-batch.
- **Test:** simulated spend past cap → paid call refused with actionable message.
- **Status:**
- **Notes:**

### ☐ 5.2 — DeepSeek client wrapper
- **Scope:** Minimal client with retry/backoff; routes through the budget gate.
- **Test:** mocked client returns a completion and records estimated cost.
- **Status:**
- **Notes:**

### ☐ 5.3 — Regroup batch job
- **Scope:** Propose inbox → category moves (and new-folder suggestions);
  dry-run/approve, reversible like pruning.
- **Test:** mocked LLM proposes moves; dry-run changes nothing; approve applies +
  is undoable.
- **Status:**
- **Notes:**

### ☐ 5.4 — Compaction / summarization
- **Scope:** Summarize an old cluster into a digest note; archive originals,
  linked from digest.
- **Test:** a cluster collapses to a digest; originals archived + linked; undo
  restores.
- **Status:**
- **Notes:**

### ☐ 5.5 — OCR ingestion
- **Scope:** Local OCR (Tesseract/Paddle) for image/scanned-PDF text → Markdown;
  hosted vision-OCR fallback (capped) for hard scans.
- **Test:** an image with text yields a note containing that text.
- **Status:**
- **Notes:**

### ☐ 5.6 — Agentic RAG `context_pack`
- **Scope:** Server-side DeepSeek loop: search → read → maybe re-search →
  return a small cited context pack within a token budget.
- **Test:** for a question, returns a pack under `budget_tokens` with valid
  citations; bounded number of LLM calls.
- **Status:**
- **Notes:**

### ☐ 5.7 — Optional local re-ranker
- **Scope:** Cross-encoder on spare GPU VRAM to reorder top hits.
- **Test:** enabling it changes ordering and improves a small relevance check;
  disabling restores base order.
- **Status:**
- **Notes:**

---

## Phase 6 — Retrieval & matching quality

Goal: make retrieval + dedup best-in-class per mature databases (see
`14-prior-art.md`) before layering more features on the core. Keep the rebuild
contract green; each step ships an offline self-check.

### ☑ 6.1 — Hybrid retrieval (lexical + vector, RRF)
- **Scope:** run a lexical retriever alongside vector search and fuse with
  **Reciprocal Rank Fusion** (k=60). Closes the "hybrid is a later phase" note.
- **Test:** an exact-term query (a code like `E1042`) that vector-only misses is
  found; a chunk ranked by both retrievers outranks a single-retriever one;
  active-over-archived preserved.
- **Status:** DONE (SQLite tested; Postgres mirrored via tsvector → D1).
- **Notes:** `servers/vault/lexical.py` = pure-Python **BM25** (k1=1.5, b=0.75) over
  the same candidate chunks the vector scan already loads — **no FTS5 dependency,
  no schema change → rebuild contract untouched** (ponytail; FTS index is the
  scale-up path with D2). New `VaultDB.lexical_search` (SQLite: BM25; Postgres:
  `tsvector`/`ts_rank`). `search.py` retrieves a pool from each retriever and
  `_rrf_fuse`s them (active ranked strictly above archived, then fused score); a
  `mode` switch (`hybrid`|`vector`|`lexical`) is exposed on `search_vault`.
  `tests/check_vault_hybrid.py`. Inspiration: ES / pgvector / Azure hybrid + RRF.

### ☐ 6.2 — Frequency-decay (aging) in `prune_score`
- **Scope:** decay `access_count` over time (periodic halving, or decay by time since
  `last_access`) so long-ago popularity fades — removes the latent LFU-pollution in
  today's monotonic counter.
- **Test:** two equal-`updated` notes, one accessed long ago vs recently → recent
  scores higher; an idle note's usage term shrinks across recomputes.
- **Inspiration:** W-TinyLFU (Caffeine), Redis LFU counter decay.

### ☐ 6.3 — Dedup precision via lexical signature
- **Scope:** compute a **SimHash/MinHash** signature per note; near-dup + smart
  capture require high embedding cosine **AND** high lexical agreement (Jaccard/
  Hamming) to call something a duplicate — semantic + literal, not either alone.
- **Test:** two same-topic-but-different notes are NOT flagged; two near-identical
  drafts (minor edits / OCR variance) ARE. Resolves D20.
- **Inspiration:** AltaVista dedup, LLM-dataset dedup pipelines.

### ☐ 6.4 — ANN index for scale (pgvector HNSW)
- **Scope:** pin the embedding dimension; add an HNSW index on Postgres; keep
  brute-force for SQLite (fine at personal scale).
- **Test (gated):** recall vs brute-force within tolerance on a sample. Promotes D2/D7.
- **Inspiration:** pgvector 0.7+, FAISS/Milvus.

### ☐ 6.5 — Cross-encoder rerank (optional, local)
- **Scope:** rerank the top-k with a local cross-encoder on spare GPU VRAM; off by
  default. (Moved here from the old 5.7.)
- **Test:** enabling improves a small relevance check; disabling restores base order.

### ☐ 6.6 — Lifecycle hard-delete with grace
- **Scope:** archived + stale beyond a configurable grace window become eligible for
  hard-delete; opt-in, **dry-run first**, logged; `60-sources/` originals honored.
- **Test:** an item past archive-grace is listed in dry-run and removed only on
  confirm; reversible until then.
- **Inspiration:** Elasticsearch ILM `min_age` delete phase, Cassandra `gc_grace`.

---

## Phase 7 — Scheduling & reminders

Goal: the vault can remind you and answer "what's next / when am I free". Builds on
the `events` layer. *(Inspiration: AppointmentScheduler reminders/working-plans;
YACS notifications service.)*

### ☐ 7.1 — Reminders
- **Scope:** `remind_at` on notes/events; the scheduler tick fires **due** reminders
  into today's journal/inbox (idempotent, reversible).
- **Test:** a due reminder produces exactly one entry and is not re-fired.

### ☐ 7.2 — Working-hours / availability
- **Scope:** a simple availability model over events → a "free slots" query.
- **Test:** given a fixture of events, free-slot query returns the expected gaps.

### ☐ 7.3 — Notifier channel
- **Scope:** pluggable notifier (email / webhook / local), consent-gated, off by
  default; reminders route through it when enabled.
- **Test:** a mock notifier receives a correctly-formatted reminder.

### ☐ 7.4 — Timeline/agenda upgrades
- **Scope:** extend `timeline` with reminders + read-only recurrence expansion.
- **Test:** a recurring event expands correctly within a window.

---

## Phase 8 — Budgeting tracker *(was backlog B2)*

Goal: track money **vault-first** — local-first CSV, cloud-optional bank sync.
*(Inspiration: Mintable — `setup`+`fetch`, pluggable sources incl. CSV, categorize +
budget.)*

### ☐ 8.1 — Finance data model
- **Scope:** derived `accounts` + `transactions` tables + transaction **notes**
  (source_ref-deduped) under `40-areas/finance/`.
- **Test:** importing a transaction set yields notes + rows; a rebuild re-derives them.

### ☐ 8.2 — CSV import adapter + wizard
- **Scope:** `setup`/`fetch` flow (Mintable pattern); column mapping; fully local.
- **Test:** a sample CSV imports and is deduped on re-import.

### ☐ 8.3 — Bank connector (Plaid/Teller, cloud-optional)
- **Scope:** encrypted creds, incremental cursor, read-only; upsert by `source_ref`.
- **Test (mocked):** mock transactions import and dedup on re-sync.

### ☐ 8.4 — Categorization + budgets
- **Scope:** rules (+ optional budget-capped LLM) assign categories; budget config per
  category/month.
- **Test:** rules categorize a fixture; a budget rollup sums correctly.

### ☐ 8.5 — Reports / rollups
- **Scope:** spend by category/month as computed views + a periodic digest note.
- **Test:** a rollup matches a known fixture total.

### ☐ 8.6 — Bills as reminders (optional)
- **Scope:** due bills surface as Phase 7 reminders.
- **Test:** a due bill creates one reminder.

---

## Phase 9 — Web app *(was backlog B3)*

Goal: a human front-end over the **one authenticated API**. *(Inspiration: YACS
monorepo + shared `api-client`; StreamBrain Next.js/shadcn chat UI; datadam shared
DB.)*

**Decisions (2026-07-05, owner):**
- **Interim: a basic server-rendered dashboard now** (`servers/vault/web.py`, plain
  Starlette HTML, no framework/CSS, localhost-only, read-only) reusing
  `search`/`timeline`/`get_note`/`vault_status`. The richer Next.js + shadcn client
  (9.1–9.5) is the later upgrade; this "walking skeleton" gives a usable product
  today and a `/build` page to watch progress.
- **Unified events, not a new todo DB.** The existing `events` table (derived from
  the `start:` field on any note) already unifies calendar items, assignment/exam
  due dates, bills, and club meetings — the dashboard's Upcoming reads it. **Google
  Calendar with ALL-CAPS title prefixes** (`SCHOOL:`, `FINANCE:`, …) is the *input*;
  the calendar adapter parses the prefix into a `group` tag so Upcoming can separate
  categories. No separate to-do database.

### ☐ 9.1 — Typed API client package
- **Scope:** a small client (search / capture / get_note / timeline / prune) reused by
  web **and** iOS.
- **Test (gated):** the client round-trips against a live server.

### ◐ 9.2 — Web UI
- **Scope:** Next.js + shadcn/Tailwind: search, capture, timeline, inbox/prune-review.
- **Test (gated):** component/e2e smoke of the core screens.
- **Status:** WALKING SKELETON DONE (server-rendered plain HTML, offline-tested). The
  Next.js/shadcn upgrade is still to come.
- **Notes:** `servers/vault/web.py` (Starlette) — pages: Home (counts + next-up),
  Upcoming (the events table), Search + Note viewer (RAG), Finances (finance-category
  notes; full module = Phase 8), Build (renders the plan's status dashboard). Escaped
  HTML, **read-only, localhost-only + unauthenticated** (bind stays 127.0.0.1; remote
  = Tailscale/bearer later). CLI `scripts/vault_web.py` + `life-vault-web`; needs the
  `serve` extra. `tests/check_vault_web.py` (Starlette TestClient, no network).

### ☐ 9.3 — Chat-over-vault + streaming
- **Scope:** server-side hybrid RAG answers **with citations**, streamed to the UI.
- **Test (gated):** a question returns a cited, streamed answer.

### ☐ 9.4 — Auth / session
- **Scope:** bearer/OAuth against Phase 2; unauthorized blocked.
- **Test:** unauthorized request rejected; authorized flow works.

### ☐ 9.5 — Prune-review & audit UI
- **Scope:** surface tombstones + dry-run plans for one-click approve/undo.
- **Test:** approve/undo round-trips through the API.

---

## Phase 10 — iOS access point *(was backlog B4)*

Goal: capture/query from the phone over Tailscale.

### ☐ 10.1 — PWA
- **Scope:** installable PWA of the web app with an **offline capture queue** that
  syncs when reconnected.
- **Test (gated):** offline capture queues, then syncs.

### ☐ 10.2 — Apple Shortcuts
- **Scope:** share-sheet / Shortcuts → capture + query via the API.
- **Test:** a Shortcut posts a capture (manual runbook).

### ☐ 10.3 — Push / reminder delivery
- **Scope:** Phase 7 reminders reach the phone (web-push or a Shortcuts automation).
- **Test:** a reminder is delivered (manual).

---

## Phase 11 — Input automation *(was backlog B1)*

Goal: capture/search from anywhere on the desktop — safely.

### ☐ 11.1 — Hotkey daemon
- **Scope:** global hotkeys → capture the selection / quick search via the **local**
  API.
- **Test:** a simulated hotkey event triggers a capture.

### ☐ 11.2 — Clipboard / screenshot capture
- **Scope:** a hotkey sends clipboard text or a screenshot (OCR via 5.5) to the inbox.
- **Test:** an image capture yields a note containing its text.

### ☐ 11.3 — Safety / consent
- **Scope:** explicit hotkey allowlist; **no background keylogging**; local-only.
- **Test:** capture fires only on the bound hotkey.

---

## Phase 12 — Structured records & multi-client memory

Goal: typed personal records + safe sharing across AI clients. *(Inspiration:
datadam_mcp persistent-memory layer + CRUD records; our extras = auth + encryption +
the Markdown vault as truth.)*

### ☐ 12.1 — Structured records
- **Scope:** frontmatter-typed records (profile, preferences, contacts) + CRUD tools,
  complementing freeform notes; derived into the existing tables.
- **Test:** create/read/update/delete round-trips; a rebuild re-derives them.

### ☐ 12.2 — Multi-client concurrency
- **Scope:** safe concurrent stdio + HTTP access to one vault (write locking /
  consistency around index + manifest).
- **Test:** concurrent writes don't corrupt the index or manifest.

### ☐ 12.3 — Portable export / import
- **Scope:** one-command export (vault is already git) + structured records, for
  backup/migration to a fresh box.
- **Test:** export → fresh import reproduces identical search results.

---

## Implementation log (chronological)

Add a dated line per meaningful change so the build has a readable history.

| Date | Step(s) | What landed | Key files |
|------|---------|-------------|-----------|
| 2026-06-29 | — | Vision/scope docs + repo rename to `life-vault-mcp` | `docs/vision/*`, `pyproject.toml`, `README.md` |
| 2026-06-29 | 0.1–0.9 | **Phase 0 done.** Vault config/taxonomy, frontmatter parser, Note model (ULID), incremental walker, relational index (SQLite default + Postgres/pgvector), indexer, `rebuild-index`, and the rebuild-contract self-check (all green). | `servers/vault/*`, `scripts/vault_index.py`, `tests/check_vault.py`, `pyproject.toml`, `.gitignore` |
| 2026-06-29 | 1.1–1.6 | **Phase 1 done.** Write path: capture / quick_note / append_to_journal, inbox triage, owner-guarded credential store; vault MCP server (stdio) + Phase 1 self-check (all green). | `servers/vault/capture.py`, `servers/vault/credentials.py`, `servers/vault/server.py`, `tests/check_vault_capture.py`, `pyproject.toml` |
| 2026-06-29 | 2.1–2.7 | **Phase 2 done.** Authenticated Streamable HTTP serving: bearer-token middleware (hashed, constant-time), audit log, encrypted-at-rest secrets (scrypt+Fernet) with boot unlock, Docker Compose + Tailscale deploy runbook, token-rotation admin CLI. Renamed `secrets.py`→`credentials.py` (stdlib shadowing fix). Self-check incl. live HTTP 401 smoke — all green. | `servers/vault/{auth,audit,crypto,credentials,server}.py`, `scripts/vault_admin.py`, `Dockerfile`, `docker-compose.yml`, `docs/DEPLOY.md`, `tests/check_vault_serve.py` |
| 2026-06-29 | 3.0, 3.3-exact | **Phase 3 batch 1 done** (user priorities). Exact dedup: `documents.body_hash` (whitespace-normalized) — capture refuses identical content, indexer records identical files in a `duplicates` table (no search double-count). Auto-relations: `[[wikilinks]]` resolve (slug/title) to `links.dst_document`, `related()` exposes outgoing+backlinks, dangling links flagged; generated auditable `.vault/relations.md`. New tools: `related`, `find_duplicates`, `write_relations_map`. New self-check + FULL suite green. Deferred D8–D11. | `servers/vault/{db,db_sqlite,db_postgres,index,capture,relations,textutil,config,server}.py`, `tests/check_vault_prune.py` |
| 2026-06-29 | 3.1, 3.2 | **Phase 3 batch 2 done.** Access signals: search bumps `access_count`/`last_access` into a durable `.vault/signals.json` sidecar (+ DB cache), reapplied on every index/rebuild. `prune_score`: weighted formula (recency/usage/importance/links/pin − age), weights from `.vault/prune.config` (defaults in code), recomputed after index/rebuild + each access bump; `explain()` per-term breakdown. New tools: `explain_prune`, `recompute_scores`, `init_prune_config`. New self-check + FULL suite green. Deviation D12 (sidecar not frontmatter); deferred D13. | `servers/vault/{prune,search,index,db,db_sqlite,db_postgres,config,server}.py`, `tests/check_vault_prune_scores.py` |
| 2026-06-30 | 3.3-near | **Near-dup detection (flag-only) — step 3.3 complete.** `find_near_duplicates(threshold=0.9)` reports document pairs whose centroid embeddings (L2-normalized mean of chunk vectors, reusing the index) meet/exceed a cosine threshold, most-similar first; never merges or deletes. Added `VaultDB.document_vectors()` + shared `unit_mean()` on both backends, the `find_near_duplicates` MCP tool, and test section 3.E. **No schema change** → rebuild contract untouched; full vault suite + knowledge regression green. D11 narrowed (persisted similarity edges still deferred). | `servers/vault/{db,db_sqlite,db_postgres,relations,server}.py`, `tests/check_vault_prune.py` |
| 2026-06-30 | 3.4, 3.6, 3.7, 3.8 | **Phase 3 batch 3 done — trustworthy archival.** TTL policy (`run_ttl`); the archive mechanism (`archive_documents`: move to `90-archive/` at the same sub-path, flip `status=archived`, one incremental reindex, search down-rank via a fixed penalty); reversible tombstones + generated `.vault/tombstones.md` + `restore` by note id or whole prune `batch`; dry-run on every entry point + `explain_prune`. Pinned protected by a HARD rule; archived status + tombstones survive `rebuild-index`. New tools: `prune_expired`, `restore_note`, `list_tombstones`, `explain_prune`, `recompute_scores`, `init_prune_config`. | `servers/vault/archive.py`, `servers/vault/{db,db_sqlite,db_postgres,server}.py`, `tests/check_vault_archive.py` |
| 2026-06-30 | 3.5 | **Decay archival + config.py hotfix.** `run_decay` archives notes that are BOTH low-value (`prune_score <= decay_score_threshold`) AND idle (`>= decay_min_idle_days`) — both gates required, pinned protected by the hard rule. Idle uses a factored-out shared `prune.days_since_touch` (later of `updated`/`last_access`); weights default in code (`0.0` / `90.0`) and tune in `.vault/prune.config`. Reuses `archive_documents`, so dry-run/tombstone/undo come free; `now` is injectable so the test ages the vault deterministically. MCP tool `prune_decayed`. Also fixed a blocking syntax error in `config.py` (a stray `learn` token before the module docstring) that had broken every vault import. Full vault suite (incl. rebuild contract) + knowledge regression green. | `servers/vault/{prune,archive,server,config}.py`, `tests/check_vault_archive.py`, `docs/vision/13-build-plan.md` |
| 2026-06-30 | 3.9, 3.10 | **Phase 3 complete — scheduler + local backup.** `backup.py`: git-commits the vault in place (init on first run, per-commit identity flags, no empty commit) and snapshots the derived DB to a sibling backup dir (`VAULT_BACKUP_DIR`) — SQLite online-backup API (tested), Postgres `pg_dump` (authored, D14). Security: `.gitignore` guard ensures `.vault/secrets.*` (+ rebuildable index/manifest, audit log) are never committed — asserted by the test. `scheduler.py`: `run_once()` = reindex → TTL → decay → backup → journal summary (prune dry-run unless `apply`/`prune_apply>0`), `loop()` for cron. CLI `scripts/vault_schedule.py` + `life-vault-schedule`; MCP tools `run_maintenance`, `backup_vault`. New self-check `tests/check_vault_scheduler.py` (batch 4, requires git). Full suite (8 checks) green. Deferred D14–D17. | `servers/vault/{backup,scheduler,config,prune,server}.py`, `scripts/vault_schedule.py`, `tests/check_vault_scheduler.py`, `pyproject.toml`, `docs/vision/13-build-plan.md` |
| 2026-06-30 | 4.2, 4.5 | **Phase 4 batch 1 — connector foundation + Calendar adapter.** `connectors/base.py::upsert_note` dedups by `source_ref` (new `VaultDB.find_document_by_source_ref` on both backends): re-sync updates the same note in place (stable id), unchanged re-sync is a no-op. Indexer now **derives `events` rows from note frontmatter** (`extract_events` → new `replace_events`/`list_events`), so calendar events are reconstructed from the vault on rebuild. `connectors/calendar.py` transforms Google event dicts → notes in `40-areas/calendar/` (`gcal://event/<id>`, timed + all-day, skips cancelled/no-start). New offline self-check (batch 4-connectors, fixture events) covers transform, dedup/update-in-place, events + rebuild survival. Full suite (9 checks) green. Live Google fetch/OAuth deferred to 4.1/4.6. Deferred D18–D19. | `servers/vault/db.py`, `servers/vault/{db_sqlite,db_postgres,index}.py`, `servers/vault/connectors/{__init__,base,calendar}.py`, `tests/check_vault_connectors.py`, `docs/vision/13-build-plan.md` |
| 2026-06-30 | 4.3 | **Gmail adapter (ephemeral).** `connectors/gmail.py`: `message_to_fields` (headers→subject/from/date + snippet/body_text, `gmail://msg/<id>`) + `sync_messages` (upsert via base with `expires=now+ttl_days` default 30d into `50-resources/mail/`, `label_filter` to ingest only wanted mail). Ephemerality reuses the Phase 3 TTL policy — the test proves an expired mail note is archived by `archive.run_ttl` with no new pruning code. Connectors self-check extended with a 4.3 section; full suite (9 checks) green. Live Gmail fetch/OAuth deferred to 4.1/4.6. | `servers/vault/connectors/gmail.py`, `tests/check_vault_connectors.py`, `docs/vision/13-build-plan.md` |
| 2026-07-02 | RAG usability | **Standard-RAG ease-of-use + readiness.** Added the read half of RAG: `get_note(ref)` (resolve by document id / vault path / source_ref; truncates huge bodies; records access) and `timeline(start,end,limit)` (events from frontmatter, ordered, window-filterable) in `search.py` + MCP tools. Search results now carry `status` (archived hits are recognizable — `VaultHit.status` on both backends). `category` search filter is now a **prefix** match (`40-areas` covers `40-areas/calendar`). `vault_status` adds a per-category breakdown so callers can discover categories. Re-ran `pip install -e .` to refresh stale entry points (rename left `study-*`; now `life-vault*`). New `tests/check_vault_usability.py`; full suite (10 checks) green; server = 25 tools. | `servers/vault/{search,server,db,db_sqlite,db_postgres}.py`, `tests/check_vault_usability.py` |
| 2026-07-02 | backlog | **Owner backlog captured (not implemented).** Recorded B1 keyboard/mouse automations, B2 budgeting tracker, B3 web app, B4 iOS access point as net-new future scope, plus the owner priority: standard RAG must work + be runnable first, then connectors/intelligence, then backlog. | `docs/vision/13-build-plan.md`, `docs/vision/11-roadmap.md` |
| 2026-07-02 | smart capture | **Smart capture (near-dup dedup) + note consolidation.** `capture` now detects a highly-similar existing note (whole-text embedding vs document centroids, reusing `document_vectors`) and acts per `on_similar`: `warn` (default — still creates, returns `similar_to`), `skip` (no duplicate; returns the match), `append` (consolidates into the match); `force_new` bypasses. New `append_to_note(ref, text)` write tool (resolves by id/path/source_ref) is the consolidation primitive; `search.resolve_document` made public and shared. Byte-identical content still refused as before. New `tests/check_vault_smart_capture.py`; full suite (11 checks) green; server = 26 tools. Default `warn` keeps behavior backward-compatible (never silently merges). Deferred D20 (threshold tuning). | `servers/vault/{capture,server,search}.py`, `tests/check_vault_smart_capture.py` |
| 2026-07-03 | plan | **Plan remade from prior art (docs only — no feature code).** Studied 5 repos (Mintable, YACS, AppointmentScheduler, datadam_mcp, AI-Second-Brain) + last turn's mature-DB research; wrote `docs/vision/14-prior-art.md` (what each borrows, adapted vault-first, mapped to steps). Promoted backlog B1–B4 into concrete phases and added new ones: **Phase 6** retrieval & matching quality (hybrid RRF, frequency-decay pruning, dedup precision, HNSW, rerank, delete-with-grace), **7** scheduling & reminders, **8** budgeting (Mintable), **9** web app (YACS api-client + StreamBrain UI), **10** iOS, **11** input automation, **12** structured records & multi-client memory (datadam). Each phase has steps + a test + an inspiration tag; added a "design principles carried into every future phase" note (vault-first, connectors-as-adapters, reversible, local-first, one door, offline self-check). Dashboard + priority + vision README index updated. | `docs/vision/14-prior-art.md`, `docs/vision/13-build-plan.md`, `docs/vision/README.md` |
| 2026-07-03 | 4.4, 4.6-core | **Connector cursors + incremental sync runner.** `connectors/cursors.py` = durable `.vault/cursors.json` per-source checkpoint (cursor + last_sync + count). `connectors/sync.py` = `run_sync` that separates FETCH (injected `fetch_fn`, mockable) from INGEST (tested calendar/gmail adapters), pages from the saved cursor until caught up, advances it, and is idempotent on full replay (source_ref dedup → no dupes); `CalendarConnector`/`GmailConnector` wrappers. Surfaces: `sync_status` MCP tool (27 total), `scripts/vault_sync.py` + `life-vault-sync` (`--status` live; service flags gated on 4.1). New `tests/check_vault_sync.py` (fake paging provider: first-sync-pages-all, caught-up, incremental pickup, idempotent full replay); full suite (12 checks) green. Only 4.1 (live Google OAuth/fetch) remains in Phase 4 — needs owner creds. | `servers/vault/connectors/{cursors,sync}.py`, `servers/vault/{config,server}.py`, `scripts/vault_sync.py`, `pyproject.toml`, `tests/check_vault_sync.py` |
| 2026-07-05 | 9.2 (skeleton), 4.2+ | **Web dashboard walking skeleton + GCal ALL-CAPS grouping.** `servers/vault/web.py` (Starlette, server-rendered plain HTML, read-only, localhost-only, unauth) reusing `get_db`/`search`/`timeline`/`get_note`: Home, Upcoming (events table), Search + Note viewer, Finances (finance-category notes), Build (renders the plan's status dashboard). CLI `scripts/vault_web.py` + `life-vault-web` (needs `serve` extra). Calendar adapter now parses an ALL-CAPS `PREFIX:` in event titles into a `group` field + lowercased tag (title kept intact) so Upcoming separates categories — honoring "Google Calendar with ALL-CAPS names" as the unified events **input** (no separate to-do DB; the `events` table already unifies dated items). New `tests/check_vault_web.py` (Starlette TestClient, offline) + full suite (13 checks) green. Decision recorded: server-rendered now, Next.js/shadcn (9.1/9.3–9.5) later. | `servers/vault/web.py`, `servers/vault/connectors/calendar.py`, `scripts/vault_web.py`, `pyproject.toml`, `tests/check_vault_web.py`, `docs/vision/13-build-plan.md` |
| 2026-07-05 | staging import | **Reusable staging importer + first real data ingested.** `servers/vault/staging.py::import_staging(area, root, dry_run)` turns `data/incoming/<area>/` drops into live vault notes: reads each content file and **upserts by its `source_ref`** via the connector base (stable id, update-in-place, no duplicates), landing it in the note's declared `category`; carries all frontmatter through; skips `README`/`_template`/`_digest`/dotfiles/non-md; a file missing `source_ref` is reported, never guessed. `import_staging` MCP tool (dry-run default) + `scripts/vault_import.py` + `life-vault-import`. New `tests/check_vault_staging.py` (create/skip/no-ref/events/search/idempotent-reimport/edit-in-place/dry-run) + full suite (14 checks) green. **Then ingested the real staged school data:** 30 notes across 6 courses (math225, phys234/242/260b/263/267) → 146 chunks, **17 events**; dashboard `/upcoming` now shows all 17 real deadlines and search returns real course content. | `servers/vault/staging.py`, `servers/vault/server.py`, `scripts/vault_import.py`, `pyproject.toml`, `tests/check_vault_staging.py` |
| 2026-07-05 | 6.1 | **Hybrid retrieval (BM25 + vector, RRF).** `servers/vault/lexical.py` = pure-Python BM25 (no FTS5 dep, no schema change → rebuild contract intact). New `VaultDB.lexical_search` (SQLite BM25 over candidate chunks; Postgres `tsvector`/`ts_rank`, D1). `search.py` runs vector + lexical retrievers over a wider pool and `_rrf_fuse`s them (RRF k=60; active ranked strictly above archived); `mode` switch (hybrid|vector|lexical) exposed on `search_vault` (now hybrid by default). Verified on real school data: "Griffiths quantum assignment" tops the right note; lexical mode nails exact token "prelab". New `tests/check_vault_hybrid.py`; full suite (15 checks incl. rebuild contract + ordering) green. | `servers/vault/{lexical,search,db,db_sqlite,db_postgres,server}.py`, `tests/check_vault_hybrid.py` |
