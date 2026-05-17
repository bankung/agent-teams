# Job match criteria

> **Lead is the only writer of this file.** Operator dictates; Lead writes.
>
> Used by secretary for: scoring job postings 0-100, filtering JobsDB / LinkedIn search results, deciding which positions warrant cover-letter drafting and operator's attention.

## Target roles [TODO — operator fills]

Job titles secretary searches for (synonyms / variations included):

- [TODO e.g. "Senior Backend Engineer (Python)", "Staff Engineer (Backend)", "AI Engineer", "ML Platform Engineer", "AI Agent Engineer", "Python Developer"]

Anti-titles (NEVER apply even if other criteria match):
- [TODO e.g. "Junior", "Internship", "QA-only", "Manager-track without coding"]

## Industries / domains

Preferred:
- [TODO e.g. "Fintech, AI/ML tooling, developer tools, SaaS B2B"]

Avoided:
- [TODO e.g. "Gambling, predatory lending, adtech, MLM"]

## Must-have skills (score boost)

Each match adds to the job's score. Score formula in algo section below.

- [TODO e.g. "Python (FastAPI / Django / Flask): +20"]
- [TODO e.g. "PostgreSQL: +10"]
- [TODO e.g. "LangGraph / LangChain / agent frameworks: +15"]
- [TODO e.g. "Docker / Kubernetes: +5"]
- [TODO e.g. "AWS / GCP: +5"]
- [TODO e.g. "Production ML / RAG: +10"]

## Nice-to-have (small score boost)

- [TODO e.g. "TypeScript / Next.js: +5"]
- [TODO e.g. "Open-source contributions valued: +5"]

## Deal-breakers (score = 0 / skip)

- [TODO e.g. "Required onsite outside Bangkok and operator marked 'Bangkok only'"]
- [TODO e.g. "Stated salary < operator's floor"]
- [TODO e.g. "Required >60h/week stated explicitly"]
- [TODO e.g. "Visa sponsorship not offered AND operator lacks local auth"]

## Salary

- **Floor (THB / month)**: [TODO]
- **Target (THB / month)**: [TODO]
- **Currency conversions** (if applying outside TH):
  - USD: [TODO]
  - SGD: [TODO]
- **Equity / RSU**: [TODO e.g. "OK but treat 0% probability for valuation"]
- **Hybrid bonus structure preference**: [TODO]

If posting doesn't state salary → score with a -5 penalty but don't skip (clarify in cover letter ask).

## Location & work model

- **Preferred locations**: [TODO e.g. "Remote globally", "Bangkok", "Singapore"]
- **Acceptable locations**: [TODO]
- **Unacceptable**: [TODO]
- **Time zone constraint**: [TODO e.g. "must overlap 4h+ with ICT (UTC+7)"]
- **Work model preference order**: [TODO e.g. "1. Remote, 2. Hybrid 2-3d/wk, 3. Onsite"]
- **Relocation willingness**: [TODO from profile.md]

## Company stage / size

- **Preferred stages**: [TODO e.g. "Series A-C startup, mid-size 100-500", "FAANG-tier if specific team"]
- **Avoid**: [TODO e.g. "Pre-seed (high failure risk this year)", "Enterprise IT services"]

## Company red-flags (auto-skip)

Secretary auto-skips postings whose company matches any flag:

- [TODO e.g. "Glassdoor < 3.0", "Recent mass-layoff news", "Crypto-only with no clear path to profit"]
- Operator-blacklist companies: [TODO list]

## Cover-letter strategy

When secretary drafts a cover letter, use these signals:

### Always include
- 1 hook sentence per `voice.md` opener pattern
- 1 paragraph: why operator is interested in THIS company specifically (secretary researches via WebFetch on company about page)
- 1 paragraph: 2-3 concrete skill-match-to-jd bullets (not a skill dump)
- 1 paragraph: 1 question for the recruiter (signals genuine interest, gives them a hook to reply)
- Operator's signature per `profile.md`

### Never include
- Salary expectations (let them ask)
- "I'm passionate about" (over-used)
- Generic "I believe my skills make me a strong fit" filler
- Anything from `voice.md` AVOID list

### Per-company customization

Secretary should spend ≤5 min researching each company:
- About page / mission
- Recent blog post / press release / funding news
- 1 specific employee mentioned in the JD (if findable on LinkedIn)

If can't find a hook in 5 min → use generic-but-warm template + flag "low customization possible" in HITL pause.

## Scoring algorithm

```
base = 0
for each must-have skill in JD:
    base += skill.weight
for each nice-to-have skill in JD:
    base += skill.weight
if location_acceptable: base += 10
if salary_in_range: base += 15
if company_in_preferred_stage: base += 5
if posting_has_clear_salary: base += 5
if any deal-breaker matches: return 0
```

Threshold for "propose application": [TODO operator picks — recommend score ≥ 40 / 100]

## Per-run scope

- **Volume cap per run**: [TODO recommend 10-20 postings reviewed per "find jobs" command]
- **Application cap per run**: [TODO recommend ≤5 applications submitted per day to avoid spam-look]
- **Sources to check per run**:
  - JobsDB: [TODO URL filters with operator's criteria]
  - LinkedIn Jobs: [TODO URL filters]
  - Other (operator preferred): [TODO]

## Tracking

Secretary appends each application to `general/applications-<YYYY-MM>.md` with shape:
```
- 2026-05-17 — Company X — Role Y — score 67/100 — status: submitted — follow-up: 2026-05-24
- 2026-05-17 — Company Z — Role W — score 45/100 — status: skipped (deal-breaker: onsite Singapore)
```

## Operator fill checklist

- [ ] Target roles (5-10 title variants)
- [ ] Anti-titles
- [ ] Preferred / avoided industries
- [ ] Must-have skills with weights
- [ ] Nice-to-have skills with weights
- [ ] Deal-breakers
- [ ] Salary floor / target / equity policy
- [ ] Location / time-zone constraints
- [ ] Company stage preferences
- [ ] Company red-flags + blacklist
- [ ] Cover-letter strategy specifics
- [ ] Scoring threshold for "propose"
- [ ] Per-run volume caps
- [ ] Source URLs (JobsDB + LinkedIn filtered search)

**Time estimate**: 20-30 min if criteria are already in operator's head; longer if doing first formal articulation.
