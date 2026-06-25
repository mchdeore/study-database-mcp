"""Extensive edge-case suite for the study MCP tools.

Run: python tests/test_edge_cases.py

Goes after the *edges* of every tool and asserts three things the model relies
on to use the tools well:
  1. Correctness at boundaries (huge precision, singular matrices, improper
     integrals, complex roots, offset units, systems of ODEs, ...).
  2. Error QUALITY -- failures return a clear {"error": ...} string, and the
     usage-error cases carry an actionable {"hint": ...} so the model can
     self-correct in one step.
  3. Output DEPTH -- structured fields (exact + decimal, latex, shape, solutions,
     citations, contributions) are present so answers can be shown accurately.

Plus a timing section asserting the tools are fast enough for interactive use.

No framework: a tiny harness that counts checks and prints a summary. Exits
non-zero on the first failure so it works in CI.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Isolated, fully-offline knowledge env BEFORE importing knowledge modules.
_TMP = tempfile.mkdtemp(prefix="study_edge_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["VECTOR_STORE"] = "numpy"
os.environ["ENABLE_GRAPHRAG"] = "false"

from servers.calculator import server as C  # noqa: E402
from servers.knowledge import ingest as K_ingest  # noqa: E402
from servers.knowledge import retrieve as K_retrieve  # noqa: E402
from servers.knowledge import graph as K_graph  # noqa: E402
from servers.knowledge.store import paths as K_paths  # noqa: E402

_N = 0


def ok(cond, msg):
    global _N
    _N += 1
    if not cond:
        print(f"  FAIL: {msg}")
        raise AssertionError(msg)
    print(f"  ok: {msg}")


def is_err(r):
    return isinstance(r, dict) and "error" in r and isinstance(r["error"], str) and r["error"]


def has_hint(r):
    return isinstance(r, dict) and isinstance(r.get("hint"), str) and r["hint"]


def section(name):
    print(f"\n[{name}]")


# ===========================================================================
section("calc_numeric edges")
ok(C.calc_numeric("2^10")["result"] == 1024, "int power exact")
ok(C.calc_numeric("factorial(20)")["result"] == 2432902008176640000, "big factorial exact int")
hp = C.calc_numeric("pi", 60)["result"]
ok(isinstance(hp, str) and hp.startswith("3.14159265358979323846"), "60-digit pi precision")
ok("decimal" in C.calc_numeric("pi"), "real result carries decimal field")
r = C.calc_numeric("1/0")
ok(not is_err(r) and r["result"] == "complex infinity" and "note" in r, "1/0 -> complex infinity + note (not silent/garbage)")
ok(C.calc_numeric("log(0)")["result"] == "complex infinity", "log(0) -> complex infinity")
ci = C.calc_numeric("sqrt(-1)")
ok(ci["real"] == 0.0 and abs(ci["imag"] - 1.0) < 1e-12, "sqrt(-1) -> real/imag split")
ck = C.calc_numeric("2 + 3*I")
ok(abs(ck["real"] - 2.0) < 1e-12 and abs(ck["imag"] - 3.0) < 1e-12, "complex literal real/imag")
for bad in ["", "   ", "2 +", "log("]:
    ok(is_err(C.calc_numeric(bad)), f"bad numeric {bad!r} -> error")
ok(is_err(C.calc_numeric("x + 1")) and has_hint(C.calc_numeric("x + 1")), "unresolved symbol -> error + hint")
ok(has_hint(C.calc_numeric("foo(2)")), "unknown function -> actionable hint")

# ===========================================================================
section("calc_symbolic edges")
ok(C.calc_symbolic("x^4", "differentiate", order=2)["result"] == "12*x**2", "2nd derivative via order")
ok(C.calc_symbolic("x^4", "differentiate", order=3)["result"] == "24*x", "3rd derivative")
ok(C.calc_symbolic("x^3", "differentiate")["result"] == "3*x**2", "default 1st derivative")
ok(C.calc_symbolic("sin(x)", "integrate", lower="0", upper="pi")["result"] == "2", "definite integral exact")
imp = C.calc_symbolic("exp(-x^2)", "integrate", lower="-oo", upper="oo")
ok(imp["result"] in ("sqrt(pi)", "pi**(1/2)"), f"improper Gaussian integral = sqrt(pi): {imp['result']}")
sol = C.calc_symbolic("x^2 - 2 = 0", "solve")
ok(sol["count"] == 2 and "roots_decimal" in sol and abs(sol["roots_decimal"][1] - 1.41421356) < 1e-6, "solve depth: count + roots_decimal")
csol = C.calc_symbolic("x^2 + 1 = 0", "solve")
ok(all(isinstance(rd, dict) and "re" in rd and "im" in rd for rd in csol["roots_decimal"]), "complex roots -> {re,im} decimals")
nosol = C.calc_symbolic("x^2 + 1 = 0", "solve", var="y")
ok(nosol["count"] == 0 and "note" in nosol, "no solution for chosen var -> count 0 + note")
ok(is_err(C.calc_symbolic("sin(x)/x", "limit")) and has_hint(C.calc_symbolic("sin(x)/x", "limit")), "limit without point -> error + hint")
ok(C.calc_symbolic("sin(x)/x", "limit", point="0")["result"] == "1", "limit value")
ok(C.calc_symbolic("1/(1-x)", "series", order=4)["result"].startswith("1 + x"), "series with custom order")
ok(C.calc_symbolic("1", "laplace", var="t")["result"] == "1/s", "laplace of 1 = 1/s")
ok(C.calc_symbolic("t", "laplace", var="t")["result"] == "s**(-2)", "laplace of t = 1/s^2")
ilap = C.calc_symbolic("1/s", "inverse_laplace", var="t")
ok("Heaviside" in ilap["result"] or ilap["result"] == "1", f"inverse laplace of 1/s: {ilap['result']}")
bad_op = C.calc_symbolic("x", "bogus")
ok(is_err(bad_op) and has_hint(bad_op), "unknown symbolic op -> error + hint listing ops")

# ===========================================================================
section("calc_ode edges")
ode = C.calc_ode("y' = y", ics={"y(0)": 1})
ok(ode["result"].replace(" ", "") in ("Eq(y(x),exp(x))", "Eq(y(x),E**x)"), "1st-order IVP closed form")
ok("classification" in ode, "ODE result carries classification depth")
ode2 = C.calc_ode("y'' + y = 0", ics={"y(0)": 0, "y'(0)": 1})
ok("sin(x)" in ode2["result"], "2nd-order IVP with two ICs -> sin(x)")
num = C.calc_ode("-y", mode="numeric", t_span=[0, 1], y0=[1.0])
ok(abs(num["final"][0] - 0.367879) < 1e-3 and num["success"], "numeric IVP decay to e^-1")
import math as _m
sysr = C.calc_ode(["y1", "-y0"], mode="numeric", t_span=[0, _m.pi / 2], y0=[0.0, 1.0], points=40)
ok(abs(sysr["final"][0] - 1.0) < 1e-2 and len(sysr["t"]) == 40, "ODE system (sin/cos) + t_eval grid")
ok(is_err(C.calc_ode(["y1", "-y0"], mode="numeric", t_span=[0, 1], y0=[1.0])), "y0/equation count mismatch -> error")
miss = C.calc_ode("-y", mode="numeric")
ok(is_err(miss) and has_hint(miss), "numeric without t_span/y0 -> error + hint")
um = C.calc_ode("y'=y", mode="bogus")
ok(is_err(um) and has_hint(um), "unknown ode mode -> error + hint")

# ===========================================================================
section("calc_matrix edges")
ok(C.calc_matrix("det", [[1, 2], [3, 4]])["result"] == "-2", "det 2x2")
ok(C.calc_matrix("det", [[2, 0, 0], [0, 3, 0], [0, 0, 4]])["result"] == "24", "det 3x3 diagonal")
inv = C.calc_matrix("inverse", [[1, 2], [3, 4]])
ok(inv["result"][0][0] == -2 and inv["shape"] == [2, 2], "inverse exact + shape field")
sg = C.calc_matrix("inverse", [[1, 2], [2, 4]])
ok(is_err(sg) and has_hint(sg), "singular inverse -> error + hint")
ok(is_err(C.calc_matrix("add", [[1, 2]], [[1, 2, 3]])) and has_hint(C.calc_matrix("add", [[1, 2]], [[1, 2, 3]])), "add shape mismatch -> error + hint")
mm = C.calc_matrix("multiply", [[1, 2]], [[1, 2]])
ok(is_err(mm) and "inner dimensions" in mm["hint"], "multiply inner-dim mismatch -> error + hint")
ok(is_err(C.calc_matrix("det", [[1, 2], [3]])), "jagged matrix -> error")
ok(is_err(C.calc_matrix("det", [])) and has_hint(C.calc_matrix("det", [])), "empty matrix -> error + hint")
ok(C.calc_matrix("det", [['1', '2'], ['3', '4']])["result"] == "-2", "string-number entries coerced")
ok(C.calc_matrix("det", [[5]])["result"] == "5", "1x1 det")
sv = C.calc_matrix("solve", [[2, 0], [0, 4]], [[2], [8]])
ok(sv["result"] == [[1], [2]], "solve Ax=b unique")
ok(is_err(C.calc_matrix("solve", [[1, 1], [1, 1]], [[1], [2]])), "inconsistent solve -> error")
ok(is_err(C.calc_matrix("solve", [[1, 1]], [[1], [2]])), "solve row mismatch -> error")
ninv = C.calc_matrix("inverse", [[4, 7], [2, 6]], numeric=True)
ok(abs(ninv["result"][0][0] - 0.6) < 1e-9, "numeric flag -> float entries")
ev = C.calc_matrix("eigenvals", [[2, 0], [0, 3]])
ok(ev["result"] == {"2": 1, "3": 1}, "eigenvalues with multiplicity")
rr = C.calc_matrix("rref", [[1, 2, 3], [4, 5, 6]])
ok("pivots" in rr and "shape" in rr, "rref returns pivots + shape")
stp = C.calc_matrix("inverse", [[1, 2], [3, 4]], steps=True)
ok(isinstance(stp.get("steps"), list) and len(stp["steps"]) >= 2, "inverse steps depth")

# ===========================================================================
section("calc_units edges")
ok(abs(C.calc_units("3 m + 2 ft", to="m")["magnitude"] - 3.6096) < 1e-3, "compound length sum")
ok(abs(C.calc_units("100 degC to degF")["magnitude"] - 212.0) < 1e-6, "temperature offset conversion")
ok(C.calc_units("60 mph", "km/h")["dimensionality"] == "[length] / [time]", "dimensionality field present")
ok(is_err(C.calc_units("3 m + 2 s")) and has_hint(C.calc_units("3 m + 2 s")), "incompatible addition -> error + hint")
ok(is_err(C.calc_units("3 m to s")) and has_hint(C.calc_units("3 m to s")), "incompatible conversion -> error + hint")
ok(is_err(C.calc_units("5 zorkmids")) and has_hint(C.calc_units("5 zorkmids")), "unknown unit -> error + hint")

# ===========================================================================
section("constants edges")
ok(round(C.constants("c")["value"]) == 299792458, "speed of light value")
for alias in ["C", "k_B", "K_B", "kb", "G", "N_A", "hbar"]:
    ok(not is_err(C.constants(alias)), f"alias {alias!r} resolves")
hc = C.constants("h")
ok({"value", "units", "uncertainty"} <= set(hc), "constant carries value/units/uncertainty depth")
unk = C.constants("flibbertigibbet")
ok(is_err(unk) and "suggestions" in unk, "unknown constant -> error + suggestions")

# ===========================================================================
section("propagate_uncertainty edges")
w = C.propagate_uncertainty("m*g", {"m": 2.0, "g": 9.81}, {"m": 0.1})
ok(abs(w["value"] - 19.62) < 1e-9 and abs(w["uncertainty"] - 0.981) < 1e-6, "weight value + sigma")
ke = C.propagate_uncertainty("0.5*m*v^2", {"m": 2.0, "v": 3.0}, {"m": 0.1, "v": 0.2})
ok("contributions" in ke and set(ke["contributions"]) == {"m", "v"}, "per-variable contributions depth")
zero = C.propagate_uncertainty("a - b", {"a": 1.0, "b": 1.0}, {"a": 0.1})
ok(zero["value"] == 0.0 and zero["relative"] is None, "zero value -> relative None (no div-by-zero)")
ok(is_err(C.propagate_uncertainty("m*g", {"m": 2.0}, {"m": 0.1})), "missing value -> error")

# ===========================================================================
section("calc_vector_calculus edges")
ok(C.calc_vector_calculus("gradient", "x^2 + y^2", ["x", "y"])["result"] == "[2*x, 2*y]", "gradient")
ok(C.calc_vector_calculus("divergence", ["x", "y", "z"])["result"] == "3", "divergence")
ok(C.calc_vector_calculus("curl", ["-y", "x", "0"])["result"] == "[0, 0, 2]", "curl")
ok(C.calc_vector_calculus("laplacian", "x^2 + y^2 + z^2")["result"] == "6", "laplacian")
ok(is_err(C.calc_vector_calculus("curl", ["x", "y"])), "curl needs 3 components -> error")
ok(is_err(C.calc_vector_calculus("divergence", "x")), "divergence of scalar -> error")
ok(is_err(C.calc_vector_calculus("bogus", "x")), "unknown vector op -> error")

# ===========================================================================
section("knowledge edges (offline)")
# empty index behavior first
ok(is_err(K_retrieve.search("")), "empty query -> error")
ok(is_err(K_retrieve.search("x", k=0)), "k=0 -> error")
empty = K_retrieve.search("anything")
ok(empty.get("results") == [] and "note" in empty, "search on empty index -> [] + note")

# build a tiny corpus
raw = K_paths()["raw"]
raw.mkdir(parents=True, exist_ok=True)
(raw / "em.md").write_text(
    "# Electromagnetism\n\n## Gauss's Law\n\nThe flux relates to enclosed charge: div E = rho/epsilon_0.\n"
    "\n## Faraday's Law\n\nA changing magnetic field induces an EMF.\n",
    encoding="utf-8",
)
rep = K_ingest.ingest(incremental=True)
ok("em.md" in rep["indexed"], "ingest indexed the new note")
res = K_retrieve.search("Gauss law flux charge", k=5)
ok(res["results"] and {"source", "heading_path", "page", "chunk_id"} <= set(res["results"][0]["citation"]), "search result full citation")
sec = K_retrieve.get_section("em.md", "Faraday's Law")
ok("EMF" in sec["text"] and "chars" in sec, "get_section verbatim + chars depth")
miss_src = K_retrieve.get_section("nope.md", "x")
ok(is_err(miss_src) and "available_sources" in miss_src, "unknown source -> error + available_sources")
miss_head = K_retrieve.get_section("em.md", "Nonexistent Heading")
ok(is_err(miss_head) and "available_headings" in miss_head, "unknown heading -> error + available_headings")
ok(is_err(K_retrieve.get_section("em.md", "")), "empty heading -> error")
K_graph.build_graph()
syn = K_graph.synthesize("how do Gauss and Faraday laws relate")
ok(syn["mode"] == "synthesis" and "citations" in syn, "synthesize returns citations")
rc = K_graph.related_concepts("Gauss's Law")
ok("concept" in rc or "error" in rc, "related_concepts responds")
bad_rc = K_graph.related_concepts("totally unrelated topic xyz")
ok(is_err(bad_rc) and "available" in bad_rc, "unknown concept -> error + available list")

# ===========================================================================
section("execution timeout guard")
import asyncio as _asyncio


def _slow():
    time.sleep(0.5)
    return {"result": "done"}


# mechanism: _run_timed abandons a too-slow call
raised = False
try:
    C._run_timed(lambda: time.sleep(1.0), seconds=0.1)
except C._Timeout:
    raised = True
ok(raised, "_run_timed raises _Timeout past the limit")
ok(C._run_timed(lambda: 7, seconds=1.0) == 7, "_run_timed returns fast results normally")

# decorator: a hang becomes a clean, hinted error (temporarily tiny limit)
_saved = C._TIMEOUT_S
C._TIMEOUT_S = 0.15
guarded = C.with_timeout(_slow)
tr = guarded()
ok(is_err(tr) and "time limit" in tr["error"] and has_hint(tr), "with_timeout converts a hang into error + hint")
C._TIMEOUT_S = _saved

# real tools still return correct results under the guard
ok(C.calc_symbolic("x^2", "differentiate")["result"] == "2*x", "guarded tool still computes normally")
ok(C.calc_matrix("det", [[1, 2], [3, 4]])["result"] == "-2", "guarded matrix tool normal")

# FastMCP schema is preserved through the decorator (model still sees params)
_schemas = {t.name: t.inputSchema for t in _asyncio.run(C.app.list_tools())}
ok({"expression", "op", "var"} <= set(_schemas["calc_symbolic"]["properties"]), "calc_symbolic schema params intact after wrapping")
ok({"op", "A"} <= set(_schemas["calc_matrix"]["properties"]), "calc_matrix schema params intact after wrapping")

# ===========================================================================
section("timing / efficiency")


def timed(fn, *a, **k):
    t0 = time.perf_counter()
    r = fn(*a, **k)
    return (time.perf_counter() - t0), r


dt, _ = timed(C.calc_numeric, "pi", 200)
ok(dt < 1.0, f"calc_numeric pi@200 digits fast ({dt*1000:.1f} ms)")
# 30x30 numeric matrix multiply
A = [[float((i * 31 + j) % 7 + 1) for j in range(30)] for i in range(30)]
dt, r = timed(C.calc_matrix, "multiply", A, A, True)
ok(not is_err(r) and dt < 2.0, f"30x30 numeric matmul fast ({dt*1000:.1f} ms)")
dt, r = timed(C.calc_matrix, "det", A, None, True)
ok(not is_err(r) and dt < 2.0, f"30x30 numeric det fast ({dt*1000:.1f} ms)")
dt, _ = timed(C.calc_symbolic, "x^2 - 5*x + 6 = 0", "solve")
ok(dt < 1.0, f"quadratic solve fast ({dt*1000:.1f} ms)")
dt, _ = timed(K_retrieve.search, "Gauss law", 8)
ok(dt < 0.5, f"vector search fast ({dt*1000:.1f} ms)")

print(f"\nALL EDGE-CASE CHECKS PASSED ({_N} assertions)")
