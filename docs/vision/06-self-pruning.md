# 06 — Self-pruning

"Self-pruning" is the feature that keeps the vault from becoming a junk drawer.
It must be **trustworthy**: never silent, never irreversible by surprise, always
explainable. The guiding rule: *prune like a good archivist, not a shredder.*

## The lifecycle of a note

```
active ──(low score / TTL hit)──▶ archived ──(grace period)──▶ tombstoned ──▶ (optional) deleted
   ▲                                  │                             │
   └──────────── undo ────────────────┴────────── restore ─────────┘
```

- **active**: normal, fully ranked in search.
- **archived**: moved to `90-archive/`, still searchable but ranked down. This is
  the default outcome of pruning. Reversible by moving back.
- **tombstoned**: removed from the vault but recorded in the tombstone log with a
  frontmatter snapshot. Restorable from the log.
- **deleted**: original file in `60-sources/` and tombstone payload removed. Only
  happens on explicit opt-in after a long grace period.

## The prune score

Each note gets a `prune_score` (lower = more prunable), recomputed on the
scheduler run. Conceptually:

```
prune_score = w_recency   * recency(updated, last_access)
            + w_usage     * usage(access_count)
            + w_pin       * (pinned ? large : 0)
            + w_importance* importance        -- 0..5
            + w_links     * incoming_link_count
            - w_age       * staleness
```

- **Pinned** or high-importance notes are effectively never pruned.
- Notes that get surfaced/used often climb; notes nothing ever touches sink.
- Highly-linked notes (a person, a live project) are protected.
- All weights live in `.vault/prune.config.yaml` so you tune the policy in one
  auditable place. **DECISION:** weights are config, not code.

## Policies (each is opt-in and independently configurable)

1. **Dedup on ingest.**
   - Exact: identical `content_hash` → keep one canonical, link the rest.
   - Near: embedding cosine above a threshold → flag as possible duplicate for
     review (never auto-merge bodies). Reuses the catalog's dedup approach.

2. **TTL / expiry.** A note with `expires:` set is auto-archived when the date
   passes. Good for transient stuff (one connector category: ephemeral emails,
   short-lived clips).

3. **Decay archival.** Notes whose `prune_score` falls below a threshold AND that
   haven't been touched in N days get archived. N and the threshold are config.

4. **Compaction / summarization.** A cluster of many small, old, related notes
   (e.g. 50 web clips on one topic) can be summarized into a single digest note;
   originals are archived (not deleted) and linked from the digest. This is the
   "self-pruning relational" magic: the *signal* is compacted, the *originals*
   are preserved. **DECISION:** compaction uses the LLM and is therefore opt-in
   and cost-gated (see `10-cost.md`); default off until you turn it on.

5. **Category caps (optional).** "Keep at most the 200 highest-scoring web clips,
   archive the rest." Prevents any one firehose category from dominating.

## Trust & safety guarantees

- **Dry-run first.** `prune --dry-run` prints exactly what *would* change and why
  (note, action, score, policy). The scheduler can be set to dry-run + report
  only, so you approve before anything moves.
- **Everything is logged.** Every archive/delete writes a `tombstones` row and a
  line in `.vault/tombstones.md` you can read.
- **Undo.** `prune --undo <batch-id>` restores a whole run. `restore <note-id>`
  restores one note from archive or tombstone.
- **Never touch pinned/important.** Hard rule, not a weight, for `pinned: true`.
- **Hard-delete is a separate command** with its own confirmation and grace
  period; the routine pruning loop never hard-deletes.

## The audit experience

You should be able to answer, for any note, "why is it where it is?" — the
tombstone log + the score breakdown (`explain-prune <note-id>`) give you that.
