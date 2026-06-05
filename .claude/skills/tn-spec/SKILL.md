---
name: tn-spec
description: >-
  Harden a raw task idea with 2 rounds of adversarial pushback + revision BEFORE creating the task,
  then hand the refined spec to /tn-task-create. Use when an idea is fuzzy or high-stakes and you
  want the acceptance criteria right the first time.
argument-hint: "<raw task idea / description>"
allowed-tools:
  - Read
  - Grep
  - Glob
---

# /tn-spec — pushback + revise twice, then create

`$ARGUMENTS` = the raw task idea. This hardens it BEFORE a task exists, so the AC and scope are
right at creation (cheaper than create-then-rework). Orchestration playbook: YOU (the Lead) run the
critique rounds (optionally spawning **dev-spec-reviewer** for an independent pass).

## Round 1 — critique the raw idea
Push back hard. Check for:
- **Ambiguity** — anything that could be built two different ways.
- **Multi-point drops** — every distinct requirement the operator stated must survive into the spec.
- **Missing / unverifiable AC** — each acceptance criterion must be concrete and independently checkable.
- **Hidden architectural implications** — does it touch a contract, migration, or another surface?
- **Conflicts** — with existing behavior, standards, or another task.
Optionally spawn **dev-spec-reviewer** for an independent read. Then REVISE the spec (title,
description, draft AC, task_type).

## Round 2 — adversarial re-critique
Attack the revised spec: are the AC now verifiable? Did scope creep in? Is anything still ambiguous?
Is each original requirement still present? Revise once more. Stop when 2 consecutive rounds surface
no new substantive gap.

## Hand off
Present the hardened spec (title, description, task_type, draft AC). Then create it via
**/tn-task-create** (or POST directly) so the footgun guards apply (project_id in body, AC at creation).

## Guards / lessons encoded
1. Don't create the task until 2 rounds of critique pass — fix the spec, not the task afterward.
2. Every requirement point the operator stated lands in the spec/AC (multi-point discipline; a silent
   drop is an incident).
3. AC must be verifiable at creation — never "create now, define done later".

## Usage
```
/tn-spec add rate-limiting to the email endpoints
```
