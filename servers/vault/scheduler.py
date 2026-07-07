"""Maintenance scheduler (build step 3.9).

One "tick" does the routine upkeep the vault needs, in order:

    reindex (incremental)  ->  prune (TTL + decay)  ->  backup  ->  journal summary

Every step is also runnable by hand elsewhere (that's the point -- the scheduler
just sequences existing, individually-tested operations), so a tick is auditable
and debuggable. Pruning is **dry-run by default** (trust: report, don't move);
set `prune_apply` in .vault/prune.config (or pass apply=True) to actually archive.

The tick writes a one-line summary to today's journal note so `git log` / the
journal reads as a maintenance changelog. (That summary note lands in the *next*
tick's commit, since the backup runs just before it -- an intentional one-tick
lag so the summary can report this tick's commit hash.)

`loop()` is the unattended cron/daemon form; `run_once()` is the testable unit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from . import archive
from . import backup as backup_mod
from . import capture
from .db import VaultDB, get_db
from .index import index
from .prune import load_config


# Decide whether this tick applies pruning: an explicit `apply` wins; otherwise
# read the `prune_apply` knob from .vault/prune.config (0 = dry-run default).
def _resolve_apply(apply: Optional[bool]) -> bool:
    if apply is not None:
        return bool(apply)
    return load_config()["prune_apply"] > 0


# A short, readable one-liner describing what the tick did, for the journal.
def _summarize(
    index_report: Dict[str, Any],
    ttl: Dict[str, Any],
    decay: Dict[str, Any],
    backup_report: Dict[str, Any],
    applied: bool,
) -> str:
    mode = "apply" if applied else "dry-run"
    verb = "archived" if applied else "would archive"

    git = backup_report.get("git", {})
    if git.get("commit"):
        commit_text = git["commit"][:10]
    elif git.get("error"):
        commit_text = f"git error ({git['error']})"
    else:
        commit_text = "no changes"

    snapshot = backup_report.get("db_snapshot", {})
    snapshot_text = Path(snapshot["path"]).name if snapshot.get("path") else snapshot.get("error", "none")

    return (
        f"Scheduler tick ({mode}): "
        f"reindexed {len(index_report.get('indexed', []))} note(s), "
        f"removed {len(index_report.get('removed', []))}; "
        f"TTL {verb} {ttl.get('count', 0)}; "
        f"decay {verb} {decay.get('count', 0)}; "
        f"backup commit {commit_text}; db snapshot {snapshot_text}."
    )


# Run one maintenance tick. Returns a structured report of every step plus the
# summary that was journaled. `apply` overrides the config knob; `now` (if given)
# is threaded into the pruning policies so tests can age the vault deterministically.
def run_once(
    *,
    apply: Optional[bool] = None,
    message: Optional[str] = None,
    now=None,
    database: Optional[VaultDB] = None,
) -> Dict[str, Any]:
    database = database or get_db()
    database.migrate()
    applied = _resolve_apply(apply)

    index_report = index(incremental=True, database=database)
    ttl = archive.run_ttl(dry_run=not applied, now=now, database=database)
    decay = archive.run_decay(dry_run=not applied, now=now, database=database)
    backup_report = backup_mod.run_backup(message=message)

    summary = _summarize(index_report, ttl, decay, backup_report, applied)
    journal = capture.append_to_journal(summary, database=database)

    return {
        "applied": applied,
        "indexed": len(index_report.get("indexed", [])),
        "removed": len(index_report.get("removed", [])),
        "ttl": ttl,
        "decay": decay,
        "backup": backup_report,
        "journal": {"path": journal.get("path")},
        "summary": summary,
    }


# Unattended loop: run a tick, print its summary, sleep, repeat. A failing tick is
# logged and the loop continues (one bad night shouldn't stop all maintenance).
def loop(interval_seconds: float, *, apply: Optional[bool] = None, message: Optional[str] = None) -> None:
    while True:
        try:
            report = run_once(apply=apply, message=message)
            print(json.dumps({"tick": report["summary"]}), flush=True)
        except Exception as error:  # noqa: BLE001 -- a bad tick must not kill the loop
            print(json.dumps({"tick_error": str(error)}), flush=True)
        time.sleep(interval_seconds)
