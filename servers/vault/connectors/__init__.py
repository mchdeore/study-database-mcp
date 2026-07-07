"""Connectors: adapters that pull external data and write it into the vault as
Markdown (build step 4.x).

A connector is NOT a live pass-through the LLM queries. It normalizes an external
item into a well-formed note (frontmatter + body) and upserts it keyed by
`source_ref`, so re-syncing the same item UPDATES its note instead of creating a
duplicate. Everything then flows through the one search/prune/rebuild path like
any other note. See docs/vision/07-connectors-credentials.md.

`base.upsert_note` is the shared write primitive; each service (calendar, gmail,
...) is a thin transform on top of it. The transforms are pure (dict -> note
fields) so they're fully testable offline with fixture data; the live network
fetch + OAuth is a separate, thin layer added later.
"""
