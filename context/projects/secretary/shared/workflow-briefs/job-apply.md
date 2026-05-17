# Workflow brief — Job application

> Spawn template for `secretary` agent. Lead reads this when operator says "find jobs" / "apply to N jobs" / "scan JobsDB" / similar.
>
> Lead spawn invocation:
> `Search jobs per context/projects/secretary/shared/job-criteria.md across JobsDB + LinkedIn. Score top N (per criteria threshold). Draft cover letter per match. HITL pause before EVERY submit. Log to general/applications-{YYYY-MM}.md. Return summary per agent definition output format.`

## Pre-flight (Lead checks before spawn)

- [ ] Lead extracted `operator_context` — REQUIRED fields: `name`, `email`, `phone`, `linkedin_url`, `resume_path`, `target_roles`, `must_have_skills`, `salary_floor`, `location_preferences`, `work_authorization`, `sources` (jobsdb_url + linkedin_url). If any missing → ask operator to provide before spawning.
- [ ] Chrome MCP connected + JobsDB + LinkedIn logged-in (operator pre-login)
- [ ] Operator confirmed application volume cap for today (default: 5; operator-overridable)
- [ ] Today's date directory exists: `context/projects/secretary/general/{YYYY-MM-DD}/`

If any pre-flight fails → halt + report.

## Secretary's expected workflow

1. **Read frameworks + operator_context** (mandatory):
   - `shared/job-criteria.md` — scoring algorithm + cover letter structure + anti-patterns
   - `shared/voice.md` — generic voice anti-patterns
   - `operator_context` from spawn brief — all PII for scoring + form prefill + cover letter signature
   - If spawn brief lacks fields + `general/operator-context.md` exists → fallback (spawn brief OVERRIDES)
2. **Compose search URLs** from criteria:
   - JobsDB: per `job-criteria.md` "Sources to check per run"
   - LinkedIn Jobs: per same section
3. **Open + scan each source** via `mcp__Claude_in_Chrome__navigate` + `read_page`
   - Extract: company, role, location, salary (if shown), JD snippet (200-300 chars)
   - Volume cap per source: 20 listings → 40 total across 2 sources max
4. **Score each listing** per `job-criteria.md` scoring algorithm:
   - Apply must-have / nice-to-have / deal-breaker rules
   - Deal-breaker hit → score 0, skip
   - Compute base score per algorithm
   - Log every scored listing (proposed OR skipped) to `general/applications-{YYYY-MM}.md`
5. **Filter to "propose"**: score >= threshold (default 40) + sorted descending
6. **Trim to operator's cap** (default 5)
7. **For each proposed application**:
   - Open JD full text via Chrome MCP
   - Quick company research (≤5 min): about page + recent news
   - Draft cover letter per `voice.md` + `job-criteria.md` cover-letter strategy
   - Save draft to `general/{YYYY-MM-DD}/cover-letter-{slug}.md`
   - Open application form
   - Pre-fill from profile (Name, email, phone, LinkedIn URL, etc.)
   - Attach resume from `profile.md` resume path (manual upload via Chrome MCP file_upload during HITL)
   - HITL pause: "Submit application to {company} for {role} (score {N}/100)? Cover letter at {path}"
   - Options: `["approve_submit", "edit_draft", "skip"]`
8. **On approve_submit**: click Submit / Apply button → capture confirmation screenshot → log status: submitted
9. **On edit_draft**: pause for operator's edits → re-prompt for approval
10. **On skip**: log status: skipped (operator declined) + reason if provided
11. **Update tracking file**: `general/applications-{YYYY-MM}.md` per shape in `job-criteria.md` tracking section
12. **Report to Lead** — counts + queue + drafts + suggested follow-up

## HITL question template

```
question: "Submit application to {company} for {role}? Score {N}/100. Cover letter: {summary_50_chars}..."
options: ["approve_submit", "edit_draft", "skip"]
```

Include in `question_payload`:
- `default_answer`: "skip" (fail-closed — don't accidentally submit without explicit yes)
- Cover letter file path in question text or follow-up note

## Cover letter quality checks (secretary self-checks before HITL)

Before pausing, verify:
- [ ] Opening hook ≠ "I am writing to apply for..." (generic)
- [ ] Specific company detail mentioned (not "your company")
- [ ] 2-3 concrete skill→JD matches (not skill dump)
- [ ] 1 question for recruiter (genuine interest signal)
- [ ] Voice.md anti-pattern scan passed (no banned phrases)
- [ ] Length: 200-350 words

If any check fails twice → halt + escalate "can't draft cover letter that passes self-check for this JD, operator review needed".

## Failure modes (report, don't work around)

- Application form requires unusual field (e.g. essay question) → pause + show field text + ask operator how to answer
- Form requires login to a third-party (Workday, Greenhouse) operator not logged in → pause + ask operator to login
- Salary required field + operator hasn't set policy → pause + ask
- Captcha appears → halt + report "captcha on {company} application; operator must complete manually"
- Resume upload fails (Chrome MCP file_upload limits) → halt + report
- LinkedIn rate-limit hit → halt + back off; report count of remaining proposed apps

## Per-run output

`general/{YYYY-MM-DD}/job-search-summary.md`:
```markdown
# Job search summary — {YYYY-MM-DD HH:MM}

- Sources scanned: JobsDB (N listings), LinkedIn (M listings)
- Total reviewed: N+M
- Deal-breakers hit: N
- Below threshold: N
- Proposed (HITL pending): N
- Submitted today: N (cap was 5)
- Skipped on operator decline: N

## Top scores
- {company} — {role} — score {N} — status: {pending|submitted|skipped}

## Cover letters drafted
- {paths}

## Follow-up reminders
- {company} — submitted today, follow up by {date+7}
```

## Operator-facing summary format (Lead renders)

```
💼 Job search done — scanned 38 listings

✅ Proposed for application (your approval needed): 4
  - #1120 — Acme Inc — Senior Backend Eng — score 78/100 — Bangkok remote
  - #1121 — Mango Tech — Staff Eng (Platform) — score 71/100 — SG hybrid
  - #1122 — Lighthouse — Python AI Engineer — score 67/100 — fully remote
  - #1123 — Sprout Co — Senior Backend — score 61/100 — Bangkok onsite (note: under preferred)
🚫 Filtered out: 27
  - 18 below score 40
  - 9 hit deal-breakers (3 onsite Singapore, 4 salary below floor, 2 anti-titles)
📝 Cover letters drafted: 4 (at general/2026-05-17/)

⏳ Today's submission cap: 5 (1 slot remaining)
📅 Follow-up reminders queued for 2026-05-24
```

## Tuning hooks

- **Volume cap per run**: edit `job-criteria.md` → "Per-run scope"
- **Score threshold**: edit `job-criteria.md` → "Scoring algorithm"
- **Sources**: edit `job-criteria.md` → "Sources to check per run"
- **Cover letter style**: edit `voice.md` + `job-criteria.md` "Cover-letter strategy"
- **Deal-breakers**: edit `job-criteria.md` → "Deal-breakers"
