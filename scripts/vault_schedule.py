"""Vault maintenance CLI: run the scheduler by hand or as a loop (build step 3.9).

    python scripts/vault_schedule.py --once           # one tick (prune dry-run)
    python scripts/vault_schedule.py --once --apply    # one tick, actually prune
    python scripts/vault_schedule.py --backup          # just snapshot + git-commit
    python scripts/vault_schedule.py --loop --interval 3600   # run every hour

Pruning is dry-run unless --apply (or prune_apply>0 in .vault/prune.config). Runs
entirely locally: git-commits the vault and snapshots the DB to VAULT_BACKUP_DIR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.vault import backup as backup_mod  # noqa: E402
from servers.vault import scheduler  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run vault maintenance (reindex, prune, backup).")
    parser.add_argument("--once", action="store_true", help="run a single maintenance tick")
    parser.add_argument("--loop", action="store_true", help="run ticks forever, sleeping between")
    parser.add_argument("--backup", action="store_true", help="only snapshot the DB + git-commit the vault")
    parser.add_argument("--apply", action="store_true", help="actually prune (default: dry-run preview)")
    parser.add_argument("--interval", type=float, default=3600.0, help="seconds between ticks in --loop mode")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    apply = True if args.apply else None  # None -> fall back to the config knob

    if args.backup:
        print(json.dumps(backup_mod.run_backup(), indent=2))
    elif args.loop:
        scheduler.loop(args.interval, apply=apply)
    else:  # --once (default action)
        print(json.dumps(scheduler.run_once(apply=apply), indent=2, default=str))


if __name__ == "__main__":
    main()
