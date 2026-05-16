# Portfolio experiment process

Process for running multiple project experiments in parallel on agent-teams. Captures the operator's reframe from 2026-05-17: agent-teams is a **platform** for hosting many lightweight experiments — not a single-bet committment to one project / one niche.

## Why portfolio not single-bet

Single-project bet:
- Concentration risk (one wrong pick = months wasted)
- High emotional commitment (sunk cost bias on abandonment)
- Pressure to pick "winners" first try (impossible without data)
- Slow feedback (one signal at a time)

Portfolio approach:
- Diversify across multiple short experiments
- Lower per-experiment cost (cheap spinup + short duration + cheap abandonment)
- Faster feedback (parallel signals)
- Winners surface from data, not from guesses
- Moat lives at the **platform layer** (low-cost portfolio orchestration), not at any single project

## Experiment lifecycle

Each project follows the same lifecycle. Variants don't.

```
1. Spinup    (~30-60 min) → project + knowledge base + KPI tracker filed
2. Run       (2-4 weeks)  → autorun + HITL + measure
3. Decide    (weekly check) → continue / pivot / abandon
4. Wind-down (~30 min, on abandon) → archive learnings, free up budget
```

### Spinup checklist

Concrete actions for kicking off a new experiment project:

1. **POST `/api/projects`** with `name`, `team` (general / business / content / etc.), basic paths/stack.
2. **Set autorun consent** if scheduled work is expected.
3. **Write `shared/` knowledge base** (Lead-direct):
   - `experiment-hypothesis.md` — what's the bet? what would prove/disprove?
   - `kpi-tracker.md` — token cost, completion rate, HITL frequency, target measurable (revenue / hours-saved / sign-ups / whatever)
   - `decisions.md` — locked decisions; revisit weekly
   - `project-specific-knowledge.md` (per niche: contacts list, voice samples, brand guidelines, etc.)
4. **File initial Kanban tasks** — first week of work as concrete tasks.
5. **Set `hitl_timeout_hours`** appropriate to operator availability.
6. **Configure budget cap** via #951 budget enforcer.
7. **Pick LLM provider** (Ollama / DeepSeek V3 / Claude tier) per project's cost-vs-quality balance.

Target: each spinup runnable in ≤ 60 min after the platform tools are in place.

### Run phase

- Autorun handles routine work (after Tier-1b — secretary template + persistent Project Lead).
- Operator approves via HITL on sensitive actions; lower-stakes auto-approve via #957 policies (once designed).
- Daily digest (#958) surfaces KPI delta + any escalations.
- Cost tracker (#944) attributes spend per project.

### Decide criteria (revisit weekly via portfolio review)

Per experiment, evaluate against locked hypothesis:

| Signal | Action |
|---|---|
| Hypothesis confirmed, measurable target hit | **Scale** — invest more time / budget |
| Hypothesis confirmed, target on track | **Continue** — let it run another period |
| Hypothesis confirmed but cost ratio < 1.0 | **Pivot** — adjust scope or LLM tier; one more period |
| Hypothesis unconfirmed, no signal after 50% of window | **Investigate** — is it execution? niche? hypothesis? |
| Hypothesis disconfirmed | **Abandon** — wind down within 1 week |

### Hard abandon rules (no exception)

Pre-commit to these to avoid sunk-cost bias:

1. **Budget breach:** project's monthly LLM cost > 2× initial estimate AND no measurable progress → abandon within 1 week
2. **Time breach:** experiment runs > 4 weeks past planned end date without explicit operator continue decision → auto-abandon
3. **Operator burnout signal:** if a project consistently produces > 3 HITL escalations per day AND operator is dreading reviewing them → halt for re-design or abandon
4. **Niche death signal:** market signal (sales / sign-ups / engagement / whatever measurable) is zero AND not improving for 2 consecutive weeks → abandon

Abandonment is success, not failure. The platform's value is making cheap abandonment possible.

### Wind-down checklist

When abandoning an experiment:

1. **Capture learnings** — write `shared/post-mortem.md` with: what was hypothesis, what was measured, why abandoning, what would different next time
2. **Archive Kanban tasks** — mark all open as `CANCELLED` with reason
3. **Soft-delete the project** — `PATCH /api/projects/<id> {status: 0}` (preserves history)
4. **Drop scheduled jobs** — disable recurrence
5. **Free LLM budget** — re-allocate to other experiments
6. **Update portfolio dashboard** — record final KPIs for cross-experiment comparison

## Active portfolio caps

To prevent operator overload + LLM cost runaway:

| Limit | Default | Rationale |
|---|---|---|
| Active experiments | 3-5 | Operator attention budget; HITL queue manageable |
| Total LLM monthly cap | Operator-set | Per #951 budget enforcer; configurable per project |
| Max new experiment per month | 2 | Spinup is cheap but ramp-up is not free |
| Min experiment lifetime | 2 weeks | Below this, no signal to evaluate |
| Max experiment lifetime | 4 weeks (default) | Force decision; explicit operator override only |

## Portfolio review cadence

Weekly review on operator's chosen day. Read each project's:
- `kpi-tracker.md` (this week's measurements)
- Daily digests (#958) since last review
- HITL escalation summary (#952 auditor logs)
- Cost delta vs cap

Apply decide-criteria table per experiment. Update `shared/portfolio-status.md` with this week's verdict per project.

If review takes > 30 min total → too many active experiments; trim.

## Cross-cutting infra dependencies

Items in agent-teams Tier-1 that the portfolio process depends on:

- **#958 daily digest** — backbone for weekly review (without it, operator reads raw Kanban — slow)
- **#1082 cross-project aggregation API** — portfolio dashboard data
- **#957 approval policies** — auto-approve safe actions to keep HITL queue from blocking experiments
- **#944 cost tracker** (DONE) — per-project token attribution
- **#951 budget enforcer** (DONE) — auto-pause at cost breach
- **#959 off-site backup** (DONE-priority) — prerequisite to running real experiments at all
- **#955 web push** — operator alerts when manual review needed (don't miss decision-windows)

## What this process does NOT solve

Capture honestly:

- **Niche selection** — process tells you HOW to run experiments, not WHICH niches to pick. Niche pick = separate research work.
- **Quality measurement** — "completion rate" is easy; "output quality" is hard (subjective). Different niches need different rubrics.
- **Operator strategic vision** — process doesn't replace operator's judgment on what to bet on. It makes the bet cheap to test.
- **Cross-experiment knowledge transfer** — if experiment A discovers a pattern useful for B, the process doesn't auto-propagate. Manual operator review.

## Cross-references

- `agent-teams/shared/session-review-2026-05-17.md` — origin of this reframe
- `_private/CONFIDENTIAL-README.md` — operator's strategic frame (user-private; not in git)
- `agent-teams/shared/decisions.md` — agent-teams platform decisions

## Living-doc note

Update when:
- Hard abandon rules trigger on a real experiment (capture pattern)
- Active portfolio caps need adjusting based on measured operator load
- New cross-cutting infra dependency emerges
- Niche selection process matures (currently out-of-scope here)
