"""CLI for the SCHOOL document catalog (SQLite at data/catalog.db).

Usage:
    python scripts/catalog.py                      # incremental scan (default)
    python scripts/catalog.py --full               # re-hash every file
    python scripts/catalog.py --school /path/to/SCHOOL
    python scripts/catalog.py --stats              # totals by course/type
    python scripts/catalog.py --list [COURSE]      # list catalogued documents
    python scripts/catalog.py --duplicates         # show duplicate copies
    python scripts/catalog.py --possible-duplicates# same-title, different bytes
    python scripts/catalog.py --plan-renames       # preview descriptive renames
    python scripts/catalog.py --apply-renames      # rename files (reversible)
    python scripts/catalog.py --undo-renames       # revert the last rename batch
    python scripts/catalog.py --delete-duplicate-files --yes   # destructive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.knowledge import catalog  # noqa: E402


def _print_scan(report: dict) -> None:
    if "error" in report:
        print(f"error: {report['error']}")
        if "hint" in report:
            print(f"  hint: {report['hint']}")
        return
    print(f"Catalog scan of {report['root']}")
    print(f"  new:        {len(report['new'])}")
    for name in report["new"]:
        print(f"    + {name}")
    print(f"  duplicates: {len(report['duplicates'])} (extra copies recorded, not entered)")
    print(f"  updated:    {report['updated']}")
    print(f"  skipped:    {report['skipped']} (unchanged)")
    print(f"  missing:    {len(report['missing'])}")
    for name in report["missing"]:
        print(f"    ? {name}")
    if report["errors"]:
        print("  errors:")
        for e in report["errors"]:
            print(f"    - {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Catalog the SCHOOL folder")
    ap.add_argument("--school", help="path to the SCHOOL folder (overrides SCHOOL_DIR)")
    ap.add_argument("--full", action="store_true", help="re-hash every file (ignore size/mtime gate)")
    ap.add_argument("--stats", action="store_true", help="print catalog statistics")
    ap.add_argument("--list", nargs="?", const="", metavar="COURSE",
                    help="list documents (optionally for one COURSE)")
    ap.add_argument("--duplicates", action="store_true", help="list byte-identical duplicate copies")
    ap.add_argument("--possible-duplicates", action="store_true",
                    help="list same-title documents with different bytes (review)")
    ap.add_argument("--plan-renames", action="store_true", help="preview descriptive renames (no changes)")
    ap.add_argument("--apply-renames", action="store_true", help="rename files on disk (reversible)")
    ap.add_argument("--undo-renames", action="store_true", help="revert the last rename batch")
    ap.add_argument("--delete-duplicate-files", action="store_true",
                    help="DELETE redundant duplicate copies from disk (needs --yes)")
    ap.add_argument("--yes", action="store_true", help="confirm a destructive action")
    args = ap.parse_args()

    # read-only / action-only modes (no scan)
    if args.stats:
        s = catalog.stats()
        print(f"Documents: {s['total_documents']}   duplicate copies: "
              f"{s['duplicate_copies']}   missing: {s['missing']}")
        print("By course:")
        for r in s["by_course"]:
            print(f"  {r['course']:<12} {r['n']}")
        print("By type:")
        for r in s["by_type"]:
            print(f"  {r['doc_type']:<14} {r['n']}")
        return

    if args.list is not None:
        for r in catalog.list_documents(args.list or None):
            pages = f" {r['pages']}p" if r["pages"] else ""
            print(f"  [{r['doc_type']}]{pages}  {r['descriptive_name']}")
            print(f"        {r['current_path']}")
        return

    if args.duplicates:
        rows = catalog.duplicates()
        if not rows:
            print("No byte-identical duplicate copies recorded.")
        for r in rows:
            print(f"  {r['document']}")
            print(f"    keep: {r['canonical']}")
            print(f"    dup:  {r['duplicate']}")
        return

    if args.possible_duplicates:
        groups = catalog.possible_duplicates()
        if not groups:
            print("No same-title near-duplicates found.")
        for g in groups:
            print(f"  ~ {', '.join(g['documents'])}")
            for p in g["paths"]:
                print(f"      {p}")
        return

    if args.plan_renames:
        plan = catalog.plan_renames()
        print(f"{len(plan)} file(s) would be renamed:")
        for item in plan:
            print(f"  {Path(item['from']).name}")
            print(f"    -> {Path(item['to']).name}")
        return

    if args.apply_renames:
        done = catalog.apply_renames()
        print(f"Renamed {len(done)} file(s). Revert with --undo-renames.")
        for item in done:
            print(f"  {Path(item['from']).name} -> {Path(item['to']).name}")
        return

    if args.undo_renames:
        reverted = catalog.undo_renames()
        print(f"Reverted {len(reverted)} rename(s).")
        return

    if args.delete_duplicate_files:
        if not args.yes:
            print("Refusing to delete files without --yes. This permanently "
                  "removes the redundant copies (the catalogued canonical file "
                  "is kept). Re-run with --delete-duplicate-files --yes.")
            return
        deleted = catalog.delete_duplicate_files()
        print(f"Deleted {len(deleted)} duplicate file(s).")
        return

    # default: scan
    _print_scan(catalog.scan(root=args.school, rehash=args.full))


if __name__ == "__main__":
    main()
