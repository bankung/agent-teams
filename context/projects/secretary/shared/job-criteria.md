# Job match criteria — scoring framework

> **Target roles, specific companies, salary numbers, deal-breakers ARE PII** (reveal operator's career intent + finances). Operator injects at session-time. This file holds the GENERIC scoring framework + algorithm.

## Scoring algorithm (operator-agnostic)

```
base = 0

# Skills overlap (operator provides skill list session-time with weights)
for each operator.must_have_skill in JD:
    base += skill.weight (default: 15 if not specified)
for each operator.nice_to_have_skill in JD:
    base += skill.weight (default: 5)

# Location fit
if JD.location matches operator.preferred_locations: base += 10
elif JD.location in operator.acceptable_locations:    base += 0
else: deal_breaker = true

# Salary fit (if JD discloses)
if JD.salary >= operator.salary_target:         base += 15
elif JD.salary >= operator.salary_floor:        base += 5
elif JD.salary <  operator.salary_floor:        deal_breaker = true
elif JD.does_not_disclose_salary:               base -= 5 (penalty, not skip)

# Title match
if JD.title matches operator.target_roles:      base += 20
elif JD.title in operator.acceptable_roles:     base += 5
elif JD.title in operator.anti_titles:          deal_breaker = true

# Company stage
if JD.company.stage in operator.preferred_stages: base += 5

# Company red flags
if JD.company in operator.blacklist:            deal_breaker = true

# Visa / work authorization
if JD.requires_visa_sponsorship AND NOT operator.has_local_auth: deal_breaker = true

# Final
if deal_breaker: return 0
return base  # operator threshold filter applied next
```

## Session-time inputs operator provides

```yaml
target_roles:           # List of role titles (synonyms / variations OK)
  - "<role 1>"
  - "<role 2>"
acceptable_roles:       # Roles operator would consider but didn't actively target
  - "<role>"
anti_titles:            # Auto-skip regardless of other matches
  - "<role>"            # e.g. "Junior", "Intern", "QA-only", "Manager-track-only"

must_have_skills:       # Each item: name + weight (1-25)
  - { name: "<skill>", weight: 20 }
nice_to_have_skills:
  - { name: "<skill>", weight: 5 }

preferred_locations:
  - "Remote"
  - "<city>"
acceptable_locations:
  - "<city or region>"
unacceptable_locations:
  - "<city>"
time_zone_constraint:   # e.g. "must overlap 4h+ with ICT (UTC+7)"
  hours_overlap_with: "UTC+7"
  min_hours: 4

salary_floor_thb_monthly:    <number>
salary_target_thb_monthly:   <number>
salary_currency_conversions: # only fill if applying outside TH
  USD: <floor>
  SGD: <floor>

preferred_stages:       # Company stages
  - "Series B-C startup"
  - "FAANG-tier specific teams"
avoided_stages:
  - "Pre-seed (high failure risk)"
  - "Enterprise IT services"

blacklist_companies:    # Auto-skip
  - "<name>"

work_authorization:     # affects visa-requiring postings
  citizenship: "<country>"
  visa_status: "<if applying abroad>"

per_run_caps:
  listings_reviewed: 20-40   # per source, secretary halts beyond
  applications_proposed: 5   # max secretary shows operator per run
  applications_submitted: 5  # max actual submits per day (anti-spam)

sources:                # secretary URLs
  jobsdb_search_url: "<operator's filtered URL>"
  linkedin_jobs_url: "<operator's filtered URL>"
```

## Per-session injection example

Operator inline:
```
operator: find matching jobs.
          context for this session:
            target_roles: [CTO, Head of Engineering, VP Eng]
            must_have_skills: [{name: 'agent orchestration', weight: 20},
                                {name: 'team leadership', weight: 15},
                                {name: 'fundraising experience', weight: 10}]
            salary_floor_thb: 350000
            location: [Bangkok, Remote-SEA timezone]
            stages: [Series A-C startup]
            anti_titles: [Junior, IC-only]
            sources:
              jobsdb_url: <pasted URL>
              linkedin_url: <pasted URL>
            propose_max: 5
            submit_today_max: 3
```

OR operator stores in `general/operator-context.md` for persistence.

## Cover letter framework (operator-agnostic)

When secretary drafts a cover letter, follow this structure:

### Structure (200-350 words target per voice.md)

1. **Hook sentence** per `voice.md` opener pattern (1 sentence, specific observation NOT generic motivational)
2. **Why THIS company** (1 paragraph, 2-3 sentences):
   - Secretary researches via `WebFetch` on company about/mission/recent news
   - References ONE concrete thing (not "I love your mission")
3. **Skill→JD match** (1 paragraph, 2-3 concrete bullets):
   - Operator's session-time `must_have_skills` → JD requirements
   - NOT a skill dump; pick the 2-3 most relevant
4. **One question for recruiter** (1 sentence):
   - Genuine interest signal + gives them a hook to reply
5. **Sign-off** per `voice.md` formal tone + operator's session-injected `signature`

### Anti-patterns (from voice.md, also banned in cover letters)

- "I'm passionate about..."
- "I believe my skills make me a strong fit"
- Salary expectations stated in letter (let them ask)
- Generic warmth that says nothing

### Per-company research budget

- Max 5 min Chrome MCP browsing per company (about page + 1 recent post)
- If can't find specific hook in 5 min → use generic-warm template + flag "low customization possible" in HITL pause

## Quality gates (self-check before HITL pause)

Before pausing operator for application submit, verify:
- [ ] Cover letter passes voice.md anti-pattern scan
- [ ] Cover letter includes 1 specific company detail
- [ ] Cover letter includes 1 genuine question
- [ ] Length within 200-350 words
- [ ] All form fields filled per operator profile
- [ ] Resume path resolves to a real file

If 2+ checks fail → halt + escalate "can't draft to spec, operator review needed".

## Failure modes

- Application form has essay question → halt + show field text + ask operator
- Form requires login to third-party (Workday, Greenhouse) operator not logged in → halt + ask to login
- Salary field required + operator hasn't set session policy → halt + ask
- Captcha → halt + report (operator completes manually)
- Resume upload fails → halt + report
- JobsDB / LinkedIn rate-limit → back off + halt + report remaining count

## Tracking output

`general/applications-{YYYY-MM}.md`:
```
- 2026-05-18 — <company> — <role> — score 67/100 — status: submitted — follow-up: 2026-05-25
- 2026-05-18 — <company> — <role> — score 45/100 — status: skipped (deal-breaker: onsite SG)
```

Per-session summary: `general/job-search-{date}/job-search-summary.md`.

## Tuning hooks

- **Scoring weights**: operator overrides per session
- **Cover letter structure**: edit this file (rare)
- **Sources / volume caps**: operator overrides per session
