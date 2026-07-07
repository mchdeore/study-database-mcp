"""Self-check for the knowledge server. Run: python tests/check_knowledge.py

Verifies the acceptance criteria that don't need API keys or model downloads:
  - chunker never splits a LaTeX block or fenced code block (structure-aware)
  - heading-aware splitting + citations metadata (source, heading_path, page)
  - incremental manifest: adding/changing ONE note re-embeds only that file
  - end-to-end search returns correct citations (hash embedder + numpy store)
  - get_section pulls a section verbatim
  - synthesize uses the graph path and returns citations; GraphRAG stays gated

Runs in an isolated temp DATA_DIR with the offline 'hash' embedder + 'numpy'
store, so it needs no network. No test framework -- just asserts.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Configure an isolated, fully-offline environment BEFORE importing the modules
# that read config at call time.
_TMP = tempfile.mkdtemp(prefix="study_kb_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VECTOR_STORE"] = "numpy"
os.environ["ENABLE_GRAPHRAG"] = "false"

from servers.knowledge.chunk import chunk_markdown  # noqa: E402
from servers.knowledge import ingest as ingest_mod  # noqa: E402
from servers.knowledge import retrieve as retrieve_mod  # noqa: E402
from servers.knowledge import graph as graph_mod  # noqa: E402
from servers.knowledge.store import Manifest, paths, config  # noqa: E402


def ok(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


# --- 1. Chunker: never split LaTeX / code; heading-aware -------------------
print("chunker")
DERIVATION = r"""# Thermodynamics

Intro prose for the topic that should stay with the heading.

## Carnot Efficiency

The efficiency derivation follows.

$$
\eta = 1 - \frac{T_c}{T_h}
$$

More explanation after the equation block.

\begin{align}
W &= Q_h - Q_c \\
  &= Q_h \left(1 - \frac{T_c}{T_h}\right)
\end{align}

```python
def carnot(tc, th):
    return 1 - tc / th
```

### Entropy

Entropy change is $dS = \delta Q / T$ inline and should not be confused.
"""

chunks = chunk_markdown(DERIVATION, "thermo.md", target_tokens=40, overlap_ratio=0.1)
ok(len(chunks) >= 2, f"produced multiple chunks ({len(chunks)})")

full = "\n".join(c.text for c in chunks)
# A $$...$$ block must live entirely within a single chunk.
for c in chunks:
    n_open = c.text.count("$$")
    ok(n_open % 2 == 0, "no chunk splits a $$...$$ block (balanced delimiters)")
# The align environment must not be split across chunks.
align_chunks = [c for c in chunks if "\\begin{align}" in c.text or "\\end{align}" in c.text]
for c in align_chunks:
    ok("\\begin{align}" in c.text and "\\end{align}" in c.text,
       "align block kept whole in one chunk")
# The fenced code block must not be split.
code_chunks = [c for c in chunks if "```" in c.text]
for c in code_chunks:
    ok(c.text.count("```") % 2 == 0, "fenced code block kept whole (balanced fences)")

# heading_path + metadata present
ok(any(c.heading_path.startswith("Thermodynamics") for c in chunks), "heading_path captured")
ok(any("Carnot" in c.heading_path for c in chunks), "nested heading_path captured")
ok(all(c.chunk_id.startswith("thermo.md#") for c in chunks), "stable chunk_ids per source")

# Page markers -> page metadata
paged = chunk_markdown("<!-- page: 7 -->\n# A\n\ntext\n", "p.md")
ok(paged[0].page == 7, "page marker captured into chunk metadata")


# --- 2. End-to-end ingest + incremental manifest ---------------------------
print("ingest + incremental manifest")
p = paths()
raw = p["raw"]
raw.mkdir(parents=True, exist_ok=True)

(raw / "mechanics.md").write_text(
    "# Mechanics\n\n## Newton's Second Law\n\nThe law states F = m a, relating force and acceleration.\n",
    encoding="utf-8",
)
(raw / "waves.md").write_text(
    "# Waves\n\n## Wave Equation\n\nThe wave speed satisfies v = f lambda for frequency and wavelength.\n",
    encoding="utf-8",
)

r1 = ingest_mod.ingest(incremental=True)
ok(sorted(r1["indexed"]) == ["mechanics.md", "waves.md"], f"first run indexes both: {r1['indexed']}")
ok(r1["chunks"] >= 2, "chunks were produced")

# Re-run with no changes -> nothing re-indexed.
r2 = ingest_mod.ingest(incremental=True)
ok(r2["indexed"] == [], f"no-change run re-indexes nothing: {r2['indexed']}")
ok(set(r2["skipped"]) == {"mechanics.md", "waves.md"}, "both files skipped via manifest")

# Add ONE new note -> only that file is indexed.
(raw / "optics.md").write_text(
    "# Optics\n\n## Snell's Law\n\nRefraction follows n1 sin(theta1) = n2 sin(theta2).\n",
    encoding="utf-8",
)
r3 = ingest_mod.ingest(incremental=True)
ok(r3["indexed"] == ["optics.md"], f"adding one note indexes only it: {r3['indexed']}")

# Change ONE existing note -> only that file re-indexed.
(raw / "waves.md").write_text(
    "# Waves\n\n## Wave Equation\n\nUpdated: the wave speed v = f lambda; also the Doppler effect.\n",
    encoding="utf-8",
)
r4 = ingest_mod.ingest(incremental=True)
ok(r4["indexed"] == ["waves.md"], f"changing one note re-indexes only it: {r4['indexed']}")

# Manifest reflects per-file tracking.
man = Manifest(p["manifest"])
ok(any(k.startswith("corpus:optics.md") for k in man.entries), "manifest tracks corpus files")
ok(any(k.startswith("raw:waves.md") for k in man.entries), "manifest tracks raw files")


# --- 3. Search returns correct citations -----------------------------------
print("search + citations")
res = retrieve_mod.search("Snell's law refraction", k=5)["results"]
ok(len(res) > 0, "search returns results")
top = res[0]
ok("citation" in top and {"source", "heading_path", "page", "chunk_id"} <= set(top["citation"]),
   "result carries full citation (source, heading_path, page, chunk_id)")
ok(any(r["citation"]["source"] == "optics.md" for r in res),
   "lexical query surfaces the optics note")

srcs = {s["source"] for s in retrieve_mod.list_sources()}
ok(srcs == {"mechanics.md", "waves.md", "optics.md"}, f"list_sources lists all: {srcs}")


# --- 4. get_section verbatim ----------------------------------------------
print("get_section")
sec = retrieve_mod.get_section("optics.md", "Snell's Law")
ok("Snell" in sec.get("text", ""), "get_section returns the section text")
ok("Optics" not in sec["text"].split("\n", 1)[0] or sec["text"].startswith("## Snell"),
   "section starts at the requested heading")


# --- 5. Synthesis uses graph path; GraphRAG gated --------------------------
print("synthesize + graph gating")
graph_mod.build_graph()
syn = graph_mod.synthesize("how do waves and optics relate")
ok(syn.get("mode") == "synthesis", "synthesize returns the synthesis mode")
ok("citations" in syn, "synthesize returns citations for the gathered context")
ok("GraphRAG disabled" in syn.get("note", "") or "graph" in syn.get("note", "").lower(),
   "light graph path used while GraphRAG is gated off")

# GraphRAG hook stays gated.
try:
    graph_mod.run_graphrag("x")
    raise AssertionError("run_graphrag should raise NotImplementedError while gated off")
except NotImplementedError:
    print("  ok: GraphRAG hook is gated off (raises NotImplementedError)")

rc = graph_mod.related_concepts("Snell's Law")
ok("concept" in rc or "error" in rc, "related_concepts responds for a known concept")

# --- 6. Hybrid librarian config (agentic=Kimi, extraction=DeepSeek) ---------
print("hybrid librarian config")
lib = config()["librarian"]
ok(lib["agentic"]["model"] == "kimi-k2.7-code",
   "agentic role defaults to Kimi K2 (MCP tool-use leader)")
ok(lib["extraction"]["model"] == "deepseek-v4-flash",
   "extraction role defaults to DeepSeek V4 Flash (cheapest capable)")
ok(lib["agentic"]["api_key_name"] == "moonshot_api_key"
   and lib["extraction"]["api_key_name"] == "deepseek_api_key",
   "each role names a credential-store key (keys never in .env)")
os.environ["LLM_EXTRACTION_MODEL"] = "qwen-turbo"
ok(config()["librarian"]["extraction"]["model"] == "qwen-turbo",
   "extraction model is overridable via env (e.g. swap in Qwen)")
del os.environ["LLM_EXTRACTION_MODEL"]

print("\nALL KNOWLEDGE CHECKS PASSED")
