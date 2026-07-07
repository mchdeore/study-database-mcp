"""CLI for the vault index (build step 0.8).

Usage:
    python scripts/vault_index.py                 # incremental index
    python scripts/vault_index.py --full          # reprocess every note
    python scripts/vault_index.py --rebuild       # drop derived tables + replay vault
    python scripts/vault_index.py --status        # show backend + row counts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the repo importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.vault.db import get_db  # noqa: E402
from servers.vault.index import index, rebuild_index  # noqa: E402


# Parse CLI flags into a namespace.
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build/maintain the vault index.")
    parser.add_argument("--full", action="store_true", help="reprocess every note")
    parser.add_argument("--rebuild", action="store_true", help="drop derived tables, replay vault")
    parser.add_argument("--status", action="store_true", help="show backend + row counts")
    return parser.parse_args()


# Run the requested action and print a JSON report.
def main() -> None:
    args = _parse_args()

    if args.status:
        database = get_db()
        database.migrate()
        print(json.dumps(database.health(), indent=2))
        return

    if args.rebuild:
        report = rebuild_index()
    else:
        report = index(incremental=not args.full)

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
