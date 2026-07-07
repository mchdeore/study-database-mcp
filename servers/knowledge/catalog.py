"""File catalog for the SCHOOL folder: one SQLite row per unique document.

Why SQLite (and not Postgres-in-Docker): this is a single-user index of a few
hundred files on one laptop. `sqlite3` ships with Python -- no daemon, no
container, no credentials -- and answers "what do I have for MATH225" in well
under a millisecond at this scale. A networked DB would add operations with no
benefit here. The file lives at data/catalog.db and opens in any SQL tool.

What a scan does (incremental, safe to re-run):
  walk SCHOOL  ->  skip env/tooling dirs (.venv, .git, __pycache__, ...) and
                   non-document files (only the configured extensions count)
  ->  a (size, mtime) gate skips already-known unchanged files without hashing
  ->  sha256 of the bytes is the identity:
        * new hash            -> new entry (title + type extracted, descriptive
                                  name built)
        * hash already known at a *different* path -> recorded as a duplicate
                                  copy (the catalog keeps ONE entry per document)
        * known path, changed bytes -> the entry is refreshed in place
  ->  entries whose file has vanished are flagged `missing` (not deleted, so an
      unmounted drive doesn't wipe the catalog).

ponytail: dedup is exact-content (sha256), which only catches byte-identical
copies. Two different scans/editions of the same textbook hash differently and
are surfaced by `possible_duplicates()` (normalized-title match) for you to judge
-- never auto-removed. Upgrade path: text/perceptual hashing.
ponytail: the (size, mtime) gate trusts the filesystem clock; pass rehash=True
(CLI --full) to force a byte re-hash of everything.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .store import config, file_hash, paths

# Directories that never contain study documents -- skip wholesale so we don't
# walk a virtualenv's thousands of site-packages files.
_EXCLUDE_DIRS = {
    ".venv", "venv", "env", ".git", "__pycache__", "node_modules", ".vscode",
    ".cursor", ".idea", ".ipynb_checkpoints", ".pytest_cache", ".mypy_cache",
    "site-packages", ".DS_Store",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- schema ----------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash      TEXT NOT NULL UNIQUE,
    course            TEXT NOT NULL,
    subpath           TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL,
    current_path      TEXT NOT NULL,
    descriptive_name  TEXT NOT NULL,
    doc_type          TEXT NOT NULL DEFAULT 'other',
    title             TEXT,
    extension         TEXT NOT NULL,
    size_bytes        INTEGER NOT NULL,
    pages             INTEGER,
    mtime             REAL NOT NULL,
    cataloged_at      TEXT NOT NULL,
    renamed           INTEGER NOT NULL DEFAULT 0,
    missing           INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS duplicate_paths (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    found_at    TEXT NOT NULL,
    UNIQUE(document_id, path)
);
CREATE INDEX IF NOT EXISTS idx_documents_course ON documents(course);
CREATE INDEX IF NOT EXISTS idx_documents_type   ON documents(doc_type);
"""


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else paths()["catalog"]
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


# --- walking & extraction --------------------------------------------------
def _doc_exts() -> set:
    raw = config()["catalog_doc_exts"]
    return {e if e.startswith(".") else "." + e
            for e in (s.strip().lower() for s in raw.split(",")) if e}


def _iter_documents(root: Path, exts: set):
    """Yield document files under root, skipping junk dirs and hidden files."""
    for dirpath, dirnames, filenames in os.walk(root):
        # prune in place so os.walk never descends into excluded dirs
        dirnames[:] = [d for d in dirnames
                       if d not in _EXCLUDE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            if Path(fn).suffix.lower() in exts:
                yield Path(dirpath) / fn


def _good_title(t: str) -> bool:
    t = (t or "").strip()
    if not (4 <= len(t) <= 140):
        return False
    low = t.lower()
    if low in {"untitled", "title"} or low.startswith("microsoft word"):
        return False
    return any(c.isalpha() for c in t)


def _first_text_line(text: str) -> Optional[str]:
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) >= 4 and len(line) <= 140 and len(line.split()) >= 2 \
                and any(c.isalpha() for c in line):
            return line
    return None


def _pdf_fields(path: Path) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """Return (page_count, metadata_title, first_page_line) for a PDF.

    Kept separate so the caller can prefer a clean filename over noisy first-page
    text. Degrades to (None, None, None) if PyMuPDF isn't installed or the file
    is unreadable -- the catalog still works, it just leans on the filename.
    """
    try:
        import fitz  # PyMuPDF (ships with the pdf-pymupdf extra)
    except Exception:  # noqa: BLE001
        return None, None, None
    try:
        doc = fitz.open(path)
        pages = doc.page_count
        meta = ((doc.metadata or {}).get("title") or "").strip()
        meta = meta if _good_title(meta) else None
        first = _first_text_line(doc[0].get_text("text")) if pages else None
        doc.close()
        return pages, meta, first
    except Exception:  # noqa: BLE001
        return None, None, None


_JUNK_PATTERNS = [
    re.compile(r"_?oceanofpdf\.com_?", re.IGNORECASE),
    re.compile(r"drive-download[-0-9tz]*", re.IGNORECASE),
    re.compile(r"_?shortdoi_?\w*", re.IGNORECASE),
]


def _clean(stem: str) -> str:
    """Turn a raw filename stem into a human label."""
    s = stem
    for pat in _JUNK_PATTERNS:
        s = pat.sub(" ", s)
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip(" -_")
    return s or stem


def _is_descriptive(s: str) -> bool:
    """True if a cleaned filename stem already reads as a real title.

    Filters cryptic names like '0321972112' or 'griffiths_4ed' (mostly digits /
    too few words) so those fall through to the extracted PDF title instead.
    """
    tokens = [t for t in s.split() if any(c.isalpha() for c in t)]
    nonspace = sum(1 for c in s if not c.isspace())
    alpha = sum(1 for c in s if c.isalpha())
    return len(s) >= 8 and len(tokens) >= 2 and nonspace > 0 and alpha / nonspace >= 0.5


def _doc_type(name: str, size: int, pages: Optional[int]) -> str:
    """Heuristic document class from filename/title, size, and page count.

    ponytail: keyword heuristic, good enough to bucket a personal corpus. It is
    only a label/filter -- it never gates ingestion or deletes anything.
    """
    # split camelCase ("PracticeMidterm") and letter/digit runs ("Assignment01")
    # so \bword\b keyword matching fires -- without this, boundaries never hit.
    n = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    n = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", n).lower()
    if "solution" in n:
        return "solutions"
    if re.search(r"\b(midterm|final|exam|test|quiz)\b", n):
        return "exam"
    if "formula" in n:
        return "formula-sheet"
    if re.search(r"\b(manual|guide|programmers?)\b", n):
        return "manual"
    if re.search(r"\b(homework|assignment|worksheet|workbook|lab|hw)\b", n):
        return "assignment"
    if pages and pages >= 150 and size > 5_000_000:
        return "textbook"
    if re.match(r"^\s*\d+[.\-_]?\d*\s+\S", name) or \
            re.search(r"\b(lecture|slides?|chapter|ch)\b", n):
        return "slides"
    return "notes"


def _course_subpath(path: Path, root: Path) -> Tuple[str, str]:
    parts = path.relative_to(root).parts
    course = parts[0] if len(parts) > 1 else "_UNFILED"
    subpath = str(Path(*parts[1:-1])) if len(parts) > 2 else ""
    return course, subpath


def _descriptive(course: str, base: str, doc_type: str) -> str:
    return f"{course} \u2014 {base} [{doc_type}]"


def _safe_filename(course: str, base: str, doc_type: str, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-")[:80] or "untitled"
    return f"{course}_{slug}_{doc_type}{ext}"


def describe(path: Path, root: Path, size: int) -> Dict:
    """Derive title, page count, course/subpath, type, and descriptive name.

    Naming priority (favours human-curated filenames, rescues cryptic ones):
      1. the cleaned filename stem, if it already reads as a title
      2. else the PDF's embedded metadata title
      3. else the first meaningful line of page 1
      4. else the cleaned stem anyway
    """
    pages = meta = first = None
    if path.suffix.lower() == ".pdf":
        pages, meta, first = _pdf_fields(path)
    clean = _clean(path.stem)
    if _is_descriptive(clean):
        base = clean
    elif meta:
        base = meta
    elif first and _good_title(first):
        base = first
    else:
        base = clean or path.stem
    title = meta or first  # best extracted title, kept for search
    course, subpath = _course_subpath(path, root)
    doc_type = _doc_type(f"{path.name} {title or ''}", size, pages)
    return {
        "title": title,
        "pages": pages,
        "course": course,
        "subpath": subpath,
        "doc_type": doc_type,
        "descriptive_name": _descriptive(course, base, doc_type),
    }


def _base_from_descriptive(course: str, doc_type: str, descriptive_name: str) -> str:
    """Recover the title portion of a descriptive name (drives renames)."""
    base = descriptive_name
    prefix = f"{course} \u2014 "
    if base.startswith(prefix):
        base = base[len(prefix):]
    suffix = f" [{doc_type}]"
    if base.endswith(suffix):
        base = base[:-len(suffix)]
    return base


# --- scan ------------------------------------------------------------------
def scan(root: Optional[Path] = None, db_path: Optional[Path] = None,
         rehash: bool = False) -> Dict:
    """Incrementally catalog every document under `root` (default SCHOOL_DIR).

    Returns a report: {root, new[], duplicates[], updated, skipped, missing[],
    errors[]}. Safe and idempotent -- re-running only touches changed files.
    """
    cfg = config()
    root = Path(root).expanduser() if root else Path(cfg["school_dir"]).expanduser()
    report: Dict = {"root": str(root), "new": [], "duplicates": [],
                    "updated": 0, "skipped": 0, "missing": [], "errors": []}
    if not root.exists():
        report["error"] = f"SCHOOL folder not found: {root}"
        report["hint"] = "set SCHOOL_DIR in .env or pass --school PATH"
        return report

    exts = _doc_exts()
    conn = _connect(db_path)
    rows = list(conn.execute("SELECT * FROM documents"))
    by_path = {r["current_path"]: r for r in rows}
    by_hash = {r["content_hash"]: dict(r) for r in rows}
    seen_paths: set = set()

    for path in _iter_documents(root, exts):
        sp = str(path)
        seen_paths.add(sp)
        try:
            st = path.stat()
        except OSError as e:  # noqa: BLE001
            report["errors"].append(f"{path}: {e}")
            continue

        prior = by_path.get(sp)
        # fast gate: known path, unchanged size+mtime -> skip without hashing
        if prior and not rehash and not prior["missing"] \
                and prior["size_bytes"] == st.st_size \
                and abs(prior["mtime"] - st.st_mtime) < 1e-6:
            report["skipped"] += 1
            continue

        try:
            digest = file_hash(path)
        except OSError as e:  # noqa: BLE001
            report["errors"].append(f"{path}: {e}")
            continue

        # known path whose content changed -> refresh that entry in place
        if prior and prior["content_hash"] != digest:
            info = describe(path, root, st.st_size)
            try:
                conn.execute(
                    "UPDATE documents SET content_hash=?, descriptive_name=?, "
                    "doc_type=?, title=?, size_bytes=?, pages=?, mtime=?, "
                    "missing=0 WHERE id=?",
                    (digest, info["descriptive_name"], info["doc_type"],
                     info["title"], st.st_size, info["pages"], st.st_mtime,
                     prior["id"]),
                )
                report["updated"] += 1
            except sqlite3.IntegrityError:
                # edited file now byte-identical to another doc -> it's a dup
                holder = by_hash.get(digest)
                if holder:
                    conn.execute(
                        "INSERT OR IGNORE INTO duplicate_paths"
                        "(document_id, path, size_bytes, found_at) VALUES (?,?,?,?)",
                        (holder["id"], sp, st.st_size, _now()))
                    conn.execute("DELETE FROM documents WHERE id=?", (prior["id"],))
                    report["duplicates"].append(sp)
            continue

        existing = by_hash.get(digest)
        if existing:
            if existing["current_path"] == sp:
                if rehash:
                    # --full: re-derive names/types (e.g. after a logic change)
                    info = describe(path, root, st.st_size)
                    conn.execute(
                        "UPDATE documents SET descriptive_name=?, doc_type=?, "
                        "title=?, pages=?, mtime=?, size_bytes=?, missing=0 "
                        "WHERE id=?",
                        (info["descriptive_name"], info["doc_type"],
                         info["title"], info["pages"], st.st_mtime, st.st_size,
                         existing["id"]))
                else:
                    conn.execute(
                        "UPDATE documents SET mtime=?, size_bytes=?, missing=0 "
                        "WHERE id=?",
                        (st.st_mtime, st.st_size, existing["id"]))
                report["updated"] += 1
            elif Path(existing["current_path"]).exists():
                # a genuine second copy of the same bytes
                conn.execute(
                    "INSERT OR IGNORE INTO duplicate_paths"
                    "(document_id, path, size_bytes, found_at) VALUES (?,?,?,?)",
                    (existing["id"], sp, st.st_size, _now()))
                report["duplicates"].append(sp)
            else:
                # the canonical copy moved here
                conn.execute(
                    "UPDATE documents SET current_path=?, mtime=?, missing=0 WHERE id=?",
                    (sp, st.st_mtime, existing["id"]))
                existing["current_path"] = sp
                report["updated"] += 1
            continue

        # brand-new document
        info = describe(path, root, st.st_size)
        cur = conn.execute(
            "INSERT INTO documents(content_hash, course, subpath, "
            "original_filename, current_path, descriptive_name, doc_type, title, "
            "extension, size_bytes, pages, mtime, cataloged_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (digest, info["course"], info["subpath"], path.name, sp,
             info["descriptive_name"], info["doc_type"], info["title"],
             path.suffix.lower(), st.st_size, info["pages"], st.st_mtime, _now()))
        new_row = dict(conn.execute("SELECT * FROM documents WHERE id=?",
                                    (cur.lastrowid,)).fetchone())
        by_hash[digest] = new_row
        by_path[sp] = new_row
        report["new"].append(info["descriptive_name"])

    # flag entries whose file is gone (not seen this run AND not on disk)
    for r in conn.execute("SELECT id, current_path, descriptive_name "
                          "FROM documents WHERE missing=0"):
        if r["current_path"] not in seen_paths and not Path(r["current_path"]).exists():
            conn.execute("UPDATE documents SET missing=1 WHERE id=?", (r["id"],))
            report["missing"].append(r["descriptive_name"])

    conn.commit()
    conn.close()
    return report


# --- queries (used by the CLI and the MCP server) --------------------------
def list_courses(db_path: Optional[Path] = None) -> List[Dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT course, COUNT(*) AS documents, "
        "SUM(missing) AS missing FROM documents GROUP BY course ORDER BY course")
    out = [{"course": r["course"], "documents": r["documents"],
            "missing": r["missing"] or 0} for r in rows]
    conn.close()
    return out


def find_documents(query: str, course: Optional[str] = None, limit: int = 20,
                   db_path: Optional[Path] = None) -> List[Dict]:
    """Substring search over descriptive name / title / filename / path."""
    conn = _connect(db_path)
    like = f"%{query.strip()}%"
    sql = ("SELECT course, descriptive_name, doc_type, pages, title, "
           "current_path FROM documents WHERE missing=0 AND ("
           "descriptive_name LIKE ? OR title LIKE ? OR original_filename LIKE ? "
           "OR current_path LIKE ?)")
    params: List = [like, like, like, like]
    if course:
        sql += " AND course = ?"
        params.append(course)
    sql += " ORDER BY course, doc_type LIMIT ?"
    params.append(int(limit))
    out = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return out


def stats(db_path: Optional[Path] = None) -> Dict:
    conn = _connect(db_path)
    total = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    missing = conn.execute(
        "SELECT COUNT(*) c FROM documents WHERE missing=1").fetchone()["c"]
    dups = conn.execute("SELECT COUNT(*) c FROM duplicate_paths").fetchone()["c"]
    by_type = [dict(r) for r in conn.execute(
        "SELECT doc_type, COUNT(*) AS n FROM documents WHERE missing=0 "
        "GROUP BY doc_type ORDER BY n DESC")]
    by_course = [dict(r) for r in conn.execute(
        "SELECT course, COUNT(*) AS n FROM documents WHERE missing=0 "
        "GROUP BY course ORDER BY course")]
    conn.close()
    return {"total_documents": total, "duplicate_copies": dups,
            "missing": missing, "by_type": by_type, "by_course": by_course}


def duplicates(db_path: Optional[Path] = None) -> List[Dict]:
    conn = _connect(db_path)
    out = [dict(r) for r in conn.execute(
        "SELECT d.descriptive_name AS document, d.current_path AS canonical, "
        "dp.path AS duplicate, dp.size_bytes FROM duplicate_paths dp "
        "JOIN documents d ON d.id = dp.document_id "
        "ORDER BY d.descriptive_name")]
    conn.close()
    return out


def possible_duplicates(db_path: Optional[Path] = None) -> List[Dict]:
    """Near-duplicate documents: same normalized title but different bytes.

    e.g. two scans/editions of the same textbook. Reported for review only.
    """
    conn = _connect(db_path)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, course, descriptive_name, title, original_filename, "
        "current_path FROM documents WHERE missing=0")]
    conn.close()

    def norm(r):
        base = (r["title"] or _clean(Path(r["original_filename"]).stem)).lower()
        return re.sub(r"[^a-z0-9]+", "", base)

    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        key = norm(r)
        if len(key) >= 6:
            groups.setdefault(key, []).append(r)
    return [{"normalized": k, "documents": [g["descriptive_name"] for g in v],
             "paths": [g["current_path"] for g in v]}
            for k, v in groups.items() if len(v) > 1]


def list_documents(course: Optional[str] = None,
                   db_path: Optional[Path] = None) -> List[Dict]:
    conn = _connect(db_path)
    sql = ("SELECT course, descriptive_name, doc_type, pages, current_path "
           "FROM documents WHERE missing=0")
    params: List = []
    if course:
        sql += " AND course = ?"
        params.append(course)
    sql += " ORDER BY course, doc_type, descriptive_name"
    out = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return out


# --- renaming (opt-in, reversible) -----------------------------------------
def plan_renames(db_path: Optional[Path] = None) -> List[Dict]:
    """Compute descriptive on-disk filenames without touching any file."""
    conn = _connect(db_path)
    plan = []
    for r in conn.execute("SELECT * FROM documents WHERE missing=0"):
        src = Path(r["current_path"])
        if not src.exists():
            continue
        base = _base_from_descriptive(r["course"], r["doc_type"],
                                      r["descriptive_name"])
        target = _safe_filename(r["course"], base, r["doc_type"], r["extension"])
        if target == src.name:
            continue
        plan.append({"id": r["id"], "from": str(src),
                     "to": str(src.with_name(target))})
    conn.close()
    return plan


def apply_renames(db_path: Optional[Path] = None,
                  log_path: Optional[Path] = None) -> List[Dict]:
    """Rename files on disk to their descriptive names. Reversible via undo.

    Each rename is recorded to the rename log so `undo_renames` can revert the
    whole batch. Collisions get a numeric suffix; files are only renamed within
    their own folder.
    """
    plan = plan_renames(db_path)
    conn = _connect(db_path)
    done: List[Dict] = []
    for item in plan:
        src = Path(item["from"])
        if not src.exists():
            continue
        dst = Path(item["to"])
        final, i = dst, 1
        while final.exists() and str(final) != str(src):
            final = dst.with_stem(f"{dst.stem}-{i}")
            i += 1
        try:
            os.rename(src, final)
        except OSError:
            continue
        conn.execute("UPDATE documents SET current_path=?, renamed=1 WHERE id=?",
                     (str(final), item["id"]))
        done.append({"id": item["id"], "from": str(src), "to": str(final)})
    conn.commit()
    conn.close()
    if done:
        _append_rename_log(done, log_path)
    return done


def undo_renames(db_path: Optional[Path] = None,
                 log_path: Optional[Path] = None) -> List[Dict]:
    """Revert the most recent rename batch."""
    import json

    lp = Path(log_path) if log_path else paths()["rename_log"]
    if not lp.exists():
        return []
    batches = json.loads(lp.read_text())
    if not batches:
        return []
    batch = batches.pop()
    conn = _connect(db_path)
    reverted: List[Dict] = []
    for item in batch["items"]:
        cur = Path(item["to"])
        orig = Path(item["from"])
        if cur.exists() and not orig.exists():
            try:
                os.rename(cur, orig)
            except OSError:
                continue
            conn.execute(
                "UPDATE documents SET current_path=?, renamed=0 WHERE id=?",
                (str(orig), item["id"]))
            reverted.append({"from": str(cur), "to": str(orig)})
    conn.commit()
    conn.close()
    lp.write_text(json.dumps(batches, indent=2))
    return reverted


def _append_rename_log(done: List[Dict], log_path: Optional[Path]) -> None:
    import json

    lp = Path(log_path) if log_path else paths()["rename_log"]
    lp.parent.mkdir(parents=True, exist_ok=True)
    batches = json.loads(lp.read_text()) if lp.exists() else []
    batches.append({"ts": _now(), "items": done})
    lp.write_text(json.dumps(batches, indent=2))


def delete_duplicate_files(db_path: Optional[Path] = None) -> List[str]:
    """Delete the redundant on-disk copies (NOT the canonical entry).

    Destructive and opt-in only (CLI requires --yes). Removes files recorded in
    duplicate_paths and clears those rows; the catalogued canonical file is never
    touched.
    """
    conn = _connect(db_path)
    deleted: List[str] = []
    for r in conn.execute("SELECT id, path FROM duplicate_paths"):
        p = Path(r["path"])
        try:
            if p.exists():
                p.unlink()
            deleted.append(str(p))
            conn.execute("DELETE FROM duplicate_paths WHERE id=?", (r["id"],))
        except OSError:
            continue
    conn.commit()
    conn.close()
    return deleted
