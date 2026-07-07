# 04 — Vault structure

The vault is the part you audit by eye, so the folder taxonomy must be obvious.
We use a **PARA-style + capture-and-archive** layout with numbered top-level
folders (Johnny.Decimal-ish) so order is stable and scannable.

## DECISION: top-level taxonomy

```
vault/
├── 00-inbox/          raw, unfiled captures land here first (triage zone)
├── 10-journal/        daily notes:  10-journal/2026/2026-06-29.md
├── 20-people/         one note per person:  20-people/jane-doe.md
├── 30-projects/       active, goal-with-an-end efforts (kitchen-reno/)
├── 40-areas/          ongoing life areas (health/, finance/, home/, work/)
├── 50-resources/      reference material, clippings, how-tos, saved reading
├── 60-sources/        IMMUTABLE originals (the PDF, the export) — never edited
├── 90-archive/        pruned / inactive material (still searchable, low-rank)
└── .vault/            machine files: manifest, taxonomy config, tombstones log
```

- `00-inbox/` is the only "messy" place. Auto-filing (later) moves notes from
  here into the right category; until then everything is still fully searchable.
- `60-sources/` holds the **binary/large originals** (PDFs, images, raw exports).
  The Markdown note that represents a source links to its file here. Originals are
  never mutated, so you always have the ground truth.
- `90-archive/` is where pruning sends things. Nothing leaves the vault on a
  prune — it just moves here and drops in rank. Hard-delete is a separate,
  explicit step (see `06-self-pruning.md`).
- `.vault/` holds generated/system files (kept in the vault so a clone is
  self-describing, but clearly namespaced with a dot).

## Note anatomy

Every note is Markdown with YAML frontmatter on top and a body below:

```markdown
---
id: 01J9Z3...              # stable unique id (never changes)
title: Kitchen reno budget
category: projects/kitchen-reno
created: 2026-06-29T18:20:00-04:00
updated: 2026-06-29T18:20:00-04:00
source: notion             # capture | file | google | notion | webclip
source_ref: notion://page/abc123
tags: [home, money]
people: [[jane-doe]]
importance: 3              # 0-5, manual pin/boost; default from policy
pinned: false
expires: null             # optional TTL date; null = no auto-expiry
status: active            # active | archived | tombstoned
---

# Kitchen reno budget

Body in Markdown. Links to other notes use [[wikilinks]].
Links to a source file: [original quote](../60-sources/contractor-quote.pdf)
```

## Links and the graph

- **Wikilinks** `[[note-name]]` express relationships between notes (people,
  projects, concepts). The indexer turns these into graph edges.
- **Typed links** (optional, later) via frontmatter keys like `people:`,
  `related:`, `parent:` give edges a meaning, which improves graph search.

## Naming rules (so the vault stays scannable)

- Lowercase, hyphenated filenames: `jane-doe.md`, `kitchen-reno-budget.md`.
- Dates as `YYYY-MM-DD`. Daily notes nested by year.
- One concept/person/project per note (atomic notes; easier to prune and link).
- The `id` in frontmatter — not the filename — is the stable identity, so files
  can be renamed/moved freely without breaking links in the DB.

## DECISION: static taxonomy + periodic regroup (not per-note auto-filing)

The folder taxonomy is **static and human-owned**. New notes land in `00-inbox/`
with correct frontmatter but are **not** LLM-categorized one-by-one (that would
spend a model call on every capture). Search works immediately regardless of
folder, because the index doesn't care where a file sits.

Instead, a cheap **periodic "regroup" batch job** (scheduler, opt-in, DeepSeek)
looks at the inbox + recent notes and **proposes** moves into existing categories
(and, if a cluster clearly needs one, proposes a *new* folder). It runs as a
**dry-run by default** — it writes a proposal you approve, then applies moves
(reversible, logged like pruning). So:

- Day to day: static, $0, predictable, fully auditable.
- Occasionally: a batch pass tidies the inbox and suggests structure changes.
- Adding/forking a category is a normal, cheap action — just a folder + a line in
  `.vault/taxonomy.yaml`. Not a "crazy feature," and it costs nothing until the
  regroup job (or you) chooses to use it.

## DECISION: Obsidian-compatible

The vault is plain Markdown + `[[wikilinks]]` + YAML frontmatter, which is exactly
what Obsidian reads. So you can point Obsidian at `vault/` for a graph view and
hand-editing, while our server owns ingestion, indexing, and pruning. The two
coexist because both treat the files as truth.
