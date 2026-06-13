---
name: tn-intense-review
description: >-
  Run the 2-round adversarial review + test-hardening pass on a change before it is considered done.
  Round 1 finds issues, you fix, Round 2 tries to refute the fixes + re-checks determinism. Use on a
  diff/feature that needs to be bulletproof (the pattern used to harden #1310).
argument-hint: "<what to review: a diff range, file list, or task id + scope>"
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
---

# /tn-intense-review — 2-round adversarial review + determinism hardening

`$ARGUMENTS` = the scope to review (e.g. "diff on dev since <sha>", "web/components/NewTaskModal.tsx",
or "task 1310's changes"). This is an orchestration playbook: YOU (the Lead) spawn the subagents and
integrate results. Do NOT mark anything done until Round 2 is clean.

## Step 0 — pin the scope
Identify exactly what's under review: `git -C <repo> diff <range>`, the touched files, and whether
those files have tests. State the scope explicitly before spawning.

## Round 1 — find issues (parallel, read-only)
Spawn IN PARALLEL:
- **dev-reviewer** — correctness, quality, standards, perf on the scope.
- **dev-security-reviewer** — auth, input handling, injection, file/shell ops, dependency surface.
- **dev-tester** (only if the touched code has tests) — run the FULL test suite (not just the one
  file) in a determinism loop (≥15× under parallel load) to surface flaky/async races. Report the
  failing run verbatim, not just "passed".

Each must return raw evidence (file:line, the failing assertion, the exact command output). Collect
findings as blockers / majors / minors.

## Fix
Address every blocker + major (delegate to the right dev-* specialist, or fix inline). Re-run the
relevant check to confirm each fix lands. Keep minors as a tracked follow-up if out of scope.

## Round 2 — adversarial re-review (parallel, read-only)
Re-spawn **dev-reviewer** + **dev-security-reviewer** on the FIXED code, prompted to REFUTE the
fixes and hunt regressions (not re-list Round-1 issues). Re-run the determinism loop. A finding
survives only if the reviewer shows concrete evidence.

## Verdict
Report per round: findings + how each was resolved + the final determinism result (e.g. "45× clean").
Declare clean ONLY if Round 2 has 0 blockers/0 majors AND the test loop is deterministic. Otherwise
loop the fix→Round-2 once more or hand back to the operator.

## Guards / lessons encoded
1. Reviewers are READ-ONLY; the Lead independently re-verifies green claims (verify-don't-trust).
2. Prove test determinism with the FULL-SUITE loop, not a single-file loop — cross-file parallel
   load is what surfaces async-fetch races (#1310 lesson).
3. Round 2 is adversarial (refute the fix), not a repeat of Round 1.

## Usage
```
/tn-intense-review diff on dev since 31f115d (the tn-* skill batch)
```
