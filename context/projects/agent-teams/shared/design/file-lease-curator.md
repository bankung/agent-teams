---
purpose: design for preventing silent cross-session overwrites in the shared working tree (file-lease curator)
status: Phase 0 + Phase 1 opened as tasks 2026-09-25; Phase 2 is design-only, gated
owner: Lead
---

# File-lease curator — concurrent sessions in one working tree

## Problem
Several Claude Code sessions (one per project or thread) run from the SAME main repo directory on branch
`dev` (worktree sessions do not fire hooks, #2355). Nothing stops two of them writing the same file, and the
git index is shared. Observed 2026-09-25: `_scratch/tn_report_payload.json` written by a MorAI session and an
agent-teams session (fixed filename used by every zb-* skill); `memory/MEMORY.md` edited concurrently. No
source-file clobber observed yet.

## Operator design points (2026-09-25) — every one must survive into Phase 2
- P1 a curator: an agent asks "is anyone using this file?" before editing.
- P2 unlock when committed.
- P3 tell the agent whether it must refresh before a new edit.
- P4 on conflict the requester releases its locks and pends its work; Lead/walker reports "my work is locked".
- P5 the pended task records a dependency so it is picked up when the dependency finishes.
- P6 on yield, commit the WIP separately — do not revert code.
- P7 who yields: score = priority x (wait_count + 1); tie -> older task (aging, starvation-free).
- P8 resume through a verifiable, cross-session trigger the walker can see — a lock cannot wake a session.

## Verified facts (Lead + Fable spec review, 2026-09-25)
- Claude Code's native stale-read check is narrower than "refuses any write after an outside change" — measured
  in Phase 0c (#3348, below): Write refuses, Edit mostly warns, a never-Read file is unprotected.
- The auto-run picker treats a `blocked_by` pointing at a DONE/CANCELLED task as unblocked for ANY blocker kind
  (`routers/tasks.py:689`); `auto_unblock_dependents` (question/decision only) just clears the column.
- `blocked_by` is same-project only (`routers/tasks.py:2227-2230`, 422) and single-valued (#771), and
  `design/async-hitl-gates.md` locks "leave blocked_by as task<->task, don't overload it". The observed
  collisions were cross-project -> Phase 2 needs its own wait record (D2).
- `/zb-git-commit` Step 4 is a plain `git commit -m` -> commits the whole shared index, including paths
  another session staged (Phase 0b).
- A PreToolUse hook emitting `permissionDecision: "allow"` BYPASSES the permission check (#3327); a lease hook's
  no-conflict path must emit nothing or `.claude/**` / `shared/**` writes stop prompting.
- There is no PreToolUse `Write|Edit` hook today; each new hook process costs ~539 ms (PowerShell start, #3327).
- The walker parks human-gated tasks at `process_status=8`; a lock-wait is a different, auto-resumable park.

## Phases
- **Phase 0 (independent, cheap):** 0a per-session scratch filenames in zb-* skills (#3347) · 0b pathspec commits
  in `/zb-git-commit` (#3346, HIGH) · 0c measure the native stale-read check across sessions (#3348) · 0d live
  probe that a work-kind blocker going DONE surfaces its dependent in `next-autorun` (#3349).
- **Phase 1 (evidence, log-only — #3350, blocked_by #3348):** per-session write log `_runtime/file-writes_<sid>.jsonl` from a PostToolUse
  hook (+ Bash/PowerShell write detection inside the EXISTING single gate), offline overlap report. No table,
  no PreToolUse, no added latency, no allow-bypass risk.
- **Phase 2 (enforcement — gated on Phase-1 evidence + operator go):** lease table + endpoints; declare-all-files
  at task start and acquire together (prevents deadlock); PreToolUse check whose no-conflict path emits nothing;
  release on commit of the path (a sweeping commit by another session must NOT release); yield = pathspec WIP
  commit (compile-clean) -> release -> wait record -> walker drains; victim by P7; resume re-reads files.

## Phase 0c result — native stale-read check (#3348, 2026-10-07, Claude Code 2.1.179)
Two live sessions on `_scratch/fl0c/*.txt`; verbatim tool text per cell in `_scratch/fl0c/protocol.md`.
"Other" = a write the editing session did not make with its own Edit/Write tool.

| Cell | Editing session's call | Result |
|---|---|---|
| Control: Read → Edit, no outside change | Edit | allowed |
| Other session's Write replaced the target line | Edit | **refused** ("File has been modified since read ... Read it again") |
| Other session's Bash `printf >>` (target line intact) | Edit | **warned, applied** — note "modified on disk since you last read it — the edit applied cleanly"; other line kept |
| Read, then other session's Bash `>>` | Write | **refused** (same stale error) |
| Existing file never Read this session (own-Bash-created and other-session-created) | Write | **silently allowed** — other session's content lost |
| Own subagent's Bash `>>` after Read | Edit | **warned, applied** (same note) |
| `touch` only (mtime changed, same content) | Edit | silently allowed (content-aware, no false positive) |
| `/compact` after Read, no change | Edit, no re-Read | allowed |
| `/compact` after Read, other session rewrote the line | Edit, no re-Read | no stale error; "String to replace not found" — compact re-attached the current disk copy |
| Stale refusal, then fresh re-Read | Edit / Write | allowed |

Reading (inference from the c1 vs c2 pair, not measured directly): Edit's stale check only bites when its
`old_string` no longer matches; otherwise it applies to the current disk content with a warning, so it does
not lose the other writer's lines. Write is the lossy path, and is guarded only once the file was Read.

**Write-idiom inventory vs the native check** (`.claude/skills/**`, `.claude/hooks/**`; detection regexes and
`*.smoke.ps1` fixtures excluded):

| Idiom | Where | Native check |
|---|---|---|
| Write tool, `_scratch/*_<sid>.json` payloads | zb-report, zb-task-create/-update/-done/-attach, zb-milestone-create | covered only if Read first — usually never-Read → uncovered; #3347 per-session names remove the collision |
| Write tool, drafts (`_scratch/<name>/SKILL.md`, memory-compact proposal, handoff) | zb-skill-new, zb-memory-compact, zb-handoff | same — uncovered when the target was never Read |
| Write/Edit on a shared tracker | zb-jobs `log` verb | Edit: warned/refused per above; Write: covered after Read |
| Bash `curl -o _scratch/*_resp_<sid>.json` | most zb-* skills | uncovered (per-session names) |
| Bash `printf '<id>' > _runtime/lead_project_id*.txt` | zb-bind | uncovered (per-session file + one-way global by design) |
| `git commit --only` / `git checkout`, `merge`, `tag` | zb-git-commit, zb-release | uncovered (index / tree-wide — D4) |
| `ng generate` | zb-mobile-scaffold | uncovered |
| `Copy-Item` promote `_scratch` → `.claude/skills` | zb-skill-new (operator-run) | uncovered |
| PowerShell `Add-Content` / `[IO.File]::WriteAllText` / `New-Item` / `Remove-Item` | hooks: parser.ps1, _shared.ps1, notify-session-waiting, posttooluse-bash-hygiene, data-dashboard-publish, sem-performance-dashboard, seo-ranking-report | uncovered (hook processes, not tool calls) |
| Bash `echo/printf >> $LOG` | hooks: data-dashboard-publish.sh, sem-performance-dashboard.sh, seo-ranking-report.sh (never wired; deleted #3489 — the wired .ps1 twins append via Add-Content) | n/a |

Consequences for Phase 1/2: (1) Phase 1 needs Bash/PowerShell write detection — every non-tool idiom above is
outside the native check; (2) the real native gap for tool writes is **Write on a never-Read existing file**,
not stale Edits, so a Phase-2 check should key on "Write without a prior Read this session"; a content hash is
not needed for Edit (it applies to current disk content); (3) `/compact` silently refreshes read state, so a
lease must not rely on the harness's read state surviving compaction. Side finding: `zb-bind` Step 2's
rationale ("the Write tool ... can't overwrite the always-present global without first Reading it") is
wrong on 2.1.179 — the printf rule still stands for the gate-allow reason.

**Re-measured on Claude Code 2.1.293 (#3476, 2026-10-08, single session, own Bash = outside change):** target line
replaced → Edit refused (same stale error) · append → Edit applied with the on-disk-modified note · Read + append →
Write refused · existing file never Read → Write silently allowed · touch only → Edit allowed. Identical to 2.1.179;
the never-Read Write gap is still open. (/compact cell not re-run.)

## Open operator decisions (Phase 2 only)
- D1 WIP push rule — all sessions share `dev`, so pushing any task pushes another task's WIP commit.
- D2 wait record — `blocked_by` cannot cross projects; option: a lease-side wait record, task stays TODO and
  resumes when the lease frees (keeps P8's verifiable + cross-session property).
- D3 hook behaviour when the API is down (no-decision vs ask).
- D4 tree-wide git ops (`git stash/restore/checkout`, auto-allowed today) while another session holds leases.
- D5 preemption yes/no — with all-at-start acquisition only the requester waits, so P7 orders a queue unless
  holders can be preempted.
- D6 lease TTL number + idle-vs-dead semantics.
- Phase-2 gate: evidence threshold + measurement window (operator numbers).
