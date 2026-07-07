---
name: exact-math
description: >-
  Use whenever a task involves ANY calculation — arithmetic, algebra, calculus,
  linear algebra, differential equations, unit conversions, physics constants,
  statistics, or lab error propagation. Routes every computation through the
  `calculator` MCP server for exact, verifiable results with LaTeX, instead of
  computing by hand. Apply this in study guides, homework checks, lab reports,
  spreadsheets, and any document that contains a number that has to be right.
---

# Exact Math

**Do not compute math by hand.** Language models silently make arithmetic and
algebra mistakes. This project ships a deterministic `calculator` MCP server —
call its tools and use what they return. Trivial mental math (e.g. `2 + 2`) is
fine; anything a student would need to *trust* goes through a tool.

## How to work

1. Translate the problem into a tool call (pick the tool from the catalog below).
2. Read the returned `result` (and `latex`). On `{"error": ..., "hint": ...}`,
   follow the hint and retry — don't fall back to hand-computation.
3. In any deliverable, show the **exact result and its LaTeX**, and for teaching
   material include the worked steps (`steps=true` where supported).

## Tool catalog (`calculator` server)

| Tool | Use it for |
|------|------------|
| `calc_numeric(expression, precision=15)` | Arbitrary-precision numeric evaluation. No `eval()`. |
| `calc_symbolic(expression, op, var="x", point, order, numeric, lower, upper)` | `op` ∈ differentiate, integrate (definite via `lower`/`upper`), simplify, solve, factor, expand, limit, series, laplace, inverse_laplace, fourier. Returns plain **and** LaTeX. |
| `calc_ode(...)` | Solve ODEs — symbolic closed form (with initial conditions) or numeric IVPs / first-order systems. |
| `calc_matrix(op, A, B?, numeric=false, steps=false)` | Linear algebra: add, subtract, multiply, transpose, det, inverse, rank, rref, eigenvals, eigenvects, solve `Ax=b`. Set `steps=true` for worked solutions. |
| `calc_vector_calculus(op, field, variables)` | gradient, divergence, curl, laplacian (Cartesian). |
| `calc_units(expression, to?)` | Unit-aware arithmetic + conversion, e.g. `"60 mph"` `to="km/h"`. |
| `constants(name)` | CODATA physics constants with units: `c`, `h`, `k_B`, `G`, `N_A`, … |
| `stats_summary(data)` · `linear_regression(data)` · `confidence_interval(data, confidence=0.95)` | Descriptive stats, fits, CIs. |
| `propagate_uncertainty(expression, values, uncertainties)` | Gaussian error propagation through a formula — for physics labs. |

## Routing hints

- "differentiate / integrate / solve / simplify / factor / limit / series /
  Laplace" → `calc_symbolic` with the matching `op`.
- "evaluate this to N digits", big-number arithmetic → `calc_numeric`.
- determinant, inverse, eigenvalues, rref, `Ax=b` → `calc_matrix`
  (use `steps=true` when teaching the method).
- "convert X to Y", mixing units → `calc_units`.
- "value of Planck's constant / speed of light …" → `constants`.
- mean/std/regression/confidence interval on data → the stats tools.
- "uncertainty / error in the result" for a lab → `propagate_uncertainty`.

## Output rules for documents

- Quote the tool's exact value; if rounding for prose, keep the exact form too.
- Render equations with the returned `latex` in docs/slides.
- For worked examples in a study guide, include the intermediate `steps`.
- Never present a hand-derived number as if it were verified.
