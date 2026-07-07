---
name: study-assistant
description: >-
  Use when studying, cramming, or working with the user's SCHOOL course
  materials (textbooks, slides, past exams, formula sheets, lab manuals), or when
  asked to build a study guide, make a practice exam, summarize a topic across a
  course, or organize/deduplicate the SCHOOL folder. Orchestrates the `knowledge`
  MCP server (catalog + cited retrieval) and the `calculator` MCP server, and
  produces cited, exact study deliverables.
---

# Study Assistant

The user's coursework lives in a `SCHOOL/` folder organized by course code
(e.g. `MATH225`, `PHYS234`). A SQLite **catalog** indexes every document; a
**knowledge** index gives cited retrieval over notes/textbooks; the
**calculator** does exact math. All three are exposed as MCP tools. Always
prefer these tools over guessing, and always carry citations.

## 1. Orient before answering

- `list_courses()` — what courses exist and how many documents each has.
- `catalog_stats()` — totals by course and type (textbook / slides / exam /
  solutions / formula-sheet / manual / assignment / notes), plus duplicates.
- `find_documents(query, course="")` — locate the exact file. Returns
  `current_path`, so you can open/read the PDF directly. Examples:
  `find_documents("orthogonal bases", "MATH225")`,
  `find_documents("past exam", "PHYS234")`, `find_documents("formula sheet")`.

## 2. Look things up (always cite)

- `search_notes(query, k=8)` — default vector lookup; each hit has a citation
  (source, heading_path, page). Use for "what's the formula", "show a worked
  example", "what did the notes say about X".
- `get_section(source, heading_path)` — pull a section **verbatim** from the
  corpus (exact wording, not reassembled).
- `synthesize(query)` — explicit cross-topic gather (e.g. "everything about
  special relativity across the course"). Use only for genuinely cross-cutting
  questions; for a simple lookup use `search_notes`.
- `related_concepts(concept)` — concept-graph neighbors ("how does X connect to
  Y").
- `list_sources()` — what's currently in the knowledge index.

If `search_notes` says the index is empty, the documents are catalogued but not
yet ingested — see Maintenance below.

## 3. Do every calculation with the calculator

Defer all math to the **exact-math** skill / `calculator` tools (calc_symbolic,
calc_numeric, calc_matrix, calc_units, constants, propagate_uncertainty, …).
Never hand-compute a worked example or a numeric answer in a study deliverable.

## 4. Deliverable recipes

**Study guide for course X**
1. `find_documents("", "X")` to see slides, notes, past exams (or
   `python scripts/catalog.py --list X`); read the relevant PDFs.
2. `search_notes` for each key concept → keep the citations.
3. Run every formula/example through the `calculator`; include exact result +
   LaTeX and worked `steps`.
4. Produce a formatted document: topic summaries → key formulas → worked
   examples → "likely exam questions", each line traceable to a source.

**Practice exam**
1. Catalog the past exams and their solutions (`doc_type` = exam / solutions).
2. Assemble a problem set from them; vary the numbers where useful.
3. Build the answer key with the `calculator` so every answer is verified.

**Organize / declutter SCHOOL**
1. Re-scan the catalog (Maintenance below).
2. Review `--duplicates` (byte-identical copies) and `--possible-duplicates`
   (same title, different bytes — e.g. two scans of one textbook).
3. Preview `--plan-renames`, then `--apply-renames` (reversible via
   `--undo-renames`). Renames and deletes are opt-in; never delete files without
   the user's explicit go-ahead (`--delete-duplicate-files --yes`).

## 5. Maintenance (run in the project folder)

```bash
python scripts/catalog.py            # incremental catalog scan of SCHOOL
python scripts/catalog.py --full     # re-hash + re-derive names/types
python scripts/catalog.py --stats    # totals by course and type
python scripts/catalog.py --list MATH225
python scripts/reindex.py            # ingest raw -> corpus -> embeddings (+ graph)
```

Both are incremental and safe to re-run — good candidates for a scheduled weekly
task ("re-scan SCHOOL, reindex new notes, tell me what's new").

## Notes

- Defaults run fully local (offline catalog, numpy store, local/hash embeddings),
  so coursework stays on the machine. If `EMBEDDING_PROVIDER=openai` or a VLM PDF
  converter is enabled, document content is sent to a cloud API — flag that to
  the user before using it.
- Keep every claim traceable: prefer an answer with a citation over an
  unsourced one.
