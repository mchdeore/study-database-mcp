"""Scale / performance test (offline).

Run: python tests/test_perf.py

Indexes a moderate synthetic corpus and asserts the interactive operations stay
fast: ingest throughput, vector search latency, concept-graph build, and a
no-op incremental reindex. Thresholds are generous to avoid flakiness but tight
enough to catch an accidental O(n^2) regression in a hot path.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="study_perf_")
os.environ["DATA_DIR"] = _TMP
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VECTOR_STORE"] = "numpy"

from servers.knowledge import ingest as ING  # noqa: E402
from servers.knowledge import retrieve as RET  # noqa: E402
from servers.knowledge import graph as GR  # noqa: E402
from servers.knowledge.store import NumpyStore, paths  # noqa: E402

_N = 0


def ok(cond, msg):
    global _N
    _N += 1
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)
    print(f"  ok: {msg}")


N_NOTES = 80
SECTIONS = 6
TOPICS = ["mechanics", "thermodynamics", "electromagnetism", "optics", "quantum",
          "relativity", "waves", "fluids", "statics", "acoustics"]

raw = paths()["raw"]
raw.mkdir(parents=True, exist_ok=True)
print(f"[building synthetic corpus: {N_NOTES} notes x {SECTIONS} sections]")
for n in range(N_NOTES):
    topic = TOPICS[n % len(TOPICS)]
    parts = [f"# Course {n} on {topic}\n"]
    for s in range(SECTIONS):
        other = TOPICS[(n + s + 1) % len(TOPICS)]
        # Heading titled exactly by a concept word, and prose that mentions OTHER
        # concept words verbatim -> exercises cross-reference detection at scale.
        parts.append(
            f"## {topic}\n\n"
            f"This section covers {topic} concept number {s}. "
            f"It connects to {other} through shared structure. "
            f"The governing relation is f{s} = a{s} * x + b{s} for the {topic} case. "
            * 3
        )
    (raw / f"note_{n:03d}.md").write_text("\n\n".join(parts), encoding="utf-8")


def timed(fn, *a, **k):
    t0 = time.perf_counter()
    r = fn(*a, **k)
    return time.perf_counter() - t0, r


print("\n[ingest]")
dt, rep = timed(ING.ingest, incremental=True, rebuild_graph=False)
n_chunks = rep["chunks"]
ok(len(rep["indexed"]) == N_NOTES, f"indexed all {N_NOTES} notes")
print(f"  · {n_chunks} chunks in {dt:.2f}s ({1000*dt/max(1,n_chunks):.2f} ms/chunk)")
ok(dt < 20.0, f"ingest of {N_NOTES} notes / {n_chunks} chunks under 20s ({dt:.2f}s)")

print("\n[search latency]")
# warm + measure average over several queries
queries = ["mechanics principle", "quantum structure", "governing relation x",
           "thermodynamics concept", "optics connects relativity"]
times = []
for q in queries:
    dt, res = timed(RET.search, q, 8)
    times.append(dt)
    ok(res["results"], f"query {q!r} returns results")
avg = sum(times) / len(times)
print(f"  · avg search {1000*avg:.2f} ms over {len(queries)} queries on {n_chunks} chunks")
ok(avg < 0.2, f"average search latency under 200 ms ({1000*avg:.1f} ms)")

print("\n[graph build]")
dt, stats = timed(GR.build_graph)
print(f"  · {stats['nodes']} nodes, {stats['edges']} edges, {stats['cross_references']} xrefs in {dt:.2f}s")
ok(dt < 10.0, f"concept-graph build under 10s ({dt:.2f}s)")
ok(stats["cross_references"] > 0, "cross-references detected at scale")

print("\n[incremental no-op reindex]")
dt, rep2 = timed(ING.ingest, incremental=True, rebuild_graph=False)
ok(rep2["indexed"] == [], "no-op reindex re-embeds nothing")
print(f"  · no-op reindex {dt:.2f}s")
ok(dt < 5.0, f"no-op reindex (hash-check {N_NOTES} files) under 5s ({dt:.2f}s)")

print("\n[change-one reindex]")
(raw / "note_000.md").write_text("# Course 0 changed\n\n## New\n\nbrand new mechanics content.\n", encoding="utf-8")
dt, rep3 = timed(ING.ingest, incremental=True, rebuild_graph=False)
ok(rep3["indexed"] == ["note_000.md"], "only the changed note is re-indexed")
print(f"  · single-file reindex {dt:.2f}s")

print("\n[synthesize at scale]")
GR.build_graph()
dt, syn = timed(GR.synthesize, "how does mechanics connect to other topics")
ok(syn["mode"] == "synthesis" and syn.get("citations"), "synthesize returns context at scale")
print(f"  · synthesize {1000*dt:.1f} ms")
ok(dt < 1.0, f"synthesize under 1s ({1000*dt:.1f} ms)")

print(f"\nALL PERF CHECKS PASSED ({_N} assertions)")
