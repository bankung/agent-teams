---
purpose: improvement plan from the Fable 5.1 review of every zb-* skill and .claude/hooks file (#3483)
status: PROPOSED 2026-10-08 — nothing applied; every item edits .claude/** and needs the operator's `doit`
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

## Operator decision needed — H1 (gate default)
Today the Bash gate is a deny-list: anything no guard denies is auto-allowed, which is why the walker and
this Lead run without prompts (`git push` and `rm -rf` included). Options:
- **A. Keep deny-list, document it** (CLAUDE.md:80 stops claiming an allowlist model); harden the deny guards instead. Zero prompt change.
- **B. Flip to the allowlist model** (no decision on no-match). Every un-allowlisted command prompts — measure first: a one-day "would-prompt" log, grow the allowlist, then flip.
- **C. Middle**: no-match → `ask` only for a small destructive class (`rm -r`, `git push --force`, `git reset --hard`, writes outside the repo), allow the rest.
Lead recommendation: **C** — keeps unattended walker throughput, puts a human on the irreversible class.
