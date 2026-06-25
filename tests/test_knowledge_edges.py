"""Edge-case suite for the knowledge pipeline (offline).

Run: python tests/test_knowledge_edges.py

Pushes the chunker, incremental ingest, store persistence, and concept graph to
their corners with the offline hash embedder + numpy store (no network).
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="study_kb_edge_")
os.environ["DATA_DIR"] = _TMP
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VECTOR_STORE"] = "numpy"
os.environ["ENABLE_GRAPHRAG"] = "false"

from servers.knowledge.chunk import chunk_markdown  # noqa: E402
from servers.knowledge import ingest as ING  # noqa: E402
from servers.knowledge import retrieve as RET  # noqa: E402
from servers.knowledge import graph as GR  # noqa: E402
from servers.knowledge.store import NumpyStore, paths  # noqa: E402

_N = 0


def ok(cond, msg):
    global _N
    _N += 1
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def balanced(text, token):
    return text.count(token) % 2 == 0


# ===========================================================================
print("[chunker corners]")
# No headings at all -> chunks with empty heading_path, content preserved.
c = chunk_markdown("Just prose with no headings.\n\nSecond paragraph.", "plain.md")
ok(len(c) >= 1 and c[0].heading_path == "", "no-heading doc -> empty heading_path")
ok("Second paragraph" in " ".join(x.text for x in c), "no-heading content preserved")

# Empty / whitespace-only docs -> no chunks.
ok(chunk_markdown("", "e.md") == [], "empty doc -> no chunks")
ok(chunk_markdown("   \n\n  \t\n", "w.md") == [], "whitespace-only doc -> no chunks")

# \[ ... \] display math kept whole even with small target.
disp = r"""# H

text

\[
\int_0^1 x^2 dx = \frac{1}{3}
\]

more text
"""
cc = chunk_markdown(disp, "d.md", target_tokens=8)
for ch in cc:
    if "\\[" in ch.text or "\\]" in ch.text:
        ok("\\[" in ch.text and "\\]" in ch.text, "\\[..\\] math block kept whole")

# ~~~ fenced code kept whole.
tilde = "# H\n\n~~~python\nx = 1\n\ny = 2\n~~~\n\nafter\n"
ct = chunk_markdown(tilde, "t.md", target_tokens=5)
for ch in ct:
    if "~~~" in ch.text:
        ok(ch.text.count("~~~") % 2 == 0, "~~~ code fence kept whole")

# A heading-like line INSIDE a code fence must NOT become a heading.
codehdr = "# Real Heading\n\n```\n## not a heading\ncode line\n```\n"
ch2 = chunk_markdown(codehdr, "c2.md")
ok(all("not a heading" not in c.heading_path for c in ch2), "## inside code fence is not parsed as heading")

# Oversized single equation > target -> its own (allowed) big chunk, not split.
big_eq = "# H\n\n$$\n" + " + ".join(f"x_{i}" for i in range(200)) + "\n$$\n"
cb = chunk_markdown(big_eq, "b.md", target_tokens=5)
eqchunks = [c for c in cb if "$$" in c.text]
ok(len(eqchunks) == 1 and balanced(eqchunks[0].text, "$$"), "oversized equation -> single intact chunk")

# Single-line $$...$$
sl = "# H\n\nInline display $$E=mc^2$$ done.\n"
csl = chunk_markdown(sl, "sl.md")
ok(all(balanced(c.text, "$$") for c in csl), "single-line $$..$$ balanced")

# Unicode preserved.
uni = "# Ångström\n\nWave function ψ and energy ΔE ≈ ℏω.\n"
cu = chunk_markdown(uni, "u.md")
ok("ψ" in cu[0].text and "Ångström" in cu[0].heading_path, "unicode content + heading preserved")

# Trailing-space heading + closed ATX heading.
trail = "#  Spaced Heading  \n\nbody\n"
ctr = chunk_markdown(trail, "tr.md")
ok("Spaced Heading" in ctr[0].heading_path, "heading whitespace trimmed")

# Deep nesting heading_path.
deep = "# A\n\n## B\n\n### C\n\nleaf text\n"
cd = chunk_markdown(deep, "deep.md")
ok(any(c.heading_path == "A > B > C" for c in cd), "deep heading_path A > B > C")

# ===========================================================================
print("\n[incremental / persistence]")
raw = paths()["raw"]
raw.mkdir(parents=True, exist_ok=True)
(raw / "a.md").write_text("# A\n\n## Topic A\n\nalpha content about gravity.\n", encoding="utf-8")
(raw / "b.md").write_text("# B\n\n## Topic B\n\nbeta content about gravity and Topic A.\n", encoding="utf-8")
r1 = ING.ingest(incremental=True)
ok(sorted(r1["indexed"]) == ["a.md", "b.md"], "initial index both")

# Full reindex (incremental=False) re-processes everything.
rfull = ING.ingest(incremental=False)
ok(sorted(rfull["indexed"]) == ["a.md", "b.md"], "full reindex re-processes all")

# Store persistence roundtrip: a fresh store instance sees the same data.
s2 = NumpyStore(paths()["vector_store"])
ok(set(s2.sources()) == {"a.md", "b.md"} and len(s2.all_records()) >= 2, "numpy store persists across instances")

# Delete a CORPUS file -> source removed on next reindex.
(paths()["corpus"] / "b.md").unlink()
rdel = ING.ingest(incremental=True)
ok("b.md" in rdel["removed"], "deleting corpus file removes its source")
ok({s["source"] for s in RET.list_sources()} == {"a.md"}, "only a.md remains after deletion")

# Deleting a RAW file leaves the canonical corpus intact (by design).
(raw / "a.md").unlink()
rrm = ING.ingest(incremental=True)
ok("a.md" not in rrm["removed"], "deleting raw leaves canonical corpus indexed (by design)")

# ===========================================================================
print("\n[graph cross-references]")
# Rebuild a clean corpus where B explicitly mentions A's concept.
for f in paths()["corpus"].glob("*.md"):
    f.unlink()
for f in raw.glob("*.md"):
    f.unlink()
(raw / "mech.md").write_text(
    "# Mechanics\n\n## Angular Momentum\n\nDefined as L = r x p.\n", encoding="utf-8")
(raw / "astro.md").write_text(
    "# Astrophysics\n\n## Orbits\n\nKepler's laws follow from Angular Momentum conservation.\n",
    encoding="utf-8")
ING.ingest(incremental=False)
stats = GR.build_graph()
ok(stats["cross_references"] >= 1, f"cross-reference edge detected ({stats['cross_references']})")
rc = GR.related_concepts("Angular Momentum")
ok("concept" in rc, "related_concepts finds the concept")
ok(any("Orbits" in t or "Astrophysics" in t for t in rc.get("referenced_by_topics", [])),
   f"cross-topic reference surfaced: {rc.get('referenced_by_topics')}")
syn = GR.synthesize("how does angular momentum relate to orbits")
ok(syn["mode"] == "synthesis" and len(syn.get("citations", [])) >= 1, "synthesis gathers cross-topic context")

print(f"\nALL KNOWLEDGE EDGE CHECKS PASSED ({_N} assertions)")
