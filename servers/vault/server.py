"""Vault MCP server: the personal-knowledge tools (stdio transport).

Read tools (search), write tools (capture / quick_note / append_to_journal),
inbox triage, index maintenance, and owner-guarded credential setup. Runs over
stdio (local) — the transport Claude Desktop / Cowork speak; there is no
network/HTTP surface.

Tools:
  vault_status()                          backend + row counts
  search_vault(query, k=8, ...)           vector search w/ citations
  capture(text, category, tags)           file free text as a note
  quick_note(title, body, category)       file a titled note
  append_to_journal(text)                 add a timestamped daily-note entry
  list_inbox()                            unfiled notes awaiting triage
  reindex(incremental=true)               index new/changed notes
  rebuild_index()                         drop derived tables + replay the vault
  prune_expired(dry_run=true)             archive notes past their expires date (TTL)
  restore_note(note_id, batch)            undo an archival (restore from tombstone)
  list_tombstones()                       the reversible prune/archival log
  missing_credentials()                   credentials still needed
  set_credential(name, value)             store a credential (owner-only)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from mcp.server.fastmcp import FastMCP

# Support both package import and direct-script execution (how Claude Desktop
# launches it), mirroring the knowledge server.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from servers.vault import archive as archive_mod
    from servers.vault import capture as capture_mod
    from servers.vault import credentials as secrets_mod
    from servers.vault import prune as prune_mod
    from servers.vault import relations as relations_mod
    from servers.vault import backup as backup_mod
    from servers.vault import scheduler as scheduler_mod
    from servers.vault import staging as staging_mod
    from servers.vault.connectors import cursors as cursors_mod
    from servers.vault.db import get_db
    from servers.vault.index import index, rebuild_index
    from servers.vault.search import search, get_note as _get_note, timeline as _timeline
else:
    from . import archive as archive_mod
    from . import capture as capture_mod
    from . import credentials as secrets_mod
    from . import prune as prune_mod
    from . import relations as relations_mod
    from . import backup as backup_mod
    from . import scheduler as scheduler_mod
    from . import staging as staging_mod
    from .connectors import cursors as cursors_mod
    from .db import get_db
    from .index import index, rebuild_index
    from .search import search, get_note as _get_note, timeline as _timeline

app = FastMCP("Vault", dependencies=["numpy", "python-dotenv"])


@app.tool()
def vault_status() -> dict:
    """Backend name, per-table row counts, and a per-category note breakdown.

    The `categories` map (category -> note count) shows what's in the vault so you
    can target `search_vault(category=...)` (which matches a folder and its
    subcategories, e.g. "40-areas" also covers "40-areas/calendar").
    """
    database = get_db()
    database.migrate()
    health = database.health()

    categories: dict = {}
    for document in database.list_documents():
        category = document.get("category") or "(uncategorized)"
        categories[category] = categories.get(category, 0) + 1
    health["categories"] = dict(sorted(categories.items()))
    return health


@app.tool()
def search_vault(query: str, k: int = 8, category: str = "", source: str = "", status: str = "",
                 mode: str = "hybrid", detail: str = "compact") -> dict:
    """Hybrid search across the vault (lexical BM25 + vector, fused with RRF).

    TOKEN-EFFICIENT TWO-STAGE CONTRACT (use this to keep context small):
      1. Call this in `detail="compact"` (default): each hit is a short snippet +
         a citation carrying `document_id`. Cheap — judge relevance, and often
         answer straight from the snippets.
      2. Only when you need a note's full text, call get_note(citation.document_id)
         for that ONE note. Avoid pulling full bodies for every hit.
    Use `detail="full"` to inline each chunk's full text when you truly need it.

    Optional filters scope the search: `category` (matches a folder and its
    subcategories, e.g. "40-areas" covers "40-areas/school"), `source` (a note's
    path), or `status` ("active"/"archived"). `mode` is "hybrid" (default),
    "vector", or "lexical" — hybrid catches both semantic matches and exact terms
    (names, codes); active notes rank above archived. Large responses are held under
    a character budget (see `has_more`); narrow with filters rather than a huge k.
    """
    filters = {key: value for key, value in
               {"category": category, "source": source, "status": status}.items() if value}
    return search(query, k=k, filters=filters or None, mode=mode, detail=detail)


@app.tool()
def get_note(ref: str) -> dict:
    """Read a full note (frontmatter + body) by document id, vault path, or source_ref.

    Pairs with search_vault: search returns chunks with a citation.document_id; pass
    that id here to read the WHOLE note and answer accurately. Also accepts a vault
    path ("30-projects/kitchen-reno-budget.md") or a connector source_ref
    ("gcal://event/..."). Long bodies are truncated with a message. Reading a note
    counts as an access (feeds prune scoring).
    """
    return _get_note(ref)


@app.tool()
def timeline(start: str = "", end: str = "", limit: int = 50) -> dict:
    """List calendar/dated events (derived from note `start:` frontmatter), soonest first.

    Optional ISO `start`/`end` bound the window (e.g. "2026-07-01" or
    "2026-07-01T00:00:00Z"). Each event carries its title, start/end, and the
    document_id of the note it came from (read the full note with get_note).
    """
    return _timeline(start or None, end or None, limit)


@app.tool()
def capture(text: str, category: str = "00-inbox", tags: Optional[List[str]] = None,
            on_similar: str = "warn", force_new: bool = False) -> dict:
    """File free text as a new note (default: the inbox). Indexes it immediately.

    Smart dedup: byte-identical content is always refused. If a very similar note
    already exists, `on_similar` decides: 'warn' (default) creates the note but
    returns `similar_to`; 'skip' does NOT create and returns the match; 'append'
    consolidates the text into the matching note (no duplicate). `force_new=True`
    always creates a separate note. When a result has `similar_to`, consider
    append_to_note to consolidate instead of keeping two copies.
    """
    return capture_mod.capture(text, category=category, tags=tags,
                               on_similar=on_similar, force_new=force_new)


@app.tool()
def append_to_note(ref: str, text: str) -> dict:
    """Append text to an existing note (found by document id, vault path, or source_ref).

    The consolidation move: instead of creating a near-duplicate, add content to the
    note it belongs with. Pair it with capture's `similar_to` (append to that id).
    Re-indexes the note so the new content is searchable.
    """
    return capture_mod.append_to_note(ref, text)


@app.tool()
def quick_note(title: str, body: str, category: str = "00-inbox") -> dict:
    """File a titled note (title becomes the H1 heading). Indexes it immediately."""
    return capture_mod.quick_note(title, body, category=category)


@app.tool()
def append_to_journal(text: str) -> dict:
    """Append a timestamped entry to today's daily journal note."""
    return capture_mod.append_to_journal(text)


@app.tool()
def list_inbox() -> dict:
    """List unfiled notes in the inbox (id, title, summary) for triage."""
    return {"inbox": capture_mod.list_inbox()}


@app.tool()
def related(note_id: str) -> dict:
    """Relations for a document: outgoing links (resolved from [[wikilinks]]) and backlinks."""
    return relations_mod.related(note_id)


@app.tool()
def find_duplicates() -> dict:
    """List files the indexer skipped as exact duplicates of an existing document."""
    return relations_mod.find_duplicates()


@app.tool()
def find_near_duplicates(threshold: float = relations_mod.NEAR_DUP_THRESHOLD) -> dict:
    """Flag pairs of documents with highly similar content (possible duplicates) for review.

    Compares document embeddings by cosine similarity; pairs at or above
    `threshold` (0..1) are returned, most-similar first. This never merges or
    deletes anything -- it's a review aid. Exact, byte-identical duplicates are
    reported separately by `find_duplicates`.
    """
    return relations_mod.find_near_duplicates(threshold=threshold)


@app.tool()
def write_relations_map() -> dict:
    """Regenerate the auditable relations map at .vault/relations.md."""
    return relations_mod.write_relations_map()


@app.tool()
def explain_prune(note_id: str) -> dict:
    """Show a document's prune_score and the per-term breakdown (why it ranks where it does)."""
    return prune_mod.explain(note_id)


@app.tool()
def recompute_scores() -> dict:
    """Reapply access signals and recompute every document's prune_score."""
    return prune_mod.refresh()


@app.tool()
def init_prune_config() -> dict:
    """Write a default, tunable .vault/prune.config if one doesn't exist."""
    return {"path": prune_mod.write_default_config()}


@app.tool()
def prune_expired(dry_run: bool = True) -> dict:
    """Archive notes past their `expires:` date (TTL policy). Dry-run by default.

    With dry_run=True (default) it only PREVIEWS: returns the notes that would be
    archived, changing nothing on disk. With dry_run=False it moves each expired,
    non-pinned, active note to 90-archive/, flips its status, and records a
    reversible tombstone. Pinned notes are never archived.
    """
    return archive_mod.run_ttl(dry_run=dry_run)


@app.tool()
def prune_decayed(dry_run: bool = True) -> dict:
    """Archive low-value, idle notes (decay policy). Dry-run by default.

    Selects active, non-pinned notes whose `prune_score` is at/below the configured
    `decay_score_threshold` AND that have gone untouched for at least
    `decay_min_idle_days` (both tunable in .vault/prune.config). With dry_run=True
    (default) it only PREVIEWS; with dry_run=False it moves each to 90-archive/,
    flips status, and records a reversible tombstone. Pinned notes are never touched.
    """
    return archive_mod.run_decay(dry_run=dry_run)


@app.tool()
def backup_vault() -> dict:
    """Back up locally: snapshot the derived DB to the backup folder and git-commit the vault.

    Nothing leaves the machine. Secrets are never committed (the vault .gitignore
    excludes them). Returns the DB snapshot path and the git commit result.
    """
    return backup_mod.run_backup()


@app.tool()
def run_maintenance(apply: bool = False) -> dict:
    """Run one maintenance tick: reindex, prune (TTL + decay), back up, journal a summary.

    Pruning is a DRY-RUN preview unless apply=True (or prune_apply>0 in
    .vault/prune.config). Returns a report of every step and the summary that was
    written to today's journal note.
    """
    return scheduler_mod.run_once(apply=True if apply else None)


@app.tool()
def restore_note(note_id: str = "", batch: str = "") -> dict:
    """Restore archived note(s) to their original location (undo an archival).

    Pass a `note_id` to restore one note, and/or a prune `batch` id to restore a
    whole prune run. Moves the file back, sets status=active, and clears the
    tombstone.
    """
    return archive_mod.restore(note_id=note_id or None, batch=batch or None)


@app.tool()
def list_tombstones() -> dict:
    """List the reversible prune/archival log (what was archived, from where, why)."""
    return {"tombstones": archive_mod.list_tombstones()}


@app.tool()
def reindex(incremental: bool = True) -> dict:
    """Index new/changed notes (incremental by default)."""
    return index(incremental=incremental)


@app.tool()
def sync_status() -> dict:
    """Per-source connector sync checkpoints (from .vault/cursors.json).

    Returns each source's saved cursor, last_sync timestamp, and the item count from
    its last run — so you can see when Calendar/Gmail last synced and how far. Advance
    them with sync_google (needs Google authorization — see google_auth_status).
    """
    return {"cursors": cursors_mod.load_cursors()}


@app.tool()
def google_auth_status() -> dict:
    """Whether Google (Calendar + Gmail) is authorized: which credential pieces are set.

    Reports client_id/secret/refresh_token presence and the read-only scopes, plus a
    `ready` flag. No secret values are returned. If not ready: store the OAuth client
    id/secret with set_credential, then run setup_google once.
    """
    from servers.vault.connectors import google_auth
    return google_auth.status()


@app.tool()
def setup_google() -> dict:
    """Run the one-time Google OAuth consent (owner-only, opens a browser locally).

    Prerequisite: google_oauth_client_id + google_oauth_client_secret stored via
    set_credential (from a Google Cloud "Desktop app" OAuth client). This opens
    Google's consent screen on this machine, catches the redirect, and stores the
    resulting refresh token (encrypted). Read-only Calendar + Gmail scopes only.
    Needs the `connectors-google` extra installed. Returns a status dict (no tokens).
    """
    from servers.vault.connectors import google_auth
    try:
        return google_auth.run_consent(owner=True)
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}"}


@app.tool()
def sync_google(calendar: bool = True, gmail: bool = True, full: bool = False) -> dict:
    """Sync Google Calendar and/or Gmail into the vault (incremental by default).

    Pulls events/messages via the read-only Google APIs and upserts them as Markdown
    notes deduped by source_ref (re-syncing updates in place, never duplicates).
    Calendar → 40-areas/calendar/; Gmail → ephemeral 50-resources/mail/ with a TTL.
    `full=True` ignores the saved cursor and re-pulls from the start. Requires Google
    authorization (see google_auth_status / setup_google). Returns per-source reports.
    """
    from servers.vault.connectors import google_auth

    if not google_auth.status()["ready"]:
        return {"error": "Google is not authorized yet. Store the OAuth client id/secret "
                         "with set_credential, then run setup_google.",
                "status": google_auth.status()}

    from servers.vault.connectors import google_fetch
    from servers.vault.connectors.sync import run_sync

    reports = []
    try:
        if calendar:
            reports.append(run_sync(google_fetch.live_calendar_connector(), full=full))
        if gmail:
            reports.append(run_sync(google_fetch.live_gmail_connector(), full=full))
    except Exception as error:  # noqa: BLE001
        return {"error": f"{type(error).__name__}: {error}", "partial": reports}
    return {"synced": reports}


@app.tool()
def import_staging(area: str = "", dry_run: bool = True) -> dict:
    """Import staged Markdown from data/incoming/<area>/ into the vault. Dry-run by default.

    An external agent stages files (per data/incoming/*/README.md); this upserts each
    content file into the vault by its `source_ref` (stable id, update-in-place, no
    duplicates) landing it in the note's declared `category`. Instruction/scratch
    files (README, _template, _digest, dotfiles) are skipped; a file missing a
    `source_ref` is reported, never guessed. Pass an `area` (e.g. "school") to limit
    scope; dry_run=True previews (created/updated/skipped) without writing.
    """
    return staging_mod.import_staging(area or None, dry_run=dry_run)


@app.tool()
def rebuild_index_tool() -> dict:
    """Drop the derived tables and replay the entire vault into the index."""
    return rebuild_index()


@app.tool()
def missing_credentials() -> dict:
    """List credentials the system still needs, with descriptions."""
    return {"missing": secrets_mod.missing_credentials()}


@app.tool()
def set_credential(name: str, value: str) -> dict:
    """Store a credential (owner-only). Over stdio the local caller is the owner."""
    try:
        return secrets_mod.set_credential(name, value, owner=True)
    except (ValueError, PermissionError) as error:
        return {"error": str(error)}


def main() -> None:
    # stdio is the only transport: this server runs locally as a subprocess of
    # Claude Desktop / Cowork (the "MCP handshake"). The optional --stdio flag is
    # accepted for config compatibility; there is no network/HTTP surface.
    parser = argparse.ArgumentParser(description="Vault personal-knowledge MCP server (stdio)")
    parser.add_argument("--stdio", action="store_true",
                        help="Use STDIO transport (default; for Claude Desktop / Cowork)")
    parser.parse_args()
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
