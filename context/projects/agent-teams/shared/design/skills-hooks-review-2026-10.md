---
purpose: improvement plan from the Fable 5.1 review of every zb-* skill and .claude/hooks file (#3483)
status: PROPOSED 2026-10-08, revised the same day against 70 days of transcript audit — nothing applied; every item edits .claude/** and needs the operator's `doit`
owner: Lead
---

# Skills + hooks review — improvement plan (#3483)

Two read-only Fable 5.1 reviewers (raw reports, local only: `_scratch/review-3483-skills.md`,
`_scratch/review-3483-hooks.md`). Lead re-verified every HIGH item below against the code on
2026-10-08 (synthetic payloads piped into the hook from files, or a direct read); evidence column says which.

## Verified HIGH findings

| # | Where | Finding | Lead evidence |
|---|---|---|---|
| H1 | hooks/pretooluse-bash-gate.ps1:543 (+ approval-policies-gate.ps1:61,67,107) | No guard matched → emits `permissionDecision: "allow"`, which skips the settings allowlist and every prompt (#3327). The allowlist / `permissions.ask` model in CLAUDE.md is not what actually runs for Lead Bash. | payload `rm -rf /tmp/nothing-here` → allow; read L536-543 |
| H2 | settings.json:283 | Matcher `mcp__Claude_in_Chrome__.*` matches no real tool (`mcp__claude-in-chrome__*`, `mcp__Claude_Browser__*`) → the browser approval-policies gate and the #1585 secretary email backstop never fire. | read matcher; tool names in this session |
| H3 | hooks/tester-curl-allow.ps1:27-43, project-auditor-readonly.ps1:123-133, researcher-firecrawl-allow.ps1:9 | Per-agent allow hooks check only the first word (+ a localhost substring) → `curl localhost ; rm -rf`, `firecrawl … ; rm -rf` are auto-allowed for those agents. | read L27-43 |
| H5 | hooks/sem-spend-cap-gate.ps1:175, data-query-perf-gate.ps1:146 | Emit `requires-attention`, not a valid permissionDecision → the intended prompt never happens; their pass paths emit `allow`. | grep |
| M3 | hooks/pretooluse-bash-gate.ps1 GUARD 5 (today's c0d20d1) | Still bypassable: `docker compose -f … exec api pytest`, `docker compose --project-directory . exec …`, `docker-compose exec/run api pytest` (hyphen binary), `python3 -c "…pytest…"` → allow. | 5 payloads → allow |
| S1 | skills/zb-test/SKILL.md | `metadata.category: testing` not in the validator set → CI `skills — frontmatter validator` red (run 37733796976). | CI log |
| S2 | skills/zb-email/SKILL.md:78-80 | "There is no send endpoint" is false (routers/tools_email.py has reply/forward/send-internal/external-send); the never-send rule must rest on the real gate, not a false premise. | read |
| S3 | skills/zb-walker/SKILL.md:150-159 | Still says in-session pytest is blocked and batches AC `na` for an operator run — stale since /zb-test (#3480). | read |
| S4 | skills/zb-git-commit:52, zb-release:42, .git/hooks/pre-push:5 | Point at `_scratch/.lifecycle-mapping.md`, which does not exist. | `ls` → no such file |

## Proposed tasks (value per operator-hour; Fable's estimates)

1. **[security] hook allow-bypass fixes** (~2.5 h) — H3 + H5 + M3 now (reuse the gate's shell-meta / foreign-URL checks via `_shared.ps1`; `requires-attention` → `ask`; silent pass paths; GUARD 5 any-flag exec/run + `docker-compose` + `python3`), each with smoke rows.
   **H1 needs an operator decision first** (see below), not a silent flip.
2. **[skills] drift fixes** (~1 h) — S1-S4 + `zb-skill-new` still says `ii` + `zb-task-create` declares a nonexistent `Task` tool + `zb-git-commit` Step 6 → `/zb-report` + CATALOG regen with a CI diff + `zb-release` gh full path / CI-green gate.
3. **[hooks] browser gate revive (H2)** (~30 min) — fix the matcher ONLY after silencing the two hooks' pass-path `allow`, or every browser action becomes auto-approved.
4. **[hooks] leanness + perf + logs** (~5 h) — delete 4 unwired `block-*` mirrors, auto-approve-safe-writes pair, six `.sh` twins (~1,350 LOC never executed); one decision-envelope + project-id helper in `_shared.ps1`; merge the two Agent PreToolUse hooks + cache the spawn check (~0.6 s/spawn); move context-bloat-guard to SessionEnd (~0.6 s/Write|Edit); rotate `_runtime/usage_capture.log` (9.1 MB); log gate deny/ask; bound-state smoke table for the gate.
5. **[skills] new paved paths** (~2 h) — `zb-git-push` (fetch/ahead check, push, `gh run watch --exit-status`, rail), `zb-decision` (decisions.md + index line), `zb-cleanup` (`_scratch` has ~3.7k files).

## Audit-based revision (2026-10-08, same task) — supersedes the task list above
Data: 70 active days of transcripts (729 files, 25,335 Bash/PowerShell calls) extracted deterministically
(`_scratch/analysis-3483/extract.py`, `digest.py`), the 290 rows needing judgement labelled by Haiku 5.5,
then re-planned and spot-checked by Fable 5.1 (`_scratch/analysis-3483/fable-replan.md`, local only).
- 19 REAL-destructive ops, 18 ran unprompted (gate default-allow): e.g. `docker volume rm` of two Postgres
  volumes (09-04, 09-25 — Lead-verified rows), `git reset --hard`, `git checkout --`, `rm -r`, `curl -X DELETE`
  on projects 590/598/661/734 (Lead-verified: operator-approved soft-deletes, #3455/#3349). 0 live SQL DML ran.
- Hook denials 94: 19 true, 60 false (41 = the researcher stdin bug, fixed 9bdb694), 15 not hooks.
  Live-DB pytest guard held against 6 `BYPASS_LIVE_DB_PYTEST_HOOK=1` attempts by subagents.
- Option B would prompt 116/active day and still miss 7 of 18 (settings.json:204-205 allow `git checkout *`,
  `git restore *`). Fable's simulation of a quote-aware per-segment destructive class (option C): 61 asks in
  70 days (0.87/active day, 0 on 43 days), 18/18 REAL ops caught, 0 text false positives.
- Per-agent allow hooks did not cause the 11 risky chains (they rode the default-allow); sem/seo/data gates saw
  0 calls in 70 days; browser tools 4,387 calls with 0 incidents; hook time ≈ 90 s/day (perf not worth a task).
- Skill use (Skill tool + operator-typed): zb-bind 85, zb-report 48, zb-handoff 50, zb-git-commit 25; zb-walker
  is the most-Read skill (69 Reads) — its stale test section matters most.

**Revised task list (proposed, in order):**
1. **Gate: ask on a destructive class + per-segment, quote-aware matching** — Option C; the same engine closes the
   GUARD 2/3/5 gaps (incl. today's M3: `-f`, `docker-compose`, `--project-directory`, `python3 -c`).
2. **Gate decision log + `usage_capture.log` rotation** — the data the audit could not see (allow vs approved-ask).
3. **Skills drift** — zb-walker test section → /zb-test; zb-git-commit/zb-release dead mapping ref; zb-email send
   premise; `ii`/`Task` nits; zb-git-commit Step 6 → /zb-report.
4. **Per-agent hooks + dead code** — fall-through instead of `allow` on pass paths, `requires-attention` → `ask`,
   delete ~1,350 never-executed LOC (mirrors, `.sh` twins, auto-approve pair).
5. **Secretary browser backstop** — move to the secretary agents' frontmatter with a silent pass path; drop the
   global `mcp__Claude_in_Chrome__.*` row.
6. **zb-git-push** (39 pushes + 29 CI watches in 70 days) + fold `zb-task-attach` into `zb-task-update`; cleanup
   folds into /zb-handoff.

## Operator decision needed — H1 (gate default)
Today the Bash gate is a deny-list: anything no guard denies is auto-allowed, which is why the walker and
this Lead run without prompts (`git push` and `rm -rf` included). Options:
- **A. Keep deny-list, document it** (CLAUDE.md:80 stops claiming an allowlist model); harden the deny guards instead. Zero prompt change.
- **B. Flip to the allowlist model** (no decision on no-match). Every un-allowlisted command prompts — measure first: a one-day "would-prompt" log, grow the allowlist, then flip.
- **C. Middle**: no-match → `ask` only for a small destructive class (`rm -r`, `git push --force`, `git reset --hard`, writes outside the repo), allow the rest.
Lead recommendation: **C** — keeps unattended walker throughput, puts a human on the irreversible class.
