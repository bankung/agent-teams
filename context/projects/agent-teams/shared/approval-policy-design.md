# Approval policy design (#957)

Design draft for the per-project approval-policy layer that sits OVER the existing HITL infrastructure (#950) and tool-tier permission gate (langgraph/tools/permission_gate.py). Goal: auto-approve safe actions so operator HITL queue doesn't bottleneck capacity scaling (resolves session-review W4).

## Two distinct gates (don't conflate)

**Gate 1 — Tool tier (existing, langgraph/tools/permission_gate.py):**
- Decides: "Is this tool category ALLOWED to run at all in this project?"
- Output: allow / halt / reject
- Source: `projects.tools_config.auto_allow_tiers` / `halt_tiers`
- Example: a project might block `shell` tier entirely

**Gate 2 — Approval policy (NEW, this design — #957):**
- Decides: "Given the tool is allowed, does it execute auto OR wait for HITL operator approval?"
- Output: auto-execute / queue-for-HITL
- Source: `projects.approval_policy` JSONB column (new)
- Example: `file_write` is tier=write (allowed) but approval=HITL on first time per path

Both gates pass → tool runs. Either gate halts → no run.

## Action categories (5)

Mapping from "tool / action class" to "policy category". Static map (in code, not config) so behavior is predictable.

| Category | Examples | Default policy |
|---|---|---|
| **`read_only`** | `http_get` (allowlisted host), `git_status`, `git_diff`, file_read, Kanban GET | **auto** |
| **`safe_write`** | Kanban PATCH (own task), draft document write to `_scratch/`, scaffold project, append to log file | **auto** |
| **`reversible_write`** | File edit in repo working tree (recoverable via git), email archive (not delete), task cancel | **auto with audit** |
| **`destructive`** | File delete, DB DELETE, git push -f, email delete (vs archive), shell with `rm`/`mv` | **HITL** |
| **`external_effect`** | Send email, post LinkedIn, submit application, payment trigger, DM, public commit/push to non-personal repo | **HITL** |
| **`financial`** | Any single LLM call > $X (configurable; default $1), tool invocation triggering > $X spend in project today | **HITL** |
| **`new_capability`** | First time per project using a tool category never used before (e.g., first git push, first shell_run) | **HITL** (one-time gate) |

Categories above are not orthogonal — an action may belong to multiple. Policy takes the MOST RESTRICTIVE (HITL beats auto). Example: sending an email > $1 LLM cost = both `external_effect` AND `financial` → HITL.

## Per-project policy override

`projects.approval_policy` JSONB (new column, migration TBD):

```json
{
  "auto_approve_categories": ["read_only", "safe_write", "reversible_write"],
  "hitl_categories": ["destructive", "external_effect", "financial", "new_capability"],
  "financial_threshold_usd": 1.00,
  "auto_approve_during": "operator_awake",
  "operator_awake_hours": {"start": "07:00", "end": "23:00", "tz": "Asia/Bangkok"},
  "max_auto_approves_per_hour": 30,
  "fallback_on_escalation": "halt_project",
  "audit_all_auto_approvals": true
}
```

Field by field:

- **`auto_approve_categories` / `hitl_categories`**: explicit per-project override of the defaults table above. Categories not in either fall back to the static default.
- **`financial_threshold_usd`**: $-amount above which an action escalates to HITL regardless of other categories.
- **`auto_approve_during`**: window when auto-approve is active.
  - `"always"` — auto-approve 24/7 (highest velocity, lowest safety)
  - `"operator_awake"` — auto-approve only during operator_awake_hours window (default; assumes operator can react to mistakes fast)
  - `"never"` — every action HITL (slowest; safest)
- **`operator_awake_hours`**: explicit window in operator's timezone.
- **`max_auto_approves_per_hour`**: rate limit. Above this, queue for HITL even if category is auto.
- **`fallback_on_escalation`**: what happens when a HITL action sits unanswered past `hitl_timeout_hours` (#989). Options:
  - `"halt_project"` — pause entire project until operator catches up
  - `"halt_task"` — halt this specific task only; project continues with other work
  - `"auto_reject"` — synthesize reject answer; task marked failed
- **`audit_all_auto_approvals`**: when true, every auto-approved action logged to `tasks_history` even if it's read-only (default true).

## How the gate fires (runtime flow)

```
Specialist node wants to invoke tool T with args A
  ↓
Gate 1: check_permission(tools_config, T) → allow/halt/reject
  ↓ (allow only — else return halt to graph)
Gate 2 NEW: check_approval_policy(approval_policy, T, A, project_id, cost_est)
  ↓
  ├─ category in auto_approve_categories
  │   AND not over rate limit
  │   AND not over financial_threshold
  │   AND within operator_awake_hours (if "operator_awake")
  │   AND not "new_capability" for this project
  │   → AUTO_APPROVE
  │       ↓ (log to tasks_history if audit_all_auto_approvals)
  │       ↓ execute tool
  │
  └─ otherwise
      → QUEUE_FOR_HITL
          ↓ task PATCH: halt_reason='approval_pending', interaction_kind='decision',
                       question_payload={question: "Approve tool X with args ...?",
                                          options: ["approve", "deny", "approve_and_remember"]}
          ↓ operator answers via FE / curl (reuses #950 HITL infrastructure)
          ↓ on 'approve_and_remember': update approval_policy to auto-approve this specific
            (tool, args-fingerprint, project) tuple for next N days
          ↓ on 'approve': execute once
          ↓ on 'deny': halt with reason
```

## Confidence scoring (deferred to v2)

Originally considered adding confidence-from-auditor as a gate input. Decided **defer to v2** because:
- Auditor fires AFTER specialist completes; not on individual mid-flight tool calls
- Heuristic flags (clean specialist run) — same constraint
- LLM self-report — unreliable for safety-critical decisions

v1: category + project policy + rate + financial = sufficient gate.

v2: add confidence scoring if data shows false-auto-approves are happening on actions the auditor would have flagged.

## Open design questions

### Q1 — Category mapping: code or config?

- **A**: static map in code (`langgraph/tools/category_map.py`) — predictable, testable, hard to misconfigure
- B: per-project YAML override — more flexible but invites foot-guns

Recommend **A**. Categories are platform-level; per-project flexibility lives in `auto_approve_categories` lists, not in re-categorizing.

### Q2 — `new_capability` tracking: per-project or per-(project, tool)?

- **A**: per-(project, tool). Project remembers `seen_capabilities: ["http_get", "file_write", ...]`. First time using a new tool in project = HITL once; remembered after.
- B: per-project flat — first 3 tool categories used trigger HITL; after that, auto.

Recommend **A**. More granular. Storage: extend `projects.tools_config` or add `projects.seen_capabilities` JSONB.

### Q3 — Cost calculation for `financial` threshold?

- **A**: rough estimate before tool call (specialist provides token-count estimate via #944's cost tracker)
- B: actual measured cost AFTER tool call (too late — can't gate retroactively)
- C: hybrid — estimate before, measure after; if measure exceeds estimate by 2× → halt project for next-call confirmation

Recommend **A** for v1. **C** as v2 enhancement.

### Q4 — Rate limit semantics?

- **A**: rolling 1-hour window (each auto-approve drops out after 1hr)
- B: fixed window (top of each hour resets)
- C: budget-style (replenish at rate of 30/hour)

Recommend **A**. Predictable; no burst at hour-rollover.

### Q5 — "approve_and_remember" granularity?

- **A**: per-(tool, args-fingerprint, project) — remember "auto-approve http_get to api.linkedin.com for project secretary"
- B: per-(tool, project) — remember "auto-approve all http_get in project secretary"
- C: per-tool-globally — too coarse; dangerous

Recommend **A**. Args fingerprint = hash of normalized args (host for http_*; file-path prefix for file_*; cmd substring for shell_*). Stored in `projects.tools_config.remembered_approvals: [{tool, args_fp, expires_at}]`. Default expiry 30 days.

### Q6 — Default policy when project has no `approval_policy` set?

- **A**: conservative — HITL for everything except `read_only`
- B: aggressive — auto-approve everything safe (reversible_write+)
- C: per-team default — dev projects vs business projects different defaults

Recommend **A**. Operator must opt-in to higher auto-approval per project. Reduces surprise.

## Migration sketch

```python
# Migration 0031_projects_approval_policy
def upgrade():
    op.add_column("projects",
        sa.Column("approval_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("projects",
        sa.Column("seen_capabilities", postgresql.JSONB(astext_type=sa.Text()), 
                  nullable=False, server_default="[]"))
    # No CHECK — element-shape validation lives in Pydantic
def downgrade():
    op.drop_column("projects", "seen_capabilities")
    op.drop_column("projects", "approval_policy")
```

## Implementation slices (sub-tasks for #957 when ready to ship)

1. **#957a — Schema + Pydantic** (1 day): migration 0031 + ProjectRead/ProjectUpdate exposes approval_policy + seen_capabilities + pydantic validation
2. **#957b — Category map + gate function** (2-3 days): `langgraph/tools/approval_gate.py` with the action-category mapping + `check_approval_policy()` function + rate limit tracking
3. **#957c — Gate integration in specialist tool-use loop** (1-2 days): wire `check_approval_policy` into the existing permission_gate flow; add `approval_pending` halt_reason; integrate with HITL question_payload pattern
4. **#957d — FE knob for per-project policy** (2 days): edit-project modal gains approval_policy form fields (categories, threshold, awake hours)
5. **#957e — Pytest end-to-end** (1 day): auto-approve flows through; HITL queue forms correctly; "approve_and_remember" persistence; rate limit; financial threshold; operator_awake window
6. **#957f — Live smoke on hitl-test project** (~1 hr): real autorun task with mixed actions; verify policy gates correctly

Total: ~7-10 days dev work. Mid-priority sub-tasks parallelizable (a + b + d together; c after a+b; e+f after c+d).

## Risks (be honest)

### R1 — Policy misconfiguration burns the operator

If `auto_approve_during: always` + `auto_approve_categories: [destructive]` is mistakenly set, agent can do real damage. Mitigation: Pydantic validator rejects high-risk combos (destructive in auto-approve list AND awake_hours != strict); FE UI shows warning banner when policy permissive.

### R2 — `approve_and_remember` enables creep

Operator clicks "approve_and_remember" carelessly; over time the auto-approve list bloats with risky entries. Mitigation: 30-day expiry default; weekly portfolio review surfaces "you approved 23 things this week — review?"; UI shows the remembered list per project.

### R3 — Rate limit doesn't catch burst risk

30/hour is a lot of auto-approves. Each individually OK; aggregate could be expensive or destructive. Mitigation: separate counter for `external_effect` (default 5/hour); financial threshold catches cost explosion regardless.

### R4 — Policy semantics drift from operator intent

What operator MEANS by "safe" vs what the category map encodes — likely to drift over time. Mitigation: explicit category-map documentation per release; changelog entry on category remapping; portfolio review surfaces "this category was previously auto, now HITL — operator should confirm".

## What this design does NOT solve

- **Adversarial agent / prompt injection** — if a malicious upstream prompt fools the specialist into thinking a destructive action is `read_only`, the gate isn't a backstop. Out-of-scope for #957; covered by category-MAP integrity (categories assigned by tool, not by LLM).
- **Insider risk** — operator with malicious intent can set `auto_approve_during: always` + `auto_approve_categories: [destructive]`. Not a software problem.
- **Multi-tenant security** — agent-teams is single-tenant. Multi-operator approval flows not in scope.

## Cross-references

- `agent-teams/shared/session-review-2026-05-17.md` W4 — origin of this design
- `langgraph/tools/permission_gate.py` — existing tool-tier gate (Gate 1)
- `langgraph/hitl.py` — HITL infrastructure (#950) that Gate 2 queues into
- `api/src/services/cost_tracker.py` — cost estimate source (#944)
- `_private/CONFIDENTIAL-README.md` — strategic context (operator-private)
