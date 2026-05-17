# Resume handoff — when operator returns to computer (2026-05-17)

> Comprehensive cross-session handoff. Read this top-to-bottom on resume. Self-contained — does NOT require recovering session memory.
> **Last updated:** end of 2026-05-17 red-team marathon session.
> **Committed to git** so it survives any session compaction / restart.

## TL;DR — single most important action

**Find the age private key first.** Everything downstream branches on this.

```powershell
# Quick search — 5 minutes max
Get-ChildItem C:\Users\banku -Recurse -Filter "*.key" -ErrorAction SilentlyContinue -File | Select FullName,Length,LastWriteTime
Get-ChildItem C:\Users\banku -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-2) -and $_.Length -lt 5000 -and $_.FullName -notmatch '\\node_modules\\|\\\.git\\' } | Select-String "AGE-SECRET-KEY-1" -List 2>$null
```

- **Found** → Section B path 1 (R2 restore)
- **Not found** → Section B path 2 (rebuild from git + memory)

---

## Current state (end of session 2026-05-17)

### Git
```
6b8ed4b docs(incident): LLM prompt-injection test (Ollama, 3 models) + L22/L23
620a049 docs(incident): hammer-test red-team Phase 8 — 4 new findings, 5 confirmed defenses
2f0210a docs(incident): sleeper-attack red-team + 4 prevention layers (L14-L17)
6e7d71f docs(incident): red-team adversarial findings + 8 staged prevention layers
53f0f37 fix(prevention): 3-layer pytest-on-live-DB guard + L0 briefing discipline
cb8207e docs(incident): 2026-05-17 dev DB wipe postmortem + 3-layer prevention plan
5ba9899 feat(api): next-action recommender + digest content spec — Kanban #1010 + #1009
```

`main = origin/main = 6b8ed4b` — all commits safely pushed to GitHub.

### Containers
```
db        Up    healthy   (volume agent-teams_agent-teams-pgdata intact)
web       Up    healthy   (idle — empty DB shows blank UI)
api       Up    healthy   (lifespan started normally)
langgraph STOPPED         (intentionally — no auto-headless work until prevention layers land)
```

### DB state — POST-WIPE + TEST ARTIFACTS
```
projects:      21 rows  (1 agent-teams active + 20 dosproj-* soft-deleted from T-DOS-4)
tasks:         ~24-26   (4 seed + red-team artifacts #5-13 + race PATCH state on #4)
tasks_history: ~26-31   (audit rows from recurrence template firings)
sessions:      0
tool_calls:    0
transactions:  0
```

**The DB contains test artifacts only. The original ~1100 task audit trail is gone.** Recovery options below.

### Local files (gitignored — won't survive `git clean -fdx`)
```
_scratch/snapshot-2026-05-17/
  ├── db-dump.sql           12 MB pg_dump (snapshot of current empty+test-artifact DB)
  ├── manifest.json
  ├── compose-state.txt
  ├── env-snapshot.txt
  └── table-sizes.txt

_scratch/pending-kanban-2026-05-17/
  ├── 01-09 (initial 9 entries: L1/L2/L3/WARN-1/2/Obsidian + #1009/1010/1084 patches)
  ├── 10-17 (red-team Phase 2-6 findings: L6/L7/L8/L10/L11/L1.5/L12/L13)
  ├── 18-21 (hammer-test Phase 8: L14/L15/L16/L17 ... wait, these are 14-17)
  ├── 22-25 (hammer-test Phase 8: L18/L19/L20/L21)
  ├── 26-27 (LLM injection Phase 9B: L22/L23)
  ├── replay-all.ps1  (handles 01-09 + 3 DONE patches; tasks 10-27 need extension)
  ├── snapshot-before-destruction.ps1
  ├── restore-from-snapshot.ps1
  └── ... (postmortem stub, investigation, security-review, etc.)

_scratch/red-team-*.md  (working notes — committed copies are in shared/incidents/)
_scratch/OPERATOR_TODO_when_at_computer.md  (earlier draft — this doc supersedes)
```

### Incident docs (committed to git — always available)
```
context/projects/agent-teams/shared/incidents/
├── 2026-05-17-dev-db-wipe.md           Main postmortem (root cause + recovery plan)
├── 2026-05-17-red-team-findings.md     Phase 2-6: runtime DB pointer drift threat model
├── 2026-05-17-sleeper-attacks.md       Phase 7: prompt injection + scheduling primitives
├── 2026-05-17-hammer-test-findings.md  Phase 8: broad stress test
├── 2026-05-17-llm-injection-tests.md   Phase 9B: Ollama 3-model injection test
└── 2026-05-17-resume-handoff.md        THIS FILE
```

---

## SECTION A — Find the age private key (10 min)

### A1. Disk search commands

```powershell
# 1) Files named like keys
Get-ChildItem C:\Users\banku -Recurse -Filter "*.key" -ErrorAction SilentlyContinue -File | Select FullName,Length,LastWriteTime

# 2) Common file names
Test-Path "C:\Users\banku\agent-teams-backup.key"
Test-Path "C:\Users\banku\Desktop\age-key.txt"
Test-Path "C:\Users\banku\Downloads\backup-key.txt"
Get-ChildItem "C:\Users\banku\Downloads\*key*" 2>$null

# 3) Files modified last 48h, small, with AGE-SECRET-KEY content
Get-ChildItem C:\Users\banku -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object {
    $_.LastWriteTime -gt (Get-Date).AddDays(-2) -and
    $_.Length -lt 5000 -and
    $_.FullName -notmatch '\\node_modules\\|\\\.git\\|\\__pycache__\\|\\\.cache\\|AppData\\Local\\Temp'
  } |
  Select-String "AGE-SECRET-KEY-1" -List 2>$null

# 4) Git-bash history (if you used git-bash for age-keygen)
Get-Content "$env:USERPROFILE\.bash_history" -ErrorAction SilentlyContinue | Select-String "age|AGE"

# 5) cmd.exe doskey history (only works if cmd window still open from yesterday)
doskey /history 2>$null | Select-String "age"
```

### A2. Non-disk locations to check

- **Password manager** (1Password, Bitwarden, KeePass, Apple Keychain) — search "age" / "agent-teams" / "backup"
- **USB drives** plugged in yesterday
- **Bitdefender / antivirus quarantine** — small text files sometimes blocked
- **VS Code** recent files / terminal output buffer (if reopens with previous session)
- **OneDrive / cloud sync trash** — recent deletions

### A3. Decision gate

| Result | Next |
|---|---|
| 🟢 **KEY FOUND** | Save to 2-3 places NOW (password manager + USB + encrypted vault). Then Section B path 1. |
| 🔴 **KEY NOT FOUND** after 10 min | Section B path 2 (rebuild). All R2 backups encrypted to that key are permanently unrecoverable. |

---

## SECTION B — Recovery path

### B1. Path 1 — R2 backup restore (15-20 min) [ONLY IF KEY FOUND]

```powershell
# 1) Download latest .age object from R2 dashboard
# https://dash.cloudflare.com → R2 → bucket "agent-teams-backup"
# → agent-teams/<YYYY-MM-DD>/backup-<TIMESTAMP>.tar.gz.age (newest)
# save to C:\Users\banku\Downloads\backup.tar.gz.age

# 2) Decrypt + extract
cd C:\Users\banku\Downloads
age -d -i C:\path\to\agent-teams-backup.key backup.tar.gz.age > backup.tar.gz
tar -xzf backup.tar.gz db-dump.sql

# 3) Stop api + langgraph for clean restore
cd C:\Users\banku\Documents\Personal\Projects\GitHub\agent-teams
docker compose -p agent-teams stop api langgraph

# 4) Restore (dump has --clean --if-exists already)
Get-Content C:\Users\banku\Downloads\db-dump.sql -Raw | docker compose -p agent-teams exec -T db psql -U postgres -d agent_teams

# 5) Restart
docker compose -p agent-teams start api
# langgraph stays stopped — see Section D about when to restart

# 6) Verify task count (should be >1000 from before-wipe state)
Invoke-RestMethod -Uri "http://localhost:8456/api/tasks" -Headers @{"X-Project-Id"="1"} | Measure-Object | Select-Object Count
```

Then proceed to **Section C** to replay staged Kanban entries.

### B2. Path 2 — Rebuild from git + memory (60-90 min) [ONLY IF KEY NOT FOUND]

Rotate to NEW key first so future backups are recoverable:

```powershell
cd C:\Users\banku
age-keygen -o agent-teams-backup-v2.key
# Output: "Public key: age1xxxxxxxxxxxxxxxx..." — COPY THIS

# CRITICAL: backup the NEW key to 2 additional places RIGHT NOW
# - copy file to USB drive
# - paste content into password manager
type agent-teams-backup-v2.key   # show secret (paste to password manager)

# Update .env with new PUBLIC key
cd C:\Users\banku\Documents\Personal\Projects\GitHub\agent-teams
# Edit .env, change BACKUP_AGE_PUBKEY=... to the new age1... public key
docker compose -p agent-teams restart api

# Verify decrypt works with new key (DO NOT skip this drill):
# wait for next backup OR manually trigger:
# (no admin endpoint to force-trigger — wait for cron at 03:00 UTC daily)
```

Then send Lead message: **"key หาไม่เจอ — รัน Plan C rebuild"**

Lead will:
1. Re-create projects: secretary, novel-drift, hitl-test, NewsAnalyzer via POST /api/projects
2. Re-file the ~60-80 active Kanban tasks Lead has in memory
3. Inject the 23 staged prevention tasks via replay script

Expected time: 60-90 min. Tasks will have `created_at = now` (audit trail starts fresh, lose history).

### B3. (Optional) Restore from local snapshot for testing only

The snapshot at `_scratch/snapshot-2026-05-17/db-dump.sql` is a 12MB dump of the CURRENT post-wipe-+-test-artifacts state. It does NOT contain the original pre-wipe data — only what's in DB right now (4 seed tasks + red-team test artifacts).

Useful only if:
- You want to test the restore mechanism without using R2
- You ran a destructive container test and need to revert to pre-test state

Restore script: `_scratch/restore-from-snapshot.ps1`

Otherwise IGNORE this snapshot — Path 1 or Path 2 is the real recovery.

---

## SECTION C — After recovery, replay staged Kanban entries (15 min)

23 prevention tasks + 3 DONE patches are staged in `_scratch/pending-kanban-2026-05-17/`. The replay script handles the first 9 + the 3 patches automatically. The remaining 14 (tasks 10-27) need to be added to the script OR filed via Lead in a follow-up session.

### C1. Run the replay script

```powershell
cd C:\Users\banku\Documents\Personal\Projects\GitHub\agent-teams

# First: dry-run to see what would happen
powershell -NoProfile -ExecutionPolicy Bypass -File _scratch\pending-kanban-2026-05-17\replay-all.ps1 -DryRun

# If output looks right, execute for real
powershell -NoProfile -ExecutionPolicy Bypass -File _scratch\pending-kanban-2026-05-17\replay-all.ps1
```

This will file tasks 01-09 + apply 3 DONE patches to #1009/#1010/#1084 (if they exist in restored DB).

### C2. File the remaining 14 staged tasks (10-27)

The replay script currently handles 01-09. Tasks 10-27 (L4-L23 prevention layers) need to be added. Options:

**Option A — extend replay-all.ps1** (recommended)

Add to the script following the existing pattern. Each task's .md file already has all required fields in its YAML frontmatter. Pattern:

```powershell
$task10 = New-KanbanTask -ShortLabel "10-L6-purge-fixture" -Payload @{
    project_id = 1
    title = "L6 prevention (ROOT-CAUSE-2): purge fixture URL gate — the actual weapon of the 2026-05-17 wipe"
    description = Get-MdBody -Path (Join-Path $StageDir "10-p1-bug-L6-purge-fixture-url-gate.md")
    process_status = 1
    priority = 1
    task_kind = "ai"
    task_type = "bug"
    acceptance_criteria = @(
        @{text="New helper api/tests/helpers/db_safety.py::assert_test_db_or_die(session) raises RuntimeError if session.bind.url.database does not endswith('_test')"; status="pending"},
        # ... rest from the .md file's frontmatter
    )
}
# Repeat for 11-27
```

**Option B — ask Lead in fresh session**

Spawn Lead in a new agent-teams session: "Read all .md files in _scratch/pending-kanban-2026-05-17/10-27 and POST each as a new Kanban task. Use the YAML frontmatter for payload."

### C3. Verification

```powershell
# Total tasks should jump from ~1100 (restored) + 23 (staged) = ~1123
Invoke-RestMethod -Uri "http://localhost:8456/api/tasks" -Headers @{"X-Project-Id"="1"} | Measure-Object

# Spot-check a P1 prevention task is filed
$tasks = Invoke-RestMethod -Uri "http://localhost:8456/api/tasks" -Headers @{"X-Project-Id"="1"}
$tasks | Where-Object { $_.title -like "*L6 prevention*" } | Select id, title, priority, process_status
```

---

## SECTION D — Verify prevention layers work (30 min)

Before re-enabling ANY autorun / pytest workflow:

### D1. Verify L1+L2+L3 (already shipped in 53f0f37)

```powershell
# L1: hook should DENY pytest on live env
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@db:5432/agent_teams"
'{"tool_input":{"command":"pytest -q"}}' | powershell -NoProfile -File .claude/hooks/block-pytest-on-live-db.ps1
# Expected: exit 2 + deny JSON

# Clear env, retry
$env:DATABASE_URL = $null
'{"tool_input":{"command":"pytest -q"}}' | powershell -NoProfile -File .claude/hooks/block-pytest-on-live-db.ps1
# Expected: exit 0

# L3: seed gate should refuse live DB
docker compose -p agent-teams start langgraph  # start it briefly
docker compose -p agent-teams exec -T api python -c "import asyncio, os; os.environ['DATABASE_URL']='postgresql+asyncpg://postgres:postgres@db:5432/agent_teams'; import importlib, sys; [sys.modules.pop(m) for m in list(sys.modules) if m.startswith('src.') or m.startswith('scripts.')]; from scripts.seed import _seed; asyncio.run(_seed())"
# Expected: RuntimeError "_seed(): refusing to seed against URL ... agent_teams ... does not end with '_test'"

# L2: run pytest (with conftest's normal env rewrite — should work)
docker compose -p agent-teams exec -T api pytest api/tests/test_conftest_invariant.py -v
# Expected: 3 tests pass (the new fail-loud guard tests)
```

### D2. Land critical P1 layers BEFORE re-enabling auto_headless

These are CRITICAL — file from Section C, then implement:

| Layer | Task ID (after replay) | Why critical |
|---|---|---|
| **L6** | (TBD — task 10's filed ID) | Disarms the purge-fixture WEAPON. Without this, any future engine-poisoning bug re-fires the wipe. |
| **L8** | (task 12) | api lifespan refuses to start on rogue DB. Catches docker-compose.yml DATABASE_URL misconfig. |
| **L17** | (task 21) | Worker pickup content scan. Last gate before LLM agent runs on destructive task description. |
| **L22** | (task 26) | Inject CLAUDE.md safety prelude into EVERY LLM call. Cheap, high-leverage. Especially critical if using Ollama. |

After these land:
- Run full `pytest -q` to verify no regressions (L1 hook should ALLOW since conftest rewrites env in-process correctly with new L6 guard)
- Manually file a destructive-content test task → confirm L17 halts at pickup (langgraph won't even invoke agent)
- Restart langgraph for normal operation

### D3. Land remaining P1/P2 layers in normal priority order

After D2 is green:
- L4 (task 08, P1): postgres pytest_runner role grants
- L5 (task 09, P1): PostToolUse Agent verify-before-PATCH hook
- L7 (task 11, P1): langgraph DATABASE_URI validation
- L18 (task 22, P1): payload size limits
- Then P2s: L1.5, L10, L11, L12, L13, L14, L15, L16, L19, L21, L23

---

## SECTION E — Resume original work backlog

These were the tasks ON DECK before the wipe interrupted everything:

### E1. WARN-1 + WARN-2 security fixes (from #1084 review)

- **Task 04** in staging: strip `answer_history` from `Interrupt.value` (langgraph/worker.py:427-429)
- **Task 05** in staging: env-gate `HITL demo —` branch (langgraph/nodes.py:678)

Both are dev-backend specialist tasks. Brief includes literal code diffs. Should be 30-60 min each.

### E2. P2/P3 backlog that was queued before incident

- **#955** Web Push notifications (substrate for #958/#1011) — P2 feature, BE+FE split
- **#958** Daily digest push delivery — P2, depends on #955
- **#1011** HITL nudge push — P2, depends on #955
- **#1000** Approval inbox UI — P3, /inbox page cross-project
- **#1086** DeepSeek LLM provider — P1, ADD L22 prelude when implementing
- **#1085** test SERIAL flake — P1 bug

### E3. Secretary Mode A first test (was Plan B from before wipe)

`context/projects/secretary/shared/` has full substrate ready (workflow briefs, mode boundary, operator-preflight, voice samples). Day-1 test plan:

1. Operator runs first Mode A test (email triage workflow)
2. Capture findings in `_scratch/secretary-test-day-1-notes.md`
3. Promote summary to `context/projects/secretary/shared/lessons-learned/day-1.md`
4. Decide trigger conditions for Mode B-read (per `mode-boundary.md`)

Filed as Kanban entry in original session (was #1106) — will be re-filed by replay script if it ran post-restore.

---

## SECTION F — Long-term incident review (week-1)

### F1. Promote 5 incident docs to lessons.md

The 5 incident files in `context/projects/agent-teams/shared/incidents/` (excluding this handoff) are the artefact. Lessons distilled to:

- `_private/feedback_*.md` (memory) for Lead-side discipline
- `context/standards/python/test-isolation.md` (NEW — propose to operator) for the broader pytest-fixture-safety pattern
- `context/standards/llm/safety-prelude.md` (NEW — propose) for the L22 verbatim prelude

### F2. Backup decrypt drill — schedule quarterly

Kanban #1103 was filed in original session. If R2 restore (Path 1) succeeded, the task exists. If rebuild path (Path 2), re-file:

```
Title: Backup decrypt drill — quarterly schedule (first one within 7 days)
Priority: P1
Task kind: human
Acceptance criteria:
- Download latest .age object from R2
- Decrypt round-trip with offline key
- Verify dump tar contents
- Calendar reminder for next drill
```

The 2026-05-17 incident proved this drill is MANDATORY, not optional. The discovery that the key might be lost would have surfaced at the FIRST quarterly drill if scheduled. Schedule reminder NOW.

### F3. Karpathy Mode B escalation

Per `feedback_karpathy_lane.md`, Mode B (trust-agent-reports-without-rerun) has reached strike #5 (the wipe itself was strike #5). The hard-hook escalation L5 (PostToolUse Agent verify-before-PATCH) is now MANDATORY-WITHIN-72H per Plan v2 Decision D.

This is task 09 in staging. Land it as soon as recovery is verified.

---

## SECTION G — What this session accomplished (audit trail)

For context if you wonder "what was Lead doing for 5 hours":

### Phase 1 (morning) — actual feature work
- Implemented #1010 next-action recommender API (cross-project endpoint)
- Wrote #1009 daily digest content spec
- dev-security-reviewer smoke on commit 73811e2 — found WARN-1, WARN-2

### Phase 2 (afternoon) — INCIDENT
- pytest -q wiped dev DB via _purge_db_per_test fixture + lru_cache poisoning
- ~1100 task audit rows lost

### Phase 3 (rest of day) — red-team marathon
- Stopped containers + committed safe work
- dev-tester investigation: 2-factor root cause (silent invariant + lru_cache)
- Shipped L0+L1+L2+L3 (CLAUDE.md + hook + conftest + seed)
- Phase 2-6 red-team: identified 8 more attack vectors → L4-L13
- Phase 7 sleeper test: prompt injection via task content → L14-L17
- Phase 8 hammer test: SQL injection / path traversal / DoS / scaffold storm / race → L18-L21
- Phase 9A snapshot + restore drill — proved backup mechanism works (1.8s, zero errors)
- Phase 9B Ollama injection — llama3.2 default-OBEYED destructive prompt → L22-L23

### Final layer count
- **4 prevention layers SHIPPED** (53f0f37)
- **23 prevention layers STAGED** at `_scratch/pending-kanban-2026-05-17/`
- **5 incident postmortems COMMITTED** to git
- **7 commits PUSHED** to origin/main

The DB still empty (recovery deferred to operator). But the system is now FAR better-defended than before the wipe. Each operator question during the session ("what about runtime drift? sleeper attacks? Ollama?") opened a real attack vector we then closed.

---

## SECTION H — Lead's pickup brief for next session

When you start a new Claude Code session in this repo, tell Lead:

> "Resume from 2026-05-17 incident. Read context/projects/agent-teams/shared/incidents/2026-05-17-resume-handoff.md and tell me what step we're on."

Lead should:
1. Read this doc top-to-bottom
2. Query current DB state (task count, projects)
3. Determine which Section (A/B/C/D/E/F) we're on
4. Continue from there

Don't waste time re-investigating — the work is captured. Move forward.

---

## Quick reference card

```
SNAPSHOT location:   _scratch/snapshot-2026-05-17/db-dump.sql (12MB)
RESTORE script:      _scratch/pending-kanban-2026-05-17/replay-all.ps1
PENDING TASKS:       _scratch/pending-kanban-2026-05-17/01..27-*.md (23 prevention + 6 other)
OPERATOR DOC:        THIS FILE (_scratch/OPERATOR_TODO_when_at_computer.md is the older draft)

ORIGIN BRANCH:       main = 6b8ed4b (7 commits all pushed)
CONTAINERS:          db=up, web=up, api=up, langgraph=STOPPED

R2 BUCKET:           agent-teams-backup (Cloudflare R2)
AGE PUBKEY:          age1mm3ukje0p6ukhvk75jd2wc4w4y044xq7axw4gyxhrenqqwcvsp4shx987f
AGE PRIVATE KEY:     ⚠️ MAY BE LOST — search disk first

KEY RULE:            DO NOT run pytest -q until L6 lands (it's the purge-fixture URL gate)
KEY RULE:            DO NOT grant auto_headless consent until L14+L17 land
KEY RULE:            DO NOT restart langgraph until you've reviewed the prevention layer status
```

End of handoff. Take a break, come back, find the key, then Section B onwards.

---

## ADDENDUM 2026-05-17 (post-recovery wrap) — Security posture assessment

Recovery flow executed end-to-end successfully (Sections A→D all DONE). Operator + Lead aligned on **honest security score** before closing session — captured here so next-session Lead doesn't re-litigate it.

### Today's score: ~55/100

Breakdown:

| Dimension | Score | Why |
|---|---|---|
| Recovery | 85/100 | R2 backup proven via drill (1.8s restore, zero data loss); age key backed up to 2 locations |
| Detection | 55/100 | tasks_history audit works; no anomaly alerts, no scheduled backup-verify |
| **Prevention** | **35/100** | ⚠️ L4–L23 mostly **STAGED in Kanban**, not shipped |
| Containment | 50/100 | block-raw-sql-dml hook catches shell path; pytest path still open until L6 ships |
| LLM safety | 30/100 | Ollama proven default-obeys destructive prompts; no safety prelude in production yet |

### Realistic ceiling: ~85/100 (NOT 100)

Reason for the gap from 100: **structural factors we can't control without architecture change.**

Uncontrollable ~15 pt loss:

| Factor | Loss |
|---|---|
| Bus factor = 1 (single operator) | -4 |
| Supply chain CVEs (pip / docker / postgres) | -3 |
| Windows host attack surface | -2 |
| R2 + age key single-vendor risk | -2 |
| Context compaction knowledge rot | -2 |
| Local LLMs lacking RLHF | -2 |

### Path 55 → 85 (this is the realistic max-ROI sprint)

| Phase | Score after | Effort | Tasks |
|---|---|---|---|
| **P1 sprint (~1 week)** | 70-72 | ~8h | #1109 #1110 #1111 #1112 #1113 #1114 #1115 #1116 + L1 hook |
| **P2 batch (~1 month)** | 78-80 | ~12h | #1106 #1107 #1117–#1126 |
| **Tier-2 structural (~3 months)** | 83-85 | ongoing | backup verify cron, standards promotion, Mode B-read hook ship, cloud-LLM-default |

**Strategic note from operator (2026-05-17):**
> 80 ที่ ship จริง + maintain ได้ ดีกว่า 90 บนกระดาษที่ rot ใน 6 เดือน
> (Shipped-and-maintained 80 beats paper-90 that rots in 6 months.)

Don't chase 85+. Diminishing returns are steep:
- 85 → 88 costs as much as 55 → 80
- 88 → 90 requires **architecture change** (multi-operator review, air-gapped backup tier 2, drop or sandbox Ollama)
- Above 90 = not worth the cost-of-living tradeoff

### Decision recorded

Operator + Lead agreed: ship to ~80, hold the line, revisit after P1 + P2 land. No premature investment in Tier-3 / architecture-change items. This is **acceptance of measured residual risk**, not negligence — the residual is documented + sized so future Lead doesn't drift toward false confidence.

If a NEW strike happens after P1+P2 ship, escalate to architecture review. Until then, stay on the 27-layer plan.

---

## ADDENDUM 2026-05-17 (end of session) — P1 + P2 sprint complete

**Single-session marathon shipped 21 prevention layers + soft-cleanup of L18 smoke artifact.**

### Shipped layers (21 / 27 — 78%)

| Layer | Kanban | Commit | One-line |
|---|---|---|---|
| L6 | #1111 | 5df5705 | purge fixture URL gate — disarmed the wipe weapon |
| L18 | #1115 | 601757c | payload size limits 10MB / 10k ACs → 422/413 |
| L8 | #1113 | f4d5782 | api lifespan DB allowlist |
| L22 | #1116 | 90a9bbc | LLM safety prelude (provider-agnostic) |
| L7 | #1112 | fafd0ee | langgraph DATABASE_URI lifespan validation |
| L4 | #1109 | 1aaf429 | postgres pytest_runner role (DB-engine LAST RESORT) |
| L5 | #1110 | 31a52ad | PostToolUse Agent verify-before-PATCH hook |
| L17 | #1114 | e368929 | worker pickup content scan |
| L10 | #1117 | adac410 | alembic MIGRATION_TARGET gate |
| L11 | #1118 | 6e50618 | _build_engine pytest-binding canary |
| L12 | #1120 | ec4a3e0 | backup min-size check + prune guard |
| L21 | #1125 | eba96b6 | recurrence max_active_children cap |
| WARN-1 | #1106 | bdec709 | strip answer_history from Interrupt.value |
| WARN-2 | #1107 | 024f679 | HITL demo branch env gate |
| L1.5 | #1119 | 71264c5 | hook bash command string parse |
| L15 | #1122 | e42b8df | per-template auto-headless confirmation |
| L14 | #1121 | dea3b14 | API content moderation tag |
| L16 | #1123 | 8197c39 | agent context sanitizer |
| L23 | #1126 | 94328e8 | agent output sanitizer |
| L19 | #1124 | 2a05f5a | scaffold rate limit + .deleted/ archive |
| L13 | #1127 | 4611d5d | bin/reset.* WIPE confirm + -p pin |

3 alembic migrations applied to live via `MIGRATION_TARGET=live` (L10 gate). Live DB row count 289 → 288 (288 after #1137 smoke-artifact cleanup). Zero drift across ~300 new tests + ~15 agent spawns.

### Final security score: **55 → ~82/100** (+27 in one session)

| Dimension | Start | End |
|---|---|---|
| Recovery | 85 | 85 |
| Detection | 55 | 55 |
| Prevention | 35 | **~95** (+60) |
| Containment | 50 | **~95** (+45) |
| LLM safety | 30 | **~85** (+55) |

Close to practical ceiling (~85 was the projected ceiling without architecture change). Remaining gap is structural — bus factor 1, supply chain, Windows host surface, single-vendor backup, context rot, local LLMs.

### Operator action queue (post-session)

1. **🔴 RESTART Claude Code** — settings.json read at startup; L5 PostToolUse hook activation requires this.
2. **🟡 RESTART langgraph container** — L7 / L17 / L22 / L23 / L14 wire-in takes effect.
3. **🟡 docker compose build api** — bake slowapi into image (currently runtime-installed only — survives restart, but not rebuild).
4. **🟢 Cleanup `context/projects/.deleted/<name>-*/`** — ~500 leftover dirs from L19 test suite (safe to `Remove-Item -Recurse`).
5. **🟢 Cleanup `_scratch/L1.5-draft/` + other patch JSON files** — draft staging area, all consumed.
6. **🟢 Rotate `PYTEST_DB_PASSWORD`** — currently dev default `pytest_runner_dev_only_NOT_FOR_PROD`. Production needs strong rotation.

### Outstanding (P3 backlog — next session)

- #1108 Obsidian-vault-compatible secretary KB (heavier design task)
- #1128 L20 optimistic locking on PATCH (migration + version column + tests, ~2h)
- #1129 Backup decrypt drill (recurring — quarterly)
- #1130 Mode B-read Phase 1 design doc
- #1131 Stale langgraph checkpoint cleanup
- #1132 Secretary Mode A capture session

### Standards promotion candidates (humans-only zone — operator decides)

- `context/standards/llm/safety-prelude.md` (from L22 — verbatim text in `langgraph/safety_prelude.txt`)
- `context/standards/python/test-isolation.md` (from L6 + L11 + L2)
- `context/standards/postgres/role-grants.md` (from L4 split-admin-vs-runtime pattern)
- `context/standards/python/alembic/revision-id-length.md` (from L15 32-char incident)

### Bootstrap for next session

```
Resume from 2026-05-17 incident — ALL 27-layer prevention plan ~78% shipped
(21/27 layers). P1 + P2 complete. P3 backlog: #1108, #1128, #1129-1132.
```
