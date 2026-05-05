# General standards (cross-framework)

> **Writer:** มนุษย์เท่านั้น Lead/subagent อ่านอย่างเดียว ห้าม propose update
>
> ไฟล์นี้รวบรวม convention ที่ **ไม่ขึ้นกับ framework** — git workflow, commit message, file naming, folder structure ระดับ root, ภาษาที่ใช้ใน comment ฯลฯ
>
> ทุกครั้งที่ Lead spawn subagent (role ใดก็ตาม project ใดก็ตาม) ไฟล์นี้จะถูก inject เข้าไปด้วยเสมอ

## Git

- **Branch naming:** `<role>/<short-slug>` — e.g., `backend/soft-delete-migration`, `frontend/kanban-board`. Lead-driven branches: `lead/<slug>`. Main branch is `main`.
- **Commit message:** imperative subject < 72 chars (e.g., "Add get_or_404 helper"); blank line; body explains *why* not *what*. English (subject + body) — see "Language for context files" below.
- **Brewed-by trailer for AI-assisted commits:** `Brewed by Claude Opus 4.7 (1M context)` at the end of the body. Project's playful version of `Co-Authored-By`; not a GitHub-recognized trailer (so GitHub won't auto-credit), but it signals AI involvement to readers. Update the model name/version when the model changes.
- **Never commit secrets.** `.env` is gitignored; `.env.example` is the template. Diff-scan before commit.
- **Stage explicitly.** `git add <files>` or `git add .` from repo root — **never** `git add -A` (catches files outside cwd, possibly including secret folders).
- **No amending pushed commits.** Create a new commit instead — amending rewrites history that others may have pulled.
- **No `--force` push to `main` / shared branches.** `--force-with-lease` only on personal branches when truly needed.

## File & folder naming

- **Python:** `snake_case.py` for modules; `PascalCase` for classes; `snake_case` for functions/variables.
- **Migrations:** `YYYY_MM_DD_HHMM_<slug>.py` — Alembic auto-generates via `file_template`; don't override the template (see `sqlalchemy/migrations.md`).
- **Tests:** pytest `test_*.py`.
- **Markdown context:** `kebab-case.md` — e.g., `current-state.md`, `soft-delete.md`, `api-contracts.md`.
- **Folders:** lowercase, no spaces, no UPPERCASE — Linux/macOS/Windows case-sensitivity differs (Windows is case-*insensitive*, which bites cross-platform repos).
- **No leading underscore in filenames** (`_internal.py`) — use `_` prefix on the symbol inside the module to signal private.
- **Phase 3 frontend conventions** (TypeScript / Next.js file naming, component casing, vitest/jest test suffix) will be added when the FE scaffold lands.

## Comments & docs

- **Default: don't write comments.** Self-explanatory identifiers > comments. If you need a comment to explain *what* the code does, rename the identifier.
- **Comment only when WHY is non-obvious** — workaround, hidden constraint, invariant, surprising behavior, security consideration. Audience: future-you reading the code in 6 months.
- **Don't reference task IDs / PR numbers / sprint** in code comments — they rot. Commit messages and `git blame` keep that context.
- **Docstrings:** 1-line summary acceptable for self-documenting functions; multi-paragraph only when the contract has subtleties not obvious from the signature (side effects, ordering requirements, etc.).
- **`# TODO` / `# FIXME`** ok if annotated with *why it's deferred* + ideally a Kanban task ID. A bare TODO with no follow-up plan rots forever.
- **Don't restate framework docs.** Comments like "this validates input via Pydantic" add no value — readers see it from the source.

## Language for context files

- **`context/projects/<p>/shared/*`** → **English**. Team contract; future contributors / Phase 3 FE devs may not read Thai.
- **`context/projects/<p>/<role>/*`** → **Thai or English** (role-owned). Lead reads all roles, so the choice must be readable to Lead. Drafts and notes in Thai are fine.
- **`context/standards/*`** → **English with examples**. Cross-project; standards may be reused by sibling projects.
- **Source code** (Python, future TypeScript, SQL): **English only** — identifiers, comments, log messages, error strings. Universal across editors, search, AI agents, and stack traces.
- **Commit messages:** **English** (subject + body). Use the body for "why" rationale; English keeps the log greppable.
- **Data and user-facing labels:** **Thai is fine** — DB string content (task titles, project names typed by users), UI labels rendered to Thai-locale users, test fixture data. The rule is "code = English; data = whatever the user types".
- **`CLAUDE.md` / `README.md`:** **Thai** (current author preference). Switch to English when onboarding non-Thai contributors; one language per file — don't mix paragraph-by-paragraph.

## Cross-cutting invariants

### Helper duplication between app and migration

When a helper exists in both application code and an Alembic migration (the migration can't import app code — see `sqlalchemy/migrations.md`), document the relationship in **both** files:

- The application copy carries a comment naming the migration that mirrors it.
- The migration copy carries a comment naming the app-side canonical source (e.g., `# kept in sync with src/constants.in_clause`).

Cheap insurance against silent drift. The canonical example is `_in_clause` / `in_clause` between `api/src/constants.py` and `api/alembic/versions/2026_05_04_2130_initial_schema.py`.

### Soft delete is project-wide

Every business table carries a uniform `status SMALLINT 0/1` column (1=active, 0=deleted); application code never issues SQL DELETE. Authoritative file: [`standards/postgresql/soft-delete.md`](postgresql/soft-delete.md). Audit append-only tables (`*_history`) are exempt.

## Kanban schema codes (DB integer constants)

ตาราง `tasks` ใน DB ใช้ INTEGER + CHECK constraint แทน ENUM — codes ต่อไปนี้คงที่ทั่วทุก project ห้ามเปลี่ยนความหมายของเลขเดิม (ขยายเลขใหม่ได้)

### `tasks.status` (INTEGER, NOT NULL, DEFAULT 1)

| Code | Name | คำอธิบาย |
|---|---|---|
| 1 | `todo` | งานสร้างใหม่ ยังไม่เริ่ม |
| 2 | `in_progress` | กำลัง implement |
| 3 | `review` | implement เสร็จ รอ review |
| 4 | `blocked` | ติดอยู่ — รอ dependency / decision |
| 5 | `done` | เสร็จสมบูรณ์ |

CHECK: `status IN (1,2,3,4,5)`

### `tasks.priority` (INTEGER, NOT NULL, DEFAULT 2)

| Code | Name | ใช้เมื่อ |
|---|---|---|
| 1 | `low` | nice-to-have, ไม่เร่งรีบ |
| 2 | `normal` | default งานปกติ |
| 3 | `high` | สำคัญ ทำก่อน |
| 4 | `urgent` | bug ปัญหา blocker / production incident |

CHECK: `priority IN (1,2,3,4)`

### `tasks.assigned_role` (INTEGER, NULLABLE)

| Code | Role |
|---|---|
| 1 | `frontend` |
| 2 | `backend` |
| 3 | `devops` |
| 4 | `qa` |
| 5 | `reviewer` |
| NULL | ยังไม่ assign |

CHECK: `assigned_role IS NULL OR assigned_role IN (1,2,3,4,5)`

### `tasks_history.operation` (CHAR(1))

| Code | คำอธิบาย |
|---|---|
| `U` | UPDATE — task ถูกแก้ |
| `D` | DELETE — task ถูกลบ |

(ไม่ track INSERT — current row อยู่ใน `tasks` แล้ว)

### Application code constants

ทั้งฝั่ง api/ (Python) และ web/ (TypeScript) ต้อง mirror codes ด้านบนเป็น constant ห้าม magic number ใน source:

```python
# api/src/constants.py (ตัวอย่าง)
class TaskStatus:
    TODO = 1
    IN_PROGRESS = 2
    REVIEW = 3
    BLOCKED = 4
    DONE = 5

class TaskPriority:
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4

class TaskRole:
    FRONTEND = 1
    BACKEND = 2
    DEVOPS = 3
    QA = 4
    REVIEWER = 5
```

```typescript
// web/lib/constants.ts (ตัวอย่าง)
export const TaskStatus = {
  TODO: 1,
  IN_PROGRESS: 2,
  REVIEW: 3,
  BLOCKED: 4,
  DONE: 5,
} as const;

export const TaskPriority = {
  LOW: 1,
  NORMAL: 2,
  HIGH: 3,
  URGENT: 4,
} as const;

export const TaskRole = {
  FRONTEND: 1,
  BACKEND: 2,
  DEVOPS: 3,
  QA: 4,
  REVIEWER: 5,
} as const;
```

**ห้าม** เพิ่ม code ใหม่โดยไม่อัปเดตไฟล์นี้ก่อน — แก้ที่นี่ → migrate DB CHECK constraint → update code ทั้ง 2 ฝั่ง
