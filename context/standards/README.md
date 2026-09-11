# Cross-project standards

ที่นี่เก็บ **convention / coding standard** ที่ใช้ร่วมกันได้หลาย project — แยก folder ตาม framework เพื่อให้ project ปลายทางเลือก load เฉพาะที่ตัวเองใช้

## ใครเขียน?

**มนุษย์เท่านั้น** — ไม่ใช่ Lead ไม่ใช่ subagent ห้าม AI เขียนหรือ propose update folder นี้โดยอัตโนมัติ เพราะการเปลี่ยน standard มี blast radius ข้าม project ทั้งหมดที่ใช้ framework นั้น ต้องให้คนเป็นคนพิจารณา

ถ้า subagent มี insight ที่ควรกลายเป็น standard — ให้ flag ใน final report Lead จะส่งต่อให้ user ตัดสินใจเอง

## กฎมาจาก incident เท่านั้น

ทุกไฟล์ในโซนนี้เป็น **หนึ่งกฎต่อหนึ่งไฟล์ ที่ derive จากของที่เคยพังจริงในเรพนี้** ไม่ใช่คู่มือ framework — ความรู้ framework ทั่วไป agent มีอยู่แล้ว ไม่ต้องเขียนซ้ำ

ไฟล์หนึ่งต้องอ้าง **path จริง + เลข Kanban จริง + วันที่จริง** ได้ (ดู `nextjs/empty-body-responses.md` → `web/lib/api.ts:push.unsubscribe`, Kanban #955.C, 2026-05-20)

ตั้งชื่อไฟล์ให้แคบตามอาการ (`notfound-dev-vs-prod.md`, `redirect-after-mutation.md`) ไม่ใช่กว้างตาม framework (`nextjs-guide.md`)

**lane เปิดเปล่าไว้ก่อนได้** — `nextjs/` เปิดวันที่ 2026-05-04 แล้วกฎแรกมา 2026-05-11 · lane เปล่าคือสถานะปกติของ framework ที่ยังไม่มีโค้ดจริง ไม่ใช่งานค้าง

## โครงสร้าง

```
context/standards/
├── README.md         ← ไฟล์นี้
├── general.md        ← rule ที่ข้าม framework (commit msg, file naming, folder convention ฯลฯ)
├── nextjs/           ← Next.js conventions (App Router patterns, route handlers, server actions)
├── react/            ← React conventions (hooks rules, component patterns, state mgmt)
├── typescript/       ← TypeScript conventions (strict mode, type style, exhaustive checks)
├── tailwind/         ← Tailwind conventions (utility ordering, custom classes, theming)
├── angular/          ← Angular conventions (standalone components, signals, change detection, RxJS teardown)
├── ionic/            ← Ionic conventions (page lifecycle, navigation stack, platform-specific UI)
├── capacitor/        ← Capacitor conventions (plugin availability web vs native, permissions, native build)
├── fastapi/          ← FastAPI conventions (dependency injection, error handling, response models)
├── python/           ← Python conventions (type hints, naming, async patterns)
├── pydantic/         ← Pydantic conventions (model design, validators, serialization)
├── sqlalchemy/       ← SQLAlchemy conventions (declarative patterns, session mgmt, query style)
├── postgresql/       ← PostgreSQL conventions (naming, indexes, migration patterns)
└── docker/           ← Docker conventions (Dockerfile structure, multi-stage, base image policy)
```

## เพิ่ม framework ใหม่

1. สร้าง folder `context/standards/<framework>/` (ใส่ `.gitkeep` ถ้ายังไม่มีกฎ)
2. เพิ่มบรรทัดใน tree ด้านบน
3. เขียน rule ลงใน `.md` ไฟล์ใด ๆ ภายใน folder — **หนึ่งกฎต่อไฟล์** ตั้งชื่อตามอาการ (เช่น `error-handling.md`)
4. เพิ่ม framework key เข้า lane ของ project ที่ใช้ ผ่าน `config.standards` (JSONB บน `projects`) — แก้ผ่าน UI หน้า Edit Project หรือ `PATCH /api/projects/<id>`:
   ```json
   "standards": { "web": ["nextjs", "<new-framework>"] }
   ```
   > lane key ที่รองรับถูก define ไว้ที่ `api/src/schemas/project.py::_Standards` — **key ที่ไม่อยู่ในนั้นจะถูก drop เงียบ** (Pydantic `extra="ignore"`) ไม่ 422 ไม่เตือน · เพิ่ม lane key ใหม่ต้องแก้ `_Standards` และ `web/components/EditProjectModal.tsx` ด้วย

## วิธีที่ Lead inject standards เข้า subagent

ตอน spawn subagent role X ของ project P Lead จะ:

1. อ่าน `config.standards.<lane>` ของ project P ที่เกี่ยวกับ role X
   - frontend (web) → `standards.web`
   - **mobile → `standards.mobile`**
   - backend → `standards.api`
   - devops → union ของ `standards.web + standards.mobile + standards.api + standards.db`
   - qa → union ทั้งหมด
   - reviewer → union ทั้งหมด
2. รวมไฟล์ `.md` ใน `context/standards/<framework>/` ของแต่ละ framework key
3. รวม `context/standards/general.md` เสมอทุก role
4. Paste เข้าไปใน spawn prompt section "Standards"

ถ้าไฟล์ใหญ่เกิน — Lead เลือก paste เฉพาะ section ที่ relevant + บอก subagent ให้อ่านเต็มที่ path
