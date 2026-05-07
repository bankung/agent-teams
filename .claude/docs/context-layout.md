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
└── projects/                             ← Bucket 3: per-project knowledge
    └── <project>/                          (auto-created on POST /api/projects)
        ├── shared/                       ← Lead writes only (committed)
        │   ├── decisions.md
        │   ├── api-contracts.md
        │   └── db-schema.md
        ├── frontend/                     ← role-owned (gitignored except .gitkeep)
        ├── backend/
        ├── devops/
        ├── qa/
        └── reviewer/
```

(Bucket 1 = DB; see `api/`, not the filesystem.)

## Write/read matrix

| Path | Writer | Readers | Commit? |
|---|---|---|---|
| `context/standards/<framework>/` | **humans only** | Lead + subagents per lane | yes |
| `context/projects/<p>/shared/` | Lead | every subagent of project p | yes |
| `context/projects/<p>/<role>/` | that role only | other roles in project p | no (gitignored except .gitkeep) |
| DB (projects/tasks/tasks_history) | UI + Lead via API | UI + Lead via API | n/a (per machine) |

## File naming inside a role folder

- `current-state.md` — exactly one per role; an always-current snapshot. Never append-only.
- Session / review / bug notes — `<type>-<YYYY-MM-DD>-<slug>.md`.
