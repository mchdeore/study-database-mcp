"""Ingestion pipeline: raw -> markdown -> chunks -> embeddings -> vector store.

Two incremental phases, both gated by content hash in the manifest:

  1. convert  raw/<f>.pdf|.md  ->  corpus/<f>.md   (gated by raw-file hash)
               Scanned PDFs (no text layer) are OCR'd first if ocrmypdf exists.
               After conversion you can hand-fix garbled equations in corpus/.
  2. index     corpus/<f>.md   ->  chunk -> embed -> upsert  (gated by corpus hash)
               Editing a corpus file re-indexes only that file on next run.

The corpus-hash gate means manual equation fixes are picked up without
re-running the (lossy) PDF conversion, and a fresh note touches exactly one file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .chunk import chunk_markdown
from .store import (
    Manifest,
    config,
    file_hash,
    get_embedder,
    get_store,
    paths,
)

_RAW_EXTS = {".pdf", ".md", ".markdown", ".txt"}


# --- raw -> markdown -------------------------------------------------------
def _needs_ocr(pdf_path: Path) -> bool:
    """True if the PDF has essentially no extractable text layer (scanned)."""
    try:
        import fitz  # PyMuPDF, ships with pymupdf4llm
    except Exception:  # noqa: BLE001
        return False
    try:
        doc = fitz.open(pdf_path)
        chars = sum(len(page.get_text("text")) for page in doc)
        pages = doc.page_count
        doc.close()
        # Heuristic: a real text layer has well over ~20 chars/page.
        return chars < max(100, 20 * pages)
    except Exception:  # noqa: BLE001
        return False


def _run_ocr(pdf_path: Path) -> None:
    """OCR a scanned PDF in place if ocrmypdf is installed; otherwise no-op."""
    if shutil.which("ocrmypdf") is None:
        print(f"  ! {pdf_path.name} looks scanned but ocrmypdf is not installed; "
              "converting without OCR (text may be empty).")
        return
    print(f"  · OCR {pdf_path.name} ...")
    subprocess.run(
        ["ocrmypdf", "--skip-text", str(pdf_path), str(pdf_path)],
        check=False, capture_output=True,
    )


def _pdf_to_markdown(pdf_path: Path) -> str:
    """Convert a PDF to Markdown using the configured converter.

    pymupdf4llm preserves headings/math reasonably and gives per-page output,
    which we tag with `<!-- page: N -->` markers so chunks can cite a page.
    """
    converter = config()["pdf_converter"]
    if _needs_ocr(pdf_path):
        _run_ocr(pdf_path)

    if converter == "marker":
        try:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict
            from marker.output import text_from_rendered

            conv = PdfConverter(artifact_dict=create_model_dict())
            return text_from_rendered(conv(str(pdf_path)))[0]
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"marker conversion failed ({e}); try PDF_CONVERTER=pymupdf4llm")

    # default: pymupdf4llm
    try:
        import pymupdf4llm
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "pymupdf4llm not installed. Install the 'pdf-pymupdf' extra to ingest PDFs."
        ) from e
    pages = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
    parts: List[str] = []
    for i, page in enumerate(pages, start=1):
        text = page["text"] if isinstance(page, dict) else str(page)
        parts.append(f"<!-- page: {i} -->\n{text}")
    return "\n\n".join(parts)


def convert_to_markdown(raw_path: Path) -> str:
    suffix = raw_path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return raw_path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        return _pdf_to_markdown(raw_path)
    raise ValueError(f"unsupported file type: {raw_path.name}")


def _corpus_rel(raw_rel: Path) -> str:
    """Map a raw relative path to its corpus markdown relative path (.md)."""
    return str(raw_rel.with_suffix(".md").as_posix())


# --- orchestration ---------------------------------------------------------
def ingest(incremental: bool = True, rebuild_graph: bool = True) -> Dict:
    """Run the full pipeline. Returns a report of what changed.

    Args:
        incremental: skip files whose content hash is unchanged (default).
                     False forces every file through both phases.
        rebuild_graph: rebuild the concept graph after indexing (default True).
    """
    p = paths()
    p["corpus"].mkdir(parents=True, exist_ok=True)
    p["raw"].mkdir(parents=True, exist_ok=True)
    manifest = Manifest(p["manifest"])
    report = {"converted": [], "indexed": [], "skipped": [], "removed": [], "chunks": 0, "errors": []}

    # Phase 1: raw -> corpus (gated by raw hash)
    raw_files = [
        f for f in sorted(p["raw"].rglob("*"))
        if f.is_file() and f.suffix.lower() in _RAW_EXTS and f.name != "README.md"
    ]
    for raw in raw_files:
        rel = raw.relative_to(p["raw"])
        key = f"raw:{rel.as_posix()}"
        digest = file_hash(raw)
        if incremental and not manifest.is_changed(key, digest):
            continue
        try:
            md = convert_to_markdown(raw)
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"{rel}: {e}")
            continue
        corpus_rel = _corpus_rel(rel)
        corpus_path = p["corpus"] / corpus_rel
        corpus_path.parent.mkdir(parents=True, exist_ok=True)
        corpus_path.write_text(md, encoding="utf-8")
        manifest.update(key, digest, corpus=corpus_rel)
        report["converted"].append(corpus_rel)

    # Phase 2: corpus -> chunks -> embeddings (gated by corpus hash)
    cfg = config()
    corpus_files = [f for f in sorted(p["corpus"].rglob("*.md")) if f.is_file()]
    present_sources = {str(f.relative_to(p["corpus"]).as_posix()) for f in corpus_files}

    to_index: List[Path] = []
    for cf in corpus_files:
        source = str(cf.relative_to(p["corpus"]).as_posix())
        key = f"corpus:{source}"
        digest = file_hash(cf)
        if incremental and not manifest.is_changed(key, digest):
            report["skipped"].append(source)
            continue
        to_index.append(cf)

    # Phases 2 & 3 share ONE store instance (avoid reloading the pickle twice).
    store = get_store()
    dirty = False
    if to_index:
        embedder = get_embedder()
        for cf in to_index:
            source = str(cf.relative_to(p["corpus"]).as_posix())
            md = cf.read_text(encoding="utf-8", errors="replace")
            chunks = chunk_markdown(
                md, source,
                target_tokens=cfg["chunk_target_tokens"],
                overlap_ratio=cfg["chunk_overlap_ratio"],
            )
            store.delete_source(source)  # clean replace by source
            if chunks:
                vectors = embedder.embed([c.text for c in chunks])
                store.upsert(chunks, vectors)
            manifest.update(f"corpus:{source}", file_hash(cf), chunks=len(chunks))
            report["indexed"].append(source)
            report["chunks"] += len(chunks)
        dirty = True

    # Phase 3: drop sources whose corpus file was deleted (reuse same store).
    for source in list(store.sources()):
        if source not in present_sources:
            store.delete_source(source)
            manifest.remove(f"corpus:{source}")
            report["removed"].append(source)
            dirty = True

    if dirty:
        store.save()

    manifest.save()

    if rebuild_graph:
        try:
            from .graph import build_graph

            build_graph()
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"graph build: {e}")

    return report
