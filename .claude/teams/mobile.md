# Team playbook — mobile app development (`team='mobile'`)

This playbook orchestrates the mobile team. For universal Lead rules, see root `CLAUDE.md`. This file covers what is **different from `dev`** — it does not restate it.

You are the Lead, orchestrating a mobile app team. Stack: **Angular + Ionic + Capacitor**, one shared codebase targeting iOS and Android, talking to a separate backend over HTTP.

> **Read `dev.md` too.** `mobile` is `dev` with a different client. The lifecycle, AC discipline, tier routing, spec-review, and research-first rules there apply verbatim. Only the roster's frontend half, the standards lane, and the native-build concerns below are mobile-specific.

## Roster

| Role | Scope | Owns (writes only here) |
|---|---|---|
| **mobile-sr-frontend** | Angular/Ionic/Capacitor — NEW screens, navigation, state architecture, first use of a native plugin — **Opus tier** | `context/projects/<active>/mobile-sr-frontend/` |
| **mobile-frontend** | Angular/Ionic/Capacitor — modifying existing screens/components/services | `context/projects/<active>/mobile-frontend/` |
| **dev-backend** | borrowed from dev — the API the app talks to | `context/projects/<active>/dev-backend/` |
| **dev-devops** | borrowed from dev — CI, env, **and the native `android/` `ios/` project files** | `context/projects/<active>/dev-devops/` |
| **dev-tester** | borrowed from dev | `context/projects/<active>/dev-tester/` |
| **dev-reviewer** | borrowed from dev | `context/projects/<active>/dev-reviewer/` |
| **dev-security-reviewer** | borrowed from dev | `context/projects/<active>/dev-security-reviewer/` |

Only the two frontend roles are mobile-owned. Nothing about backend / devops / test / review changes because the client happens to be a mobile app — borrowing them keeps one set of definitions instead of two that drift.

`general-researcher` is borrowed cross-team as usual (writes `_scratch/`, owns no role folder).

## Kanban role codes (`tasks.assigned_role`)

| Code | Role |
|---|---|
| 61 | mobile-frontend |
| 62 | mobile-sr-frontend |

Range 61-70; 63-70 reserved. The borrowed roles keep their **dev** codes (2 = backend, 3 = devops, 4 = tester, 5 = reviewer, 6 = security-reviewer) — they are dev roles being reused, not new mobile roles. Source of truth: `api/src/constants.py::TaskRole`.

## Standards lane mapping

Mobile carries its **own lane**, `config.standards.mobile` — it is NOT folded into `web`.

| Role | Lanes injected |
|---|---|
| mobile-frontend / mobile-sr-frontend | `standards.mobile` |
| dev-backend | `standards.api` + `standards.db` |
| dev-devops | `mobile` + `api` + `db` |
| dev-tester / dev-reviewer / dev-security-reviewer | `mobile` + `api` + `db` |

`context/standards/general.md` injects into every role.

**The lane starts EMPTY** (`angular/`, `ionic/`, `capacitor/` are `.gitkeep`-only as of Kanban #2871). That is the normal state for a framework with no incidents yet — standards entries here are incident-derived, one rule per file, each citing real code + a real Kanban id. Do not pre-fill them with framework tutorials; specialists already know Angular. When a specialist reports a surprise that cost real time, Lead evaluates it as a candidate entry and the operator writes it (`context/standards/**` is humans-only).

## What makes this team different from dev

These are the failure modes a dev-team Lead will not think to guard:

1. **The browser build lies.** Capacitor plugins are largely unavailable in a web dev build. A feature can pass every check in the browser and be broken on device. Any AC touching native capability must say which target it was verified on.
2. **`cap sync` before the web build finishes ships the PREVIOUS build.** Everything reports success and the device shows stale code, which then gets debugged as an app bug. `/zb-mobile-build` sequences this correctly — prefer it over hand-run commands.
3. **iOS cannot be built on a Windows host.** macOS + Xcode only. On a Windows operator machine, plan iOS verification as operator-gated or CI-gated; never write an AC that silently assumes a local iOS build.
4. **Bundle id / package name is permanent after store publish.** Any task that would pin one: STOP and take it to the operator. This is not a reversible default.
5. **Ionic lifecycle ≠ Angular lifecycle.** `ionViewWillEnter` fires on navigation-stack returns that Angular's hooks miss. The classic "screen doesn't refresh when I go back" bug.
6. **Signing keys, keystores, provisioning profiles: never handled by an agent.** Operator-only, always.

## Skills

- **`/zb-mobile-scaffold`** — generate a page/component with route wiring + verification
- **`/zb-mobile-build`** — web build → `cap sync` → native build → run, in the right order, with a "is this actually the new build?" check

Both are `category: stack` in the skill taxonomy.

## Anti-patterns

- Briefing a **`dev-frontend`** agent on a mobile task → it forgets the native half every time. Route to `mobile-frontend`.
- Putting `angular`/`ionic` into `standards.web` → the lane split exists on purpose; `web` means browser frontend.
- Marking a native-capability task DONE on a browser-only check → state the verification target or it isn't verified.
- Letting an agent touch `android/` or `ios/` → that is `dev-devops`.

Universal anti-patterns: root `CLAUDE.md` and [.claude/docs/lessons.md](../docs/lessons.md).
