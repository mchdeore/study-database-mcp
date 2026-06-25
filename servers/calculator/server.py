"""Deterministic calculator MCP server for reliable studying.

Extends huhabla/calculator-mcp-server to meet the project capability table:

    calc_numeric   arbitrary-precision numeric eval (mpmath via sympy) -- no eval()
    calc_symbolic  sympy: differentiate/integrate/simplify/solve/factor/limit/series/expand
    calc_matrix    sympy.Matrix: multiply/inverse/det/eigenvals/eigenvects/rank/rref/solve/transpose/add
    calc_units     pint: unit-aware arithmetic + conversion
    constants      scipy.constants: physics constants with units

Plus deterministic stats helpers kept from the original repo.

Every tool returns a structured dict: {"result": ..., "latex": ...(when meaningful), "steps"?: ...}
On bad input every tool returns {"error": "<message>"} -- a clear string so the
caller can correct the expression, never a silently wrong number.

Design notes (ponytail):
- No eval() on raw input. Expressions are parsed with sympy's parser using a
  restricted symbol table; numeric eval is delegated to mpmath through evalf.
- Exact (symbolic) by default; pass numeric=True / a precision to get floats.
"""

from __future__ import annotations

import argparse
import functools
import inspect
import os
import queue
import threading
from typing import Any, Callable, List, Optional, Tuple, get_type_hints

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from mcp.server.fastmcp import FastMCP

app = FastMCP(
    "Calculator",
    dependencies=["sympy", "numpy", "scipy", "mpmath", "Pint"],
)

# Student-friendly parsing: "2x" -> 2*x, "x^2" -> x**2, implicit multiplication.
_TRANSFORMS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)

# Symbols that should keep their mathematical meaning when parsing.
_LOCALS = {
    "pi": sp.pi,
    "E": sp.E,
    "I": sp.I,
    "oo": sp.oo,
    "inf": sp.oo,
    "infinity": sp.oo,
    "gamma": sp.gamma,
}


def _parse(expr: str) -> sp.Expr:
    """Parse a string into a sympy expression without using eval().

    Raises ValueError with a clean message on failure.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("empty expression")
    try:
        return parse_expr(
            expr,
            local_dict=dict(_LOCALS),
            transformations=_TRANSFORMS,
            evaluate=True,
        )
    except Exception as e:  # noqa: BLE001 - surface a clean message to the model
        raise ValueError(f"could not parse expression {expr!r}: {e}") from e


# Plain transforms: ^ -> ** but NO implicit multiplication / symbol splitting,
# so multi-character names like state variables y0, y1 stay intact. Used where
# identifiers matter more than "2x" ergonomics (e.g. ODE system RHS parsing).
_PLAIN_TRANSFORMS = standard_transformations + (convert_xor,)


def _parse_plain(expr: str) -> sp.Expr:
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("empty expression")
    try:
        return parse_expr(
            expr,
            local_dict=dict(_LOCALS),
            transformations=_PLAIN_TRANSFORMS,
            evaluate=True,
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not parse expression {expr!r}: {e}") from e


def _sym(name: str) -> sp.Symbol:
    return sp.Symbol(name)


def _latex(obj: Any) -> str:
    try:
        return sp.latex(obj)
    except Exception:  # noqa: BLE001
        return str(obj)


def _err(message: str, hint: Optional[str] = None) -> dict:
    """Build a structured, actionable error so the model can self-correct fast."""
    out = {"error": message}
    if hint:
        out["hint"] = hint
    return out


# ---------------------------------------------------------------------------
# Execution timeout guard
# ---------------------------------------------------------------------------
# Some symbolic operations (hard integrals, dsolve on nonlinear ODEs, symbolic
# eigenvectors of large matrices) can run for a very long time or effectively
# not terminate. To keep the agent responsive we run the compute-heavy tools
# under a wall-clock timeout and return a clean, actionable error instead of
# hanging the whole session.
#
# ponytail: implemented with a daemon worker thread (not a process), so on
# timeout the runaway computation is *abandoned*, not force-killed -- the thread
# keeps running until sympy finishes and then dies (it can't block process exit
# because it's a daemon). For a single-user interactive study tool that's an
# acceptable ceiling; the upgrade path is a persistent worker-process pool with
# hard termination if heavy concurrent misuse ever becomes a problem.
_TIMEOUT_S = float(os.environ.get("CALC_TIMEOUT", "12"))


class _Timeout(Exception):
    pass


def _run_timed(fn: Callable[[], Any], seconds: Optional[float] = None) -> Any:
    """Run fn() in a daemon thread, raising _Timeout if it exceeds `seconds`."""
    seconds = seconds if seconds is not None else _TIMEOUT_S
    if seconds <= 0:  # 0/negative disables the guard
        return fn()
    box: "queue.Queue" = queue.Queue(maxsize=1)

    def worker():
        try:
            box.put((True, fn()))
        except BaseException as e:  # noqa: BLE001 - propagate to caller thread
            box.put((False, e))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise _Timeout(f"computation exceeded the {seconds:g}s time limit")
    succeeded, value = box.get_nowait()
    if succeeded:
        return value
    raise value


def with_timeout(func):
    """Decorator: run a tool under the wall-clock guard.

    The tool's own try/except still produces structured errors; this only adds
    an outer guard that converts a hang into a clean timeout error. Signature is
    preserved so FastMCP generates the correct tool schema.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return _run_timed(lambda: func(*args, **kwargs))
        except _Timeout as e:
            return _err(
                str(e),
                "this computation is hard or may not terminate symbolically; try "
                "mode='numeric' (for ODEs/integrals), narrow the input, give a "
                "definite range, or raise the CALC_TIMEOUT env var",
            )

    # Under `from __future__ import annotations` the signature/annotations are
    # strings; resolve them eagerly so FastMCP/pydantic builds the tool schema
    # correctly (otherwise it sees unresolved forward refs like "Optional[str]").
    wrapper.__signature__ = inspect.signature(func, eval_str=True)
    try:
        wrapper.__annotations__ = get_type_hints(func)
    except Exception:  # noqa: BLE001
        pass
    return wrapper


# ---------------------------------------------------------------------------
# calc_numeric
# ---------------------------------------------------------------------------
@app.tool()
@with_timeout
def calc_numeric(expression: str, precision: int = 15) -> dict:
    """Evaluate a numeric expression to arbitrary precision (mpmath via sympy).

    No eval() is used; the expression is parsed symbolically then evaluated.

    Args:
        expression: e.g. "2*pi*sqrt(9.81)", "factorial(20)", "sin(pi/4)", "2^10".
        precision: number of significant digits (default 15; raise for high precision).

    Returns:
        {"result": <float|int|str>, "exact": <symbolic form>, "latex": <str>}
        or {"error": <str>} on failure.

    Examples:
        >>> calc_numeric("2^10")["result"]
        1024
        >>> calc_numeric("sqrt(2)", 30)["result"]
        '1.41421356237309504880168872421'
    """
    try:
        expr = _parse(expression)
        val = expr.evalf(precision)
        if val.free_symbols:
            syms = sorted(map(str, val.free_symbols))
            return _err(
                f"expression has unresolved symbols: {syms}",
                "calc_numeric needs a fully numeric expression. If you meant an "
                "unknown function, note multi-letter names are read as products "
                "(e.g. 'foo' = f*o*o); use a known function or define the symbols. "
                "For symbolic work use calc_symbolic.",
            )
        # Non-finite results: report clearly instead of an opaque 'zoo'/'oo'.
        if val == sp.nan:
            return _err("result is indeterminate (NaN)", "likely a 0/0 or oo-oo form; try op='limit' in calc_symbolic")
        if getattr(val, "is_infinite", False):
            name = "inf" if val == sp.oo else "-inf" if val == -sp.oo else "complex infinity"
            return {
                "result": name,
                "note": "diverges / undefined (e.g. division by zero or a pole)",
                "exact": str(expr),
                "latex": _latex(expr),
            }
        # Complex results: give clean real/imag parts plus the exact form.
        if val.is_real is False:
            cv = complex(val)
            return {
                "result": str(expr),
                "real": cv.real,
                "imag": cv.imag,
                "exact": str(expr),
                "latex": _latex(expr),
            }
        # Real: prefer native int/float when it round-trips cleanly.
        if val.is_Integer:
            result: Any = int(val)
        elif precision <= 15:
            result = float(val)
        else:
            result = str(val)
        out = {"result": result, "exact": str(expr), "latex": _latex(expr)}
        try:  # convenience decimal; skip if it would overflow a float
            out["decimal"] = float(val)
        except (OverflowError, TypeError):
            pass
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# calc_symbolic
# ---------------------------------------------------------------------------
_SYMBOLIC_OPS = {
    "differentiate",
    "integrate",
    "simplify",
    "solve",
    "factor",
    "expand",
    "limit",
    "series",
    "laplace",
    "inverse_laplace",
    "fourier",
}


@app.tool()
@with_timeout
def calc_symbolic(
    expression: str,
    op: str,
    var: str = "x",
    point: Optional[str] = None,
    order: Optional[int] = None,
    numeric: bool = False,
    lower: Optional[str] = None,
    upper: Optional[str] = None,
) -> dict:
    """Symbolic mathematics with sympy. Returns both plain and LaTeX forms.

    Args:
        expression: the expression, or "lhs = rhs" for op="solve".
        op: differentiate, integrate, simplify, solve, factor, expand, limit,
            series, laplace, inverse_laplace, fourier.
        var: variable to operate on (default "x"). For solve, the unknown.
        point: required for op="limit" (e.g. "0", "oo"); optional base point for series.
        order: for op="differentiate", the derivative order (default 1, e.g. 2 for
            d²/dx²); for op="series", the truncation order (default 6).
        numeric: if True, evaluate the result to a float at the end (for a
            definite integral this gives a numeric value via sympy/scipy).
        lower, upper: integration bounds for op="integrate". If both are given
            the DEFINITE integral is computed (e.g. lower="0", upper="pi", or
            "oo"); omit them for the indefinite integral.

    Returns:
        {"result": <str>, "latex": <str>} (+ "steps"/"solutions"/"roots_decimal"
        where helpful) or {"error": <str>, "hint": <str>}.
        Transforms (laplace -> s domain, fourier -> k domain) return the
        transformed expression.

    Examples:
        >>> calc_symbolic("x^2", "differentiate")["result"]
        '2*x'
        >>> calc_symbolic("x^4", "differentiate", order=2)["result"]
        '12*x**2'
        >>> calc_symbolic("sin(x)", "integrate", lower="0", upper="pi")["result"]
        '2'
        >>> calc_symbolic("x^2 - 5*x + 6 = 0", "solve")["result"]
        '[2, 3]'
        >>> calc_symbolic("sin(x)/x", "limit", point="0")["result"]
        '1'
    """
    if op not in _SYMBOLIC_OPS:
        return _err(f"unknown op {op!r}", f"expected one of {sorted(_SYMBOLIC_OPS)}")
    try:
        v = _sym(var)
        if op == "solve":
            # Accept "lhs = rhs" or a bare expression (solved == 0).
            if "=" in expression:
                lhs_s, _, rhs_s = expression.partition("=")
                eq = sp.Eq(_parse(lhs_s), _parse(rhs_s))
            else:
                eq = sp.Eq(_parse(expression), 0)
            sol = sp.solve(eq, v)
            out = sol
        else:
            expr = _parse(expression)
            if op == "differentiate":
                out = sp.diff(expr, v, order if order is not None else 1)
            elif op == "integrate":
                if lower is not None and upper is not None:
                    out = sp.integrate(expr, (v, _parse(lower), _parse(upper)))
                else:
                    out = sp.integrate(expr, v)
            elif op == "simplify":
                out = sp.simplify(expr)
            elif op == "laplace":
                s = sp.Symbol("s")
                out = sp.laplace_transform(expr, v, s, noconds=True)
            elif op == "inverse_laplace":
                # input is F(s); transform back to the time variable `var`
                s = sp.Symbol("s")
                out = sp.inverse_laplace_transform(expr, s, v)
            elif op == "fourier":
                k = sp.Symbol("k")
                out = sp.fourier_transform(expr, v, k)
            elif op == "factor":
                out = sp.factor(expr)
            elif op == "expand":
                out = sp.expand(expr)
            elif op == "limit":
                if point is None:
                    return _err("op='limit' requires a 'point'", "e.g. point='0', point='oo', or point='-oo'")
                out = sp.limit(expr, v, _parse(point))
            elif op == "series":
                p = _parse(point) if point else 0
                out = sp.series(expr, v, p, order if order is not None else 6)
            else:  # pragma: no cover - guarded by _SYMBOLIC_OPS
                return _err(f"unhandled op {op!r}")

        if numeric and hasattr(out, "evalf"):
            out = out.evalf()

        result = {"result": str(out), "latex": _latex(out)}
        if op == "solve":
            # Depth: expose solutions as a list, a count, and decimal roots.
            result["solutions"] = [str(soln) for soln in out]
            result["count"] = len(out)
            roots_dec = []
            for soln in out:
                try:
                    roots_dec.append(complex(soln.evalf()) if not soln.is_real else float(soln.evalf()))
                except Exception:  # noqa: BLE001
                    roots_dec.append(None)
            if any(r is not None for r in roots_dec):
                result["roots_decimal"] = [
                    {"re": r.real, "im": r.imag} if isinstance(r, complex) else r for r in roots_dec
                ]
            if not out:
                result["note"] = f"no solution found for {var!r}; check the equation or the unknown variable"
        if op == "integrate":
            result["steps"] = (
                "definite integral" if (lower is not None and upper is not None)
                else "indefinite integral; constant of integration omitted"
            )
        return result
    except Exception as e:  # noqa: BLE001
        return _err(str(e), "check the expression syntax (use * for multiply, ** or ^ for powers) and that 'var' matches a variable in the expression")


# ---------------------------------------------------------------------------
# calc_ode  (symbolic via sympy.dsolve, numeric IVP via scipy.solve_ivp)
# ---------------------------------------------------------------------------
import re as _re


def _parse_ode(equation: str, func: str, var: str) -> "sp.Eq":
    """Parse student ODE notation into a sympy Eq.

    Accepts y, y', y'', y''' (primes) for the dependent function and its
    derivatives, with the independent variable `var`. "lhs = rhs" becomes an
    equation; a bare expression is taken as "= 0".
    """
    f = sp.Function(func)
    x = sp.Symbol(var)
    locals_ = {func: f, var: x, **_LOCALS}

    def subst(text: str) -> str:
        # Replace highest-order primes first so y'' isn't eaten by the y' rule.
        for order in (4, 3, 2, 1):
            primes = "'" * order
            pat = _re.compile(rf"{_re.escape(func)}{_re.escape(primes)}")
            text = pat.sub(f"Derivative({func}({var}),{var},{order})", text)
        # Bare dependent var not already a call -> make it func(var).
        text = _re.sub(rf"\b{_re.escape(func)}\b(?!\s*\()", f"{func}({var})", text)
        return text

    locals_["Derivative"] = sp.Derivative
    if "=" in equation:
        lhs_s, _, rhs_s = equation.partition("=")
        lhs = parse_expr(subst(lhs_s), local_dict=locals_, transformations=_TRANSFORMS)
        rhs = parse_expr(subst(rhs_s), local_dict=locals_, transformations=_TRANSFORMS)
        return sp.Eq(lhs, rhs)
    expr = parse_expr(subst(equation), local_dict=locals_, transformations=_TRANSFORMS)
    return sp.Eq(expr, 0)


def _build_ics(ics: dict, func: str, var: str) -> dict:
    """Turn {"y(0)": 1, "y'(0)": 0} into the dict sympy.dsolve expects."""
    f = sp.Function(func)
    x = sp.Symbol(var)
    out = {}
    pat = _re.compile(rf"{_re.escape(func)}('*)\((.*)\)")
    for key, val in ics.items():
        m = pat.fullmatch(key.strip())
        if not m:
            raise ValueError(f"bad initial condition key {key!r}; expected e.g. \"{func}(0)\" or \"{func}'(0)\"")
        order = len(m.group(1))
        point = _parse(m.group(2))
        value = val if isinstance(val, (int, float)) else _parse(str(val))
        cond = f(x).diff(x, order).subs(x, point) if order else f(point)
        out[cond] = value
    return out


@app.tool()
@with_timeout
def calc_ode(
    equation,
    func: str = "y",
    var: str = "x",
    ics: Optional[dict] = None,
    mode: str = "symbolic",
    t_span: Optional[List[float]] = None,
    y0: Optional[List[float]] = None,
    points: int = 0,
    method: str = "RK45",
) -> dict:
    """Solve ordinary differential equations reliably.

    Two backends, both validated library solvers (not hand-rolled):
      mode="symbolic"  exact closed-form via sympy.dsolve (general or, with ics,
                       particular). Use primes: "y'' + y = 0", "y' = y*x".
      mode="numeric"   initial-value problem via scipy.solve_ivp (adaptive
                       Runge-Kutta and friends). Supersedes manual Euler/RK4 and
                       predictor-corrector with production-grade solvers.

    Symbolic args:
        equation: the ODE string (primes for derivatives).
        func, var: dependent function and independent variable (default y, x).
        ics: optional initial/boundary conditions, e.g. {"y(0)": 1, "y'(0)": 0}.

    Numeric args (Cauchy IVP y' = f(x, y); supports first-order systems):
        equation: RHS f as a string for a single ODE, OR a list of RHS strings
                  [f0, f1, ...] for a system with state vars y0, y1, ... .
        t_span: [t0, t1] integration interval (required).
        y0: list of initial state values at t0 (required).
        points: number of evenly spaced output points (0 -> solver's own grid).
        method: scipy solver, e.g. RK45 (default), Radau (stiff), DOP853, LSODA.

    Returns:
        symbolic -> {"result", "latex", "classification"?}
        numeric  -> {"t": [...], "y": [[...], ...], "final": [...], "success": bool}
        or {"error": <str>} on bad input.

    Examples:
        >>> calc_ode("y' = y", ics={"y(0)": 1})["result"]
        'Eq(y(x), exp(x))'
        >>> r = calc_ode("-y", mode="numeric", t_span=[0, 1], y0=[1.0])
        >>> round(r["final"][0], 3)
        0.368
    """
    try:
        if mode == "symbolic":
            f = sp.Function(func)
            x = sp.Symbol(var)
            eq = _parse_ode(equation if isinstance(equation, str) else str(equation), func, var)
            ics_dict = _build_ics(ics, func, var) if ics else None
            sol = sp.dsolve(eq, f(x), ics=ics_dict) if ics_dict else sp.dsolve(eq, f(x))
            result = {"result": str(sol), "latex": _latex(sol)}
            try:
                result["classification"] = list(sp.classify_ode(eq, f(x)))[:5]
            except Exception:  # noqa: BLE001
                pass
            return result

        if mode == "numeric":
            if t_span is None or y0 is None:
                return _err("numeric mode requires t_span=[t0,t1] and y0=[...]",
                            "e.g. calc_ode('-y', mode='numeric', t_span=[0,1], y0=[1.0])")
            import numpy as np
            from scipy.integrate import solve_ivp

            rhs_list = equation if isinstance(equation, (list, tuple)) else [equation]
            n = len(rhs_list)
            if len(y0) != n:
                return _err(f"y0 has {len(y0)} value(s) but there are {n} equation(s)",
                            "provide one initial value per equation; for a single n-th order ODE, "
                            "reduce it to n first-order equations and pass n RHS strings + n y0 values")
            x = sp.Symbol(var)
            state = [sp.Symbol(func)] if n == 1 else [sp.Symbol(f"{func}{i}") for i in range(n)]
            funcs = [
                sp.lambdify((x, *state), _parse_plain(r if isinstance(r, str) else str(r)), "numpy")
                for r in rhs_list
            ]

            def rhs(t, Y):
                return [fn(t, *Y) for fn in funcs]

            t_eval = np.linspace(t_span[0], t_span[1], points).tolist() if points and points > 1 else None
            sol = solve_ivp(rhs, (float(t_span[0]), float(t_span[1])), [float(v) for v in y0],
                            method=method, t_eval=t_eval, dense_output=False)
            if not sol.success:
                return _err(f"solver failed: {sol.message}",
                            "try a different method (e.g. method='Radau' or 'LSODA' for stiff systems) "
                            "or a smaller t_span")
            return {
                "t": [float(t) for t in sol.t],
                "y": [[float(v) for v in row] for row in sol.y],
                "final": [float(row[-1]) for row in sol.y],
                "success": bool(sol.success),
                "method": method,
            }

        return _err(f"unknown mode {mode!r}", "expected mode='symbolic' or mode='numeric'")
    except Exception as e:  # noqa: BLE001
        return _err(
            str(e),
            "symbolic: write the ODE with primes, e.g. \"y'' + y = 0\"; sympy may not "
            "find a closed form for nonlinear ODEs (try mode='numeric'). numeric: give "
            "t_span=[t0,t1] and y0=[...], and RHS in x and y0,y1,... for systems.",
        )


# ---------------------------------------------------------------------------
# calc_matrix
# ---------------------------------------------------------------------------
_MATRIX_OPS = {
    "add",
    "subtract",
    "multiply",
    "transpose",
    "det",
    "inverse",
    "rank",
    "rref",
    "eigenvals",
    "eigenvects",
    "solve",
}


def _to_matrix(data: Any, name: str) -> sp.Matrix:
    if data is None:
        raise ValueError(f"matrix {name} is required for this op")
    if not isinstance(data, (list, tuple)) or not data:
        raise ValueError(f"matrix {name} must be a non-empty list of rows, e.g. [[1, 2], [3, 4]]")
    if isinstance(data[0], (list, tuple)):
        widths = {len(r) for r in data}
        if len(widths) != 1:
            raise ValueError(
                f"matrix {name} has rows of unequal length {sorted(widths)}; every row must have the same number of columns"
            )
    try:
        return sp.Matrix(data)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"could not read matrix {name}: {e}; expected numbers or numeric strings") from e


def _scalar(x: Any) -> Any:
    """JSON-safe scalar: int when integral, float when numeric, else exact str."""
    try:
        if getattr(x, "is_Integer", False):
            return int(x)
        if getattr(x, "is_Float", False):
            return float(x)
    except Exception:  # noqa: BLE001
        pass
    return str(x)


def _mat_to_list(M: sp.MatrixBase, numeric: bool) -> list:
    if numeric:
        return [[float(x) for x in row] for row in M.tolist()]
    return [[_scalar(x) for x in row] for row in M.tolist()]


def _matrix_steps(op: str, M: sp.Matrix, N: Optional[sp.Matrix]) -> Optional[List[str]]:
    """Human-readable, exact step-by-step working for the common student ops.

    Incorporates the "step-by-step solutions" learning feature from
    mkr-infinity/Matrix_Calculator, backed by exact sympy so the steps are
    reliable. Returns None for ops where a worked breakdown isn't meaningful.
    """
    try:
        if op in ("add", "subtract"):
            sign = "+" if op == "add" else "-"
            lines = [f"Element-wise {op}: C[i,j] = A[i,j] {sign} B[i,j]"]
            for i in range(M.rows):
                for j in range(M.cols):
                    a, b = M[i, j], N[i, j]
                    val = a + b if op == "add" else a - b
                    lines.append(f"C[{i+1},{j+1}] = {a} {sign} {b} = {val}")
            return lines
        if op == "transpose":
            return ["Transpose: C[i,j] = A[j,i] (swap rows and columns)."]
        if op == "multiply":
            lines = [f"Multiply ({M.rows}x{M.cols}) . ({N.rows}x{N.cols}): "
                     "C[i,j] = sum_k A[i,k]*B[k,j] (dot of row i with column j)."]
            for i in range(M.rows):
                for j in range(N.cols):
                    terms = [f"{M[i,k]}*{N[k,j]}" for k in range(M.cols)]
                    val = sum(M[i, k] * N[k, j] for k in range(M.cols))
                    lines.append(f"C[{i+1},{j+1}] = " + " + ".join(terms) + f" = {sp.simplify(val)}")
            return lines
        if op == "det":
            if M.rows == 2:
                a, b, c, d = M[0, 0], M[0, 1], M[1, 0], M[1, 1]
                return [f"2x2 determinant = ad - bc = ({a})({d}) - ({b})({c}) = {M.det()}"]
            if M.rows == 3:
                lines = ["3x3 determinant by cofactor expansion along row 1:"]
                total = 0
                for j in range(3):
                    minor = M.minor_submatrix(0, j)
                    cof = (-1) ** j * M[0, j] * minor.det()
                    total += cof
                    lines.append(
                        f"  term {j+1}: (-1)^(1+{j+1}) * {M[0,j]} * det({minor.tolist()}) "
                        f"= {cof}"
                    )
                lines.append(f"  sum = {M.det()}")
                return lines
            return [f"det computed via sympy (n>3 cofactor expansion is verbose): {M.det()}"]
        if op == "inverse":
            det = M.det()
            adj = M.adjugate()
            return [
                "Inverse via adjugate: A^-1 = adj(A) / det(A).",
                f"det(A) = {det}",
                f"adj(A) = transpose of the cofactor matrix = {adj.tolist()}",
                f"A^-1 = (1/{det}) * adj(A) = {M.inv().tolist()}",
            ]
        return None
    except Exception:  # noqa: BLE001
        return None  # steps are best-effort; never block the result


@app.tool()
@with_timeout
def calc_matrix(
    op: str,
    A: List[List[float]],
    B: Optional[List[List[float]]] = None,
    numeric: bool = False,
    steps: bool = False,
) -> dict:
    """Matrix / linear-algebra operations. Exact (sympy) by default.

    Args:
        op: add, subtract, multiply, transpose, det, inverse, rank, rref,
            eigenvals, eigenvects, solve.
        A: the (first) matrix as a list of rows. For op="solve", the coefficient
           matrix; for det/inverse/eigen* it must be square.
        B: second matrix for add/subtract/multiply; right-hand-side vector/matrix
           b for op="solve" (Ax = b).
        numeric: if True, return floats instead of exact symbolic values.
        steps: if True, include a "steps" list with worked, exact intermediate
            results (add/subtract/multiply/transpose/det/inverse).

    Returns:
        {"result": ..., "latex": ..., "steps"?: [...]} or {"error": <str>}.

    Examples:
        >>> calc_matrix("det", [[1, 2], [3, 4]])["result"]
        '-2'
        >>> calc_matrix("multiply", [[1, 2]], [[3], [4]])["result"]
        '[[11]]'
        >>> calc_matrix("subtract", [[5, 5]], [[1, 2]])["result"]
        '[[4, 3]]'
    """
    if op not in _MATRIX_OPS:
        return _err(f"unknown op {op!r}", f"expected one of {sorted(_MATRIX_OPS)}")
    try:
        M = _to_matrix(A, "A")
        N_for_steps: Optional[sp.Matrix] = None

        if op in ("add", "subtract"):
            N = _to_matrix(B, "B")
            if M.shape != N.shape:
                return _err(
                    f"shape mismatch for {op}: A is {M.shape}, B is {N.shape}",
                    "add/subtract require A and B to have identical shapes",
                )
            N_for_steps = N
            out: Any = (M + N) if op == "add" else (M - N)
        elif op == "multiply":
            N = _to_matrix(B, "B")
            if M.cols != N.rows:
                return _err(
                    f"cannot multiply A {M.shape} by B {N.shape}",
                    f"inner dimensions must match: A has {M.cols} column(s) but B has {N.rows} row(s)",
                )
            N_for_steps = N
            out = M * N
        elif op == "transpose":
            out = M.T
        elif op == "det":
            if not M.is_square:
                return _err(f"det requires a square matrix, got {M.shape}", "rows must equal columns")
            out = M.det()
        elif op == "inverse":
            if not M.is_square:
                return _err(f"inverse requires a square matrix, got {M.shape}", "rows must equal columns")
            d = M.det()  # computed once
            if d == 0:
                return _err("matrix is singular (det = 0); no inverse exists",
                            "rows/columns are linearly dependent; check for duplicate or proportional rows")
            out = M.inv()
        elif op == "rank":
            out = M.rank()
        elif op == "rref":
            rref_mat, pivots = M.rref()
            out = rref_mat
            extra = {"pivots": list(pivots)}
        elif op == "eigenvals":
            if not M.is_square:
                return _err(f"eigenvals requires a square matrix, got {M.shape}", "rows must equal columns")
            ev = M.eigenvals()  # {eigenvalue: algebraic multiplicity}
            out = {str(k): int(v) for k, v in ev.items()}
        elif op == "eigenvects":
            if not M.is_square:
                return _err(f"eigenvects requires a square matrix, got {M.shape}", "rows must equal columns")
            triples = M.eigenvects()
            out = [
                {
                    "eigenvalue": str(val),
                    "multiplicity": int(mult),
                    "eigenvectors": [str(list(vec)) for vec in vecs],
                }
                for val, mult, vecs in triples
            ]
        elif op == "solve":
            b = _to_matrix(B, "B")
            if M.rows != b.rows:
                return _err(
                    f"Ax=b dimension mismatch: A is {M.shape}, b has {b.rows} row(s)",
                    "b must have one entry per equation (A.rows). Pass b as a column, e.g. [[1],[2]]",
                )
            try:
                out = M.solve(b)
            except Exception as e:  # noqa: BLE001
                return _err(
                    f"no unique solution for Ax=b ({e})",
                    "the system may be singular (no solution or infinitely many); "
                    "try op='rref' on the augmented matrix to inspect it",
                )
        else:  # pragma: no cover
            return _err(f"unhandled op {op!r}")

        # numeric coercion for scalar sympy results
        if numeric and not isinstance(out, sp.MatrixBase) and hasattr(out, "evalf"):
            out = out.evalf()

        if isinstance(out, sp.MatrixBase):
            result: dict = {"result": _mat_to_list(out, numeric), "latex": _latex(out),
                            "shape": [out.rows, out.cols]}
        elif isinstance(out, (dict, list)):
            result = {"result": out}
        else:
            result = {"result": _scalar(out) if numeric else str(out), "latex": _latex(out)}

        if op == "rref":
            result.update(extra)
        if steps:
            worked = _matrix_steps(op, M, N_for_steps)
            if worked is not None:
                result["steps"] = worked
        return result
    except Exception as e:  # noqa: BLE001
        return _err(str(e), "pass matrices as lists of equal-length rows of numbers, e.g. [[1, 2], [3, 4]]")


# ---------------------------------------------------------------------------
# calc_units (pint)
# ---------------------------------------------------------------------------
_UREG = None


def _ureg():
    global _UREG
    if _UREG is None:
        import pint  # lazy: only needed for this tool

        _UREG = pint.UnitRegistry()
    return _UREG


@app.tool()
def calc_units(expression: str, to: Optional[str] = None) -> dict:
    """Unit-aware arithmetic and conversion (pint).

    Args:
        expression: a quantity expression, e.g. "3 m + 2 ft", "60 mph", "9.81 m/s^2 * 5 kg".
        to: optional target unit to convert into, e.g. "km/h", "newton".
            You can also write the conversion inline as "60 mph to km/h".

    Returns:
        {"result": <str>, "magnitude": <float>, "units": <str>, "dimensionality": <str>}
        or {"error": <str>, "hint": <str>}.

    Examples:
        >>> calc_units("3 m + 2 ft", to="m")["units"]
        'meter'
        >>> calc_units("100 degC to degF")["magnitude"]  # doctest: +SKIP
        212.0
    """
    try:
        import re as _re2

        ureg = _ureg()
        expr = expression
        # Support inline "<expr> to <unit>".
        if to is None and " to " in expression:
            expr, _, to = expression.rpartition(" to ")
            to = to.strip()

        # A simple "<number> <unit>" (single whitespace-free unit token) is built
        # as Quantity(mag, unit) so offset units (temperatures) convert correctly;
        # anything with operators/spaces (compound) goes through the string parser.
        m = _re2.fullmatch(r"\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([^\s+][^\s]*)\s*", expr)
        qty = ureg.Quantity(float(m.group(1)), m.group(2)) if m else ureg.Quantity(expr)

        if to:
            try:
                qty = qty.to(to)
            except Exception as conv_err:  # noqa: BLE001
                return _err(
                    f"cannot convert to {to!r}: {conv_err}",
                    "source and target must share dimensionality (length->length, "
                    "time->time); for temperatures use degC / degF / kelvin",
                )
        return {
            "result": f"{qty:~P}",  # pretty, abbreviated units
            "magnitude": float(qty.magnitude) if hasattr(qty, "magnitude") else float(qty),
            "units": str(qty.units) if hasattr(qty, "units") else "dimensionless",
            "dimensionality": str(qty.dimensionality) if hasattr(qty, "dimensionality") else "",
        }
    except Exception as e:  # noqa: BLE001
        return _err(
            str(e),
            "write quantities with a space, e.g. '3 m + 2 ft', '60 mph', and put the "
            "target unit in `to` or inline as 'X to Y'. Use ** or ^ for powers (m/s^2).",
        )


# ---------------------------------------------------------------------------
# constants (scipy.constants)
# ---------------------------------------------------------------------------
# A few friendly aliases on top of scipy's CODATA names.
_CONST_ALIASES = {
    "c": "speed of light in vacuum",
    "speed of light": "speed of light in vacuum",
    "h": "Planck constant",
    "hbar": "reduced Planck constant",
    "k": "Boltzmann constant",
    "k_b": "Boltzmann constant",
    "kb": "Boltzmann constant",
    "g": "Newtonian constant of gravitation",
    "na": "Avogadro constant",
    "n_a": "Avogadro constant",
    "avogadro": "Avogadro constant",
    "e": "elementary charge",
    "me": "electron mass",
    "mp": "proton mass",
    "r": "molar gas constant",
    "epsilon_0": "vacuum electric permittivity",
    "mu_0": "vacuum mag. permeability",
}


@app.tool()
def constants(name: str) -> dict:
    """Look up a physics constant with its value and units (scipy.constants, CODATA).

    Args:
        name: a constant name or common alias, e.g. "c", "h", "k_B", "G", "N_A",
              "elementary charge", "electron mass".

    Returns:
        {"name": <str>, "value": <float>, "units": <str>, "uncertainty": <float>}
        or {"error": <str>} with suggestions when the name is unknown.

    Examples:
        >>> round(constants("c")["value"])
        299792458
        >>> constants("k_B")["units"]
        'J K^-1'
    """
    try:
        from scipy import constants as C  # lazy import

        key = _CONST_ALIASES.get(name.strip().lower(), name)
        pc = C.physical_constants
        if key in pc:
            value, units, uncertainty = pc[key]
            return {
                "name": key,
                "value": float(value),
                "units": units or "dimensionless",
                "uncertainty": float(uncertainty),
            }
        # Fall back to module-level scalars like constants.pi, constants.g.
        attr = name.strip().lower()
        if hasattr(C, attr) and isinstance(getattr(C, attr), (int, float)):
            return {"name": attr, "value": float(getattr(C, attr)), "units": "(SI)", "uncertainty": 0.0}
        # Suggest near matches.
        lc = key.lower()
        hits = [k for k in pc if lc in k.lower()][:8]
        return {"error": f"unknown constant {name!r}", "suggestions": hits}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Deterministic statistics helpers (kept from the original repo, restructured)
# ---------------------------------------------------------------------------
@app.tool()
def stats_summary(data: List[float]) -> dict:
    """Descriptive statistics for a dataset in one call.

    Returns mean, median, mode, variance, standard deviation, min, max, n.
    {"error": <str>} on bad input (e.g. empty list).

    Examples:
        >>> stats_summary([1, 2, 2, 3, 4])["mean"]
        2.4
    """
    try:
        import numpy as np
        from scipy import stats as st

        if not data:
            return {"error": "empty dataset"}
        arr = np.asarray(data, dtype=float)
        mode_res = st.mode(arr, keepdims=False)
        return {
            "n": int(arr.size),
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "mode": float(mode_res.mode),
            "variance": float(np.var(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


@app.tool()
def linear_regression(data: List[Tuple[float, float]]) -> dict:
    """Ordinary least-squares fit of (x, y) points.

    Returns slope, intercept, r-value, r_squared, p-value, std error.
    {"error": <str>} on bad input.

    Examples:
        >>> r = linear_regression([(1, 2), (2, 4), (3, 6)])
        >>> round(r["slope"], 6), round(r["intercept"], 6)
        (2.0, 0.0)
    """
    try:
        import numpy as np
        from scipy import stats as st

        if len(data) < 2:
            return {"error": "need at least two points"}
        x = np.array([p[0] for p in data], dtype=float)
        y = np.array([p[1] for p in data], dtype=float)
        res = st.linregress(x, y)
        return {
            "slope": float(res.slope),
            "intercept": float(res.intercept),
            "r_value": float(res.rvalue),
            "r_squared": float(res.rvalue) ** 2,
            "p_value": float(res.pvalue),
            "std_err": float(res.stderr),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


@app.tool()
def confidence_interval(data: List[float], confidence: float = 0.95) -> dict:
    """Confidence interval for the population mean (Student t).

    Examples:
        >>> ci = confidence_interval([1, 2, 3, 4, 5])
        >>> ci["lower"] < ci["mean"] < ci["upper"]
        True
    """
    try:
        import numpy as np
        from scipy import stats as st

        if len(data) < 2:
            return {"error": "need at least two values"}
        arr = np.asarray(data, dtype=float)
        mean = float(np.mean(arr))
        sem = float(st.sem(arr))
        margin = sem * st.t.ppf((1 + confidence) / 2, arr.size - 1)
        return {"mean": mean, "lower": mean - margin, "upper": mean + margin, "confidence": confidence}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# propagate_uncertainty  (physics-lab error propagation)
# ---------------------------------------------------------------------------
@app.tool()
def propagate_uncertainty(expression: str, values: dict, uncertainties: dict) -> dict:
    """Propagate measurement uncertainties through a formula (Gaussian / partials).

    Computes f(values) and the combined standard uncertainty
        sigma_f = sqrt( sum_i (df/dx_i * sigma_i)^2 )
    assuming independent variables -- the standard physics-lab error propagation.

    Args:
        expression: the formula, e.g. "0.5*m*v^2" or "G*m1*m2/r^2".
        values: measured value per variable, e.g. {"m": 2.0, "v": 3.0}.
        uncertainties: 1-sigma uncertainty per variable, e.g. {"m": 0.1, "v": 0.2}.
            Variables omitted here are treated as exact (no contribution).

    Returns:
        {"value", "uncertainty", "relative", "formula_latex", "contributions"}
        or {"error": <str>}.

    Examples:
        >>> r = propagate_uncertainty("m*g", {"m": 2.0, "g": 9.81}, {"m": 0.1})
        >>> round(r["value"], 2), round(r["uncertainty"], 3)
        (19.62, 0.981)
    """
    try:
        expr = _parse(expression)
        subs = {sp.Symbol(k): v for k, v in values.items()}
        missing = expr.free_symbols - set(subs)
        if missing:
            return {"error": f"missing values for: {sorted(map(str, missing))}"}
        value = float(expr.subs(subs))

        var_sq = sp.Integer(0)
        contributions = {}
        sigma_f_expr = []  # for the symbolic formula
        for name, sigma in uncertainties.items():
            sym = sp.Symbol(name)
            partial = sp.diff(expr, sym)
            term = partial * sym  # placeholder for latex
            sigma_f_expr.append((partial, name))
            contrib = float(partial.subs(subs)) * float(sigma)
            contributions[name] = abs(contrib)
            var_sq += (partial.subs(subs) * sigma) ** 2
        uncertainty = float(sp.sqrt(var_sq))

        # symbolic uncertainty formula in LaTeX
        terms = [ (sp.diff(expr, sp.Symbol(n)))**2 * sp.Symbol(f"sigma_{n}")**2 for _, n in sigma_f_expr ]
        formula = sp.sqrt(sum(terms, sp.Integer(0))) if terms else sp.Integer(0)

        return {
            "value": value,
            "uncertainty": uncertainty,
            "relative": (uncertainty / value) if value else None,
            "formula_latex": _latex(formula),
            "contributions": contributions,
        }
    except Exception as e:  # noqa: BLE001
        return _err(
            str(e),
            "pass values and uncertainties as dicts keyed by variable name, e.g. "
            "values={\"m\": 2.0, \"v\": 3.0}, uncertainties={\"m\": 0.1}. Every symbol "
            "in the expression needs a value.",
        )


# ---------------------------------------------------------------------------
# calc_vector_calculus  (grad / div / curl / laplacian)
# ---------------------------------------------------------------------------
@app.tool()
@with_timeout
def calc_vector_calculus(op: str, field, variables: Optional[List[str]] = None) -> dict:
    """Vector calculus operators in Cartesian coordinates (sympy).

    Args:
        op: gradient, divergence, curl, laplacian.
            - gradient(scalar) -> vector
            - divergence(vector) -> scalar
            - curl(vector, 3 components) -> vector
            - laplacian(scalar) -> scalar
        field: a scalar expression string (gradient/laplacian) OR a list of
            component strings (divergence/curl), e.g. ["-y", "x", "0"].
        variables: coordinate names (default ["x", "y", "z"]).

    Returns:
        {"result": ..., "latex": ...} or {"error": <str>}.

    Examples:
        >>> calc_vector_calculus("gradient", "x^2 + y^2", ["x", "y"])["result"]
        '[2*x, 2*y]'
        >>> calc_vector_calculus("divergence", ["x", "y", "z"])["result"]
        '3'
    """
    try:
        vars = [sp.Symbol(v) for v in (variables or ["x", "y", "z"])]
        if op in ("gradient", "laplacian"):
            f = _parse(field if isinstance(field, str) else str(field))
            if op == "gradient":
                grad = [sp.diff(f, v) for v in vars]
                return {"result": str(grad), "latex": _latex(sp.Matrix(grad))}
            lap = sum(sp.diff(f, v, 2) for v in vars)
            lap = sp.simplify(lap)
            return {"result": str(lap), "latex": _latex(lap)}

        if op == "divergence":
            if not isinstance(field, (list, tuple)):
                return {"error": "divergence needs a vector field (list of components)"}
            comps = [_parse(c if isinstance(c, str) else str(c)) for c in field]
            if len(comps) != len(vars):
                vars = vars[: len(comps)]
            div = sp.simplify(sum(sp.diff(comps[i], vars[i]) for i in range(len(comps))))
            return {"result": str(div), "latex": _latex(div)}

        if op == "curl":
            if not isinstance(field, (list, tuple)) or len(field) != 3:
                return {"error": "curl needs a 3-component vector field"}
            F = [_parse(c if isinstance(c, str) else str(c)) for c in field]
            x, y, z = (vars + [sp.Symbol("y"), sp.Symbol("z")])[:3]
            curl = [
                sp.simplify(sp.diff(F[2], y) - sp.diff(F[1], z)),
                sp.simplify(sp.diff(F[0], z) - sp.diff(F[2], x)),
                sp.simplify(sp.diff(F[1], x) - sp.diff(F[0], y)),
            ]
            return {"result": str(curl), "latex": _latex(sp.Matrix(curl))}

        return {"error": f"unknown op {op!r}; expected gradient, divergence, curl, laplacian"}
    except Exception as e:  # noqa: BLE001
        return _err(
            str(e),
            "gradient/laplacian take a scalar string (e.g. 'x^2 + y^2'); divergence/curl "
            "take a list of component strings (e.g. ['-y','x','0']); curl needs exactly 3 "
            "components. Set `variables` to match (default ['x','y','z']).",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic calculator MCP server")
    parser.add_argument("--stdio", action="store_true", help="Use STDIO transport (Claude Desktop)")
    args = parser.parse_args()
    app.run(transport="stdio" if args.stdio else "stdio")


if __name__ == "__main__":
    main()
