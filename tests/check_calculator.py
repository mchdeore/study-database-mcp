"""Self-check for the calculator server. Run: python tests/check_calculator.py

Asserts the capability table works: structured + exact results, LaTeX where
meaningful, and clean error strings on bad input (never a wrong number).
No test framework -- just asserts so it runs anywhere.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servers.calculator.server import (  # noqa: E402
    calc_matrix,
    calc_numeric,
    calc_ode,
    calc_symbolic,
    calc_units,
    calc_vector_calculus,
    confidence_interval,
    constants,
    linear_regression,
    propagate_uncertainty,
    stats_summary,
)


def ok(cond, msg):
    assert cond, msg
    print(f"  ok: {msg}")


print("calc_numeric")
ok(calc_numeric("2^10")["result"] == 1024, "2^10 == 1024 (exact int, ^ accepted)")
ok(abs(calc_numeric("sin(pi/4)")["result"] - 0.7071067811865476) < 1e-12, "sin(pi/4)")
hp = calc_numeric("sqrt(2)", 30)["result"]
ok(str(hp).startswith("1.4142135623730950488"), f"sqrt(2) 30-digit precision: {hp}")
ok("latex" in calc_numeric("2*pi")["exact"] or "result" in calc_numeric("2*pi"), "numeric returns structure")
ok("error" in calc_numeric("2 +"), "bad numeric input -> error string")
ok("error" in calc_numeric("x + 1"), "unresolved symbol -> error, not wrong number")

print("calc_symbolic")
ok(calc_symbolic("x^2", "differentiate")["result"] == "2*x", "d/dx x^2 = 2*x")
ok("latex" in calc_symbolic("x^2", "differentiate"), "symbolic returns latex")
ok(calc_symbolic("x^2 - 5*x + 6 = 0", "solve")["result"] == "[2, 3]", "solve quadratic")
ok(calc_symbolic("sin(x)/x", "limit", point="0")["result"] == "1", "limit sin(x)/x -> 1")
ok(calc_symbolic("(x+1)^2", "expand")["result"] == "x**2 + 2*x + 1", "expand")
ok(calc_symbolic("x^2 + 2*x + 1", "factor")["result"] == "(x + 1)**2", "factor")
ok("steps" in calc_symbolic("x^2", "integrate"), "integrate notes constant omitted")
ok(calc_symbolic("sin(x)", "integrate", lower="0", upper="pi")["result"] == "2", "definite integral sin 0..pi = 2")
ok("exp" in calc_symbolic("1", "laplace", var="t")["result"] or "1/s" in calc_symbolic("1", "laplace", var="t")["result"], "laplace transform of 1 = 1/s")
ok("error" in calc_symbolic("x^2", "bogus_op"), "bad op -> error string")

print("calc_ode")
ode1 = calc_ode("y' = y", ics={"y(0)": 1})
ok(ode1["result"].replace(" ", "") in ("Eq(y(x),exp(x))", "Eq(y(x),E**x)"), f"y'=y, y(0)=1 -> exp(x): {ode1['result']}")
ok("latex" in ode1, "symbolic ODE returns latex")
ode2 = calc_ode("y'' + y = 0")
ok("sin" in ode2["result"] and "cos" in ode2["result"], f"y''+y=0 general solution has sin & cos: {ode2['result']}")
ode3 = calc_ode("-y", mode="numeric", t_span=[0, 1], y0=[1.0])
ok(abs(ode3["final"][0] - 0.367879) < 1e-3, f"numeric y'=-y from 1 -> e^-1: {ode3['final'][0]}")
ode4 = calc_ode(["y1", "-y0"], mode="numeric", t_span=[0, 1.5707963267948966], y0=[0.0, 1.0], points=50)
ok(abs(ode4["final"][0] - 1.0) < 1e-2, f"system y0'=y1,y1'=-y0 (sin) at pi/2 -> 1: {ode4['final'][0]}")
ok("error" in calc_ode("y' = ", mode="symbolic"), "bad ODE -> error string")
ok("error" in calc_ode("-y", mode="numeric"), "numeric without t_span/y0 -> error string")

print("calc_matrix")
ok(calc_matrix("det", [[1, 2], [3, 4]])["result"] == "-2", "det 2x2")
ok(calc_matrix("multiply", [[1, 2]], [[3], [4]])["result"] == [[11]], "matmul")
inv = calc_matrix("inverse", [[1, 2], [3, 4]])
ok("result" in inv and inv["result"][0][0] == -2, "inverse exact entry (-2 top-left)")
ok(inv["result"][1][0] == "3/2", "inverse keeps exact fraction 3/2")
ok("error" in calc_matrix("inverse", [[1, 2], [2, 4]]), "singular -> error string")
ok(calc_matrix("rank", [[1, 2], [2, 4]])["result"] == "1", "rank of singular = 1")
rref = calc_matrix("rref", [[1, 2, 3], [4, 5, 6]])
ok("pivots" in rref, "rref returns pivots")
ev = calc_matrix("eigenvals", [[2, 0], [0, 3]])
ok(ev["result"] == {"2": 1, "3": 1}, f"eigenvals diag: {ev['result']}")
sol = calc_matrix("solve", [[2, 0], [0, 4]], [[2], [8]])
ok(sol["result"] == [[1], [2]], f"solve Ax=b: {sol['result']}")
ok("error" in calc_matrix("det", [[1, 2, 3]]), "non-square det -> error")
# Features incorporated from mkr-infinity/Matrix_Calculator: subtract + steps.
ok(calc_matrix("subtract", [[5, 5]], [[1, 2]])["result"] == [[4, 3]], "subtract element-wise")
det_steps = calc_matrix("det", [[1, 2], [3, 4]], steps=True)
ok("steps" in det_steps and any("ad - bc" in s for s in det_steps["steps"]), "det steps shown")
mul_steps = calc_matrix("multiply", [[1, 2]], [[3], [4]], steps=True)
ok("steps" in mul_steps and len(mul_steps["steps"]) >= 2, "multiply steps shown")
inv_steps = calc_matrix("inverse", [[1, 2], [3, 4]], steps=True)
ok("steps" in inv_steps and any("adj" in s for s in inv_steps["steps"]), "inverse steps via adjugate")

print("calc_units")
u = calc_units("3 m + 2 ft", to="m")
ok(abs(u["magnitude"] - 3.6096) < 1e-3, f"3 m + 2 ft in m = {u['magnitude']}")
u2 = calc_units("60 mph to km/h")
ok(abs(u2["magnitude"] - 96.56) < 0.1, f"60 mph -> km/h = {u2['magnitude']}")
ok("error" in calc_units("3 zorkmids + 2 m"), "unknown unit -> error string")

print("constants")
ok(round(constants("c")["value"]) == 299792458, "speed of light")
ok(constants("k_B")["value"] > 1e-23 and constants("k_B")["value"] < 2e-23, "Boltzmann magnitude")
ok("J" in constants("h")["units"], "Planck has Joule-seconds units")
ok("error" in constants("not_a_constant"), "unknown constant -> error string")

print("stats")
ok(stats_summary([1, 2, 2, 3, 4])["mode"] == 2.0, "stats mode")
ok(abs(linear_regression([(1, 2), (2, 4), (3, 6)])["slope"] - 2.0) < 1e-9, "regression slope")
ci = confidence_interval([1, 2, 3, 4, 5])
ok(ci["lower"] < ci["mean"] < ci["upper"], "confidence interval brackets mean")

print("propagate_uncertainty")
u = propagate_uncertainty("m*g", {"m": 2.0, "g": 9.81}, {"m": 0.1})
ok(abs(u["value"] - 19.62) < 1e-6, f"weight value = {u['value']}")
ok(abs(u["uncertainty"] - 0.981) < 1e-3, f"weight uncertainty = {u['uncertainty']}")
ke = propagate_uncertainty("0.5*m*v^2", {"m": 2.0, "v": 3.0}, {"m": 0.1, "v": 0.2})
ok(abs(ke["value"] - 9.0) < 1e-6, "kinetic energy value")
ok(ke["uncertainty"] > 0 and "contributions" in ke, "uncertainty combines independent terms")
ok("error" in propagate_uncertainty("m*g", {"m": 2.0}, {"m": 0.1}), "missing value -> error string")

print("calc_vector_calculus")
ok(calc_vector_calculus("gradient", "x^2 + y^2", ["x", "y"])["result"] == "[2*x, 2*y]", "gradient")
ok(calc_vector_calculus("divergence", ["x", "y", "z"])["result"] == "3", "divergence of position field = 3")
ok(calc_vector_calculus("curl", ["-y", "x", "0"])["result"] == "[0, 0, 2]", "curl of rotation field = 2 z-hat")
ok(calc_vector_calculus("laplacian", "x^2 + y^2 + z^2")["result"] == "6", "laplacian = 6")
ok("error" in calc_vector_calculus("curl", ["x", "y"]), "curl needs 3 components -> error")

print("\nALL CALCULATOR CHECKS PASSED")
