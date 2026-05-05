# General standards (cross-framework)

> **Writer:** มนุษย์เท่านั้น Lead/subagent อ่านอย่างเดียว ห้าม propose update
>
> ไฟล์นี้รวบรวม convention ที่ **ไม่ขึ้นกับ framework** — git workflow, commit message, file naming, folder structure ระดับ root, ภาษาที่ใช้ใน comment ฯลฯ
>
> ทุกครั้งที่ Lead spawn subagent (role ใดก็ตาม project ใดก็ตาม) ไฟล์นี้จะถูก inject เข้าไปด้วยเสมอ

## Git

<!-- e.g.,
- Commit message: imperative mood, English, < 72 chars subject
- Branch naming: `<role>/<short-slug>` เช่น `frontend/user-avatar`
- ไม่ commit secret, ไม่ใช้ `git add -A`
-->

<!-- (rules ยังไม่ถูกเขียน — เติมที่นี่เมื่อพร้อม) -->

## File & folder naming

<!-- e.g.,
- File names: kebab-case (`user-avatar.tsx`, not `UserAvatar.tsx`)
- Test files: เพิ่ม suffix `.test.ts` / `.spec.ts` / `_test.py`
- Migration files: timestamped prefix `YYYY_MM_DD_HHMM_<slug>.py`
-->

<!-- (rules ยังไม่ถูกเขียน) -->

## Comments & docs

<!-- e.g.,
- Default: ไม่เขียน comment — ตั้งชื่อ identifier ให้อธิบายตัวเอง
- เขียน comment เฉพาะตอน WHY ไม่ obvious (workaround, hidden constraint, invariant)
- ห้ามอ้างถึง task / PR / ticket ใน comment (rot fast)
-->

<!-- (rules ยังไม่ถูกเขียน) -->

## Language for context files

<!-- e.g.,
- `context/projects/<p>/shared/*` เขียนภาษาอังกฤษ (เพราะเป็น contract ของทีม)
- `context/projects/<p>/<role>/*` เขียนภาษาไทยได้ (state ส่วนตัว)
- `context/standards/*` เขียนสองภาษาได้ตามดุลพินิจ
-->

<!-- (rules ยังไม่ถูกเขียน) -->

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
