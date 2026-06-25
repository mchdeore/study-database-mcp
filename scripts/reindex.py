"""CLI to (re)index the corpus. Full or incremental.

Usage:
    python scripts/reindex.py              # incremental (default)
    python scripts/reindex.py --full       # force re-process every file
    python scripts/reindex.py --no-graph   # skip concept-graph rebuild
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.knowledge.ingest import ingest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Reindex the study corpus")
    ap.add_argument("--full", action="store_true", help="force full reindex (ignore content hashes)")
    ap.add_argument("--no-graph", action="store_true", help="skip rebuilding the concept graph")
    args = ap.parse_args()

    report = ingest(incremental=not args.full, rebuild_graph=not args.no_graph)

    print("Reindex report")
    print(f"  converted: {len(report['converted'])}  {report['converted'] or ''}")
    print(f"  indexed:   {len(report['indexed'])}  {report['indexed'] or ''}")
    print(f"  skipped:   {len(report['skipped'])}")
    print(f"  removed:   {len(report['removed'])}  {report['removed'] or ''}")
    print(f"  chunks:    {report['chunks']}")
    if report["errors"]:
        print("  errors:")
        for e in report["errors"]:
            print(f"    - {e}")


if __name__ == "__main__":
    main()
