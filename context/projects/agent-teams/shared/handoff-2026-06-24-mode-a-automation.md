# Handoff — 2026-06-24 session: start 0.8 with theme #5 (Mode A automation)

> Paste the **Paste-ready prompt** block as the first message of a fresh `claude` session.
> The **Reference** section below is context for that session (no need to paste it).

## Paste-ready prompt

We're working on **agent-teams** (team=dev, id=1) — bind the session to it.

Today's goal: **start 0.8 theme #5 — Mode A automation: auto pick-up of ready tasks + HITL over Telegram / MS Teams.** Do #5 first: once it works we can dogfood it to drive the rest of 0.8 more hands-off, and it gets tested in the process.

Before writing any code:
1. Read memory `project_v0_8_0_theme.md` for the full 0.8 direction (7 themes). #5 is the headline new surface — the biggest design lift.
2. Research-first: spawn a researcher to scope (a) Telegram Bot API send + receive (webhook vs long-poll) and (b) MS Teams notify (incoming webhook) + approve-reply (bot / Power Automate), and how an "approve / reject" reply maps back to releasing a gated task action.
3. Open the **0.8 milestone** (if not present) and a task for #5 with acceptance criteria. Likely sub-scope:
   - **notify channel** — push task lifecycle events (spawn / blocked / needs-approval / done) to Telegram + Teams;
   - **approve channel** — operator replies approve / reject from chat to release a gated action;
   - **auto pick-up** — the Lead selects the next ready TODO and starts it within an autonomy boundary you define.
4. Get my sign-off on the design — especially the **autonomy boundary** (what runs without asking) and **which actions stay HITL-gated** — before building the integration.

Guardrails: keep code / commits / docs in neutral product vocabulary (the pre-push scan is active); auto pick-up must respect existing gates — no `--dangerously-skip-permissions`, secretary email stays secretary-only, DB writes via the API. A Telegram bot token + an MS Teams webhook are new secrets → .env / Infisical, never hardcoded.

## Reference for the new session (not part of the paste)

- **Mode A** = the interactive Lead loop (CLAUDE.md + `.claude/teams/dev.md`). "Auto pick-up" automates the *"which task next + start it"* step the operator does by hand today.
- **HITL / notify precedents already in the repo:**
  - `.claude/hooks/notify-session-waiting.ps1` — session-waiting notify (30s cache); the closest existing notify hook.
  - Secretary email-action gate — operator-token approval pattern (a gated action that needs human proof); the model for the approve channel.
  - Kanban statuses `BLOCKED` / needs-approval + the activity rail — the events worth notifying on.
- **Secrets:** memory `project_infisical_secrets_direction` — `.env` daily, Infisical on-demand; add the bot token / webhook there.
- **Also queued for 0.8 (do NOT start unless directed):**
  - **#2558** — Artifacts / Files page (theme #7) — backlog task already open.
  - Theme **#6** (activity-analysis skills) — being scoped in a separate session.
- **Context hygiene:** fresh session — re-bootstrap (resolve the project, read the team playbook + shared hot-set); don't carry assumptions from the 0.7 work.

---
*Source of direction: memory `project_v0_8_0_theme.md` (7 themes, pre-shared 2026-06-23). Written at the close of the v0.7.1 session.*
