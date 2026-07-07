"""Knowledge MCP server: hybrid retrieval over course notes + textbooks.

Vector search is the default path (search_notes / get_section / list_sources).
The concept graph is reached only via synthesize / related_concepts -- never
auto-escalated from a lookup. Every retrieval result carries citations.

Tools:
  list_sources()                       list indexed notes/textbooks
  search_notes(query, k=8)             default vector lookup w/ citations
  get_section(source, heading_path)    pull a full section verbatim
  synthesize(query)                    explicit cross-topic synthesis (slow path)
  related_concepts(concept)            concept-graph neighbors
  reindex(incremental=true)            run ingestion; incremental by default

  list_courses()                       courses in the SCHOOL catalog + counts
  find_documents(query, course="")     locate a school file (cram sessions)
  catalog_stats()                      catalog overview (totals, dups, missing)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Work both as a package module (`python -m servers.knowledge.server`) and as a
# direct script (`python servers/knowledge/server.py --stdio`, how Claude Desktop
# launches it). Direct execution has no package context, so relative imports
# would fail; fall back to absolute imports with the repo root on sys.path.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from servers.knowledge import catalog as catalog_mod
    from servers.knowledge import graph as graph_mod
    from servers.knowledge import retrieve as retrieve_mod
    from servers.knowledge.ingest import ingest
else:
    from . import catalog as catalog_mod
    from . import graph as graph_mod
    from . import retrieve as retrieve_mod
    from .ingest import ingest

app = FastMCP(
    "Knowledge",
    dependencies=["numpy", "networkx", "python-dotenv"],
)


@app.tool()
def list_sources() -> dict:
    """List indexed notes/textbooks with their chunk counts.

    Returns {"sources": [{"source": str, "chunks": int}, ...]}.
    """
    return {"sources": retrieve_mod.list_sources()}


@app.tool()
def search_notes(query: str, k: int = 8) -> dict:
    """Default local lookup: vector top-k search over the notes.

    Use this for "what's the formula", "show me a worked example", "what did the
    notes say about X". Fast, cheap, reliable. Each result includes a citation
    (source, heading_path, page).

    Returns {"results": [{"text", "score", "citation"}, ...], "k": int}, or
    {"error", "hint"} on bad input.
    """
    return retrieve_mod.search(query, k)


@app.tool()
def get_section(source: str, heading_path: str) -> dict:
    """Pull a full section verbatim by source + heading_path.

    heading_path may be a full path ("Topic > Subtopic") or just the section
    title; the deepest heading is matched. Returns the section text exactly as
    written in the canonical Markdown corpus.
    """
    return retrieve_mod.get_section(source, heading_path)


@app.tool()
def synthesize(query: str) -> dict:
    """Cross-topic synthesis -- the EXPLICIT slow/global path.

    Gathers related chunks across multiple topics via the concept graph and
    returns the assembled multi-topic context with citations for you to
    synthesize an answer from. Use only for genuinely cross-cutting questions
    ("how do X and Y relate", "summarize everything about Z across the course").
    For a simple lookup use search_notes instead.
    """
    return graph_mod.synthesize(query)


@app.tool()
def related_concepts(concept: str) -> dict:
    """Concept-graph neighbors of a concept (parents, sub-concepts, cross-links).

    For "how does X connect to Y" navigation. Returns the matched concept node,
    its parent topic, sub-concepts, direct chunk count, and the topics whose
    notes reference it.
    """
    return graph_mod.related_concepts(concept)


@app.tool()
def reindex(incremental: bool = True) -> dict:
    """Trigger ingestion (raw -> markdown -> chunks -> embeddings + graph).

    Incremental by default: only files whose content hash changed are
    re-processed. Pass incremental=false to force a full rebuild.

    Returns a report: converted, indexed, skipped, removed, chunks, errors.
    """
    return ingest(incremental=incremental)


@app.tool()
def list_courses() -> dict:
    """List course codes in the SCHOOL catalog with their document counts.

    Use this first when the user mentions a course (e.g. "MATH225", "PHYS234")
    so you know what material exists before searching. Returns
    {"courses": [{"course", "documents", "missing"}, ...]}.
    """
    return {"courses": catalog_mod.list_courses()}


@app.tool()
def find_documents(query: str, course: str = "") -> dict:
    """Find catalogued school documents by title / name / type / path.

    The catalog indexes everything in the SCHOOL folder (textbooks, slides, past
    exams, formula sheets, manuals). Use this during a cram session to locate the
    exact file -- e.g. find_documents("orthogonal bases", "MATH225") or
    find_documents("griffiths"). Pass an empty course to search all courses. Each
    result includes current_path so you can read or ingest the file.

    Returns {"results": [{"course", "descriptive_name", "doc_type", "pages",
    "title", "current_path"}, ...]}.
    """
    return {"results": catalog_mod.find_documents(query, course or None)}


@app.tool()
def catalog_stats() -> dict:
    """Overview of the SCHOOL catalog: totals, per-course and per-type counts,
    duplicate copies, and missing files. Use for "what do I have to study"
    framing. Run the `life-catalog` CLI to (re)build the catalog from disk.
    """
    return catalog_mod.stats()


def main() -> None:
    parser = argparse.ArgumentParser(description="Knowledge retrieval MCP server")
    parser.add_argument("--stdio", action="store_true", help="Use STDIO transport (Claude Desktop)")
    parser.parse_args()
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
