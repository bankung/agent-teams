---
name: tn-jobs
description: >-
  Job-search operations playbook — mine alert emails, sweep application responses,
  reconcile against the tracker, deep-dive a company/role, check live posting status,
  run a postmortem on a rejection, rank the pipeline by comp, or log a status update.
  Use when the operator mentions job search, job application, recruiter, JobsDB,
  Michael Page, Robert Walters, comp band, salary floor, pipeline ranking, tracker,
  interview, rejection, postmortem, or asks about any specific company/role in the
  job-search context.
argument-hint: >-
  mine-alerts | sweep | reconcile | deep-dive <company/role> |
  live-status <role> | postmortem <application> | comp-rank | log <update>
allowed-tools:
  - Read
  - Bash(curl:*)
  - WebFetch
  - Write
  - Edit
---

# /tn-jobs — job-search operations playbook

`$ARGUMENTS` contains the verb and its options. Follow this playbook exactly.

---

## 0. Compose boundary (MANDATORY — read before anything else)

**tn-jobs owns job LOGIC. tn-email owns email MECHANICS.**

- For any verb that reads email (mine-alerts, sweep): compose tn-email via its verbs
  (`search`, `read`, `thread`, `sweep-jobs`, `open --jobs`). Never call
  `curl http://localhost:8456/api/tools/email/*` directly from this skill.
- tn-jobs ONLY processes what tn-email returns: it matches roles, deduplicates, classifies
  responses, updates the tracker, and presents findings. The email transport layer is
  tn-email's domain entirely.
- tn-email requires `X-Project-Id: 599`. Pass that binding when composing.

---

## 1. Dedup-before-recommend guard (NON-NEGOTIABLE — every verb)

**BEFORE recommending, adding, or flagging any role:**

1. Read the LATEST tracker file:
   `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\job-search-tracker.md`
   The tracker timestamp tells you which version you have. Memory and shortlists LAG reality
   (prior incident: 4 already-applied roles re-recommended because memory was stale — 2026-06-04).
2. Cross-check against ALL of:
   - **A-rows** (APPLIED section, A1–A25+) — never re-recommend an already-applied role.
   - **SKIP / ❌** rows — confirmed exclusions; do not re-surface.
   - **Stop-list** (FWD all entities — the stop-list entities, former employers, and HARD-RED companies enumerated in the tracker's stop-list and `project_job_search_red_flags.md`; do not re-surface any of them).
   - **Red flags** (Section 3 of tracker: HARD RED = auto-skip; YELLOW = flag explicitly, do not auto-recommend).
3. If a role is already in the tracker with any status, report its current status — do NOT present it as new.
4. If the tracker has been updated since the session started, re-read before the next dedup pass.

---

## 2. Reference files (do NOT copy content — point to these)

- **Scoring / criteria:** `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\shared\job-criteria.md`
  — 4-category 0–100 rubric (skills 35%, salary 25%, location 20%, deal-breakers 20%),
  cover letter structure, operator-specific overrides.
- **Comp-first strategy:** `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\shared\feedback_comp_first_strategy.md`
  — comp band > title; over-tier penalty reality; salary floor and sweet-spot bands defined in that file;
  recruiter channel > Easy Apply at this level.
- **Master tracker:** `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\job-search-tracker.md`
  — A-row format, status column, SKIP list, red flags, section 5 workflow.
- **Red flags supplement:** `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\shared\project_job_search_red_flags.md`

---

## 3. Hard rules (no exceptions)

- **No auto-apply.** This skill never submits an application. Apply/submit is always operator-gated
  and performed manually by the operator. The workflow is: prepare bundle → operator reviews → operator submits.
- **Recommend-not-execute on exploratory.** If the operator asks an open-ended question ("what jobs should I look at?")
  → produce a ranked list with rationale + STOP. Do not begin preparing bundles without an explicit go-signal.
- **PII guard.** Email body text, salary figures, and personal recruiter details stay in chat output only —
  do not write them to any _scratch file or Kanban task body.
- **LinkedIn via WebFetch only** (Terms of Service). Do NOT use firecrawl or scraping tools on LinkedIn URLs.
  For all other job sites (JobsDB, company careers pages), WebFetch is fine.

---

## 4. Verb playbooks

### 4a. `mine-alerts`

Mine JobsDB and Michael Page job-alert emails for new roles, match vs criteria, dedup vs tracker.

**Steps:**

1. Invoke tn-email to pull job alert emails from both inboxes:
   - Gmail: `tn-email search "from:jobsdb OR from:michaelpage subject:(job alert OR new jobs)" --cap 30`
   - Outlook: equivalent KQL search via tn-email
2. For each alert email, invoke `tn-email read <id>` to get the body (alert emails contain listing summaries).
3. Extract roles: for each listing in the email body, capture:
   - Role title · Company · Location · Salary (if stated) · Source (JobsDB id or MP ref)
4. **Dedup pass** (Section 1 — mandatory): cross-check every extracted role against tracker A-rows, SKIP, stop-list.
   Discard already-applied/skipped. Flag YELLOW companies explicitly.
5. **Score new roles** using the rubric in `job-criteria.md` (4-category weighted score).
   Apply comp-first ranking: sort by salary band first, title second (per `feedback_comp_first_strategy.md`).
6. Present:
   - New roles table: rank · title · company · salary · score · flag (NEW/YELLOW/verify-needed)
   - Deduped/skipped: brief list (role + reason)
7. STOP. Do not prepare bundles until operator approves.

---

### 4b. `sweep`

Sweep both inboxes for job-application mail (responses, interviews, rejections). Classify.

**Steps:**

1. Invoke tn-email `sweep-jobs` — this searches both Gmail and Outlook for application-response mail.
   It returns threads grouped by type (response/interview/reject/no-update). Use its output directly.
2. For any thread that needs more body context, invoke `tn-email read <id>` or `tn-email thread <id>`.
3. Classify each response into:
   - **Interview invited** — explicit scheduling request or phone screen
   - **Rejection** — "regret to inform", "not moving forward", "unsuccessful"
   - **Holding pattern** — "under review", "will be in touch"
   - **Offer/negotiation** — offer details, terms, next steps
   - **Recruiter outreach (new)** — inbound recruiter, not from an existing application
   - **Noise** — automated acknowledgement, no-reply, newsletter
4. **Dedup / tracker match:** for each classified response, identify the matching A-row in the tracker
   (by company + role title). Flag if no A-row found (untracked application).
5. Present summary table: A-row # · company · role · current tracker status → found email status.
   Highlight mismatches (tracker says "รอผล" but email says "ปฏิเสธ").
6. Propose `reconcile` as a natural follow-up. STOP.

---

### 4c. `reconcile`

Cross-reference email findings (from sweep) vs tracker A-rows. Apply factual status updates; flag ambiguous.

**Steps:**

1. Read the latest tracker (mandatory dedup read).
2. Take the sweep output (or re-run sweep if not already done this session).
3. For each A-row in the tracker with status "รอผล" (awaiting result):
   - Check if sweep found a matching email response.
   - If found rejection → propose status update: mark A-row result column "❌ ปฏิเสธ <date> (<source>)".
   - If found interview invite → propose: mark "📅 นัด interview <date>".
   - If found offer → propose: mark "✅ Offer received <date>".
   - If no email found → leave "รอผล" unchanged.
4. Flag ambiguous cases (multiple matches, unclear company identity, Confidential/MP references).
5. Show proposed changes as a diff table: A-row # · before → after. STOP and wait for operator approval.
6. After approval: apply updates via the `log` verb (Section 4h below).

---

### 4d. `deep-dive <company/role>`

Company profile + role scope + comp + requirements. Dedup vs tracker/stop-list first.

**Steps:**

1. **Dedup check** (Section 1): is this company/role already in the tracker (applied, skipped, or stop-listed)?
   If stop-listed → report and STOP (do not research further unless operator overrides).
   If YELLOW → proceed with explicit flag.
2. Research sources (parallel):
   - Company website careers page (WebFetch)
   - LinkedIn company page (WebFetch — ToS compliant read)
   - JobsDB posting if a job id is known (WebFetch)
   - Michael Page / recruiter page if applicable (WebFetch)
3. Compile:
   - **Company profile:** industry · size · ownership · BKK presence · culture signals
   - **Role scope:** title · seniority · team size · direct reports · reporting line · key responsibilities
   - **Requirements:** must-have skills · experience bar · language requirements · any hard-stops (SAP required? sponsorship?)
   - **Comp:** stated range or market estimate · base vs package · equity/bonus mentions
   - **Red flag scan:** cross-check against red flags in tracker Section 3 + `project_job_search_red_flags.md`
4. Score using `job-criteria.md` rubric. Apply comp-first ranking.
5. **Recommendation:** PROPOSE (apply / skip / investigate further / operator decides). Do NOT begin preparing bundle until operator approves.

---

### 4e. `live-status <role>`

Check if a posting is still accepting applications.

**Steps:**

1. Identify the source URL/id: JobsDB id, LinkedIn id, company careers link, or MP reference.
2. Fetch the apply page:
   - JobsDB: `https://th.jobsdb.com/job/<id>` via WebFetch
   - LinkedIn: `https://www.linkedin.com/jobs/view/<id>` via WebFetch (ToS — read only)
   - Company careers page: direct URL via WebFetch
   - Michael Page: MP ref link via WebFetch
3. Look for: "Apply now" button, "No longer accepting applications", "Position filled", "Closed",
   posting date vs today's date (if >90 days old on JobsDB, likely stale).
4. Report: status (OPEN / CLOSED / UNKNOWN) · last-seen date · apply link if open.
5. If CLOSED and A-row exists in tracker → flag for reconcile (remove from active pipeline).

---

### 4f. `postmortem <application>`

Reconstruct a rejected application → compare vs JD → rank rejection causes → improvement actions.

**Pattern (per Kanban #1952 rejection-analysis):**

1. **Reconstruct submission:** read tracker A-row for the application (role, company, date, source, CV version used).
   Use `tn-email sweep` output or `tn-email search` to find the original application email/confirmation
   and the rejection email. Read both via `tn-email read`.
2. **Retrieve JD:** if a JobsDB id or URL is in the tracker, fetch the JD via WebFetch.
   If no longer live, check `_jd-scrape/` folder:
   `C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\_jd-scrape\`
3. **Gap analysis:** compare what was submitted (CV version, cover letter framing) vs what JD required.
   Identify: missing skills, seniority framing mismatch, over-tier signals (CTO-framing to below-tier seat),
   salary-field entry (if Easy Apply — per #1952 easy-apply-hygiene rule: log submitted salary + fields).
4. **Rejection cause ranking:** score each potential cause (1 = most likely):
   1. Over-tier framing (senior-level CV to below-tier seat = flight-risk screen)
   2. Hard-skill gap (stated required skill not evidenced in CV)
   3. Comp mismatch (submitted salary > budget)
   4. Volume / automated screen (>100 applicants, keyword mismatch)
   5. Timing (role already filled internally before posting closed)
5. **Improvement actions:** concrete, actionable per cause (e.g., "re-tier CV for IC-level roles",
   "add PostgreSQL project to CV Section 2", "anchor desired salary to posted band floor").
6. Present: timeline table + gap analysis + ranked causes + actions. Note: do NOT re-recommend the
   same role unless the posting reopens and operator explicitly asks.

---

### 4g. `comp-rank`

Rank the ACTIVE pipeline by comp band first, title second.

**Steps:**

1. Read the latest tracker. Collect all ACTIVE rows: NEW (🔵⚠️⚪), SPOTTED, plus pending A-rows where result = "รอผล".
2. For each active role, extract comp:
   - Stated salary range from tracker or JD
   - If "undisclosed": use market estimate from `feedback_comp_first_strategy.md` heuristic
     (big-co Director/Head undisclosed = OK, flag as "est. above floor" for ranking purposes)
3. Sort by:
   - Primary: salary band LOW-END descending (high → low)
   - Tie-breaker: title tier (CTO/VP > Head/Director > IC)
4. Annotate each: floor match and sweet-spot flag per the salary floor / sweet-spot bands defined in
   `feedback_comp_first_strategy.md`; comp-first strategy fit (per that file).
5. Present ranked table: rank · role · company · salary (stated/est) · comp tier · status · note.
6. STOP. Do not auto-recommend applications from this ranking without operator approval.

---

### 4h. `log <update>`

Write a status update or new candidate entry to the master tracker.

**Pre-conditions:**
- `Write` and `Edit` are ONLY used in this verb. All other verbs are READ-ONLY.
- Operator must have explicitly approved the update (either in the current session or via prior go-signal).

**Steps:**

1. Read the latest tracker (mandatory — never overwrite stale content).
2. Identify the target section and row (A-row, NEW section, SPOTTED, SKIP, etc.).
3. Apply the minimum surgical edit:
   - Status update on an A-row: update the `ผล` column with date + outcome.
   - New application logged: add A-row with next sequence number, all columns populated.
   - New spotted role: add row to 👀 SPOTTED table.
   - Skip confirmed: move to ❌ SKIP table with reason.
4. Use `Edit` for surgical row-level changes. Do NOT rewrite whole sections.
5. After edit: read back the changed section to verify. Report the before → after diff in chat.
6. If this is a rejection status update, prompt: "Run `tn-jobs postmortem <A-row>` to analyse the rejection?"

**Tracker location:**
`C:\Users\banku\Documents\Personal\Projects\WebApp\secretary\jobs-search\job-search-tracker.md`

---

## 5. Error and edge cases

| Situation | Action |
|---|---|
| tn-email not authenticated | Run `tn-email auth-status` first; report OAuth URL; stop. |
| JobsDB / MP page returns 403 or blank | Note "live status unknown"; fall back to tracker date to estimate staleness. |
| LinkedIn WebFetch blocked / partial | Report what was read; flag as "partial — operator should verify on LinkedIn directly". |
| Tracker file not found at expected path | STOP; report path; do NOT guess an alternate location. |
| Ambiguous company match (Confidential / MP client) | Flag; ask operator for clarification before dedup conclusion. |
| Posting is listed as both NEW in alert AND A-row applied | A-row wins; do NOT re-recommend. Show A-row status instead. |

---

## 6. Usage examples

```
/tn-jobs mine-alerts
/tn-jobs sweep
/tn-jobs reconcile
/tn-jobs deep-dive "<Company Name>"
/tn-jobs deep-dive "<recruiter-listed role>"
/tn-jobs live-status "JobsDB <job-id>"
/tn-jobs postmortem <A-row>
/tn-jobs comp-rank
/tn-jobs log "<A-row> <company> — ❌ ปฏิเสธ <date> (no-fit; keep-in-system)"
```
