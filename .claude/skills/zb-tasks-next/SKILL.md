---
name: zb-tasks-next
description: >-
  List the next N actionable tasks for the active project, ordered the way you actually work
  them: current (or a specified) milestone first, blockers first, then priority, spilling into
  the next milestone when one runs dry. Read-only. Answers "what should I do next?".
argument-hint: "[N] [milestone:<id>]  — N is 5/10/20 (default 10)"
allowed-tools:
  - Bash(curl:*)
  - Read
metadata:
  version: 1.0.0
  category: kanban
  tags: [kanban, task, planning, read-only, queue]
---

# /zb-tasks-next — the prioritized "what's next" queue

`$ARGUMENTS` may contain an N (default **10**; accept 5/10/20) and an optional `milestone:<id>`
to pin a single milestone. Read-only (no mutations).

> NOTE: the API `GET /tasks/summary` (like `/tasks`) orders by `id` only — so ALL the ordering below is done CLIENT-SIDE
> by this skill from the raw rows. Codes: priority 1 LOW / 2 NORMAL / 3 HIGH / 4 URGENT (higher =
> more important); process_status 1 TODO / 2 IN_PROGRESS / 3 REVIEW / 4 BLOCKED / 5 DONE / 6 CANCELLED.

## Step 1 — resolve the active project id
Resolve `X-Project-Id` by running `powershell -File bin/lead-project-id.ps1` — it prints THIS session's bound project id and exits non-zero if this session is unbound (→ STOP, run `/zb-bind`). Never read the global `lead_project_id.txt` (it may hold another concurrent session's project). [#2680]

## Step 2 — fetch milestones (for ordering + spill)
```
curl --silent -H "X-Project-Id: <id>" http://localhost:8456/api/milestones \
  -o _scratch/tn_next_ms.json -w "%{http_code}"
```
Build the milestone order:
- EXCLUDE `released` and `cancelled` milestones (not "next" work).
- Order the rest by `sort_order` ASC (NULLs LAST), then `id` ASC.
- If `milestone:<id>` was given, use ONLY that milestone (no spill).

## Step 3 — fetch the actionable task pool
```
curl --silent -H "X-Project-Id: <id>" "http://localhost:8456/api/tasks/summary?pending=true&limit=500" \
  -o _scratch/tn_next_tasks.json -w "%{http_code}"
```
`/api/tasks/summary` is the slim list projection (#2345) — SAME query + SAME `id` order as
`/api/tasks`, but ~8x smaller (full ~421KB → slim ~52KB at limit=500), carrying every field this
skill orders on (`id`, `title`, `process_status`, `priority`, `milestone_id`, `blocked_by`,
`sort_order`). `pending=true` returns process_status != 5 and excludes cancelled. `limit=500` is the server
maximum (default is 50; values above 500 return HTTP 422). If the actionable pool may exceed 500,
paginate with `offset=<n>` and merge the pages before ordering. From this pool:
- **Actionable** = process_status in {1 TODO, 2 IN_PROGRESS, 3 REVIEW}. EXCLUDE 4 BLOCKED (can't act
  on it yet) — but keep the blocked rows around for blocker detection in the next bullet.
- **Blocker set** = every `blocked_by` value that appears on ANY task in the pool. An actionable
  task whose own `id` is in this set is "a blocker" (finishing it unblocks others) → rank it first.

## Step 4 — order and take N
Walk milestones in the Step-2 order. Within each milestone, sort its actionable tasks by:
1. **is-blocker** (blockers first)
2. **priority** DESC (URGENT → HIGH → NORMAL → LOW)
3. **sort_order** ASC (NULLs last)
4. **id** ASC

Append to the result; move to the next milestone when the current one is exhausted; stop at **N**.
Tasks with `milestone_id = null` form a final bucket AFTER all milestones (same intra-sort).
(If `milestone:<id>` was pinned: only that milestone, no spill, no null bucket.)

## Step 5 — print
For each of the N: `#id` · title · `[milestone:<id or none>]` · priority label · `BLOCKER` tag if it
is one · status label. Note which milestone(s) the list spans, and if fewer than N were available.

---

## Why this exists
Encodes a consistent "work the current sprint first, clear blockers first, by priority" policy so
"what's next?" returns the same disciplined answer every time — instead of an ad-hoc scan.

## Related skills
- `zb-task` — inspect a single task by id once you've identified it from this queue
- `zb-milestones` — see all milestones and their progress percentages for broader sprint context
