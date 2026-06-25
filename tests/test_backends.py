"""Backend store contract test: NumpyStore / LanceStore / ChromaStore.

Run: python tests/test_backends.py

Exercises every store backend through the SAME contract the ingest/retrieve code
relies on -- upsert, query (cosine ranking), delete_source, sources, all_records,
persistence across instances -- so the LanceDB and Chroma paths (otherwise only
hit when a user sets VECTOR_STORE) are actually verified.

Backends that aren't installed are skipped with a notice rather than failing.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from servers.knowledge.chunk import Chunk  # noqa: E402
from servers.knowledge import store as STORE  # noqa: E402

_N = 0


def ok(cond, msg):
    global _N
    _N += 1
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def _chunks(source, texts):
    return [
        Chunk(text=t, source=source, heading_path=f"{source} > S{i}",
              chunk_id=f"{source}#{i}", page=i + 1, token_estimate=len(t) // 4,
              headings=[source, f"S{i}"])
        for i, t in enumerate(texts)
    ]


def _unit(vs):
    vs = np.asarray(vs, dtype=np.float32)
    n = np.linalg.norm(vs, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return vs / n


def run_contract(make_store, name, root):
    print(f"\n[{name}]")
    # Deterministic 4-d unit vectors so cosine ranking is predictable.
    a = _chunks("a.md", ["apple apple", "banana"])
    b = _chunks("b.md", ["cherry"])
    va = _unit([[1, 0, 0, 0], [0, 1, 0, 0]])
    vb = _unit([[0, 0, 1, 0]])

    s = make_store(root)
    s.upsert(a, va)
    s.upsert(b, vb)
    s.save()

    ok(set(s.sources()) == {"a.md", "b.md"}, f"{name}: sources after upsert")
    ok(len(s.all_records()) == 3, f"{name}: all_records count")

    # Query nearest to [1,0,0,0] -> a.md#0
    hits = s.query(_unit([[1, 0, 0, 0]])[0], k=3)
    ok(hits and hits[0].chunk_id == "a.md#0", f"{name}: top hit is the nearest vector")
    ok(hits[0].source == "a.md" and hits[0].heading_path.startswith("a.md"), f"{name}: hit carries citation metadata")
    ok(len(hits) <= 3, f"{name}: respects k limit")

    # delete_source removes only that source
    s.delete_source("a.md")
    s.save()
    ok(set(s.sources()) == {"b.md"}, f"{name}: delete_source removes only target")
    ok(len(s.all_records()) == 1, f"{name}: records updated after delete")

    # persistence: a fresh instance sees the saved state
    s2 = make_store(root)
    ok(set(s2.sources()) == {"b.md"}, f"{name}: persists across instances")
    hits2 = s2.query(_unit([[0, 0, 1, 0]])[0], k=1)
    ok(hits2 and hits2[0].chunk_id == "b.md#0", f"{name}: query works after reload")

    # delete a non-existent source is a no-op (must not raise)
    s2.delete_source("ghost.md")
    ok(set(s2.sources()) == {"b.md"}, f"{name}: deleting unknown source is a safe no-op")


# --- NumpyStore (always available) -----------------------------------------
run_contract(STORE.NumpyStore, "NumpyStore", Path(tempfile.mkdtemp(prefix="np_")))

# --- LanceStore -------------------------------------------------------------
import importlib.util as _ilu  # noqa: E402

if _ilu.find_spec("lancedb"):
    run_contract(STORE.LanceStore, "LanceStore", Path(tempfile.mkdtemp(prefix="lance_")))
else:
    print("\n[LanceStore] skipped (lancedb not installed)")

# --- ChromaStore ------------------------------------------------------------
if _ilu.find_spec("chromadb"):
    run_contract(STORE.ChromaStore, "ChromaStore", Path(tempfile.mkdtemp(prefix="chroma_")))
else:
    print("\n[ChromaStore] skipped (chromadb not installed)")

print(f"\nALL BACKEND CONTRACT CHECKS PASSED ({_N} assertions)")


# ===========================================================================
# End-to-end: the full ingest -> search pipeline against each backend.
def run_pipeline(backend):
    print(f"\n[pipeline: VECTOR_STORE={backend}]")
    d = tempfile.mkdtemp(prefix=f"pipe_{backend}_")
    os.environ["DATA_DIR"] = d
    os.environ["VECTOR_STORE"] = backend
    os.environ["EMBEDDING_PROVIDER"] = "hash"
    # import lazily so each call re-reads env via config()
    from servers.knowledge import ingest as ING
    from servers.knowledge import retrieve as RET

    raw = Path(d) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "phys.md").write_text(
        "# Physics\n\n## Ohm's Law\n\nVoltage equals current times resistance: V = I R.\n"
        "\n## Power\n\nElectrical power P = V I.\n", encoding="utf-8")
    rep = ING.ingest(incremental=True)
    ok("phys.md" in rep["indexed"], f"{backend}: ingest indexed the note")
    res = RET.search("Ohm law voltage resistance", k=3)
    ok(res["results"] and res["results"][0]["citation"]["source"] == "phys.md",
       f"{backend}: search returns cited result")
    # incremental no-op
    rep2 = ING.ingest(incremental=True)
    ok(rep2["indexed"] == [] and "phys.md" in rep2["skipped"], f"{backend}: incremental skips unchanged")


run_pipeline("numpy")
if _ilu.find_spec("lancedb"):
    run_pipeline("lancedb")
if _ilu.find_spec("chromadb"):
    run_pipeline("chromadb")

print(f"\nALL BACKEND PIPELINE CHECKS PASSED (total {_N} assertions)")
