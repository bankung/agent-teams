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
