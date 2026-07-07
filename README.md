# Life Vault MCP

> Formerly **Study Database MCP**. Being evolved into a self-hosted personal
> knowledge system — see [`docs/vision/`](docs/vision/README.md) for the vision,
> scope, and architecture. The study-focused docs below describe the current,
> working servers that the larger system builds on.

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

## Catalog (your SCHOOL folder)

A lightweight index of everything in your `SCHOOL/` folder (organized by course
code), so Claude can answer "what do I have for MATH225" and locate the exact
slide deck / past exam / formula sheet during a cram session.

It's a single **SQLite** file at `data/catalog.db` — no server, no Docker. For a
few hundred files on one laptop, `sqlite3` (stdlib) is faster to query and zero
ops; Postgres-in-a-container would add overhead with no benefit at this scale.
It's still SQL — open `data/catalog.db` in any SQL tool.

Point it at your folder (defaults to `~/Documents/SCHOOL`):

```bash
# .env
SCHOOL_DIR=~/Documents/SCHOOL
CATALOG_DOC_EXTS=.pdf,.docx,.pptx,.md,.markdown   # what counts as a "document"
```

```bash
python scripts/catalog.py                 # incremental scan (default)
python scripts/catalog.py --full          # re-hash + re-derive names/types
python scripts/catalog.py --stats         # totals by course and type
python scripts/catalog.py --list MATH225  # list a course's documents
python scripts/catalog.py --duplicates           # byte-identical copies
python scripts/catalog.py --possible-duplicates  # same title, different bytes
python scripts/catalog.py --plan-renames         # preview descriptive renames
python scripts/catalog.py --apply-renames        # rename files (reversible)
python scripts/catalog.py --undo-renames         # revert the last rename batch
```

What a scan does, incrementally and safe to re-run:

- **One entry per unique document.** Identity is the SHA-256 of the file's bytes.
  A `(size, mtime)` gate skips unchanged known files without re-hashing.
- **Dedup.** A byte-identical copy elsewhere is recorded as a *duplicate* of the
  one canonical entry (never a second row). `--possible-duplicates` also flags
  same-title files with different bytes (e.g. two scans of the same textbook) for
  you to judge — it never auto-deletes. `--delete-duplicate-files --yes` removes
  the redundant copies (keeps the canonical) only when you explicitly ask.
- **Descriptive names.** Prefers a clean filename (`4.3 Bases and Dimension`),
  falls back to the PDF's embedded title or first page for cryptic names
  (`griffiths_4ed.pdf` → `Introduction to Electrodynamics`). A heuristic tags each
  doc: textbook / slides / exam / solutions / formula-sheet / manual / assignment /
  notes. Stored on every entry; **renaming files on disk is opt-in and reversible**
  (`--apply-renames` writes a log; `--undo-renames` reverts).
- **Skips noise.** Virtualenvs, `.git`, `__pycache__`, hidden files, and
  non-document types are ignored, so a course folder full of code/data doesn't
  pollute the catalog.

Query tools (exposed by the knowledge MCP server, for use mid-session):
`list_courses`, `find_documents(query, course="")`, `catalog_stats`.

`tests/check_catalog.py` is the offline self-check (no deps/network): exclusion
rules, dedup, descriptive naming/typing, incremental rescan, edit-in-place, the
queries, and a rename apply/undo round-trip.

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

## Using with Cowork

[Claude Cowork](https://claude.com/product/claude-cowork) is the desktop agent
that works directly on your local files — ideal for "build me a study guide from
my MATH225 slides" while you step away. Cowork shares Claude Desktop's config, so
the same two local servers work; the **catalog** then gives the agent a fast map
of your SCHOOL folder instead of crawling it every time.

**1. Connect the servers.** Merge `cowork_config.example.json` into your Desktop
config (Settings → Developer → Edit Config). It's the same `mcpServers` block plus
Cowork switches (`coworkScheduledTasksEnabled`, `coworkWebSearchEnabled`).

**2. Install the Skills.** The `skills/` folder holds two
[Agent Skills](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)
(portable `SKILL.md` folders that auto-load when relevant):

- **`exact-math`** — routes *every* calculation through the `calculator` server,
  so worked examples in your study guides are exact, not hand-computed guesses.
- **`study-assistant`** — orchestrates the catalog + knowledge + calculator tools
  into cram deliverables (study guide, practice exam, organize-the-folder).

Add them via **Customize → Skills** (zip each skill's folder and upload), or drop
them in your Claude Code skills directory. They follow the open Agent Skills
standard, so they're portable across Cowork and Claude Code.

**3. Build the catalog once** (`python scripts/catalog.py`) so `find_documents`
and friends have data, then just ask:

```
"What do I have for PHYS234, and which are past exams?"
"Build a study guide for the MATH225 midterm from my slides — verify every
 worked example with the calculator and cite the source slide."
"Make a practice exam from my PHYS234 past tests, with a checked answer key."
"Find duplicate and near-duplicate files in SCHOOL and propose descriptive
 renames (don't apply them yet)."
```

With scheduled tasks on, you can also have Cowork re-scan SCHOOL and reindex new
notes weekly and report what changed. Everything runs locally by default; switching
on the OpenAI embedder or a VLM PDF converter sends document content to a cloud API.

## Tests

```bash
# Offline (no extra deps, no network) — always runnable:
.venv/bin/python tests/check_calculator.py     # capability table + clean errors
.venv/bin/python tests/check_knowledge.py      # chunker, incremental, citations, graph
.venv/bin/python tests/test_edge_cases.py      # tool boundaries, error-quality, timeouts, timing
.venv/bin/python tests/test_knowledge_edges.py # chunker corners, reindex, persistence, cross-refs
.venv/bin/python tests/check_catalog.py        # SCHOOL catalog: dedup, naming, rescan, rename undo
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
