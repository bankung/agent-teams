# Next.js — Feature wire-level attestation

**Scope:** how a frontend agent (`dev-frontend`) reports a feature as "shipped" before handoff. The rule applies to any FE slice that adds user-visible markup, attributes, or behavior — not just Next.js — but the canonical incident is a `next dev` HMR symptom, so the standard lives here.

## Rule

A `dev-frontend` handoff attestation MUST include at least one **wire-level marker grep** against the running container's actual rendered HTML — not only `tsc --noEmit` + healthcheck + non-2xx-free curl.

Concretely, before reporting a feature complete, the agent runs:

1. `curl -s http://localhost:<port>/<route>` to capture the rendered HTML from the running container.
2. `grep` (or `grep -oE`) for **at least one feature-specific marker that ONLY exists post-feature** — a new `data-*` attribute, an `aria-label`, a `class` substring, a new container `id`, etc.
3. Assert `≥1 match` (or the expected count for multi-marker features). Include the raw match line(s) in the handoff report.

If the feature has no visible markup change (e.g., a pure refactor or a typing-only narrowing), say so explicitly in the report: *"feature has no wire-observable markup; attestation limited to tsc + healthcheck."*

## Why

`tsc --noEmit` checks **source-file** type correctness. `docker compose ps web` checks **container liveness**. `curl http://.../<route>` checks **HTTP 200**. None of these check that the source-file edits **actually shipped to the rendered output**.

`next dev` HMR has been observed to silently stall — compiled chunks under `/app/.next/static/chunks/` lagging the source mtime by hours despite tsc clean, healthcheck green, and curl 200. The first dev-tester probe pass after a stale HMR returns 0 markers; without a wire-level grep at handoff time, the broken state escapes into the review/test cycle (one full lifecycle round-trip wasted).

Cost of the extra grep: ~2 seconds + 3 lines in the report. Cost of skipping it: a full subagent retry once dev-tester catches the gap.

## Verification recipe

For a new component / picker / badge:

```sh
curl -s http://localhost:5431/p/agent-teams > /tmp/probe.html
grep -oE 'data-theme-picker|aria-label="theme [a-z]+"' /tmp/probe.html | sort -u
# Expect ≥4 unique markers (1 container + 3 button labels).
```

For a dark-mode pass:

```sh
grep -c 'class="[^"]*dark:' /tmp/probe.html
# Expect a non-zero count; report the actual number.
```

For an `inline <script>` (e.g., FOUC bootstrap) that must appear in `<head>`:

```sh
grep -n 'classList.add(.dark.)' /tmp/probe.html
grep -n '__next_f.push' /tmp/probe.html
# The first match line MUST be smaller than the second (script appears before
# the React hydration push).
```

## Failure recovery

If the markers are missing despite source-file edits being correct:

1. Compare source mtime vs compiled-chunk mtime:
   ```sh
   docker compose exec -T web sh -c "stat -c '%y %n' /app/app/layout.tsx /app/.next/static/chunks/app/layout.js"
   ```
   A compiled chunk older than the source is the symptom.
2. `docker compose restart web` forces a clean recompile.
3. Re-run the marker grep. If still missing after a clean restart, suspect a real implementation bug — not an HMR stall.

## Strike log

- **#1, 2026-05-11, Kanban #710:** theme picker + dark-mode pass. dev-frontend reported tsc clean + healthcheck Up + curl 200; first dev-tester probe found 0 theme-picker markers, 0 FOUC script in `<head>`, 0 `dark:` classes in the rendered output. Source files were correct; `/app/.next/static/chunks/app/layout.js` was 2h stale vs `/app/app/layout.tsx`. `docker compose restart web` recovered; post-restart probe showed all expected markers verbatim. This rule landed in the same close-out to prevent strike #2.

## Anti-patterns

- **Vacuous-PASS attestation:** "tsc clean + healthcheck Up + curl 200, feature shipped." All three can be true while the rendered output is stale. The new feature's markup is NOT verified.
- **Source-grep instead of wire-grep:** `grep -l "dark:" web/components/*.tsx` confirms the dark-mode pass landed *in source* but NOT *in the served HTML*. It is a necessary but not sufficient check.
- **Visual deferral without wire attestation:** "Visual verification deferred to dev-tester" is fine for screenshot / pixel-contrast checks, but the **markup-shipped** check is the FE agent's job — not dev-tester's. Wire markers prove the source edits compiled and shipped; visual probes prove the styling renders correctly.
