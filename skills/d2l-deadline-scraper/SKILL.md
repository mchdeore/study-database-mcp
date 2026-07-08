---
name: d2l-deadline-scraper
description: >-
  Use when the user wants to pull, refresh, or check all their D2L / Brightspace
  (UWaterloo LEARN) course deadlines, assignments, quizzes, exams, and
  announcements, and stage them as structured Markdown for the Life Vault. Drives
  the browser live (Claude-in-Chrome) across every currently-enrolled course,
  extracts every time-sensitive item, writes one contract-compliant document per
  item into data/incoming/school/<course>/, and produces an urgency-ranked digest
  weighted by user-supplied per-course "fear" factors. Trigger on: "check my D2L",
  "pull my course deadlines", "what's due in LEARN", "refresh my school folder",
  "rank my courses by urgency".
---

# D2L Deadline & Priority Scraper

Pull every current deadline out of UWaterloo LEARN (D2L/Brightspace), stage each
as a Markdown document that satisfies `data/incoming/school/README.md`, and rank
the courses by urgency. This skill is **re-runnable**: run it any time to refresh
the school drop-folder.

## What you produce (end state)

- `data/incoming/school/<course-code>/` — one lowercase folder per enrolled course.
- One `.md` **per document** (assignment, quiz, exam, project, todo, note) with
  full YAML frontmatter per the school README contract (see *Contract mapping*).
- `data/incoming/school/_digest.md` — root ranking of courses + all deadlines.
- `data/incoming/school/<course>/_digest.md` — per-course summary.
  (Files prefixed `_`, like `_template.md`, are treated as non-content.)

## Before you start — confirm with the user

1. **Fear weights (0–1)** per course — higher = harder/scarier ⇒ surfaced earlier.
   If unknown, prompt after you've listed the enrolled courses. Last run's values:
   `PHYS234=0.75, PHYS242=0.75, PHYS260B=0.75, PHYS263=0.75, MATH225=0.25, PHYS267=0.25`.
2. **Scan depth** — default "exhaustive per course" (announcements + dropbox +
   quizzes + content modules + course schedule).
3. **Skip window** — ignore deadlines more than ~1 month past today.
4. Login is **live via Claude-in-Chrome**; the user completes any Duo/SSO prompt.
   Do NOT reuse stored credentials or handle passwords.

## Procedure

### 0. Browser + session
- Load Chrome tools via ToolSearch; `list_connected_browsers`; `tabs_context_mcp{createIfEmpty:true}`.
- Navigate to `https://learn.uwaterloo.ca`. If not signed in, pause and let the
  user finish SSO + Duo. Screenshot to confirm the homepage.

### 1. List enrolled courses (get org-unit IDs)
Read the homepage tiles for the **current term** (e.g. "1265 - Spring 2026"). To
get clean course codes + numeric IDs, in the page console run the Valence
enrollments call **for reading only** (this one worked; note some other API calls
are blocked by the sandbox — see Gotchas):

```js
// via javascript_tool
const r = await fetch('/d2l/api/lp/1.31/enrollments/myenrollments/?orgUnitTypeId=3&pageSize=100',
  {headers:{'Accept':'application/json'}, credentials:'include'});
const j = await r.json();
(j.Items||[]).map(it=>({id:it.OrgUnit.Id, code:it.OrgUnit.Code, name:it.OrgUnit.Name}));
```
Exclude non-course communities (e.g. departmental pages, training, SciSpace).
Confirm the course list with the user and collect fear weights.

### 1.5. Course outlines / syllabi — ONE-TIME per term (do this first run; skip on refreshes)

**The official course outline is the single richest source** — it has the full grade
breakdown (weights!), every exam/test date, project due dates, late/missed-work
policy, textbook, instructor/TA contacts, and the term schedule. Crucially, many
of these deadlines appear **nowhere on LEARN** (they're on Crowdmark, in in-person
tutorials, or set by the Registrar), so **skipping the outline means missing real,
high-weight deadlines.** Outlines **don't change during the term**, so pull them
once, write a `syllabus.md` per course, and on later refreshes only re-scan the
live sources in Step 2 (skip this step unless a `syllabus.md` is missing).

**Where UWaterloo outlines live:** `https://outline.uwaterloo.ca`. Fastest path —
open it and use **"My Courses" / "My Enrolled Courses"**: it lists every enrolled
course for the term with a **VIEW** button (each opens the outline in a new tab).
This also confirms full course titles and section numbers. Some courses also link
their outline from LEARN Content as a "course outline" module (an *External
Resource* link) — that link goes to the same outline site.

- **Duo/2FA:** the outline site may trigger a **Duo prompt**. Do NOT touch 2FA —
  ask the user to approve the push; once authenticated, the whole session (all
  courses) is accessible.
- **Read with `get_page_text`** on the outline tab — it returns the full article
  text cleanly (no screenshot needed). Grab: Class Schedule, Instructional Team,
  **Assessments & Activities** (the grade table), Late/Missed Content, Generative
  AI policy, Required Materials, Tentative Class Plan, and any "important dates".
- **Not every course uses the outline site.** If a course isn't listed (e.g. some
  lab courses), fall back to LEARN Content ("course outline"/"syllabus"/"Course
  Information" module) and note in `syllabus.md` that the grade table wasn't
  published there.

**Then extract every datable item** from the outline into its own contract-compliant
doc (exams/tests → `exam-*.md`, projects → `project-*.md`, home components →
`assignment-*.md`, make-up lectures/info → `note-*.md`), applying the ~1-month-past
skip. Tentative dates (e.g. "likely Thursday Aug 13"): capture the date but note it
as tentative in the body and set `start` to that date (use `00:00` offset time if
the time is TBD, and say so). Registrar exam **windows** (e.g. "Aug 7–19") with no
exact date → record in `syllabus.md` + a to-verify note, not a fake dated doc.

### 2. Per course, sweep these sources (URL patterns, `<ID>` = org unit id)
Navigate directly — much faster than clicking:

| Source | URL | Notes |
|--------|-----|-------|
| Announcements (bodies render **inline** on this page) | `/d2l/lms/news/main.d2l?ou=<ID>` | Click the **Start Date** column to sort newest-first. `get_page_text` often misses the bodies — **screenshot** to read them. |
| Dropbox / assignments | `/d2l/lms/dropbox/user/folders_list.d2l?ou=<ID>` | `get_page_text` works well here; shows due date, submission status, score. |
| Quizzes | `/d2l/lms/quizzing/user/quizzes_list.d2l?ou=<ID>` | Often empty even when a course has "quizzes" (they may live in Content or off-LEARN). |
| Content modules | `/d2l/le/content/<ID>/home` | SPA. Use `find` to locate sidebar module links, click via `ref`, then **screenshot** (page text caches the old module). Look for Quizzes/Midterm/Final Exam/HW modules. |
| Course Schedule (upcoming-events count) | sidebar link in Content, or `/d2l/le/calendar/<ID>` | The **List** view shows upcoming items with exact due date/time, starting today (auto-skips past). "N upcoming events" tells you instantly if a course is clear. |

**The all-courses Calendar List view** (`/d2l/le/calendar/<any ID>` → List) aggregates
every course's dated items, color-coded. Click an item to see its true course in
the detail (`… PHYS 267 - Spring 2026`). Use it as a cross-check — but it is **not
complete**: it misses announcement-only deadlines (see Gotchas), so still sweep
each course's announcements + dropbox.

### 3. For each time-sensitive item, capture
title · type · **posted date → `created`** · **due date/time → `start`** (+ `end`
if it spans time) · instructor · location · source URL · submission status ·
a **workload estimate** ("small / moderate / large — because …"). Gauge workload
from what's visible (e.g. "5 Griffiths problems"); **do not download PDFs** — if
you'd have to open a file to judge, leave workload blank and rely on the weighted
deadline-vs-difficulty score. **Skip** anything due > ~1 month ago.

### 4. Write one contract-compliant `.md` per item
Follow `data/incoming/school/README.md` exactly. Filenames `<type>-<slug>.md`.
Put "date checked" + source URL in a `## Provenance` body section (the contract
has no `checked` field — keep frontmatter to the allowed keys). **Never guess**:
omit optional fields you don't know; use README fallbacks for required ones.

### 5. Compute urgency + write digests
```
effective_days = days_until_due × (1 − 0.5 × fear_weight)
urgency        = 1 / (effective_days + 1)      # higher = do sooner
```
Rank items by urgency; rank courses by their most-urgent item. Compute in a real
script (don't hand-math — compute it). Write `_digest.md` at
the root (course priority table + all deadlines soonest-first + a "this week"
view + an undated/to-verify list) and one `_digest.md` per course folder.

### 6. Verify (always finish with this)
- Every `.md` frontmatter parses as YAML; all **required** fields present.
- `category` == `40-areas/school/<folder>` and matches the folder.
- `source_ref` is `school://<course>/<type>/<slug>`, unique across the tree.
- Every date-time is quoted ISO-8601 with `-04:00` (EDT) / `-05:00` (EST) offset.
- No placeholders (`TBD`, `N/A`, guesses). Spot-check 2–3 due dates against LEARN.

## Contract mapping (D2L → school README frontmatter)

| D2L thing | `type` | `created` | `start` |
|-----------|--------|-----------|---------|
| Dropbox assignment | `assignment` | announcement/post date (else today) | due date/time |
| Quiz (tool or calendar) | `quiz` | open date (else today) | due date/time |
| Midterm / final | `exam` | date scheduled/announced | exam start (+`end`) |
| Poster / project | `project` | availability date | due date/time |
| Unsubmitted item with **no** date | `todo` | today | *(omit — undated)* |
| Review session / cancellation / info | `note` | posted date | event time if any |
| Course outline / syllabus | `syllabus` | outline publish date | *(omit — undated)* |
| Make-up lecture / scheduled event | `note` | outline/announce date | event start (+`end`) |

## Gotchas (learned the hard way)

- **Each course structures deadlines differently.** Some put them in announcements
  (PHYS 234: Assignment #4 + final only appear in announcements), some only in the
  dropbox (PHYS 267), some in Content modules (MATH 225 midterm/quizzes), some
  nowhere on LEARN (in-class tests, off-LEARN Crowdmark quizzes). **Sweep all
  sources; don't trust a single one.**
- **⚠️ LEARN alone WILL miss high-weight deadlines — always read the outline (Step
  1.5).** Real examples from the last run: PHYS 242's Second Home Component (5%) +
  Final (50%) are on **Crowdmark**; PHYS 263's Test 2 (Jul 10) and Test 3 (Jul 24)
  are **in-person tutorial tests**; PHYS 267's **Term Project (25%, Aug 3)** and
  PHYS 234's **Test #2 (Jul 15, 20%)** only appear in the outline. A LEARN-only
  scan reported "0 upcoming events" for PHYS 242 and PHYS 263 — both were WRONG.
  If a course's Course Schedule says "0 upcoming events," that means *0 LEARN
  calendar items*, NOT "no deadlines" — verify against the outline.
- **The Calendar misses announcement-only deadlines** — cross-check, don't rely.
- **Announcement bodies**: the `news/main.d2l` list page renders bodies inline, but
  `get_page_text` frequently returns only titles/dates → **screenshot** to read.
- **Content is a SPA**: after clicking a sidebar module, `get_page_text` may still
  show the previous module → **screenshot**, or read the tab title.
- **Valence API is largely blocked** in this environment: only simple GETs like
  `myenrollments` succeed; calls with query-string datetimes or that return
  cookie-scoped data get `[BLOCKED: Cookie/query string data]`. **Do not try to
  route around the block** — fall back to visible-page scraping.
- **Do not** download files, enter credentials, or write to the user's calendar/
  Notion unless they explicitly ask — this skill only stages Markdown.
- Announcement lists default to **oldest-first**; click the Start Date header to
  reverse so the newest (current) items are on top.

## Reference: last run (2026-07-05, Spring 2026, 6 courses)
Course IDs — MATH225=1261421, PHYS263=1266168, PHYS234=1268660, PHYS260B=1271009,
PHYS242=1277772, PHYS267=1278886. Nine upcoming deadlines found; priority order
PHYS267 > PHYS234 > PHYS260B > (PHYS263, PHYS242, MATH225 had none active).
