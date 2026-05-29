---
decay_class: review-on-touch
review_when: the add-team/add-agent surface changes (constants.py, scaffold, schemas, FE team dropdown) or a new team is onboarded
scope: team-methodology (dev) — how to add a team / agent to the agent-teams platform post-#1620
proposed_by: lead (Kanban #1643 — validated live by onboarding the netops team)
last_reviewed: 2026-05-29
---

# Team / agent onboarding runbook (post-#1620)

How to add a new team or a new agent to the agent-teams platform, the full blast
radius, and the residual friction. **Validated live by onboarding `netops`
(Kanban #1643)** — a read-only network-diagnosis team.

## Add a TEAM (blast radius)

Single source of truth = `api/src/constants.py`. Post-#1620 the DB `CHECK` on
`projects.team` was dropped (migration `0051_drop_projects_team_check`), so:

| Surface | Edit needed? | Note |
|---|---|---|
| `api/src/constants.py` — `ProjectTeam.<T>` + `ProjectTeam.ALL` | **YES** | the enum value |
| `api/src/constants.py` — `TEAM_ROSTERS[<T>]` | **YES** | the dedicated-agent roster (import-time invariant requires every team to have one) |
| `.claude/teams/<T>.md` | **YES** | the team playbook |
| `.claude/agents/<role>.md` for each NEW roster role | **YES** | the agent definitions |
| DB migration | **NO** | enum is app-validated; CHECK dropped #1620 |
| ORM `CheckConstraint` | **NO** | none on `projects.team` |
| `schemas/project.py` `TeamCode` | **NO (auto)** | `Literal[*ProjectTeam.ALL]` derives it |
| `GET /api/teams`, scaffold manifest, FE team dropdown, `bin/agent-teams-init.ps1` | **NO (auto)** | all derive from `constants.py` + the API |

**Confirmed for netops (#1643):** added `ProjectTeam.NETOPS` + a roster entry +
`.claude/teams/netops.md` + one agent, and `POST /api/projects {team:"netops"}`
succeeded (project id=660, HTTP 201) with **zero migration files added**
(`git status api/alembic/versions/` empty). `GET /api/teams` lists netops.

## Add an AGENT to an existing team (blast radius)

| Surface | Edit needed? | Note |
|---|---|---|
| `.claude/agents/<role>.md` | **YES** | the agent definition (humans-only zone) |
| `api/src/constants.py` — `TEAM_ROSTERS[<team>]` += role | **YES** | makes it scaffolded + listed |
| `TaskRole` code (in the team's 10-code band) | **only if Kanban-assignable** | needed for `tasks.assigned_role` routing. Diagnosis teams that spawn-by-name (netops) skip this |
| DB migration | **NO** | — |

## Residual friction found onboarding netops (#1643)

These are the real costs #1620 did NOT remove — capture them so the next onboarding
doesn't trip:

1. **Agent loading is operator-gated, not a pure code change.** New
   `.claude/agents/*.md` are humans-only AND load only at session start. So "add an
   agent" = code edit (`constants.py`) + a humans-only file move + a **session restart**
   before the agent is invokable. Budget a manual restart step; it can't be a
   single committed change that's live immediately.
2. **`TEAM_ROSTERS` ↔ `.claude/agents/` coupling is load-bearing.** A roster entry
   whose `.claude/agents/<role>.md` file does not yet exist degrades scaffold manifest
   resolution (`services/zero_config_scaffold._resolve_manifest`, which copies the
   per-role `.md`): the missing file is recorded in the scaffold report's `errors`
   (best-effort — it does NOT raise), so a project created with that team in the gap
   scaffolds those roles with **no agent definition**. Consequence: you **cannot**
   commit the roster line ahead of the file — the constants edit and the file move
   must land together. This is why #1643 shipped
   the roster expansion as an operator batch (see `_scratch/netops-build/MOVE-INSTRUCTIONS.md`),
   not a pre-commit.
3. **No `TaskRole` band for netops.** The partition (dev 1-10, novel 11-20, seo 21-30,
   sem 31-40, data-analytics 41-50) has no netops range, so netops agents are
   spawn-by-name only, not Kanban-assignable via `assigned_role`. Fine for a Lead-driven
   diagnosis team; a gap if you later want netops tasks routed by role code (would need
   a `RANGE_MAX` bump + a netops band).
4. **No netops node in the langgraph headless graph.** A scheduled (`recurrence_rule`)
   or `auto_pickup` netops task would be picked up by the headless worker and misroute
   to a non-netops specialist — there is no netops dispatch in `langgraph/graph.py`. So
   netops is **interactive-only** today: scheduled sweeps must be `run_mode=manual`
   (they fire interactive TODOs an operator runs via a Lead session). Headless auto-run
   is Phase 2 (add a graph node + a read-only Zabbix token).

## What #1620 actually saved

Before #1620, each new team needed its own migration to widen the `projects.team`
CHECK — the repo still carries those one-per-team migrations as evidence
(`2026_05_18_*_projects_team_content`, `2026_05_20_*_seo_team_allowed`,
`..._data_team_allowed`, `..._sem_team_allowed`). netops needed **none** of that: the
team enum is now a pure `constants.py` edit, app-validated at the API boundary, with
every derived surface (schemas Literal, `GET /api/teams`, FE dropdown, scaffold) tracking
automatically. Net: "add a team" dropped from {migration + constants + playbook + agents +
FE} to {constants + playbook + agents}. The remaining cost is the operator-gated agent
load (friction #1) — not a migration.
