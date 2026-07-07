"""Basic read-only web dashboard over the vault (build step 9.2, walking skeleton).

Server-rendered plain HTML (no framework, no CSS to start -- function over form),
served by Starlette. It reuses the same read path as the MCP tools
(`get_db`/`search`/`timeline`/`get_note`), so the browser sees exactly what the LLM
sees. Pages:

  /            Home: vault counts + the next few upcoming items
  /upcoming    everything with a date, soonest first (the unified "events" table --
               calendar items, assignment/exam due dates, bills, club meetings)
  /search      RAG search box -> results linking to the note viewer
  /note?ref=   full note (frontmatter + body) by id / path / source_ref
  /finances    notes under 40-areas/finance (until the Phase 8 module formalizes it)
  /build       the build-plan status dashboard, so you can watch the product grow

SECURITY: this dashboard is UNAUTHENTICATED and read-only. It must stay bound to
127.0.0.1 (localhost). For remote access, reach it over Tailscale or put it behind
the same bearer auth as the MCP server -- do NOT bind it to 0.0.0.0 as-is.

ponytail: handlers call the (sync) vault functions directly; fine for a local,
single-user dashboard. If it ever needs concurrency, wrap them in run_in_threadpool.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Route

from .db import get_db
from .search import get_note as _get_note, search as _search, timeline as _timeline

# Nav shown on every page.
_NAV: List[Tuple[str, str]] = [
    ("/", "Home"), ("/upcoming", "Upcoming"), ("/search", "Search"),
    ("/finances", "Finances"), ("/build", "Build"),
]

# Vault-relative category prefix that counts as "finance".
_FINANCE_PREFIX = "40-areas/finance"


# Escape any dynamic string before it goes into HTML (single-user + local, but
# still: never inject unescaped note content into the page).
def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# Wrap page body in a minimal HTML document with the nav header.
def _page(title: str, body_html: str) -> HTMLResponse:
    nav = " · ".join(f'<a href="{href}">{_e(label)}</a>' for href, label in _NAV)
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{_e(title)} — Life Vault</title></head><body>"
        f"<header><strong>Life Vault</strong> — {nav}</header><hr>"
        f"<h1>{_e(title)}</h1>{body_html}</body></html>"
    )


# Render a simple HTML table from headers + pre-escaped row cells.
def _table(headers: List[str], rows: List[List[str]]) -> str:
    if not rows:
        return "<p><em>Nothing here yet.</em></p>"
    head = "".join(f"<th align='left'>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table border='1' cellpadding='6' cellspacing='0'><tr>{head}</tr>{body}</table>"


# Link to the note viewer for a document id (or path / source_ref).
def _note_link(ref: str, label: str) -> str:
    return f'<a href="/note?ref={_e(ref)}">{_e(label)}</a>'


def _upcoming_rows(limit: int) -> List[List[str]]:
    events = _timeline(limit=limit).get("events", [])
    rows = []
    for event in events:
        when = event.get("start_at") or ""
        title = event.get("title") or "(untitled)"
        link = _note_link(event.get("document_id") or "", "open") if event.get("document_id") else ""
        rows.append([_e(when), _e(title), _e(event.get("source") or ""), link])
    return rows


async def home(request):
    database = get_db()
    database.migrate()
    counts = database.health().get("counts", {})
    count_rows = [[_e(table), _e(n)] for table, n in sorted(counts.items())]
    upcoming = _upcoming_rows(5)
    body = (
        "<h2>Vault</h2>" + _table(["table", "rows"], count_rows) +
        "<h2>Next up</h2>" + _table(["when", "item", "source", ""], upcoming) +
        '<p><a href="/upcoming">See all upcoming →</a></p>'
        '<h2>Search</h2><form action="/search" method="get">'
        '<input name="q" size="48" placeholder="search your vault"> <button>Search</button></form>'
    )
    return _page("Home", body)


async def upcoming(request):
    database = get_db()
    database.migrate()
    body = (
        "<p>Everything with a date, soonest first — calendar items, due dates, bills, "
        "meetings. (Add items to Google Calendar with an ALL-CAPS prefix like "
        "<code>SCHOOL:</code> / <code>FINANCE:</code> to group them.)</p>"
        + _table(["when", "item", "source", ""], _upcoming_rows(200))
    )
    return _page("Upcoming", body)


async def search(request):
    query = (request.query_params.get("q") or "").strip()
    form = (
        '<form action="/search" method="get">'
        f'<input name="q" size="48" value="{_e(query)}" placeholder="search your vault"> '
        "<button>Search</button></form>"
    )
    if not query:
        return _page("Search", form)

    results = _search(query, k=10).get("results", [])
    rows = []
    for hit in results:
        citation = hit.get("citation", {})
        snippet = (hit.get("snippet") or hit.get("text") or "").strip().replace("\n", " ")[:200]
        rows.append([
            _note_link(citation.get("document_id") or citation.get("source") or "",
                       citation.get("title") or citation.get("source") or "(note)"),
            _e(citation.get("status") or ""),
            _e(snippet),
        ])
    body = form + f"<p>{len(rows)} result(s) for <strong>{_e(query)}</strong>.</p>" + \
        _table(["note", "status", "snippet"], rows)
    return _page("Search", body)


async def note(request):
    ref = (request.query_params.get("ref") or "").strip()
    result = _get_note(ref) if ref else {"error": "no note reference given"}
    if result.get("error"):
        return _page("Note", f"<p><strong>Error:</strong> {_e(result['error'])}</p>"
                             f"<p>{_e(result.get('hint', ''))}</p>")

    frontmatter = result.get("frontmatter", {})
    fm_rows = [[_e(key), _e(value)] for key, value in frontmatter.items()]
    status_note = " · <em>archived</em>" if result.get("status") == "archived" else ""
    body = (
        f"<p><code>{_e(result.get('path'))}</code>{status_note}</p>"
        "<h2>Frontmatter</h2>" + _table(["field", "value"], fm_rows) +
        "<h2>Body</h2><pre style='white-space:pre-wrap'>" + _e(result.get("body", "")) + "</pre>"
    )
    if result.get("truncated"):
        body += f"<p><em>{_e(result.get('truncation_message', 'truncated'))}</em></p>"
    return _page(result.get("title") or "Note", body)


async def finances(request):
    database = get_db()
    database.migrate()
    rows = []
    for document in database.list_documents():
        if (document.get("category") or "").startswith(_FINANCE_PREFIX):
            rows.append([
                _note_link(document["id"], document.get("title") or document.get("path")),
                _e(document.get("category")),
            ])
    if not rows:
        body = ("<p><em>No finance data yet.</em> Stage transactions/accounts/budgets in "
                "<code>data/incoming/finance/</code>; the budgeting module (Phase 8) will turn "
                "these into accounts, rollups, and charts.</p>")
    else:
        body = ("<p>Finance notes (Phase 8 will add balances, categories, and rollups).</p>"
                + _table(["note", "category"], rows))
    return _page("Finances", body)


# Extract the build-plan "Status dashboard" table so you can watch the product grow.
def _build_dashboard() -> str:
    plan = Path(__file__).resolve().parents[2] / "docs" / "vision" / "13-build-plan.md"
    if not plan.exists():
        return "<p><em>Build plan not found.</em></p>"
    lines = plan.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "## Status dashboard")
    except StopIteration:
        return "<p><em>No status dashboard section.</em></p>"

    table_rows = []
    for line in lines[start + 1:]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):  # skip the |---|---| separator
                continue
            table_rows.append([_e(c) for c in cells])
        elif table_rows:
            break  # table ended
    if not table_rows:
        return "<p><em>No dashboard rows.</em></p>"
    headers, body_rows = table_rows[0], table_rows[1:]
    return _table(headers, body_rows)


async def build(request):
    body = ("<p>What we're building and how far along — from the build plan.</p>"
            + _build_dashboard() +
            "<p>Full detail: <code>docs/vision/13-build-plan.md</code> and "
            "<code>docs/vision/14-prior-art.md</code>.</p>")
    return _page("Build", body)


# Assemble the Starlette app (routes only; state is read per-request from the vault).
def create_app() -> Starlette:
    return Starlette(routes=[
        Route("/", home),
        Route("/upcoming", upcoming),
        Route("/search", search),
        Route("/note", note),
        Route("/finances", finances),
        Route("/build", build),
    ])


# Run the dashboard with uvicorn. Localhost-only by default; warn loudly if bound
# wider, since the dashboard has no auth of its own.
def run(host: str = "127.0.0.1", port: int = 8760) -> None:
    import sys
    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: the dashboard is UNAUTHENTICATED and read-only; binding to {host} "
              "exposes your vault. Prefer 127.0.0.1 and reach it over Tailscale.", file=sys.stderr)
    print(f"Life Vault dashboard on http://{host}:{port}", file=sys.stderr)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
