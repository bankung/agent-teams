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
- Claude Code's Edit/Write already refuse a write when the file changed since this session read it (native;
  exact cross-session behaviour to be measured — Phase 0c).
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
