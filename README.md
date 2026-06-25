# Study Database MCP

Two independent [MCP](https://modelcontextprotocol.io) servers that make Claude a
reliable study partner:

- **`calculator`** — deterministic math (numeric, symbolic, units, constants,
  matrices) so Claude never hand-computes. Exact by default.
- **`knowledge`** — hybrid retrieval over your course notes + textbooks. Vector
  search is the default path; a lightweight concept graph handles cross-cutting
  synthesis questions. Every answer carries citations.

The two servers stay separate: different dependencies, different failure modes,
independently restartable.

## Layout

```
servers/calculator/server.py   calc_numeric / calc_symbolic / calc_matrix / calc_units / constants
servers/knowledge/             chunk · store · ingest · retrieve · graph · server
scripts/reindex.py             CLI: full or incremental reindex
data/raw/                      <- drop your PDFs and .md notes here
data/corpus/                   normalized Markdown (review/fix equations here)
data/vector_store/  data/graph/  data/manifest.json   (generated)
tests/                         offline self-checks (no API key needed)
```

## Setup

Requires Python 3.10+ (built/tested on 3.12).

```bash
python3.12 -m venv .venv
source .venv/bin/activate

# Calculator only:
pip install -e ".[calculator]"

# Knowledge (core, fully offline with the numpy store + hash embedder):
pip install -e ".[knowledge]"

# Optional knowledge backends (pick to match your .env):
pip install -e ".[knowledge,embeddings-local,pdf-pymupdf]"   # local embeddings + PDF support
pip install -e ".[embeddings-openai]"                         # OpenAI embeddings
pip install -e ".[store-lancedb]"                             # scalable vector store
```

Copy `.env.example` to `.env` and adjust. Defaults run entirely offline
(`VECTOR_STORE=numpy`, hash-embedding fallback) — good for trying things out and
for the tests, but switch `EMBEDDING_PROVIDER` to `local` or `openai` for real
retrieval quality.

## Calculator

| Tool | What it does |
|------|--------------|
| `calc_numeric(expr, precision=15)` | Arbitrary-precision numeric eval (mpmath via sympy). No `eval()`. |
| `calc_symbolic(expr, op, …)` | `op` ∈ differentiate, integrate (indefinite **or** definite via `lower`/`upper`), simplify, solve, factor, expand, limit, series, laplace, inverse_laplace, fourier. Returns plain **and** LaTeX. |
| `calc_ode(eq, mode, …)` | Solve ODEs. `mode="symbolic"` → exact closed form (sympy `dsolve`, with optional initial conditions); `mode="numeric"` → initial-value problem & first-order systems via scipy `solve_ivp` (adaptive RK45/Radau/…). |
| `calc_matrix(op, A, B?, numeric, steps)` | add, subtract, multiply, transpose, det, inverse, rank, rref, eigenvals, eigenvects, solve `Ax=b`. Exact by default; `steps=true` shows worked solutions. |
| `calc_vector_calculus(op, field, vars)` | gradient, divergence, curl, laplacian in Cartesian coordinates. |
| `calc_units(expr, to?)` | Unit-aware arithmetic + conversion (pint). Also accepts `"60 mph to km/h"`. |
| `constants(name)` | Physics constants with units (scipy CODATA): `c`, `h`, `k_B`, `G`, `N_A`, … |
| `propagate_uncertainty(expr, values, uncertainties)` | Gaussian error propagation through a formula (∂f/∂xᵢ · σᵢ in quadrature) — for physics labs. |

Plus `stats_summary`, `linear_regression`, `confidence_interval`. Every tool
returns a structured result and a clear `{"error": …}` string on bad input —
never a silently wrong number.

Compute-heavy tools run under a wall-clock guard (`CALC_TIMEOUT`, default 12s) so
a hard symbolic integral or `dsolve` returns a clean "timed out, try numeric"
error instead of hanging the session.

## Knowledge

Pipeline: `raw/ → corpus/ (Markdown) → chunks → embeddings → vector_store (+ graph)`.

1. Drop PDFs / `.md` notes in `data/raw/`.
2. `python scripts/reindex.py` converts them to Markdown in `data/corpus/`.
   **Review and hand-fix any garbled equations there**, then run reindex again to
   embed the corrected Markdown.
3. Indexing is **incremental** — only files whose content hash changed are
   re-processed (tracked in `data/manifest.json`).

Tools: `list_sources`, `search_notes` (default vector lookup), `get_section`
(verbatim by heading), `synthesize` (explicit cross-topic path), `related_concepts`,
`reindex`.

Chunking is structure-aware: it splits on heading boundaries and **never** splits
a LaTeX block (`$$…$$`, `\[…\]`, `align`, …) or a fenced code block, so
derivations stay intact.

The full Microsoft GraphRAG layer is **off by default** (`ENABLE_GRAPHRAG=false`).
The light concept graph handles synthesis at near-zero cost; only enable GraphRAG
per-subject if cross-cutting questions prove frequent (it adds per-chunk LLM calls
at index time and per-query token cost).

## Register with Claude Desktop

See `claude_desktop_config.example.json`. Use the venv's Python and absolute
paths, e.g.:

```json
{
  "mcpServers": {
    "calculator": { "command": "/abs/path/.venv/bin/python", "args": ["/abs/path/servers/calculator/server.py", "--stdio"] },
    "knowledge":  { "command": "/abs/path/.venv/bin/python", "args": ["/abs/path/servers/knowledge/server.py", "--stdio"] }
  }
}
```

## Tests

```bash
# Offline (no extra deps, no network) — always runnable:
.venv/bin/python tests/check_calculator.py     # capability table + clean errors
.venv/bin/python tests/check_knowledge.py      # chunker, incremental, citations, graph
.venv/bin/python tests/test_edge_cases.py      # tool boundaries, error-quality, timeouts, timing
.venv/bin/python tests/test_knowledge_edges.py # chunker corners, reindex, persistence, cross-refs
.venv/bin/python tests/test_perf.py            # scale: ingest/search/graph timing on a synthetic corpus

# Require optional backends (install the matching extras first):
.venv/bin/python tests/test_backends.py        # NumpyStore / LanceStore / ChromaStore same contract
.venv/bin/python tests/test_pdf.py             # PDF -> Markdown -> chunks (pymupdf4llm)
```

`test_edge_cases.py` (105 assertions) pushes every tool to its boundaries
(arbitrary precision, singular matrices, improper integrals, complex roots,
offset/temperature units, ODE systems, empty/jagged inputs), asserts that
failures return a clear `error` plus an actionable `hint`, that outputs carry
useful depth (exact+decimal, latex, shape, solutions/roots, citations), that the
wall-clock timeout guard fires cleanly, and that the tools stay interactive-fast.
`test_backends.py` runs the identical store contract against all three vector
stores; `test_perf.py` indexes 80 notes / ~560 chunks and checks search (<1 ms),
graph build, and incremental reindex stay fast.
