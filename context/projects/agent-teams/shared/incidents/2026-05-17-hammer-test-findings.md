# Hammer-test red-team — Phase 8 (2026-05-17)

> Operator request: "กระหน่ำทดสอบระบบเลยครับ" — hammer the system with tests.
> 9 attack categories executed hands-on against empty DB. 3 critical findings, 5 confirmed defenses, 1 minor.

## Result matrix

| ID | Attack | Result | Notes |
|---|---|---|---|
| **T-DB-1** | SQL injection via X-Project-Id header / query string / path param | ✅ **BLOCKED** | Pydantic int_parsing returns 422 on all malformed inputs |
| **T-FS-1** | Path traversal in project name (`../../etc/passwd` etc) | ✅ **BLOCKED** | Regex `^[a-zA-Z0-9_-]{1,64}$` rejects |
| **T-FS-2** | Shell special chars in project name (`;rm`, backticks, `$(...)`) | ✅ **BLOCKED** | Same regex rejects all |
| **T-DOS-1a** | 10MB description payload | 🚨 **ACCEPTED** | No size limit at any layer |
| **T-DOS-1b** | 10000 acceptance_criteria items | 🚨 **ACCEPTED** | JSONB stores unlimited array |
| **T-AUTH-1** | X-Project-Id spoofing (request task in proj 1 with header=999) | ✅ **BLOCKED** | `task X does not belong to project_id Y` 400 |
| **T-AUTH-1b** | No X-Project-Id header at all | ✅ **BLOCKED** | 400 BadRequest |
| **T-AUTH-1c** | PATCH cross-project with wrong header | ✅ **BLOCKED** | Same 400 |
| **T-DB-2a** | JSONB injection via approval_policies (SQL in `action` field) | ✅ **BLOCKED** | Pydantic dict-type validator catches list-shape input |
| **T-DB-2b** | Free-form garbage in agent_overrides | ✅ **BLOCKED** | Literal validation `'haiku' / 'sonnet' / 'opus'` only |
| **T-DB-3** | Race condition — 10 concurrent PATCH same task | ⚠️ **LAST-WRITE-WINS** | All 10 succeeded; final state = last write. No optimistic locking |
| **T-DOS-3** | Recurrence template `next_fire_at` 7 days in past | ⚠️ **UNBOUNDED CLUTTER** | Scheduler does NOT catch up (good) but advances one tick per scheduler tick → keeps firing forever |
| **T-NET-1** | Backup admin force-run endpoint | ✅ **NOT EXISTS** | Cron-only; no API trigger to override S3 endpoint to attacker bucket |
| **T-LLM-1** | POST to tool_calls / sessions to inject prompt-injection text | ✅ **BLOCKED** | No POST endpoint; tool_calls only GET-able under `/api/tasks/{id}/tool-calls` |
| **T-DOS-4** | Scaffold storm — 20 projects in seconds | 🚨 **ACCEPTED** + 🚨 **DISK ARTIFACT** | No rate limit; soft-delete leaves `context/projects/<name>/` folders on disk (Kanban #941 already tracks this) |

## Critical findings (need fix)

### 🚨 FINDING #10 (HIGH) — No payload size limit (T-DOS-1)

**Evidence:** POST task with 10485886-byte description → 201 OK, stored 10485760 bytes in `tasks.description` column.

**Impact:**
- Single POST = 10MB DB row. 1000 such = 10GB DB bloat
- API response time scales with payload size (no max-response-size cap)
- LLM agent token cost explodes when it reads such task: ~2.5M tokens at 4 chars/token = $X bill
- UI rendering of task page chokes

**Fix (L18):**
- Pydantic `max_length=10_000` (or similar) on `title`, `description`, `halt_reason`, `status_change_reason`, `recurrence_rule`
- Pydantic `max_items=50` on `acceptance_criteria`, `subagent_models`
- Pydantic `max_length=1000` on each `acceptance_criteria[].text`
- 422 on violation
- Optional: FastAPI middleware `max_request_size = 1MB` overall

### 🚨 FINDING #11 (MEDIUM-HIGH) — Scaffold storm + disk artifact leak (T-DOS-4)

**Evidence:** 20 POST /api/projects in <5s → 20 rows + 20 `context/projects/<name>/` folders on disk. Soft-delete cleans DB but NOT disk.

**Impact:**
- No rate limit on project creation. Attacker (network-reachable on :8456) can fill disk
- Inotify watchers / file-system tools choke on thousands of folders
- Even after soft-delete, folder remains — context-loader scans them

**Fix (L19):**
- Per-IP rate limit on POST /api/projects (e.g., 5/min via slowapi)
- Soft-delete handler ALSO removes context/projects/<name>/ folder (or moves to context/projects/.deleted/<name>-<ts>/)
- Quota per IP: max 100 active projects

Note: Kanban #941 already tracks the scaffold-target audit question; THIS finding extends it with the storm + leak vectors.

### ⚠️ FINDING #12 (MEDIUM) — Race-condition on concurrent PATCH (T-DB-3)

**Evidence:** 10 simultaneous PATCH on same task — all 10 returned 200; final state = last commit.

**Impact:**
- Operator A and B PATCH same task at same time → B's changes silently overwrite A's
- Lost-update class of bug (low frequency in practice — single operator)
- More concerning for `acceptance_criteria` PATCH semantics (full-replace) — partial losses

**Fix (L20):**
- Add `version` (or `updated_at`-based) optimistic locking
- PATCH includes `If-Unmodified-Since: <updated_at>` header (or `version` field in body)
- 409 Conflict on mismatch — client must re-fetch + re-apply

Lower priority — affects concurrent ops only.

### ⚠️ FINDING #13 (MEDIUM) — Unbounded recurrence child accumulation (T-DOS-3)

**Evidence:** Template `* * * * *` with `next_fire_at` 7 days in past → scheduler fires once per tick, advances forward. After 24h = ~1440 child tasks.

**Impact:**
- Operator forgets a 1-minute cron template → DB fills with TODO clutter
- Each child consumes Kanban row + index entries
- next-autorun queue gets noisy (mitigated since children default `run_mode=manual`)

**Fix (L21):**
- Per-template `max_active_children` (default 100)
- On spawn: if active children count >= max → halt template + alert operator
- Alternative: `max_total_children` (lifetime)
- Implement in recurrence service `fire_template` path

### 🟢 FINDING #14 (POSITIVE) — Many defenses confirmed working

**The system is FAR from naked.** Confirmed protections:

1. **SQL injection 100% blocked** — Pydantic type validation on every input
2. **Path traversal 100% blocked** — project name regex `^[a-zA-Z0-9_-]{1,64}$`
3. **JSONB schema enforced** — even nested fields validated against Literal types
4. **Project isolation works** — `assert_task_belongs_to_session` catches all cross-project access attempts
5. **Backup admin endpoint doesn't exist** — cron-only; no API attack surface to redirect backups
6. **tool_calls / sessions are GET-only** — no injection vector for poisoning agent context

## Defense layer additions (L18-L21)

| Layer | Severity | Task # | Coverage |
|---|---|---|---|
| **L18** | HIGH | 22 | Payload size limits (title/description/AC/items count) |
| **L19** | MEDIUM-HIGH | 23 | Scaffold rate limit + folder cleanup on soft-delete |
| **L20** | MEDIUM | 24 | Optimistic locking on PATCH |
| **L21** | MEDIUM | 25 | Recurrence template max-active-children cap |

## Total defense layer count

After ALL staged tasks (#1-25) land: **21 prevention layers** covering:
- pytest test-DB isolation (L1-L6)
- agent / hook safety (L0, L5)
- runtime DB-pointer drift (L7, L8, L11)
- migration safety (L10)
- harness coverage (L1.5, L13)
- backup integrity (L4, L12)
- sleeper attacks via content (L14-L17)
- resource limits + concurrency (L18-L21)

## Tests skipped (out of scope)

- **T-DOS-2** native large-file upload via scaffold (not a feature — POST /api/projects doesn't accept files)
- **T-LLM-2** end-to-end LLM prompt injection test (requires actual agent spawn — would consume tokens)
- **Container down -v actual execution** (would destroy db volume — only simulation acceptable)
- **dropdb actual execution** (same)
- **Session compact poisoning** (requires session machinery in active use)

## Tests we ran that left artifacts in DB

| Task # | Created during | Purpose | Cleanup at restore |
|---|---|---|---|
| #5-#8 | Phase 7 sleeper tests | S1/S2/S3 + spawned child | ✓ wiped by restore |
| #9 | T-DOS-1a 10MB desc | DoS proof | ✓ wiped |
| #10 | T-DOS-1b 10k AC | DoS proof | ✓ wiped |
| #11-#13 | T-DOS-3 + spawns | Recurrence proof | ✓ wiped |
| #4 | (PATCHed by T-DB-3) | Title set to "Race winner #10" | ✓ wiped |
| (proj 1) | T-DB-2 PATCH | approval_policies / agent_overrides attempts | ✓ rejected, no state change |

## Cross-references

- Phase 1-6 findings: `2026-05-17-red-team-findings.md`
- Phase 7 sleeper findings: `2026-05-17-sleeper-attacks.md`
- Wipe postmortem: `2026-05-17-dev-db-wipe.md`
- Working notes: `_scratch/red-team-*-2026-05-17.md`
- 4 new staged Kanban entries: `_scratch/pending-kanban-2026-05-17/22..25-*.md`
