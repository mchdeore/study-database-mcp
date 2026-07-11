---
name: email-context-capture
description: >-
  Use when the user wants to scan/triage their email (Gmail and/or Outlook) and
  capture what they're up to into the Life Vault. For Gmail, the built-in
  connector now does the heavy lifting: it classifies every message from its
  headers and either keeps it as a clean note, rolls it into one weekly digest,
  or skips it — so your job is to preview, sync, review the keepers/digest, and
  route anything actionable (courses→school, bills→finance, events→calendar).
  Outlook (no connector) is still read live in the browser. Redacts secrets,
  keeps finance separate. Trigger on: "read my email", "scan my inbox", "what's
  in my Gmail/Outlook", "capture my email context", "triage my email".
---

# Email Context Capture

Record **the context of what the user is up to** from their inboxes into the Life
Vault, so it's available to whatever reads the DB later. This is broader than
deadline extraction: capture ambient life/career/school context, not just action
items.

The big change from earlier runs: **Gmail is now handled by the connector, which
triages automatically.** Don't hand-copy 400 messages. Preview → sync → review the
handful of keepers and the weekly digest → route the actionable few. Spend your
effort on judgment (what matters, where it belongs), not transcription.

## Two inboxes, two paths

- **Gmail (personal, marc.chedore@gmail.com)** — use the **connector** (structured,
  read-only, deduped). It classifies each message header-only and keeps / digests /
  skips it (see below). Only if the connector isn't authorized, fall back to reading
  live at `https://mail.google.com`. This is where **job alerts, banking, personal**
  mail lives.
- **Outlook (UW, mchedore@uwaterloo.ca)** — **no connector**; read live in the
  browser (already signed in) at `https://outlook.office.com/mail/`. This is where
  **course / co-op / university** mail lives (mostly LEARN mirrors + co-op + dept).

## How the Gmail connector triages (so you know what's already handled)

Each message is classified from its Gmail labels + list headers (never its body):

| Class | Action | Where it goes |
|-------|--------|---------------|
| starred | **keep** | own note, importance 4, never auto-expires |
| important | **keep** | own note, importance 3, ~180-day TTL |
| personal / primary | **keep** | own note, importance 3, ~90-day TTL |
| bulk / list mail (newsletters, job alerts, `List-Unsubscribe`) | **digest** | one rolling **weekly digest** note (`gmail://digest/<year>-Wxx`) |
| promotions, social, drafts/spam | **skip** | not written (only counted) |

So promos and LinkedIn-style alert floods never hit the vault as individual notes;
a week of job alerts becomes one skimmable digest; real mail is kept and scored.

## Procedure — Gmail (connector path)

1. **Preview first (writes nothing):** call `preview_gmail` (optionally with a Gmail
   `query` like `is:important newer_than:7d`). Read the returned `classes`/`actions`
   counts and `keepers` list to see the inbox shape and pick a good query.
2. **Sync:** call `sync_google(gmail=True, gmail_query="…")` (or
   `scripts/vault_sync.py --gmail`). The report's `classes` + `digests` tell you
   exactly what landed. Re-syncing is idempotent (deduped by `source_ref`).
3. **Review the keepers + digest:** `search_vault(query="…", category="50-resources/mail")`,
   or open the current weekly digest note. This is where your judgment adds value.
4. **Route actionable items** out of mail into their proper area (see Routing). The
   connector does NOT route — that's your job.
5. If the connector is **not authorized** and the user wants Gmail now, read live in
   the browser (search `in:inbox newer_than:7d`); rows lazy-load and shift, so prefer
   search over clicking a position, and re-screenshot before clicking.

## Procedure — Outlook (browser path)

1. **Read the contracts first:** parent `data/incoming/README.md`, then
   `data/incoming/email/README.md` (+ `finance/` and `school/` READMEs for routing).
2. Open mail, search the past week (`received>=YYYY-MM-DD`), **screenshot** the list
   to scan headers. `get_page_text` returns the open message body. Open only the
   messages whose context you actually need.
3. **Distill → write** context notes into `data/incoming/email/` per its contract
   (`type: email`, `source: email`, `category: 50-resources/mail`,
   `source_ref: email://msg/<id>` or `email://thread/<slug>`, `from`, `date`), then
   `import_staging(area="email")`. Group related routine mail (e.g. a week of alerts)
   into **one** context note — mirror the connector's digest style, don't flood.
   Set `expires` ≈ received + 30 days for routine mail; for a keeper raise
   `importance` (4–5) / `pinned: true` and omit `expires`.

## Routing (both paths) — where things become useful

**In addition to** the mail note/digest, extract actionable items into their area:

- A dated **event / exam / assignment → `school/`** (contract-compliant doc; also
  puts it on the timeline via `start`).
- A **bill → `finance/`** — *deferred for now; just note its existence.*
- A hard external deadline that isn't a course (e.g. WaterlooWorks co-op ranking) —
  **surface it to the user**; file only if they want it.

**Finance is SEPARATE:** do not write finance docs from email in this pass. When you
see banking/statements/transfers, note their existence but leave them for the finance
area. Never copy account numbers, balances, or figures into mail notes.

## Privacy (always)

Redact secrets: no full account numbers, passwords, 2FA/OTP codes, API keys. Last-4 +
summaries only (parent `data/incoming/README.md` privacy rule). The connector stores
only cleaned header + snippet text (no message bodies), but anything YOU write from a
browser must be redacted by you.

## Decluttering the inbox (optional, ask first)

The connector already keeps the *vault* clean; decluttering the actual Gmail inbox is
a separate, optional action. If the user asks: **archive, don't mark spam** (archiving
removes clutter but keeps mail searchable and doesn't train the spam filter). Only ever
archive clearly-promotional mail — never mail from a person, the university
(uwaterloo.ca / LEARN / co-op / Crowdmark / Piazza), banking, or recruiter/job mail.
Confirm the list with the user before any bulk action. (Browser archive is fiddly:
hover the row, click the inline **Archive** icon — the box-with-down-arrow, just LEFT
of trash — one at a time, re-screenshotting as the list shifts.)

## Notes / gotchas

- Outlook's past-week mail is often ~90% **LEARN announcement mirrors** already
  captured by the D2L scraper — don't re-file those; capture only *new* signals
  (e.g. "MATH 225 midterm graded", "PHYS 263 active on Piazza").
- Context that has lived ONLY in email before: a **co-op ranking deadline**
  (WaterlooWorks), **job/intern alerts**, **course completions**, and a **housing
  thread** (landlord inspection). These are exactly what to surface/route.
- Prefer the connector for Gmail: it's deduped and idempotent, so you can re-run it
  safely, and it won't flood the vault the way the old one-note-per-message sync did.
