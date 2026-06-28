---
name: zb-handoff
description: >-
  Generate a session-boundary handoff prompt the operator pastes as the FIRST message of the
  next session (before /clear or a restart). Preserves the ephemeral in-flight thread that
  /clear destroys — work half-done now, unfinished plan steps, the operator's just-spoken
  asks — instead of dumping Kanban (Kanban survives /clear; the session thread does not).
  Use at the end of a working session, or when the operator says "hand off", "handoff",
  "wrap up the session", "prep for /clear", or "summarize for the next session".
argument-hint: "(no args — reads the bound project + this session's context)"
allowed-tools:
  - Bash(curl:*)
  - Bash(git:*)
  - Read
  - Write
---

# /zb-handoff — session-boundary handoff generator (continuity-first)

You are generating a **handoff prompt** the operator will paste as the FIRST message of
the next session (typically right before a `/clear` or restart).

**Core principle — what this skill is FOR:** a handoff exists to **preserve the ephemeral
session state that `/clear` destroys** — the work in-flight right now, the steps of the
current plan not yet finished, and anything the operator just asked for. The **Kanban
survives `/clear` anyway**, so it is the *fallback*, not the headline. A handoff that just
dumps open Kanban tasks has missed the point. The irreplaceable part is **your in-session
summary (Step 2)** — it cannot be re-derived from a query once the context is gone.

## Step 1 — resolve the bound project

Resolve the bound project id by running `powershell -File bin/lead-project-id.ps1` (THIS session's binding; exits non-zero if unbound → run `/zb-bind`, never the global `lead_project_id.txt`); use it as
`X-Project-Id` on every `/api/*` call. Missing/empty → run `/zb-bind <project>` first.

## Step 2 — summarize the in-session thread (THE CORE — judgment, not a query)

From THIS conversation's context, write down concisely the state a fresh session could not
otherwise know:

- **In-flight now:** what is half-done at this moment? (code written but not tested, a
  migration authored but not applied, a multi-file edit with 1 file left, a fix awaiting the
  operator's pytest, …)
- **Unfinished plan steps:** if the session was executing a plan / milestone slice, which
  steps remain? Keep the *thread* (e.g. "plan was A→B→C; A,B done, C left").
- **Deferred this session:** anything consciously parked (an `na` AC + its follow-up id, a
  "do later" note, a known-but-out-of-scope issue you found).

This is the **headline** of the handoff. If context genuinely has none, say so explicitly —
do NOT pad it with Kanban.

## Step 3 — ask the operator for additional asks

Ask, in one line: **"Anything you want the next session to do beyond finishing the current
thread?"** Capture the reply verbatim. These rank just under the in-session continuation.

## Step 4 — gather the LIVE durable anchors (verify, don't trust memory)

- **DONE — do not redo:** recently-closed tasks (`GET /api/tasks?process_status=5`, windowed,
  or the ids closed this session) + the pushed commit range (`git -C <repo> log --oneline -N`
  + last pushed hash). The next session must not re-do these.
- **READ FIRST:** the **top entry of `<shared>/decisions.md`** (newest locked decision — often
  the anti-re-litigation pointer) + any story / consolidated doc this thread touched. A fresh
  session re-reads these so it does not re-flag a settled decision.
- **In-progress / blocked** tasks (`process_status` 2 / 4) — cite only those tied to the thread.

## Step 5 — assemble PICK-UP-NEXT in PRIORITY ORDER

1. **In-session continuation** (Step 2) — finish the current thread first.
2. **Operator's handoff-time asks** (Step 3).
3. **Kanban fallback** — ONLY if 1 and 2 are both empty (nothing in-flight, plan complete, no
   new asks): surface the next actionable tasks (reuse `/zb-tasks-next`).

Never lead with Kanban when there is a live thread. The order is the whole point.

## Step 6 — emit + save

Print the handoff as a fenced code block (so it copies cleanly), with these sections:

```
Project: <name> (<team>, id=<id>). Continuing from the <date> session.

DONE — do NOT redo: <recently-closed ids + commit range>.

READ FIRST: <decisions.md top-entry summary + doc pointers> (do not re-flag settled decisions).

PICK UP NEXT:
1. <in-session continuation / unfinished plan>
2. <operator's additional asks>
3. <Kanban fallback — only if 1+2 empty>

CONTEXT FLAGS: <standing constraints this session relied on — e.g. pytest operator-run,
commit-no-push default, project-specific quirks>.
```

Then save the same content to `_scratch/handoff-<YYYY-MM-DD>.md` (a durable copy in case the
chat scrolls before the operator pastes it).

## Footgun guards (why each step exists)

| Step | What it prevents |
|---|---|
| 2 | Headline-omission: handing off a Kanban dump while the irreplaceable in-flight thread evaporates on `/clear`. |
| 3 | Losing the operator's just-spoken "also do X next time." |
| 4 | A fresh session re-doing closed work or re-litigating a locked decision (decisions.md is the guard — a stale review re-surfaces settled items otherwise). |
| 5 | Kanban-first ordering — Kanban survives `/clear`; the session thread does not. |

## Usage

```
# At the end of a working session, just before /clear or restart:
/zb-handoff
# → then paste the emitted code block as the first message of the new session.
```

## Loading note

A NEW skill dir under `.claude/skills/` is picked up only after a Claude Code **restart**
(Lead cannot write `.claude/` — it drafts in `_scratch/`, the operator moves it to
`.claude/skills/zb-handoff/SKILL.md`, then restarts). Because a restart also clears context,
`/zb-handoff` cannot hand off the very session in which it was first installed — use a manual
handoff for that one; the skill pays off from the next session's close onward.

## Related skills

- **zb-tasks-next** — the Kanban "what's next" source this skill falls back to (Step 5.3) when
  there is no live thread.
- **zb-bind** — resolves/persists the project binding this skill reads in Step 1.
- **zb-report** — the activity-rail checkpoint; a handoff is a read-only synthesis (not a rail
  event), but the closed-task ids it cites come from the rail / Kanban.
