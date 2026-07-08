"""Local backup: git-commit the vault + snapshot the derived DB (build step 3.10).

Two independent safety nets, both local (no network, nothing leaves the box):

  1. The vault (the source of truth) is versioned with **git in place** -- every
     backup stages the notes and commits, so `git log` is your life's changelog
     and any note is recoverable to any past state.
  2. The derived relational index is **snapshotted** to a dated file in a folder
     OUTSIDE the vault (so it isn't committed back into the vault). SQLite uses the
     online-backup API (a consistent copy even with the server running); Postgres
     uses `pg_dump` (opt-in backend, untested offline -- see the deferred log).

Security: secrets are NEVER committed. Before every commit we make sure the
vault's `.gitignore` excludes the secrets store (and the rebuildable index/manifest
and the churny audit log). This runs on every backup, not just on init, so an
existing repo also gets the guard.

ponytail: git and pg_dump are invoked via subprocess -- they are the standard,
correct tools for the job and shelling out to them is far less code (and less
risk) than reimplementing either. Timestamp-per-second dump filenames collide only
if two backups run within the same second (not a real scheduler cadence).
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import backup_dir, config, paths

# Patterns the vault's .gitignore MUST contain so a backup never commits secrets
# and doesn't bloat history with the rebuildable derived index. `secrets.*` is the
# non-negotiable one; the rest are just noise-reduction (all rebuildable/derived).
_MANDATORY_IGNORES = [
    ".vault/secrets.json",   # plaintext credential store (must never be committed)
    ".vault/secrets.enc",    # encrypted credential store (belt-and-suspenders)
    ".vault/index.db",       # derived relational index (rebuildable; snapshotted separately)
    ".vault/manifest.json",  # derived content-hash manifest (rebuildable)
]


# Current UTC time as a compact, filesystem-safe stamp for dump filenames.
def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# Run a git subcommand inside the vault. `check=False` so callers decide how to
# react (some commands, e.g. `commit` with nothing staged, exit non-zero).
def _git(args: List[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=check,
        capture_output=True, text=True,
    )


# Ensure the vault's .gitignore excludes secrets (and the derived index). Appends
# only the missing mandatory patterns, preserving anything the user added. Called
# before every commit so even a pre-existing repo gets the secret guard.
def _ensure_gitignore(vault: Path) -> None:
    gitignore = vault / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    present = {line.strip() for line in existing}
    missing = [pattern for pattern in _MANDATORY_IGNORES if pattern not in present]
    if not missing:
        return

    lines = list(existing)
    if not existing:
        lines.append("# Life Vault: never commit secrets; skip the rebuildable derived index.")
    lines.extend(missing)
    gitignore.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


# Snapshot the derived DB into `destination` with a dated filename. SQLite uses
# the online-backup API (consistent even under concurrent access); Postgres shells
# out to pg_dump. Returns {backend, path} or {backend, error} on failure.
def snapshot_db(destination: Optional[Path] = None) -> Dict[str, Any]:
    destination = destination or backup_dir()
    destination.mkdir(parents=True, exist_ok=True)
    backend = config()["db_backend"]

    if backend == "sqlite":
        source_path = paths()["db_sqlite"]
        dump_path = destination / f"index-{_stamp()}.db"
        source = sqlite3.connect(str(source_path))
        target = sqlite3.connect(str(dump_path))
        try:
            with target:
                source.backup(target)
        finally:
            source.close()
            target.close()
        return {"backend": "sqlite", "path": str(dump_path)}

    # Postgres: pg_dump to a dated .sql file. Untested offline (see deferred log).
    dsn = config()["postgres_dsn"]
    if not dsn:
        return {"backend": "postgres", "error": "POSTGRES_DSN is not set; cannot pg_dump."}
    dump_path = destination / f"pg-{_stamp()}.sql"
    if shutil.which("pg_dump") is None:
        return {"backend": "postgres", "error": "pg_dump not found on PATH."}
    result = subprocess.run(
        ["pg_dump", "--dbname", dsn, "--file", str(dump_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"backend": "postgres", "error": result.stderr.strip() or "pg_dump failed."}
    return {"backend": "postgres", "path": str(dump_path)}


# Git-commit the whole vault. Initializes a repo on first run, guarantees secrets
# are gitignored, stages everything, and commits. Returns whether a commit was made
# (no changes -> committed=False, not an error) and the resulting HEAD hash.
# Uses per-commit identity flags so it never touches the user's global git config.
def git_commit_vault(message: str) -> Dict[str, Any]:
    vault = paths()["vault"]
    vault.mkdir(parents=True, exist_ok=True)

    try:
        if not (vault / ".git").exists():
            _git(["init"], vault, check=True)
        _ensure_gitignore(vault)
        _git(["add", "-A"], vault, check=True)

        staged = _git(["status", "--porcelain"], vault).stdout.strip()
        if not staged:
            return {"committed": False, "reason": "no changes", "commit": None}

        _git(
            ["-c", "user.email=vault@localhost", "-c", "user.name=Life Vault",
             "commit", "-m", message],
            vault, check=True,
        )
        head = _git(["rev-parse", "HEAD"], vault).stdout.strip()
        return {"committed": True, "commit": head, "message": message}
    except FileNotFoundError:
        return {"committed": False, "error": "git not found on PATH; install git to back up the vault."}
    except subprocess.CalledProcessError as error:
        return {"committed": False, "error": (error.stderr or str(error)).strip()}


# Run a full local backup: snapshot the DB, then commit the vault. Returns a report
# combining both, plus the backup directory and timestamp.
def run_backup(message: Optional[str] = None) -> Dict[str, Any]:
    destination = backup_dir()
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db_snapshot = snapshot_db(destination)
    git = git_commit_vault(message or f"vault backup {at}")
    return {"backup_dir": str(destination), "db_snapshot": db_snapshot, "git": git, "at": at}
