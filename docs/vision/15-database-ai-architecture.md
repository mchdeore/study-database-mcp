# 15 — Database-side AI, two-model retrieval & the feature superset

> **Status: THIS IS THE CURRENT DIRECTION ("Phase B").** Design decisions from the
> 2026-07-10 session. This doc **deliberately re-expands** the lean "Phase A" scope —
> see the ⭐ CURRENT TRUTH banner in `README.md`. **Where this doc conflicts with docs
> 01–14 (local-only, $0, stdio-only), THIS wins and those are history.** It's a *target
> architecture*, logged so nothing evaporates — not a claim about what's built today.
> `[exists]` = already in the code; `[new]` = still to build. The code today is still
> Phase A; this is the roadmap.

## 0. The frame

The MCP is the **wire between two models**:

- **Front-end / frontier model (e.g. Opus)** — in the conversation with the user.
  It never touches files, embeddings, or the graph directly. It sends the backend a
  **structured request (JSON spec)** and reasons over what comes back.
- **Back-end / database model (cheap, local on the Mac mini; Haiku-class or Ollama)**
  — does the messy agentic work: graph-RAG retrieval, identity resolution, version
  linking, categorization. Returns a **clean, minimal, cited payload**.

**Why split them:** the messy determinism lives in a small model you can tool- and
prompt-engineer hard against a strict contract (its job is narrow: *find, resolve,
cite* — never be creative). The reasoning lives in the big model, which stays
un-bloated because it only ever sees verified, cited material. This makes the whole
system *more* deterministic and cheaper, not less capable.

## 1. Three-layer model (how to think about every feature)

Every capability is one of three things. Most of the whiteboard list is Layer 1.

- **Layer 1 — Areas** (storage + retrieval): data + an ingestion contract + search.
  Cheap, mostly **[exists]**. No autonomous behaviour.
- **Layer 2 — Managers** (an agent + a schedule): a *cheap* backend model that
  periodically reads an area and *does* something (categorize, decompose, summarize,
  check-in). **Proposes by default**; destructive/external actions need owner approval.
- **Layer 3 — Tools** (pure functions the LLM calls): deterministic, no storage.

## 2. Whiteboard superset → consolidated build set

The 2026-07-10 whiteboard listed 8 wants. Consolidated:

| Whiteboard item | Layer | Verdict |
|-----------------|-------|---------|
| 8. Matrix solver w/ LLM-usable output | Tool | **[exists]** — fold into `calculator` + `exact-math`. Not new. |
| 6. Course information (deadlines, weights, office hours, links) | Area | **[exists]** — School area + D2L scraper feed it. |
| 7. Course content mgmt (textbooks, equations, derivations, definitions) | Area (+ retrieval tuning) | **flagship, see §3** — same "School" domain, semantic mode. |
| 5. Personal info (jobs, things I study, worth remembering) | Area | contract + light curation. Mostly storage. |
| 4. Finance (spending across cards, categorized, analyzed) | Area + Manager | see §5. Storage = area; analysis = a cheap manager. |
| 2. To-do (grouped, AI subtasks) | Conversational + Manager | decomposition happens **when asked**, not by a nagging background agent. |
| 1. Goals (habits, spending) | Manager | merge with fitness → one "Goals & Habits" manager. |
| 3. Fitness / health / mood | Manager (journal) | see §4. Reflective journal, not a compliance tracker. |

Net: **~4 areas + a few managers + 1 tool**, most already present. The only genuinely
new *intelligence* is (a) the two-model school retrieval and (b) transaction
categorization. Everything else is "scheduled prompt + storage + deterministic rollup."

## 3. FLAGSHIP — School: two-model graph-RAG retrieval [new on top of existing index]

Goal (user's words): *"it knows the exact textbook definition and doesn't make stuff
up or lead me astray."* The design that delivers this:

- **Front-end fills a JSON query spec**, e.g.
  `{ "intent": "definition", "term": "closure relation", "course": "PHYS234",
     "want": ["verbatim_source", "related_concepts"], "must_cite": true }`.
- **Back-end runs agentic graph-RAG**: hybrid search (`search.py`, vector + lexical)
  → follow graph edges (`relations.py`: definition→theorem→assignment-that-uses-it)
  → pull sections **verbatim** (`get_section`, exact wording, not reassembled).
- **Returns a cited payload** — text + `{source, heading_path, page}` for every
  claim. Front-end is contractually instructed to state only what's cited.
- **The anti-hallucination guarantee is structural, not vibes:** generation is
  separated from retrieval; the backend returns quotes-with-citations; "no citation →
  don't say it."

Already-built substrate **[exists]**: vault, SQLite catalog, `search.py`,
`relations.py` (graph), `chunk.py`, vector store, `study-assistant` skill.

**Good news — the substrate is better than the "hash-embeddings" note implied:**
`chunk.py` is **already structure-aware** (keeps LaTeX/math blocks + code fences atomic,
splits on headings, carries source/heading_path/page). The embedder is **already
pluggable** (`hash` | `local` bge | `openai`). So the work is *config + relations*, not
building.

**EMBEDDER DECISION (2026-07-10, finalized):** **OpenAI `text-embedding-3-large` for
ALL content** — the user is not privacy-constrained, so **no per-area split**, one
embedder everywhere (school, finance, personal, all of it). Under the ~$10/mo cap (see
`10-cost.md`). **Key nuance:** for *exact* definition/term retrieval, the win is
**lexical/heading match + verbatim `get_section`** (deterministic, free) — embeddings
carry the *fuzzy* half. So exactness comes from hybrid retrieval, not just a bigger
embedder; the strong embedder makes "explain X / how does X relate to Y" good.

- Definitions stay **intact** (chunker already does this).
- **Density comes from intrinsic multi-field chunk TAGS, not from a dense edge graph.**
  → The full, authoritative retrieval design is **`16-retrieval-and-tagging.md`**
  (open-vocabulary tagging + emergent self-maintaining concept index + minimal relations).
  Read doc 16 for anything about tagging/relations/aliases; §3a below is just the primary
  query it serves.

**Priority: build this first and well.** It's the most valuable, most buildable, and
the place the two-model design most clearly pays off.

### 3a. PRIMARY use case — "help me study for test Y" (design around THIS) [2026-07-10]
The user's **most-frequent** query is: *"help me study for the test in <course>, it
covers <concept(s)> according to the teacher — ready, set, go."* Retrieval and the graph
should be **optimized around this intent first**; everything else is secondary.

**What it needs (the traversal):**
`test/assignment → covers → topic(s) → { definition, formula, worked-example,
past-test-question, your-weak-spots }`, all returned **verbatim + cited**, packaged so
the front-end can immediately produce a study guide.

**Required edges (mostly structural + cheap — see §3b):**
- `test → covers → topic` — from the syllabus/outline coverage (already scraped) OR
  supplied by the user in the prompt ("it covers Z"). The user-supplied path means this
  works **even when the graph is thin** — the front-end passes the concept, the backend
  retrieves everything under it. No dependence on perfect extraction.
- `topic → has → {definition, formula}` — from headings in lecture notes/textbook
  (structural, from `chunk.py` heading_path).
- `topic ← tests ← past-test-question` — from the Crowdmark/past-test docs.
- `topic ← weak-spot ← your-graded-work` — from Crowdmark per-question feedback
  (e.g. PHYS 234 Test 1 → closure relation + adjoint were wrong).

**Why this is achievable now:** the "it covers Z" comes *from the user in the prompt*,
so the hard part (auto-deciding coverage) is optional. The backend just has to retrieve
everything anchored to Z, verbatim, with citations + weak-spots — which is lexical anchor
+ 1 hop + `get_section`. This is the flagship demo and the first thing to make excellent.

## 4. Fitness / health / mood — reflective journal, NOT a tracker [new, easy]

User's framing: *"track my general health and mood… a data-analysis template and
forecast over a long time of checking in, little 2 cents about myself — how I'm
feeling, what I'm working on, what I'm enjoying / not."*

- **Mechanism:** scheduled gentle check-in prompt → a dated journal entry in a
  `journal`/`health` area → periodic **trend summaries + longitudinal view**.
- **Low AI, low risk.** No autonomous agent.
- **Design guardrail (important):** keep it **descriptive/reflective, not evaluative**.
  It reflects patterns back ("you're consistently low in exam weeks"); it does **not**
  grade or nag. A health/mood system that becomes a compliance tracker can reinforce
  unhealthy self-monitoring — deliberately avoid that. Opt-in, gentle, skippable.

## 5. Finance — chase the outcome, not the risky method [partial build]

Desired outcome (fully legitimate): *"ask how much I owe total and across which cards,
categorized."*

**Acquisition — DECISION: no credential-based auto-login to banks/cards.** An AI
logging into financial portals with stored credentials is the exact pattern that
drains accounts, trips fraud detection (freezes), and is brittle (login flows change).
Not worth a standing liability on primary finances. Get the *same outcome* safely via:

| Method | Cost | Live vs snapshot | Notes |
|--------|------|------------------|-------|
| **Statement export** (CSV/PDF, or auto-forward monthly statement emails) | **$0** | monthly snapshot | safest; already the designed seam (`data/incoming/finance/`). Start here. |
| **SimpleFIN Bridge** | ~$1.50/mo | live balances | hobbyist-friendly, read-only tokens. |
| **Plaid** | free dev tier (a few accounts) | live balances | industry standard; what budgeting apps use. Add later only if live numbers are genuinely needed. |
| MX / Flinks / Yodlee | enterprise $$ | live | not worth it for one user. |

Real tradeoff is **live vs. snapshot**, not money.

### DECISION (2026-07-10): use Plaid as the aggregator (with caveats)
Plaid is the chosen default — it gives the actual outcome the user wants:
- **Transactions product** returns, per transaction: amount, date, merchant name, and a
  **Plaid category** (e.g. `Food and Drink > Restaurants`). So "what I spent on what" is
  available **without** the user or a model guessing most of it.
- **Longitudinal by design:** Plaid is only the *acquisition* method. Pull on a schedule
  → each transaction becomes a dated `transaction` note in the finance area. Once in the
  vault it's **yours forever**, queryable any way, independent of Plaid (drop Plaid later,
  history stays). This is the vault-is-truth principle applied to money.

**Caveats (confirm before building):**
1. Plaid's free tier is its **dev/sandbox** tier — free for a limited number of linked
   "Items," fine for personal use, but it's dev access, not a blessed "personal forever."
2. Plaid auto-categorization is **decent, not perfect** (e.g. "Amazon" is ambiguous).
   → the Layer-2 categorizer *corrects/refines* Plaid's first pass and learns recurring
   merchants. **Plaid + a light manager**, not Plaid alone.
3. **Canadian coverage** is narrower than US. **Verify the user's actual banks (BMO, RBC)
   are supported before committing.** If not → fall back to SimpleFIN or statement export.

Fallbacks if Plaid doesn't fit: SimpleFIN Bridge (~$1.50/mo, live) or statement export
($0, monthly snapshot — the `data/incoming/finance/` seam already exists). Still **no
credential-based auto-login** in any case (see decision above).

- **Storage** = the finance area (**[exists]** contract; provisional per `07`).
- **Analysis** = a Layer-2 **manager** (cheap model): refine Plaid categories, flag
  subscriptions, total per card. Intelligence is small and bounded; charts/rollups are
  deterministic (no model needed). User can then run any analysis over the logged history.

## 6. THE "DON'T MAKE ME FIGHT IT" PRINCIPLE — identity + version chains [new]

User's core constraint: *"hand it something, tell it a thing or two, and it figures out
where it goes, or if I already have one, or if it finishes something else — keep a
merged, properly-identified version… maybe as a line of relations for versions, go to
the end to get the newest."* This is the make-or-break UX rule. DECISION:

- **Identity is stable; versions are a chain.** Every "thing" has a stable identity
  (this is what `source_ref` already is). New info about an existing thing → **append a
  new version node** linked to the prior via a `supersedes` relation. **Walk to the tip
  of the chain = newest.** Never overwrite; never silently duplicate.
- **Buys three things the user explicitly wants:** (1) no fighting over placement — hand
  it a blob, it resolves identity and files it; (2) nothing lost — old versions stay,
  auditable/revertable, you just read the tip; (3) IDE surgery anytime — it's plain
  Markdown + a graph, so you or another agent can restructure by hand.
### 6a. Keep it SIMPLE (2026-07-10 decision) — three edges, one health signal
Deliberately **not** over-engineering this. The earlier fan-out / over-split /
intentional-fork taxonomy was cut. Minimum viable model:

| Edge | Meaning | Effect |
|------|---------|--------|
| `supersedes` | B is a newer version of A (confident) | the chain; walk to tip = newest |
| `might_copy` | A and B *might* be the same (unsure) | **soft flag** — nothing merged, decide later |
| `related` | reference each other but genuinely distinct | not a duplication question |

- **Resolver policy:** high-confidence match → `supersedes`/merge automatically;
  otherwise **file the new thing normally + drop a `might_copy` flag** and move on.
  Never block the user at hand-off. Prefer a flag over a wrong merge (flags are cheap to
  clear; bad merges hurt). Start conservative.
- **Retrieval:** return the **tip** and note if there's an unresolved `might_copy`.
  Don't dump both and make the reader choose.
- **ONE health signal (not four):** *too many unresolved `might_copy` flags → clean up.*
  A misbehaving resolver shows up as *lots of flags*, so this one signal already catches
  it. Alert is **informational, never auto-deletes**. Add finer diagnostics only IF this
  proves insufficient in practice.

### 6b. School identity is DETERMINISTic — barely needs a model [2026-07-10]
UWaterloo course codes follow a fixed pattern: **`DEPT`(letters) + 3-digit number +
optional trailing letter** — `PHYS234`, `MATH225`, `PHYS260B`, `PHYS267`. Regex:
`^[A-Z]{2,6}\s?\d{3}[A-Z]?$`. Combined with the **known-courses list** the D2L +
Crowdmark scrapes already produce (org-unit IDs confirm which codes are real courses),
a course's identity is just its normalized code. So the fuzzy "is PHYS 234 the course
or the folder?" worry mostly **evaporates for the school domain** — identity there is
regex + a lookup, not a model call. Fuzzy resolution is only needed for the messy
domains (personal info, loose notes). This strengthens the flagship and reduces risk.

## 7. The backend agent — a PROPOSING LIBRARIAN, not an operator [decision]

Scale justifies a persistent backend agent for active management. Firm boundary:

- **May freely (unattended):** read, retrieve, embed, relate, categorize, summarize,
  version-link on high confidence, draft proposals, file to inbox.
- **Must propose + get owner approval:** anything **destructive or external** — delete,
  move/spend money, send email, change sharing/settings. Gated by the authenticated
  owner token (already a decision in `09-hosting-auth.md`).
- This single boundary is what makes "an AI managing my database" safe to run
  unattended. Append-and-relate is inherently reversible; that's the safety model.

## 8. Managers to build, in order (Layer 2)

1. **Transaction categorizer** (finance) — the one place a cheap background model
   clearly earns its cost (repetitive bounded judgment over many rows).
2. **Identity/version reconciler** — the engine behind §6; runs on new drops + inbox.
3. **Journal check-in + trend summarizer** (health/mood) — scheduled, gentle.
4. **To-do decomposer** — mostly **on-demand in conversation**, not a background nag.

Managers run on `scheduler.py` **[exists]**, use the cheap/local model, and write
**proposals** for anything beyond append+relate.

## 9. Hosting tie-in (see `09`)

Runs on a **Mac mini**, reachable via **Tailscale + bearer token** (never public
internet). Front-end (Opus, anywhere) → MCP over the tailnet → backend model + vault on
the mini. Human navigation of structure = point **Obsidian** at the vault (instant,
zero build). A live monitoring dashboard is **[new]** and optional. Backups to a
**second physical disk** in the box (same-machine backups don't survive disk failure).

## 10. Queueable automations (scripts run on the server) [new]

The user wants to **queue automations from the MCP** — Python or shell scripts that run
on the Mac mini doing API automations or browser/"open-claw" tasks, results captured back.
This is powerful **and the single most dangerous capability in the system** (arbitrary
code execution on the always-on box that holds your life). So it's designed tight:

- **Registered, not arbitrary.** The MCP does NOT execute model-supplied code. It runs
  **named automations from a registry** — scripts placed in an `automations/` folder by
  the user/IDE, each with a **manifest** (what it does, what it touches, read-only?, tier).
  The model queues *by name* with declared params; it never injects code. This single rule
  removes the worst risk.
- **Two tiers (declared in the manifest):**
  - *Safe / read-only* (fetch an API, generate a report, run the D2L scrape) → may run on
    queue.
  - *Side-effecting / external* (write externally, spend money, send messages, delete,
    **browser/open-claw automations that log in and act**) → **owner-token approval**, and
    browser/open-claw ones default to **confirm-per-run**, not auto-queue.
- **Sandboxed + logged.** Each run: timeout, captured stdout/stderr/exit-code to the audit
  log, optional result note written to the vault. No secrets printed.
- **Queue semantics** (what the user asked for): queue by name → backend runs → result
  visible. `scheduler.py` **[exists]** is the runner; this adds the **registry + manifest
  + safe/approved gate**. Automations are essentially reusable, parameterized Layer-2/3
  jobs the front-end can trigger on demand or on schedule.

## 11. PRIORITY ORDER (what actually matters — read this)

Most of this doc is secondary to one thing: **nailing the primary study-flow (§3a) —
"help me study for test Y on concept Z, go" — with exact, cited retrieval.** If the DB
can't do that, none of the rest matters. Rank:

1. **Embedding + chunking quality** — real embeddings (not hash), concept-aware chunking
   that keeps definitions/theorems intact. This is what makes "knows the exact definition,
   doesn't make stuff up" *true*.
2. **Right relations in the right places** — the graph edges that make school retrieval
   dense: definition→used-in→theorem→tested-on→assignment; course→topic→lecture. This is
   where graph-RAG beats flat RAG.
3. **The graph-traversal retrieval approach** — how the backend walks that graph from a
   query spec (§3).
4. *Everything else* — version chains, automations, managers. Keep these **simple** until
   a real need forces more; spend effort on 1–3.

## Open questions (feed `12-open-questions.md`)

- Which local embedding model for exact-definition recall, and does School retrieval
  need an API-quality embedder to hit the "exact textbook definition" bar?
- Confidence threshold + features for identity resolution (§6) — start conservative.
- Does the backend "database model" run as a persistent agent, or as stateless
  per-request calls the MCP spins up? (Leaning stateless-per-request for determinism.)
- Live finance (Plaid/SimpleFIN) vs. statement-only — defer until the export flow is
  proven insufficient.
