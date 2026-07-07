"""Vault configuration & on-disk paths (build step 0.1).

The vault is the human-auditable source of truth. This module decides WHERE the
vault lives, names the categorical top-level folders, and resolves the locations
of generated machine files (DB, manifest) which live under a dotted `.vault/`
folder so a clone is self-describing.

Everything is read from environment variables (loaded from .env if present) so a
test can point VAULT_DIR at a temp folder and run fully offline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

# Repo root is three levels up from this file (servers/vault/config.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# The categorical top-level folders the vault is organized into. Order is stable
# and numeric so the vault stays scannable in a file browser. See
# docs/vision/04-vault-structure.md for the meaning of each.
TAXONOMY_FOLDERS = [
    "00-inbox",
    "10-journal",
    "20-people",
    "30-projects",
    "40-areas",
    "50-resources",
    "60-sources",
    "90-archive",
]

# Generated/system files live here (dotted so it's clearly not hand-authored).
SYSTEM_FOLDER = ".vault"

# Where archived (pruned-but-reversible) notes are moved to. It is one of the
# taxonomy folders above; named here so the archival mechanism doesn't hardcode it.
ARCHIVE_FOLDER = "90-archive"

# Default relational backend when VAULT_DB is unset. SQLite is zero-ops and runs
# the whole pipeline offline; Postgres is opt-in for scale.
DEFAULT_DB_BACKEND = "sqlite"


# Load .env into the environment if python-dotenv is available, so config knobs
# can live in a file. Missing dotenv is fine -- we just rely on os.environ.
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env")
    except Exception:  # noqa: BLE001  -- dotenv is optional; never fail import
        pass


_load_dotenv()


# Resolve the vault root directory, honoring the VAULT_DIR override and expanding
# a leading ~ so "~/vault" works. Defaults to ./vault next to the repo.
def vault_dir() -> Path:
    configured = os.environ.get("VAULT_DIR")
    if configured:
        return Path(configured).expanduser()
    return _REPO_ROOT / "vault"


# Resolve every path the vault server needs, derived from the vault root. Keeps
# all path knowledge in one place so nothing else hardcodes folder names.
def paths() -> Dict[str, Path]:
    root = vault_dir()
    system = root / SYSTEM_FOLDER
    resolved: Dict[str, Path] = {"vault": root, "system": system}

    # Each categorical folder gets a key equal to its folder name.
    for folder in TAXONOMY_FOLDERS:
        resolved[folder] = root / folder

    # Generated machine files.
    resolved["db_sqlite"] = system / "index.db"
    resolved["manifest"] = system / "manifest.json"
    resolved["tombstones_md"] = system / "tombstones.md"
    resolved["relations_md"] = system / "relations.md"
    resolved["signals_json"] = system / "signals.json"
    resolved["prune_config"] = system / "prune.config"
    resolved["cursors_json"] = system / "cursors.json"
    return resolved


# Where local backups live. The vault itself is versioned with git in place; the
# DB snapshot needs a home OUTSIDE the vault so it isn't committed back into it.
# Overridable with VAULT_BACKUP_DIR (put it on a 2nd physical disk on the server
# box). Defaults to a sibling folder next to the vault.
def backup_dir() -> Path:
    configured = os.environ.get("VAULT_BACKUP_DIR")
    if configured:
        return Path(configured).expanduser()
    root = vault_dir()
    return root.parent / f"{root.name}-backups"


# Create the vault root, every categorical folder, and the system folder if they
# don't exist yet. Safe to call repeatedly (used on first run and by tests).
def ensure_layout() -> Dict[str, Path]:
    resolved = paths()
    resolved["vault"].mkdir(parents=True, exist_ok=True)
    resolved["system"].mkdir(parents=True, exist_ok=True)
    for folder in TAXONOMY_FOLDERS:
        resolved[folder].mkdir(parents=True, exist_ok=True)
    return resolved


# Read the non-path runtime configuration (which DB backend, optional DSN). The
# embedding/chunking knobs are reused from the knowledge server's config so the
# two stay consistent.
def config() -> Dict[str, str]:
    backend = os.environ.get("VAULT_DB", DEFAULT_DB_BACKEND).strip().lower()
    return {
        "db_backend": backend,
        "postgres_dsn": os.environ.get("POSTGRES_DSN", ""),
    }
