# Context layout

```
context/
├── standards/                            ← Bucket 2: cross-project, humans only
│   ├── README.md
│   ├── general.md                        ← cross-framework rules + Kanban codes
│   ├── nextjs/  react/  typescript/  tailwind/
│   ├── fastapi/  python/  pydantic/  sqlalchemy/
│   └── postgresql/  docker/
│
├── leads/                                ← Bucket 3: cross-project per-lead methodology, Lead writes
│   ├── dev/
│   │   ├── decisions.md                  ← system/methodology decisions log (append-only)
│   │   ├── smoke-methodology.md          ← Tier-1 probe shape, decision matrix, anti-patterns
│   │   └── release-methodology.md        ← Tier-2 release wrap-up flow + severity scale
│   └── <future-lead>/                    ← novel/, data-science/, etc.
│
└── projects/                             ← Bucket 4: per-project knowledge
    └── <project>/                          (auto-created on POST /api/projects)
        ├── shared/                       ← Lead writes only (committed)
        │   ├── decisions.md
        │   ├── api-contracts.md          (dev lead)
        │   ├── db-schema.md              (dev lead)
        │   ├── smoke-matrix.md           (dev lead — project-specific Tier-1 config)
        │   └── release-matrix.md         (dev lead — project-specific endpoint matrix)
        ├── <role-1>/                     ← role-owned (gitignored except .gitkeep)
        ├── <role-2>/
        └── ...
```

Role folder names follow the active lead's roster. For `lead='dev'`: `dev-frontend/`, `dev-backend/`, `dev-devops/`, `dev-tester/`, `dev-reviewer/`. For `lead='novel'`: `novel-writer/`, `novel-editor/`. See `.claude/leads/<lead>.md` for the canonical list.

(Bucket 1 = DB; see `api/`, not the filesystem.)

## Write/read matrix

| Path | Writer | Readers | Commit? |
|---|---|---|---|
| `context/standards/<framework>/` | **humans only** | Lead + subagents per lane | yes |
| `context/leads/<lead>/` | Lead | Lead + subagents of any project under that lead | yes |
| `context/projects/<p>/shared/` | Lead | every subagent of project p | yes |
| `context/projects/<p>/<role>/` | that role only | other roles in project p | no (gitignored except .gitkeep) |
| DB (projects/tasks/tasks_history) | UI + Lead via API | UI + Lead via API | n/a (per machine) |

## File naming inside a role folder

- `current-state.md` — exactly one per role; an always-current snapshot. Never append-only.
- Session / review / bug notes — `<type>-<YYYY-MM-DD>-<slug>.md`.
