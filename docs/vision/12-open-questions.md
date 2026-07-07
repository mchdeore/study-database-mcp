# 12 — Decisions log & remaining questions

## Decided (2026-06-29)

| # | Decision | Choice |
|---|----------|--------|
| Q1 | Relational store | **PostgreSQL + pgvector**, behind a swappable interface |
| Q2 | Remote access | **Tailscale + bearer token** (no public exposure) |
| Q3 | Embeddings | **Local** (bge via sentence-transformers) — free, GPU is overkill for this |
| Q4 | Server-side LLM (categorize/compact) | **Cloud, budget-capped** (local embeddings + cloud LLM) |
| Q5 | Google services first | **Calendar + Gmail** (Gmail on a short TTL / ephemeral category) |
| Q7 | Connector mode | **Ingest to vault only** for now; live-tool proxy deferred |
| Q9 | Project identity | **Rename/fork to `life-vault-mcp`**; calculator stays a separate optional server |
| Q10 | Hardware | NVIDIA box, **~14GB VRAM total across 2 GPUs**. Plenty for embeddings (<2GB) and a local reranker; cloud handles the big LLM work |

### Hardware note
Two consumer GPUs totaling ~14GB. NVLink VRAM-pooling only works on cards that
support it and still requires model-parallel splitting — don't count on a single
14GB pool. It doesn't matter here: embeddings need <2GB and the heavy LLM work is
cloud (Q4), so the GPUs are free for local embeddings + an optional local
re-ranker. A ~7–8B local LLM fallback (Q4 can flip to local per-task) fits on a
single card if you ever want it.

## Decided (2026-06-29, round 2)

| # | Decision | Choice |
|---|----------|--------|
| Q6 | Calendar source | **Google Calendar** (reuses the Gmail Google OAuth — one login). Notion dropped from v1; swap to Notion calendar later if preferred. |
| Q8 | Taxonomy evolution | **Static folder taxonomy** + a cheap **periodic "regroup" batch job**. No per-note LLM cost on capture. |
| Q11 | Backups | **Local files on the server machine** (git vault snapshot + DB dump to a folder). See caveat below. |
| Q12 | Cloud LLM | **DeepSeek** (cheap) for server-side categorize/compact/regroup + a separate **OCR model** for image/scanned-PDF text. Budget-capped. Eventually also drives **agentic RAG** (Q14). |

### Backup caveat
Same-machine backup protects against accidental edits/deletes and DB corruption,
**not** against disk failure or theft of the box. Strongly recommend the backup
folder live on a **second physical disk** in the machine, and consider an
occasional copy to an external drive. Your call; v1 writes to a local folder.

## Decided

### Q13 — Repo name & migration — DONE (in place)
Renamed **in place**, keeping git history. In-repo identity updated:
`pyproject.toml` name + console scripts (`life-calculator`, `life-knowledge`,
`life-reindex`, `life-catalog`), README title, and a code docstring reference.
The on-disk folder rename, venv recreate, and GitHub remote rename are manual
steps (they break absolute paths if done mid-session) — see the project README /
the rename command list. The vision docs intentionally keep `study-database-mcp`
references as the tagged ancestor.

## Still open

### Q14 — Agentic RAG (later phase)
Should the cheap server-side LLM (DeepSeek) act as a **retrieval agent** — doing
multi-step search + reading chunks server-side and returning a distilled, cited
"context pack" — so your expensive main models spend far fewer tokens? *Default:*
yes, as a phase-5 lever once basic search is solid. Cost-capped like all paid LLM
use.
