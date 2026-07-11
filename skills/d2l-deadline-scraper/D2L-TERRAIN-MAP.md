# D2L Terrain Map — UWaterloo LEARN (recon 2026-07-05)

A one-time structural crawl of all 6 courses, so future scrapes know *where information
lives, how deep it goes, and what external sites are linked* — instead of guessing.
This is the planning substrate for the tiered scrape (see SKILL.md).

## Course IDs (org units)
| Course | ou (ID) | Instructor | Platform notes |
|--------|---------|-----------|----------------|
| MATH 225 | 1261421 | Trelford | quizzes off-LEARN (tutorial/Crowdmark) |
| PHYS 263 | 1266168 | Martin | in-person tests; **Piazza** (code: newton); no Assessments menu |
| PHYS 234 | 1268660 | Thompson | dropbox assignments; Crowdmark tests |
| PHYS 260B | 1271009 | Crone | lab; deepest content tree; Crowdmark dropbox |
| PHYS 242 | 1277772 | Mariantoni | **Crowdmark** for home components + exams; deep HA tree |
| PHYS 267 | 1278886 | Pattar | **.IPYNB** notebooks; LEARN quizzes + dropbox |

## Universal nav bar (same on every course)
Reachable by direct URL (`<ou>` = course id):

| Menu → item | URL pattern | What's there / why it matters |
|-------------|-------------|-------------------------------|
| Course Home | `/d2l/home/<ou>` | Announcements widget, Content Browser, Zoom widget |
| **Course Materials → Content** | `/d2l/le/content/<ou>/home` | the module tree (see per-course below) |
| Course Materials → **Rubrics** | `/d2l/lms/rubric/...?ou=<ou>` | **grading criteria** — how work is scored (crevice) |
| Course Materials → **Checklist** | `/d2l/le/checklist/...?ou=<ou>` | instructor's intended task list (crevice) |
| **Connect → Discussions** | `/d2l/le/...discussions...?ou=<ou>` | instructor clarifications / Q&A (crevice) |
| Connect → Classlist / Groups | — | who's in the course / group assignments |
| Connect → **Virtual Classroom** | Zoom LTI | live-class / recording links |
| **Assessments → Dropbox** | `/d2l/lms/dropbox/user/folders_list.d2l?ou=<ou>` | assignment due dates + **your submission status + scores** |
| Assessments → **Quizzes** | `/d2l/lms/quizzing/user/quizzes_list.d2l?ou=<ou>` | LEARN quizzes (often empty) |
| Assessments → **Surveys** | `/d2l/lms/survey/...?ou=<ou>` | occasionally holds pre-class / feedback items (crevice) |
| **Grades** | `/d2l/lms/grades/index.d2l?ou=<ou>` | **weights + what's been returned + your standing** (crevice) |
| Calendar / Course Schedule | `/d2l/le/calendar/<ou>` | definitive upcoming-events list (List view) |
| Competencies | `/d2l/lms/competencies/competency_list.d2l?ou=<ou>` | learning outcomes (low value) |
| Content **"Course Schedule"** sidebar widget | in Content | shows "N upcoming events" — instant has-deadlines check |

**Menu-label variants:** MATH 225 & PHYS 267 label the assessments menu **"Submit."**
PHYS 263 has **no Assessments menu at all** (Course Home · Content · Grades · Course Admin
+ dropdowns) — its tests are in-person and it uses Piazza.

## The crevices — where hidden info actually hides
Ranked by how often instructors bury real info there and how much a normal scrape misses it:

1. **Course outline (external)** → `outline.uwaterloo.ca` — full grade weights, every
   exam date, policies, schedule. **Duo-gated.** Linked from Content as a "Link" item;
   also reachable via the outline site's "My Enrolled Courses" list. *One-time per term.*
2. **Crowdmark (external)** → `app.crowdmark.com` — graded tests/assignments **with
   per-question feedback and where you lost marks**. Sign in via "Sign in with LEARN"
   (SSO, no password). PHYS 234, 242, 260B, MATH 225 use it (263 & 267 quizzes do not).
   *Highest-value weak-spot source.*
3. **Announcement bodies** → `/d2l/lms/news/main.d2l?ou=<ou>` — bodies render **inline**
   on this page (sort newest-first by clicking Start Date; screenshot to read — page-text
   misses bodies). Early-term posts state policies that still apply.
4. **Content "Web Page" items** — HTML pages *inside* Content (not files): e.g. MATH 225
   "Midterm Information", PHYS 260B "Installing Python… / Running Jupyter". Easy to skip.
5. **Formula sheets** in exam/midterm-related modules (found `phys263_formula_sheet_generated`
   and PHYS 267 "Midterm/Exam Related Content → Formula sheet").
6. **Rubrics + Checklists** (Course Materials menu) — grading criteria + task lists.
7. **Grades page** — the real weight table + what's returned.
8. **Piazza (external, 263)** → piazza.com/uwaterloo.ca/spring2026/phys263 (code: newton)
   — pinned posts, "what's on the test" threads, instructor answers.
9. **External file hosts** — Dropbox-hosted PDFs (263 links a generated-notes PDF on
   dropbox.com), Vitalsource (etext), Zoom recordings.
10. **`.IPYNB` notebooks (267)** — Jupyter files; content not readable as plain text
    without a notebook viewer/conversion.

## Per-course content-tree shape (top level; deep-dig expands all)
- **PHYS 234**: course outline(ext) · eTextbook · notes(1) · solutions(3) · **tests(4)**
  (S2023test1 + solutions, S2026 test1 solutions, Test #1 review video). Menu: Course
  Materials/Connect/Assessments all present.
- **PHYS 263**: Course information · **formula sheet PDF** · SR notes · Kleppner NYT article ·
  "Papers, etc." · code · course outline(ext). Lean nav; Piazza off-site.
- **PHYS 242**: **deep** — Syllabus · Notes · Lectures(21) · Videos OPTIONAL(91) ·
  HA01–HA07 (+ Solutions subfolders) · HC01. Home components + exams on **Crowdmark**.
- **PHYS 260B**: **deepest** — Web-page (Python setup) · **Exp 1 EKG / Exp 2 Circuit /
  Exp 3 Noise / Exp 4 Chaotic Pendulum** folders, each with manual/data/homework
  sub-items. Expand-All needed.
- **PHYS 267**: Lectures(32) · **Workbooks/Worksheets (.IPYNB)** · Assignments(3) ·
  Midterm/Exam Related (formula sheet) · Quiz Solutions(5).
- **MATH 225**: TOC · Course Information(2, incl. a Web-page) · Resources(3) · Lectures ·
  Quizzes(solutions) · **Midterm**(info page + practice) · **Final Exam**(placeholder).

## Practical scraping notes (learned)
- **Valence API is blocked** in this environment except simple GETs (`myenrollments`
  works; anything with query-string datetimes / cookie-scoped data → `[BLOCKED]`).
  Don't route around it — use visible-page scraping.
- **Content is a SPA**: after clicking a sidebar module, page-text may show the *previous*
  module → screenshot, or read the tab title. Use `read_page filter:interactive` to get
  the module list + external hrefs in one shot (fast, no clicking).
- **Duo** may prompt on outline.uwaterloo.ca / Crowdmark first hit → pause for the user;
  once authed, the whole session is open.
