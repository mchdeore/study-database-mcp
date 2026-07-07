---
name: email-context-capture
description: >-
  Use when the user wants to scan/triage their email (Gmail and/or Outlook) and
  capture what they're up to into the Life Vault. Reads everything (read + unread)
  in the past week, distills substantive context — courses, co-op/job-hunt,
  appointments, admin, meaningful personal (housing, interviews) — into
  contract-compliant Markdown notes under data/incoming/email/, routes any
  bills→finance/, events/assignments→school/, keeps finances separate, redacts
  secrets, and (optionally) declutters the inbox by archiving promos. Trigger on:
  "read my email", "scan my inbox", "what's in my Gmail/Outlook", "capture my
  email context", "clean up my email".
---

# Email Context Capture

Scan the user's inboxes and record **the context of what they're up to** into the
Life Vault, so that information is available to whatever/whoever reads the DB later
— regardless of whether the user already knows it. This is broader than deadline
extraction: capture ambient life/career/school context, not just action items.

## Scope (current defaults — confirm if unsure)

- **Window:** everything **(read + unread) in the past week** (`newer_than:7d` in
  Gmail; `received>=<date 7 days ago>` in Outlook). Not just unread.
- **Capture:** all *substantive* context — course/LEARN items, co-op & job-hunt
  activity, appointments, admin, and meaningful personal (housing, interviews,
  opportunities). **Skip pure junk** (marketing, promos, newsletters).
- **Finance is SEPARATE:** do **not** write finance docs from email in this pass
  (the user is building the finance module later). When you see banking/statements/
  transfers, **note their existence** but leave them for the finance area. Never
  copy account numbers, balances, or figures into email notes.
- **Redact secrets** always: no full account numbers, passwords, 2FA/OTP codes,
  API keys. Last-4 + summaries only (parent `data/incoming/README.md` privacy rule).

## Accounts

- **Outlook** (UW, mchedore@uwaterloo.ca) — already signed in in the browser; read
  live via Claude-in-Chrome at `https://outlook.office.com/mail/`. This is where
  **course / co-op / university** mail lives (mostly LEARN announcement mirrors +
  co-op + department mail).
- **Gmail** (personal, marc.chedore@gmail.com) — the Gmail **connector** is the
  clean path (structured search) but is often not connected; if so, read live in
  the browser at `https://mail.google.com`. This is where **job alerts, banking,
  personal** mail lives (little/no course mail).

## Procedure

1. **Read the contracts first** (they define exactly how to write): parent
   `data/incoming/README.md`, then `data/incoming/email/README.md` (+ `finance/`
   and `school/` READMEs so you can route items correctly).
2. **Outlook:** open mail, search the past week (`received>=YYYY-MM-DD`), read the
   list. `get_page_text` on the reading pane gives an open message's body;
   **screenshot** the list to scan headers. Open only the messages whose context
   you need to capture accurately.
3. **Gmail:** search `in:inbox newer_than:7d`. Same approach. The list **lazy-loads
   and rows shift** — prefer **search** for a specific message over clicking a
   position; when clicking, re-screenshot first.
4. **Distill → write email notes** into `data/incoming/email/` per its contract
   (`type: email`, `source: email`, `category: 50-resources/mail`,
   `source_ref: email://msg/<id>` or `email://thread/<slug>`, `from`, `date`).
   - **Ephemeral:** set `expires` = received + ~30 days for routine mail; for
     something worth keeping raise `importance` (4–5) / `pinned: true` and omit
     `expires`.
   - Keep each note a **concise summary with headings**, not a raw dump. Group
     related routine mail (e.g. a week's job alerts) into one context note.
5. **Route actionable items** out of email into their proper area **in addition to**
   the email note: a dated **event/exam/assignment → `school/`** (contract-compliant
   doc); a **bill → `finance/`** *(deferred here — just note it)*. A hard external
   deadline that isn't a course (e.g. WaterlooWorks co-op ranking) — surface it to
   the user; file only if they want it.
6. **Validate** every new `email/` doc against the contract (YAML parses, required
   fields present, `source_ref` unique, dates quoted ISO, no secrets, headings in
   body). 0 errors before finishing.

## Junk cleanup (declutter the inbox)

- **Rule for "junk":** clearly-promotional mail only — store blasts, marketing,
  newsletters, "X% off". **Never** touch mail from a person, the university
  (uwaterloo.ca / LEARN / co-op / Crowdmark / Piazza), banking/financial, LinkedIn
  job alerts, or recruiter/job mail. Leave LinkedIn connection requests.
- **Method: ARCHIVE, don't mark spam.** Archiving removes clutter from the inbox
  but keeps mail searchable and does **not** train the spam filter (marking spam can
  hide future legit mail from that sender). Confirm the method with the user if they
  literally said "junk/spam".
- **Gmail archive that works reliably:** the toolbar-icon click and the `e` shortcut
  proved flaky via automation. What works: **`hover` over the row**, then click the
  inline **Archive** icon that appears at the right (≈ x=1414 at 1549-wide; it's the
  box-with-down-arrow, immediately LEFT of the trash — don't hit trash). Do **one at
  a time** and re-screenshot: the list shifts up after each archive.
- Outlook: existing user rules may already route promos (e.g. Walmart → "semi
  trash"); don't duplicate. Show the user the list before a first bulk action.

## Notes / gotchas (from the last run, 2026-07-05)

- Outlook's past-week mail was ~90% **LEARN announcement mirrors** already captured
  by the D2L scraper — don't re-file those; capture only *new* context signals
  (e.g. "MATH 225 midterm graded", "PHYS 263 active on Piazza").
- Real captured context that lived ONLY in email: a **co-op Cycle 2 ranking
  deadline** (WaterlooWorks; rankings close a hard date), **job/intern alerts**
  (LinkedIn: Clio/Hitachi/Scotiabank/TD; Nokia ASIC CAD Coop), **Coursera** ML
  course completions + new RL specialization, and a **housing thread** (landlord
  inspection: broken CO/smoke detector cost-split, thermostat rules, remove hoop).
- `get_page_text` returns the currently-open message body, not the list — screenshot
  the list; search for specific messages rather than clicking shifting rows.
- Financial mail seen but deliberately NOT captured (deferred to finance module):
  BMO/RBC alerts, Interac e-Transfers, Wealthsimple, bill payments.
