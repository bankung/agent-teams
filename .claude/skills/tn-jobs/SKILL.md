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
metadata:
  version: 1.0.0
  category: secretary
  tags: [job-search, email, tracker, comp]
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

## Reference Directory

Open the file for the verb in `$ARGUMENTS` before executing any steps.

| Verb | Reference file | Description |
|---|---|---|
| `mine-alerts` | `references/mine-alerts.md` | Mine JobsDB and Michael Page alert emails for new roles; match vs criteria; dedup vs tracker |
| `sweep` | `references/sweep.md` | Sweep both inboxes for application responses; classify by type |
| `reconcile` | `references/reconcile.md` | Cross-reference sweep findings vs tracker A-rows; propose status updates |
| `deep-dive` | `references/deep-dive.md` | Company profile + role scope + comp + requirements; dedup first |
| `live-status` | `references/live-status.md` | Check if a posting is still accepting applications |
| `postmortem` | `references/postmortem.md` | Reconstruct a rejected application; gap analysis; ranked causes; improvement actions |
| `comp-rank` | `references/comp-rank.md` | Rank the active pipeline by comp band first, title second |
| `log` | `references/log.md` | Write a status update or new candidate entry to the master tracker (ONLY Write/Edit verb) |

Quick usage examples: `references/usage.md`.

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
