# Context layout

Filesystem zones (the **DB** zone lives in PostgreSQL, not here — see `api/`). Zone names follow the CLAUDE.md storage table; the Q0–Q2 placement framework there decides which zone any new content goes into.

```
context/
├── standards/                            ← Standards zone — universal, humans only
│   ├── README.md
│   ├── general.md                        ← cross-framework rules + Kanban codes
│   ├── nextjs/  react/  typescript/  tailwind/
│   ├── fastapi/  python/  pydantic/  sqlalchemy/
│   └── postgresql/  docker/
│
├── teams/                                ← Team-methodology zone — per-team cross-project, Lead writes
│   ├── dev/
│   │   ├── decisions.md                  ← system/methodology decisions log (append-only)
│   │   ├── smoke-methodology.md          ← Tier-1 probe shape, decision matrix, anti-patterns
│   │   └── release-methodology.md        ← Tier-2 release wrap-up flow + severity scale
│   └── <future-team>/                    ← novel/, data-science/, etc.
│
└── projects/                             ← Project zones (shared + role state)
    └── <project>/                          (auto-created on POST /api/projects)
        ├── shared/                       ← Project-shared zone — Lead writes only (committed)
        │   ├── decisions.md
        │   ├── api-contracts.md          (dev team)
        │   ├── db-schema.md              (dev team)
        │   ├── smoke-matrix.md           (dev team — project-specific Tier-1 config)
        │   └── release-matrix.md         (dev team — project-specific endpoint matrix)
        ├── <role-1>/                     ← Role-state zone — role-owned (gitignored except .gitkeep)
        ├── <role-2>/
        └── ...
```

Role folder names follow the active team's roster. For `team='dev'`: `dev-frontend/`, `dev-backend/`, `dev-devops/`, `dev-tester/`, `dev-reviewer/`. For `team='novel'`: `novel-writer/`, `novel-editor/`. See `.claude/teams/<team>.md` for the canonical list.

## Write/read matrix

| Path | Writer | Readers | Commit? |
|---|---|---|---|
| `context/standards/<framework>/` | **humans only** | Lead + subagents per lane | yes |
| `context/teams/<team>/` | Lead | Lead + subagents of any project under that team | yes |
| `context/projects/<p>/shared/` | Lead | every subagent of project p | yes |
| `context/projects/<p>/<role>/` | that role only | other roles in project p | no (gitignored except .gitkeep) |
| DB (projects/tasks/tasks_history) | UI + Lead via API | UI + Lead via API | n/a (per machine) |

## File naming inside a role folder

- `current-state.md` — exactly one per role; an always-current snapshot. Never append-only.
- Session / review / bug notes — `<type>-<YYYY-MM-DD>-<slug>.md`.

## Storage zones — full table (with blast radius)

The CLAUDE.md storage table is the compact version (zone · path · writer · read scope). This is the full table including blast radius — the decision driver for "pick zone by scope, not convenience".

| Zone | Path | Writer | Read scope | Blast radius |
|---|---|---|---|---|
| **DB** | PostgreSQL | UI / Lead via API | UI + Lead | per-project transactional |
| **Standards** | `context/standards/<framework>/` | humans only | Lead + subagents (per lane) | universal — every team, every project |
| **Team methodology** | `context/teams/<team>/` | Lead | Lead + subagents of any project under that team | every project under one team |
| **Project shared** | `<working_path>/shared/` | Lead | every subagent of project p | one project, every role |
| **Role state** | `<working_path>/<role>/` | that role only | other roles in project p | one project × one role |

## Path resolution — `projects.working_path`

The two project-scoped zones (Project shared + Role state) resolve their filesystem path via the `projects.working_path` column:

- **`working_path` is set** (typical for non-agent-teams projects) → `<working_path>/shared/` + `<working_path>/<role>/`. Lives OUTSIDE the agent-teams repo.
- **`working_path` is null** (agent-teams itself + legacy projects) → fallback `agent-teams/context/projects/<name>/shared|<role>/`.

**Rule:** every NEW non-agent-teams project SHOULD set `working_path` on creation. **Agent prompts** must use the resolved absolute path. When `working_path` changes, both the DB row AND agent prompts must update together.

## Q3 — operator memory dir vs project shared/ (the detail behind CLAUDE.md's Q3)

Auto-memory (`~/.claude/projects/<cwd-hash>/memory/`) lives OUTSIDE the 5 zones above — it's operator-personal, scoped to Claude Code's CWD, not Lead's project binding. A session in agent-teams writes here even when bound (via API) to project=secretary. Before saving any memory, walk:

- **Q3a. Universal Lead behavior** (Karpathy, AC, subagent rules, operator personal prefs)? → **memory dir** (default).
- **Q3b. agent-teams platform rule** (classifier, hooks, AT ops)? → **memory dir**.
- **Q3c. Project-scoped** (workflow rules, project state, project-specific rules)? → **`<working_path>/shared/<filename>.md`** of that project — **NOT** the memory dir.

Project-scoped content in the agent-teams memory dir is an anti-pattern: loads every agent-teams session even for unrelated work, pollutes Lead context, invisible to other operators. Refactor incident 2026-05-27 (Kanban #1593) moved 18 misplaced memories out.
