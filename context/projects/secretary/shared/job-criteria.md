# Job Criteria: Scoring framework + cover letter structure

**Purpose:** Provide a 0–100 scoring rubric for job match quality, and define cover letter structure. Operator-specific target roles, salary floor, and location preferences arrive via `operator_context` in spawn brief.

**Source:** Secretary agent definition, Pattern 2, lines 150–158. Required fields from `profile.md` lines 104.

## Scoring framework (0–100)

Secretary scores each job posting independently. Use the rubric below; final score is weighted average of 4 categories.

### Category 1: Skills overlap (35% weight)

**Rubric:**

| Score | Criteria |
|---|---|
| **90–100** | Job lists all 3+ of operator's must-have skills. 2+ bonus skills match. |
| **70–89** | Job lists 2 of 3+ must-have skills. 1 bonus skill match. Minor skill gap bridgeable in 1–3 months. |
| **50–69** | Job lists 1 of 3+ must-have skills. Job requires skill learning but in operator's domain (e.g., operator knows Python, job wants Golang). |
| **30–49** | Job lists 1 must-have skill. 2+ required skills are new to operator (different programming paradigm, unfamiliar domain). |
| **0–29** | Job requires few/none of operator's must-have skills. Steep learning curve; operator would struggle in first 3 months. |

**How to assess:** Read job posting title + responsibilities section. Look for explicit skill names (Python, Kubernetes, etc.). Cross-reference against `operator_context.must_have_skills` (e.g., ["Python", "PostgreSQL", "Kubernetes"]).

**Note:** Don't penalize for "nice-to-have" section; it's negotiable. Focus on the main responsibilities.

### Category 2: Salary fit (25% weight)

**Rubric:**

| Score | Criteria |
|---|---|
| **90–100** | Salary band (low end) is 10%+ above operator's floor. No ceiling cap needed. |
| **70–89** | Salary band (low end) meets or slightly below operator's floor (≥95%). Upside negotiation likely. |
| **50–69** | Salary band (low end) is 15–30% below floor. Negotiable; operator willing to discuss but prefers higher. |
| **0–49** | Salary band (low end) is >30% below floor OR salary not listed (unknown). Deal-breaker for operator's financial target. |

**How to assess:** Extract salary range from job posting. If range is "100k–150k" and operator's `salary_floor` is 120k, score is around 70–80 (low end = 100k, which is 83% of floor). If not listed, ask operator (spawn brief) or score 0 (too risky).

**Special case — equity + bonus:** If posting lists equity/bonus + salary, note it in scoring comment ("base 90k + equity package worth ~120k vesting 4yr"), but score on BASE salary. Equity is upside, not guaranteed.

**Note:** If score is 0–49 but job is otherwise perfect fit (skills 95, location 100), flag to operator in summary: "Low pay but strong match otherwise — review?"

### Category 3: Location (20% weight)

**Rubric:**

| Score | Criteria |
|---|---|
| **100** | Job is remote or in operator's top 1–2 preferred cities. |
| **80–99** | Job is remote-flexible (3–5 days/week in-office in one of operator's preferred cities) OR in preferred city but on-site 5 days/week (operator willing). |
| **60–79** | Job location is secondary preference. Operator willing to relocate / commute, but not ideal. Example: not preferred city, but good relocation package or temporary assignment. |
| **40–59** | Job location mismatches preferences. Operator would need relocation or visa sponsorship not listed. |
| **0–39** | Job location is outside operator's geographical scope entirely. Relocation not feasible OR visa sponsorship not mentioned AND operator needs it. |

**How to assess:** Extract location + work-arrangement from job posting. Cross-reference against `operator_context.location_preferences` (e.g., ["remote", "San Francisco", "Singapore"]).

**Work auth:** If operator needs visa sponsorship and posting says "no sponsorship", score 0 regardless of location. If posting doesn't mention sponsorship, ask operator (score 50 = "unclear — verify before applying").

### Category 4: Deal-breakers (20% weight)

**Rubric:**

| Score | Criteria |
|---|---|
| **100** | No deal-breakers detected. Company, industry, and team size align with operator's preferences or are neutral. |
| **70–99** | One minor deal-breaker or concern. Example: company is startup (operator prefers stable) but team seems strong. OR: industry is X (operator neutral on), but role is strategic. Operator willing to consider. |
| **40–69** | One moderate deal-breaker. Example: company is in industry operator wants to avoid (finance if operator said "not finance"), but salary/skills are too good to ignore. Flag to operator. |
| **0–39** | One major deal-breaker or 2+ minor ones. Example: company in forbidden industry, OR team size is >5000 and operator wants <500, OR non-negotiable requirement (must be remote; job is on-site). Auto-decline unless operator overrides. |

**How to assess:** Extract company, industry, size, team structure from posting + company research (LinkedIn, Crunchbase, website). Cross-reference against `operator_context` for:
- **Forbidden industries** (e.g., operator said "no finance/defense/tobacco").
- **Company size preference** (e.g., "startups only" or "stable 50–500 person company").
- **Team composition** (e.g., "avoid 100+ person orgs" or "need 3–5 person focused team").
- **Work style** (e.g., "no micromanagement" = red flag if posting says "daily standups + surveillance").

**Note:** If deal-breaker is ambiguous, mention in score comment and ask operator.

## Composite score (final: weighted average)

```
Final Score = (Skills × 0.35) + (Salary × 0.25) + (Location × 0.20) + (Deal-breakers × 0.20)
```

**Guidance:**
- **80–100:** Strong match. Top-tier candidates for applications.
- **60–79:** Good fit. Apply if capacity; review each for must-have gaps.
- **40–59:** Borderline. Secretary proposes to operator for decision.
- **0–39:** Poor fit. Skip unless operator intervenes.

## Cover letter structure (3-paragraph template)

**Trigger:** When job match scores ≥60 and operator approves application, secretary drafts cover letter per this structure.

### Paragraph 1: Hook + role-fit understanding (3 sentences, ~50 words)

**Sentence 1:** Why you're writing (found role, referred, company mission resonates, urgent hiring signal).
```
Example: "I found your job posting for Senior Backend Engineer and was immediately interested because you're 
rebuilding payments infrastructure — a problem I've solved twice."
```

**Sentence 2:** One sentence showing you understand the role and value you'd bring.
```
Example: "Your team is migrating from monolith to microservices; I've led 3 migrations and can hit the ground running."
```

**Sentence 3:** Express genuine interest (avoid "I am excited to apply for the position of...").
```
Example: "I'd love to discuss how my experience can accelerate your migration."
```

### Paragraph 2: Proof — 2–3 concrete achievements (4 sentences, ~100 words)

**Pattern per achievement:** "When [situation], I [action], resulting in [metric or outcome]. [Connection to role]."

```
Example 1:
"At TechCorp, I migrated a legacy Postgres 9.6 → 14 with zero downtime, reducing query latency 
by 35% and unblocking 5 pending features. This mirrors your team's focus on infrastructure stability."

Example 2:
"I designed a circuit-breaker system that reduced cascading failures by 60% and improved on-call 
happiness (fewer pages). Your posting mentions reliability as a core value; this is what I'd bring."
```

**Structure:** Each achievement = 2 sentences (situation + action + metric, then connection to role). Operator's `must_have_skills` or `target_roles` should map to achievements. Secretary pairs job requirements ↔ operator proof points.

### Paragraph 3: Close + call-to-action (2 sentences, ~30 words)

**Sentence 1:** Restate eagerness + focus area.
```
Example: "I'm excited to bring my infrastructure and migration expertise to your team and help you ship faster."
```

**Sentence 2:** Clear CTA (discuss schedule, jump on call, send references, highlight availability).
```
Example: "I'm available for a call this week — let me know what works."
```

**Avoid:** "Sincerely", "Yours truly" (too formal). Use "Thanks," / "Best," / "Cheers," / "Looking forward," / "[Operator name]".

## How to populate cover letter at runtime

1. **Operator context fields needed:**
   - `name` (from profile)
   - `must_have_skills` (list, from profile)
   - `target_roles` (list, from profile)
   - Operator's achievements (optionally from spawn brief or prior job history)

2. **Job-specific fields:**
   - Job title + company name (from posting)
   - Key responsibilities (from posting)
   - Nice-to-have / bonus skills (from posting)

3. **Secretary workflow:**
   - Extract 2–3 operator achievements from context that MATCH job requirements.
   - Rewrite achievements in concrete language (numbers, timelines, impact).
   - Map each achievement to one job requirement ("You mentioned X, I've done Y").
   - Draft letter per template above.
   - Return to Lead with letter in Action-required (HITL before submit).

## Example cover letter (template instance)

```
Hi [Company Hiring Manager],

I found your job posting for Senior Backend Engineer and was immediately interested 
because you're scaling to 50M users — a problem I've helped solve twice. Your team is 
prioritizing reliability and migration velocity; I'd love to discuss how my experience 
in both areas can accelerate your growth.

At StartupX, I led a migration from Django monolith to FastAPI microservices, reducing 
API latency by 40% and enabling the team to ship 3 new features that were previously 
blocked by infrastructure. This directly mirrors your team's goal of supporting faster 
feature velocity. I also designed a circuit-breaker system that reduced cascading failures 
by 60% — key for the scale you're operating at.

I'm excited to bring infrastructure expertise and migration patterns to your team. 
I'm available for a call this week — let me know what works.

Best,
[Operator Name]
```

## Operator-specific criteria at spawn time

If spawn brief includes `job_criteria_overlay` or `override_*` fields, they take precedence:

```json
{
  "skip_roles": ["DevOps", "QA"],
  "priority_company_list": ["TechCorp", "Startup Y"],
  "must_have_skills_override": ["Rust", "PostgreSQL", "AWS"],
  "salary_floor_override": 150000,
  "location_preferences_override": ["remote"],
  "work_auth": "no_sponsorship_needed"
}
```

Use overrides to adjust score weights or add/remove companies from review.
