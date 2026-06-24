# Design — Mode A automation: the autonomy boundary (0.8 theme #5)

> **Status:** operator sign-off **2026-06-24**. Lead-authored. Companion to `async-hitl-gates.md`
> (the HITL *mechanism* — `task_gates` + Telegram) — this doc fixes the *policy*: what the
> Mode-A continuous runner does on its own vs. what it must ask a human.
>
> **Live tasks:** Task A `#2564` (gate model + resolve + unified read) → Task B `#2565` (Telegram
> notify + poller + tier policy) → Task C `#2566` (runner resume) → runner `#2531` (ZommmBeeean)
> consumes C. All under milestone **v0.8.0 (#50)**.

## 1. What this governs

The Mode-A continuous runner (`#2531`) automates the *"which task next + start it"* step the operator
does by hand today: pick the next actionable task → run the normal Lead lifecycle → pick the next →
stop on operator / stuck / empty. This doc defines the **autonomy boundary** of that loop — signed off
before any integration code was written.

**Non-negotiable framing:** the runner is a normal Mode-A Lead session. **Per-action permission
prompts stay ON; no `--dangerously-skip-permissions`, ever.** The automation is in task *selection +
sequencing*, not in bypassing any existing gate. On top of the unchanged per-action model sit the four
rings below.

## 2. Decisions locked (2026-06-24)

| Fork | Decision | Why |
|---|---|---|
| **Channel** | **Telegram only** for v0.8.0 (MS Teams revisited at v0.9.0) | Telegram `getUpdates` long-poll is the only purely-outbound path — no inbound port, drops Tailscale. Every Teams approve path needs an inbound public HTTPS endpoint; O365 webhook connectors retire May 18–22 2026. Evidence: `_scratch/research-telegram-teams-hitl.md`. |
| **Commit tier** | Runner **auto-commits locally; only `push` is gated** (informed-approval) | Local commit is reversible and frequent; the leak boundary is `push`, guarded by the existing pre-push keyword scan. Matches the operator's commit-no-push batch rule. Resolves the `async-hitl-gates.md` §10 open item ("does push get its own tier"). |
| **Notify events** | Push on **gate-opened / blocked / done / runner-stopped-or-empty** only | Skips routine task-start + subagent-spawn to avoid phone spam while keeping decision points + milestones visible. |

## 3. The four rings

- **Ring 1 — Auto (no operator contact).** Select the next actionable task in scope (picker order:
  milestone → blocker → priority, via `next-autorun`); run the full lifecycle — spawn specialist
  subagents, edit via subagents under the existing permission gates, run tests, scoped verification,
  write `decisions.md` + the activity rail, verify every AC, flip DONE; **commit locally** (reversible);
  advance to the next task.
- **Ring 2 — Notify, don't block.** Fire a Telegram message on the events in §2 (gate-opened, blocked,
  done, runner stopped/empty), then keep going. Fire-and-continue; no wait.
- **Ring 3 — HITL-gate (halt `ps=8`, open a `task_gate`, async-wait on Telegram).**
  - a **decision** the runner cannot self-resolve (ambiguous/under-specified/conflicting requirement,
    or an AC it cannot verify) — simple approve/reject buttons;
  - anything explicitly flagged **needs-approval / `requires_human_review`** — simple buttons;
  - **git push** — *informed-approval*: the card must carry **diff-stat + pre-push keyword-scan result
    + test result** before an approve action is offered. The scan stays the real leak guard; the tap is not.
- **Ring 4 — Never auto, never chat-approvable (terminal + operator only).**
  - **key / secret provisioning** (incl. the Telegram bot token + Infisical) — terminal only;
  - **external** outward-facing third-party actions not otherwise covered;
  - **secretary email** — stays secretary-only; the runner never sends mail (the email-action gate backstops);
  - **`.claude/**` self-modification** — requires the operator's literal `ii` in an interactive session;
  - **raw DB writes** — API only, never `psql`/ad-hoc ORM;
  - **budget / cost-cap breach** — hard halt.

**One-liner:** the runner drains tasks hands-off, pings on progress, stops to the phone only for genuine
decisions and for pushing — and never for the Ring-4 tiers.

## 4. Consent + permission posture

`run_mode=auto_pickup` (Mode A2) does **not** require `auto_run_consent_at` (that gate is for
`auto_headless` / Mode B). The runner's "consent" is the operator starting the runner session plus the
session's per-action permission mode (the operator may run it in an accept-edits mode for a hands-off
drain; the runner itself never escalates to skip-permissions). The Ring-3/Ring-4 gates fire regardless
of permission mode.

## 5. Secrets

Telegram **bot token** + **operator chat_id** are new secrets → `.env` (daily) / Infisical (on-demand),
read at call time, never hardcoded. Absent token ⇒ notify soft-fails (`ok=False`, no exception), mirroring
`notify_ntfy.send_push`. See memory `project_infisical_secrets_direction`.

## 6. Relationship to other records

- `async-hitl-gates.md` — the gate *mechanism* (`task_gates` table, resolve flow, Telegram poller, coexistence with the legacy ntfy/web flow). This doc is the *policy* layered on top.
- `#2531` — the runner; its AC3 (clean stuck exit) + AC4 (compaction resilience) are realized by Task C.
- ntfy + Tailscale stay in place during v0.8.0 (coexist); removal is evaluated at v0.9.0 once Telegram is proven and Tailscale's non-HITL uses (remote board/API) are confirmed gone.
