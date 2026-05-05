# Cross-project standards

ที่นี่เก็บ **convention / coding standard** ที่ใช้ร่วมกันได้หลาย project — แยก folder ตาม framework เพื่อให้ project ปลายทางเลือก load เฉพาะที่ตัวเองใช้

## ใครเขียน?

**มนุษย์เท่านั้น** — ไม่ใช่ Lead ไม่ใช่ subagent ห้าม AI เขียนหรือ propose update folder นี้โดยอัตโนมัติ เพราะการเปลี่ยน standard มี blast radius ข้าม project ทั้งหมดที่ใช้ framework นั้น ต้องให้คนเป็นคนพิจารณา

ถ้า subagent มี insight ที่ควรกลายเป็น standard — ให้ flag ใน final report Lead จะส่งต่อให้ user ตัดสินใจเอง

## โครงสร้าง

```
context/standards/
├── README.md         ← ไฟล์นี้
├── general.md        ← rule ที่ข้าม framework (commit msg, file naming, folder convention ฯลฯ)
├── nextjs/           ← Next.js conventions (App Router patterns, route handlers, server actions)
├── react/            ← React conventions (hooks rules, component patterns, state mgmt)
├── typescript/       ← TypeScript conventions (strict mode, type style, exhaustive checks)
├── tailwind/         ← Tailwind conventions (utility ordering, custom classes, theming)
├── fastapi/          ← FastAPI conventions (dependency injection, error handling, response models)
├── python/           ← Python conventions (type hints, naming, async patterns)
├── pydantic/         ← Pydantic conventions (model design, validators, serialization)
├── sqlalchemy/       ← SQLAlchemy conventions (declarative patterns, session mgmt, query style)
├── postgresql/       ← PostgreSQL conventions (naming, indexes, migration patterns)
└── docker/           ← Docker conventions (Dockerfile structure, multi-stage, base image policy)
```

## เพิ่ม framework ใหม่

1. สร้าง folder `context/standards/<framework>/`
2. เขียน rule ลงใน `.md` ไฟล์ใด ๆ ภายใน folder (ตั้งชื่อตามหัวข้อ — เช่น `naming.md`, `error-handling.md`)
3. เพิ่ม framework key ลงใน `projects.json` ของ project ที่ใช้:
   ```json
   "standards": { "web": ["nextjs", "<new-framework>"] }
   ```

## วิธีที่ Lead inject standards เข้า subagent

ตอน spawn subagent role X ของ project P Lead จะ:

1. อ่าน `projects.json` → ดู `projects[P].standards.<lane>` ที่เกี่ยวกับ role X
   - frontend → `standards.web`
   - backend → `standards.api`
   - devops → union ของ `standards.web + standards.api + standards.db`
   - qa → union ทั้งหมด
   - reviewer → union ทั้งหมด
2. รวมไฟล์ `.md` ใน `context/standards/<framework>/` ของแต่ละ framework key
3. รวม `context/standards/general.md` เสมอทุก role
4. Paste เข้าไปใน spawn prompt section "Standards"

ถ้าไฟล์ใหญ่เกิน — Lead เลือก paste เฉพาะ section ที่ relevant + บอก subagent ให้อ่านเต็มที่ path
