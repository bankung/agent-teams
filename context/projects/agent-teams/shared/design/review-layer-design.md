---
task: 2767
status: design-lock (v0.9.0 gate)
decided: 2026-07-10 (operator-confirmed, 4/4 recommendations accepted)
scope: NO code — this doc is the data contract #2770–2776 build on
---

# Review-layer design lock — evidence-first close card + earned autonomy (#2767)

Source thread: 2026-07-01 design session (v0.9.0 milestone #57). Goal: every task close
produces a structured, evidence-backed record that (a) shows the operator exactly what was
verified and how, (b) separates evidence the work generated about itself from evidence
gathered independently, and (c) feeds a trust metric that becomes the autonomy dial —
earned fast-tracking, never assumed.

## The 4 decisions (operator-locked 2026-07-10)

### D1 — Surface: BOTH (one record, two renderers)
The close record renders as a card on the web task-detail drawer AND as a markdown block
in the Lead's chat close-message. Rationale: chat is where the operator actually reads at
close time (live surface); the web card is the durable audit surface. Both render from the
SAME `close_record` JSONB — no divergent content permitted (contamination rule: facts live
in one place). Neither renderer may add facts the record doesn't carry.

### D2 — Rework-rate: placeholder now → history query later
`trust_metrics.rework_rate` ships as `null` with `rework_rate_source: "placeholder"`.
A follow-up task builds the live Kanban-history computation (reopened tasks + follow-up-
tagged closes per risk class) and flips the source to `"history_query"`. Rationale: the
query is real work with its own definitional questions ("reopened" needs a durable marker);
blocking the whole layer on it inverts the value order. The card is honest about the gap —
a placeholder that LOOKS live is forbidden.

### D3 — Tiers: 3 risk tiers + orthogonal permanent `never_auto_clear` flag
`tier ∈ {low, medium, high}` classifies the blast radius of the change. `never_auto_clear`
is a SEPARATE boolean class, not a 4th tier: items carrying it always require operator
eyes regardless of trust score, forever. Pinned per team:

| Team | never_auto_clear (always-human) |
|---|---|
| dev | security / auth / migrations / tool-layer grants |
| novel | locking a chapter's final voice / POV-defining rewrites |
| content | external publish; must_be_real claims that failed verification |
| seo | strategy pivots; anything mutating a customer site |
| sem | budget changes; campaign launch |
| data-analytics | DML/DDL recommendations; PII handling plans |
| netops | any config-apply proposal (estate is read-only by design) |
| social | platform publish (operator pastes manually by design) |
| general | operator-named per project at bootstrap; default = external side effects (e.g. the secretary project — a `general`-team profile — pins email send / batch-archive / application submissions, already HITL-gated) |

Registered teams = `ProjectTeam.ALL` (9: dev / novel / general / content / seo / data-analytics / sem / netops / social — `api/src/constants.py`). **Secretary is a project profile under `general`, not a team** — profile-level always-human actions ride the project row, not a team playbook.

Rationale for 3-not-4: a "trivial" fast-lane is what LOW + high trust already produces;
a 4th tier adds per-task classification burden without new behavior. Binary (2-tier) gives
the trust metric nothing to differentiate on.

### D4 — Emphasis: per-team default (two-emphasis card)
`emphasis ∈ {fast_track, judgment_first}` selects which half of the card leads:
- **dev teams → `fast_track`** — card leads with "can I fast-track this?": clearance
  evidence first, open items second. Evidence-gated work reads pass/fail naturally.
- **non-dev (novel/content/seo/sem/data-analytics/netops/social/general) →
  `judgment_first`** — card leads with "what needs your judgment?": open judgment items
  first, evidence second. Judgment-heavy domains invert (2026-07-01 thread).
Per-project override is a future optional (`projects.review_emphasis`), NOT built in the
v0.9.0 chain.

## close_record JSONB schema (v1)

Lives at `tasks.close_record` (nullable JSONB; migration lands in the #2770-series, not
here). Written ONCE by the Lead at done-flip time, in the same PATCH as the AC verdicts.
Walker (Mode A) and interactive closes both write it; absence = pre-layer legacy close.
**Immutable once written (spec-review WARN-3):** a kickback (#2771) never mutates the
existing record — rework re-opens the task, and the next legitimate done-flip OVERWRITES
`close_record` (prior versions recoverable from `tasks_history` row snapshots). At write
time `ac[].status ∈ {passed, na}` only — `pending`/`failed` block the flip per the
AC-discipline gate, so they cannot appear in a valid record.

```json
{
  "version": 1,
  "tier": "low | medium | high",
  "never_auto_clear": false,
  "emphasis": "fast_track | judgment_first",
  "evidence": [
    {
      "kind": "test_run | live_curl | file_read | grep | explain_delta | source_check | rerun | ci_run | other",
      "independence": "independent | born_in_work",
      "teeth_check": "red_green | mutation | coverage | post_fix_recheck | hypotheses_first | explain_delta | two_independent_sources | none",
      "source": "<command / file:line / url that produced it>",
      "result": "<observed outcome, one line>"
    }
  ],
  "ac": [
    {
      "text": "<criterion>",
      "status": "passed | na",
      "evidence_idx": [0],
      "verified_by": "lead | operator"
    }
  ],
  "deferrals": [
    { "what": "<deferred item>", "followup_task": 123 }
  ],
  "loc_delta": { "insertions": 0, "deletions": 0, "files": 0 },
  "spot_check": [
    { "claim": "<subagent claim>", "how": "<Lead's independent check>", "result": "<observed>" }
  ],
  "trust_metrics": {
    "rework_rate": null,
    "rework_rate_source": "placeholder | history_query"
  }
}
```

Field notes:
- `version` — schema evolution guard; renderers refuse unknown majors gracefully.
- `evidence[].source` — artifact-backed only (command, file:line, url, task id). Free-text
  claims without a source do not qualify as evidence (rail contamination rule applies).
- `ac[].evidence_idx` — indexes into `evidence[]`; an AC with an empty list is visibly
  "asserted, not evidenced" on the card. High tier forbids empty evidence_idx on passed ACs.
- **Relationship to `tasks.acceptance_criteria` (authoritative, spec-review WARN-1):**
  `close_record.ac[]` is a READ-ONLY SNAPSHOT of the live `tasks.acceptance_criteria`
  array taken at flip time, enriched with `evidence_idx` links. The live array keeps its
  locked shape `{text,status,verified_by,verified_at,notes,gate,gate_kind}` UNCHANGED —
  this layer adds NO fields to it; gates keep reading the live array, renderers read the
  snapshot. (#2770 implements the snapshot write — NOT an inline extension of
  `acceptance_criteria`.)
- `deferrals[]` — mirrors the AC `na`-needs-followup rule (#ac-status discipline): every
  deferral names a live follow-up task id.
- `loc_delta` — from `git diff --stat`; the over-generation signal (Karpathy item 4). Doc
  or non-code tasks may zero it.
- `spot_check[]` — the Lead's independent verify record (the Mode-B guard made durable):
  which subagent claims were re-checked and what was observed.

## Evidence taxonomy: independent vs born-in-work

**Independent** — produced by an actor/mechanism OUTSIDE the change's own artifacts:
Lead live-curl against the running system; Lead re-read of the modified file; operator-run
suite; CI run; pre-existing tests passing on the new code; external authoritative source.

**Born-in-work** — produced by the change about itself: its own new tests passing, the
agent's self-reported output, logs emitted by the changed code.

Born-in-work evidence is upgraded (not reclassified) by **teeth-checks** — proofs the
evidence can actually bite:
- `red_green` — the new test shown failing before the fix, passing after.
- `mutation` — deliberately break the code; the test catches it.
- `coverage` — changed lines/branches demonstrably exercised.
- `post_fix_recheck` — the original failure scenario re-driven end-to-end after the fix.
- Non-dev analogues: `hypotheses_first` (expected outcome stated BEFORE the check runs —
  novel/content/research), `explain_delta` (before/after query plans — data-analytics),
  `two_independent_sources` (veracity/research claims).

**Tier gates (the rule the card enforces visually):**
- `high` → ≥2 independent evidence items AND no passed AC with empty `evidence_idx`.
- `medium` → ≥1 independent evidence item.
- `low` → born-in-work acceptable when carrying ≥1 teeth-check, or the change is trivially
  observable (doc/CSS class).
- `never_auto_clear` → operator review regardless of everything above.

**Enforcement semantics (spec-review WARN-4):** the tier gates are card semantics —
advisory rendering + audit trail. v0.9.0 hard-enforces exactly ONE rule server-side
(#2773: born-in-work evidence carrying no teeth-check cannot satisfy a passed AC's
evidence link). `never_auto_clear` enforcement REUSES the existing operator-gate
mechanism — `tasks.operator_gate` / AC `gate='operator'` (#2127, FE-supported) — no new
blocking primitive. High-tier's ≥2 independent items must be of DIFFERENT `kind`s
(diversity, not redundancy — two identical live-curls don't clear it). `evidence_idx`
index-links are the intentional normalized realization of AC2's `evidence` key. At close
time this section formalizes and supersedes the informal risk-tier verify split of
CLAUDE.md Karpathy item 5C; 5C remains the mid-session behavior rule.

## Trust metric → autonomy dial (forward pointer, not built here)

`rework_rate` per (team, tier) is the earned-autonomy input: low rework at a tier over a
window widens what the operator lets fast-track; a spike narrows it. The dial CONSUMES the
close records; it never edits them. Concrete thresholds are a later design (post
history-query data), deliberately not locked now.

## Renderers (implementation contract for #2770-series)

- Web: task-detail drawer gains a "Close record" card section — emphasis half first,
  evidence table second, deferrals + loc_delta footer. Read-only.
- Chat: Lead close-message embeds the same content as a markdown block (emphasis-ordered).
- Both derive every pixel from `close_record`; no renderer-side facts.

## Follow-ups opened by this lock

- Rework-rate history query (D2) — filed as its own task (ms57).
- Per-project emphasis override — optional, unfiled; revisit after first real usage.
