# 10 — Cost & budget guardrails

Principle: **local-first, so steady-state cost is ~$0.** Cloud APIs are opt-in
levers, each with a known price and a cap. Numbers below are order-of-magnitude
planning figures (verify current pricing before relying on them).

## Where money can be spent

| Lever | Local (default) | Cloud option | Rough cloud cost |
|-------|-----------------|--------------|------------------|
| **Embeddings** | `bge-small` via sentence-transformers, free | OpenAI `text-embedding-3-small` | ~$0.02 / 1M tokens |
| **LLM** (categorize, summarize/compact, synthesize) | Ollama (Llama/Qwen) on your box, free | API model | varies by model |
| **OCR / image** (later) | local OCR, free | vision API | per-image |
| **Re-ranker** (optional) | local cross-encoder, free | hosted | small |

## Sizing the embedding cost (one-time + incremental)

- Say 100,000 notes averaging 500 tokens = **50M tokens**.
- Initial embed with `text-embedding-3-small`: 50M × $0.02/1M ≈ **$1 one-time**.
- Re-embedding is **incremental** (only changed files, via the content-hash
  manifest), so ongoing embedding spend is negligible.
- Conclusion: embeddings are cheap enough to use the cloud, but **local is free
  and you have the hardware**, so default to local; flip to OpenAI only if you
  measure a real quality gap.

## The real cost driver: LLM calls

- **Search/synthesis** spends tokens in *your AI client*, not the server (the
  client is whatever you connect — you control that model/budget).
- **Server-side LLM use** = optional categorization and **compaction/summarization**
  pruning. These are batch jobs over many notes and are the only thing that can
  quietly add up. Therefore:
  - **DECISION:** server-side LLM features default to a **local** model (Ollama),
    cost $0. If you opt into a cloud model, they run under a budget.

## Budget guardrails (DECISION)

- A configurable **monthly spend cap** in `.vault/budget.config.yaml`. The server
  tracks estimated spend per provider and **refuses paid calls past the cap**,
  returning a clear error telling you to raise the cap or switch to local.
- Every paid call is logged with an estimated cost so monthly spend is auditable.
- Batch jobs (compaction, bulk re-embed) print a **cost estimate and ask for
  confirmation** before running when using a paid provider.

## Chosen posture (decided)

- **Embeddings: local** (bge), free. Search: free.
- **Server-side LLM: a hybrid "librarian",** budget-capped. Split by role so the
  reliability premium is paid only where it counts:
  - **Extraction (bulk, cost-first): DeepSeek V4 Flash** ($0.14/$0.28 per 1M,
    cache-hit $0.0028). Does the high-volume, mechanical per-chunk entity/relation
    extraction — the cost that scales with corpus size.
  - **Agentic (orchestration, reliability-first): Kimi K2** ($0.95/$4.00, cache-hit
    $0.19). Drives the MCP tools, dedup/regroup decisions, and query-time
    context-packing. It leads MCP tool-use benchmarks; the token volume here is
    low, so the premium is cheap in absolute terms.
  - Sizing on the current catalog (67 docs, 5,881 pages, ~5,200 chunks): a **one-time**
    full GraphRAG index costs **~$3–5** hybrid (vs ~$2–4 all-DeepSeek, or ~$20–30
    all-Kimi), then **~$1–3/month** incremental. Trivial, and capped regardless.
  - *Caveat:* Kimi K2 is a ~1T-param model — **not** self-hostable on the ~14GB GPU,
    so the agentic role is API-only (no free local fallback). DeepSeek/Qwen remain
    the local-friendly options if that ever matters.
- **OCR: a separate OCR/vision model** for scanned PDFs and images (DeepSeek's
  text API doesn't do OCR). Options: local OCR (Tesseract / PaddleOCR / docTR) on
  your spare GPU = free; or a cheap hosted vision-OCR model when quality matters.
  *Default:* local OCR first (free), hosted only for hard scans.
- GPUs (~14GB total) handle local embeddings + local OCR + an optional local
  re-ranker, all free. Cloud spend is only the DeepSeek batch/agentic jobs.

Net: near-$0/month steady state. The only spend is the capped librarian calls for
batch tidying and (later) agentic context-packing — bounded, auditable, never by
surprise. Agentic RAG is designed to *reduce* total spend across your main models
by shrinking their input tokens.
