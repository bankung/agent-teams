---
name: tn-milestones
description: >-
  List the active project's milestones with their task rollup (done/total, progress %). Read-only.
  Quick "where do the milestones stand?" overview.
argument-hint: "(no args)"
allowed-tools:
  - Bash(curl:*)
  - Read
metadata:
  version: 1.0.0
  category: kanban
  tags: [kanban, milestone, read-only, overview]
---

# /tn-milestones — milestone overview with rollup

Read-only (no mutations).

## Step 1 — resolve the active project id
Resolve `X-Project-Id` by running `powershell -File bin/lead-project-id.ps1` — it prints THIS session's bound project id and exits non-zero if this session is unbound (→ STOP, run `/tn-bind`). Never read the global `lead_project_id.txt` (it may hold another concurrent session's project). [#2680]

## Step 2 — list
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/milestones \
  -o _scratch/tn_ms_list.json -w "%{http_code}"
```
The list (MilestoneRead) does NOT include the rollup. For each milestone you want progress on,
fetch its detail (MilestoneDetail carries `rollup`):
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/milestones/<mid> -o _scratch/tn_ms_<mid>.json
```

## Step 3 — print
Order: active first, then planned (by sort_order NULLs-last, then id), then released/cancelled last.
For each: `#id` · title · status · `sort_order` · **progress** `done/total (progress_pct%)` from the
rollup. Keep it compact.

## Related skills
- `tn-milestone-create` — create a new milestone once you've reviewed the current ones here
- `tn-milestone-done` — release a milestone when its progress is 100%
- `tn-task-attach` — attach a task to a milestone id discovered via this skill
