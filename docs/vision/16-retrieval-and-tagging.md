# 16 — Retrieval & tagging (THE design for "damn-accurate recall")

> **Status: CURRENT, authoritative for retrieval (2026-07-10).** This supersedes the
> earlier "frontier model builds a fixed glossary once" framing in doc 15 §3 — that
> idea was **considered and rejected** (see §7). Where doc 15 or docs 01–14 conflict
> with this on retrieval/tagging, **this wins.** Reached by working the tradeoffs
> from Tier 0→3 (see §6); the user drove the design to its final shape.

## 0. What we're optimizing
Primary use case (see doc 15 §3a): *"help me study for the test in <course>, it covers
<concept Z> — go."* Must return, **verbatim + cited**, every relevant chunk for Z — the
definition, the worked example (even if it never names Z), the past-exam question, and
the user's weak spots. Precision-first ("don't lead me astray") but with real recall of
*implicit* mentions. Open-world: new imports bring new concepts constantly.

## 1. The layers (density lives in TAGS, not in edges)
Four signals, each covering the others' gaps:

1. **Lexical / heading match** — chunk literally names the term. Free, deterministic,
   zero false positives. `chunk.py` already carries `heading_path`.
2. **Embeddings** (`text-embedding-3-large`, all content) — catches the *implicit* case:
   a chunk that's *about* Z without naming it lands near Z in vector space. Fuzzy,
   continuous, not filterable — great recall, can't be queried as "is it? yes/no".
3. **Open-vocabulary multi-field chunk tags** (the core; §2) — discrete, filterable,
   catches implicit AND stays crisp. This is where the density lives.
4. **Minimal graph relations** (§4) — versions, splits, aliases only. Light + mutable.

**Fusion:** lexical + vector fused (RRF, per `08-search.md`), then filter/boost by tags,
1-hop typed graph expansion, re-rank by importance/recency, return **cited** chunks.
Trust order: exact-name link > `talks_about` tag > embedding-near > `keywords` tag.

## 2. Open-vocabulary, multi-field chunk tagging [the core decision]
A model reads each chunk **once** and emits *structured* tags. **Open-vocabulary** — it
names whatever is actually there, so a brand-new concept is tagged the first time it
appears (no pre-authored list to map to). Re-run only when the chunk changes
(content-hash gated). **Tags are INTRINSIC to the chunk → they do NOT rot** (a tag
describes the chunk alone; it references no other doc, so nothing external can stale it).

**Why multiple fields, not one tag list:** the *field* encodes the confidence/meaning,
which defuses false-positives far better than "more tags." Suggested fields (tune):
- `talks_about` — concepts the chunk is genuinely *about* (**high bar, few, high-trust**).
  This is the field "study X" filters on.
- `keywords` — terms/buzzwords that merely appear (**low bar, many, low-trust**) → broad
  recall, weighted below `talks_about`.
- `includes` — structural facts: has-equations / has-worked-example / has-figure /
  has-proof / has-definition (**not fuzzy at all**) → enables "chunks about X that have a
  worked example". Very high value, near-zero risk.
- `summary` — a 1–2 line chunk summary (human- and LLM-readable; also improves retrieval).
- (add fields as needed, e.g. `people`, `formulas`.)

A stray mention lands in `keywords` (low-trust), never in `talks_about` (the filter) — so
recall goes up without precision loss. Closed hallucination risk further by having the
model choose `talks_about` conservatively; `keywords` can be liberal.

Cost: a per-chunk model pass (cheap model, ~$1–2 over the current corpus, delta-gated).
Slightly pricier than a single tag because it's structured — worth it.

## 3. The concept index — EMERGENT & self-maintaining (NOT a fixed glossary) [decision]
Concepts are **nodes with their own embedding** (of the concept's canonical text), kept in
a small **separate concept index** (dozens–hundreds per course, not thousands of chunks).
Two jobs: (a) **query resolution** — "study X" → embed X → nearest concept node → resolve
the user's phrasing to the canonical concept; (b) **alias detection by geometry** (§3a).

**Crucially, the concept list is NOT authored up-front — it ACCRETES from the tags.** The
first time a `talks_about` tag names a concept, a concept node is created on demand. So
the "glossary" is a *byproduct of tagging*, not a prerequisite. New imports → new tags →
new concept nodes automatically. **No build-once step, no constant manual update.**

### 3a. Synonym merge — by geometry + learned from use (NOT re-embedding) [decision]
The one real risk of open tagging is fragmentation: `closure relation` / `completeness
relation` / `resolution of the identity` become three nodes for one concept, so "study X"
under-recalls. Fix it two cheap ways, both self-maintaining:
- **Geometry:** two concept nodes whose embeddings are very close are probably synonyms →
  propose an `alias_of` merge. **This is a nearest-neighbor scan over embeddings you
  ALREADY computed — NOT a re-embedding pass.** (Re-embedding is only needed if you change
  the embedding *model*. Rejected as over-engineering; see §7.)
- **Learned from use:** when the user searches phrasing A and then uses a chunk tagged B,
  that co-selection is free evidence A≈B → accumulate → propose `alias_of`. **Usage is
  free labeled data; the vocabulary tightens the more the system is used.** (Elevated to a
  core principle — this is what removes the maintenance burden entirely.)

Merges are **lazy proposals** (reuse the §6 `might_copy`-style reconcile machinery),
never block tagging, never need to be complete, self-heal over time. Retrieval on a
concept follows `alias_of` edges so all synonyms are caught.

## 4. Graph relations stay MINIMAL (density is in tags, not edges) [decision]
Only these edges exist, and they're light + mutable between docs:
- `supersedes` — version chains (walk to tip = newest; doc 15 §6).
- `alias_of` — concept synonyms (§3a).
- `might_copy` — unsure duplicate flag (doc 15 §6).
- `related` / wikilinks — user/model-authored, genuinely-distinct references (already
  self-building from `[[wikilinks]]`, `relations.py`).
**No blanket chunk-to-chunk semantic web.** That's the extrinsic, rot-prone kind (§7).

## 5. How the primary query runs ("study for test on Z")
1. Resolve Z → concept node (embed Z, nearest concept; follow `alias_of`).
2. Gather chunks: `talks_about = Z` (crisp) ∪ vector-near(Z) (implicit) — dedup, rank.
3. Optional filters via `includes` ("...that have a worked example") and structural edges
   (test→course, past-exam docs, Crowdmark weak-spots for Z).
4. Return **verbatim + cited**; front-end builds the study guide. Works even with a thin
   graph because Z comes from the user's prompt and tags+embeddings carry the recall.

## 6. Why this point on the Tier 0→3 spectrum (recap of the reasoning)
- Edges are cheap to *store*, expensive to *keep true*. Three costs: creation (cheap),
  **maintenance/rot** (the real bill), **wrong-edge precision loss** (worst for our goal).
- **Intrinsic tags don't rot** (describe one chunk); **extrinsic chunk-to-chunk edges do**
  (pinned across two docs). So put the density in intrinsic tags, keep edges minimal.
- Dollar cost was never the constraint (full tag+embed pass ≈ $2, delta-gated). The
  constraints are rot and precision — both handled by intrinsic tagging + emergent aliases.

## 7. Explicitly REJECTED (so they're not re-litigated)
- **Fixed build-once glossary** — closed vocabulary in an open world; can't tag new
  concepts without constant rebuilds. Replaced by the emergent concept index (§3).
- **Blanket per-chunk chunk-to-chunk semantic extraction (Tier 3)** — extrinsic edges
  that rot with content churn and erode precision as they densify. Density goes in tags
  instead. (Surgical typed edges on *immutable* `60-sources/` remain a possible later add,
  capped + low-trust — not a blanket pass.)
- **Re-embedding passes to find aliases** — unnecessary; cluster the existing concept
  embeddings. Only re-embed if the embedding model itself changes.

## 8. Build order
1. Multi-field tags + `summary` on every chunk (delta-gated). ← biggest recall+precision win
2. Concept nodes accrete from `talks_about`; concept index with embeddings.
3. Alias merge: geometry pass + usage-co-selection signal → lazy `alias_of`.
4. Wire the §5 query path; tune trust weights + thresholds with a small eval set.
