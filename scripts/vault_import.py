"""Import staged drops into the vault (build: staging importer).

    python scripts/vault_import.py --dry-run           # preview all areas
    python scripts/vault_import.py --area school        # import just school/
    python scripts/vault_import.py                      # import everything

Reads data/incoming/<area>/ (override with STAGING_DIR), upserts each content file
into the vault by its `source_ref` (stable id, update-in-place, no duplicates) in
the note's declared `category`. README/_template/_digest/dotfiles are skipped; a
file with no `source_ref` is reported, not guessed. Dry-run first is recommended.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.vault.staging import import_staging  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import staged drops into the vault.")
    parser.add_argument("--area", default="", help="limit to one area subfolder (e.g. school)")
    parser.add_argument("--dry-run", action="store_true", help="preview without writing")
    args = parser.parse_args()

    report = import_staging(args.area or None, dry_run=args.dry_run)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
