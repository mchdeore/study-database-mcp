"""PDF ingestion end-to-end test (requires pymupdf4llm).

Run: python tests/test_pdf.py

Generates a real multi-page text PDF with PyMuPDF, then drives the full ingest
pipeline: PDF -> Markdown (with <!-- page: N --> markers) -> chunks (page
metadata) -> embeddings -> search. Verifies the scanned-PDF detector too.

Skipped cleanly if pymupdf4llm isn't installed.
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if not importlib.util.find_spec("pymupdf4llm"):
    print("[pdf] skipped (pymupdf4llm not installed; install the pdf-pymupdf extra)")
    sys.exit(0)

_TMP = tempfile.mkdtemp(prefix="study_pdf_")
os.environ["DATA_DIR"] = _TMP
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VECTOR_STORE"] = "numpy"
os.environ["PDF_CONVERTER"] = "pymupdf4llm"

from servers.knowledge import ingest as ING  # noqa: E402
from servers.knowledge import retrieve as RET  # noqa: E402
from servers.knowledge.store import NumpyStore, paths  # noqa: E402

_N = 0


def ok(cond, msg):
    global _N
    _N += 1
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)
    print(f"  ok: {msg}")


# --- build a 3-page text PDF -----------------------------------------------
import fitz  # PyMuPDF, ships with pymupdf4llm  # noqa: E402

raw = paths()["raw"]
raw.mkdir(parents=True, exist_ok=True)
pdf_path = raw / "lecture.pdf"
doc = fitz.open()
topics = ["Kinematics and velocity", "Newton's laws of motion", "Conservation of energy"]
for i, topic in enumerate(topics, start=1):
    page = doc.new_page()
    page.insert_text((72, 72), f"Chapter {i}: {topic}", fontsize=18)
    page.insert_text((72, 110), f"This page {i} discusses {topic} in detail with worked examples.",
                     fontsize=11)
doc.save(str(pdf_path))
doc.close()

print("[pdf conversion]")
# scanned-PDF detector: this PDF has a real text layer -> no OCR needed
ok(ING._needs_ocr(pdf_path) is False, "text PDF correctly detected as NOT needing OCR")

md = ING.convert_to_markdown(pdf_path)
ok("<!-- page: 1 -->" in md and "<!-- page: 3 -->" in md, "per-page markers injected")
ok("Kinematics" in md and "energy" in md.lower(), "PDF text extracted into Markdown")

print("\n[pdf ingest pipeline]")
rep = ING.ingest(incremental=True)
ok("lecture.md" in rep["converted"], "PDF converted to corpus Markdown")
ok("lecture.md" in rep["indexed"], "converted Markdown indexed")
ok((paths()["corpus"] / "lecture.md").exists(), "corpus/lecture.md written")

# chunks carry page metadata derived from the markers
recs = NumpyStore(paths()["vector_store"]).all_records()
pages = {r.get("page") for r in recs}
ok(any(p is not None for p in pages), f"chunks carry page metadata: {sorted(p for p in pages if p)}")

# search finds page-specific content with a citation
res = RET.search("conservation of energy", k=5)
ok(res["results"] and res["results"][0]["citation"]["source"] == "lecture.md", "search cites the PDF source")

print("\n[pdf incremental]")
rep2 = ING.ingest(incremental=True)
ok(rep2["converted"] == [] and "lecture.md" in rep2["skipped"], "unchanged PDF is skipped (raw + corpus hashes)")

print(f"\nALL PDF CHECKS PASSED ({_N} assertions)")
