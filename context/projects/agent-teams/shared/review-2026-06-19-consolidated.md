# Intense review — FE + BE + Langgraph — 2026-06-19 (consolidated)

Task #2497. Method: 4 parallel read-only reviewers (dev-reviewer x3 + dev-security-reviewer),
then **Lead Mode-B verification** of every headline finding against the actual code. Full
per-subsystem detail: `_scratch/review-{fe,be,langgraph,security}-2026-06-19.md` (ephemeral —
this file is the durable record).

## Verdict

**No Critical/blocking bugs. Nothing forces a stop before continuing.** The codebase is in good
shape — strong layering (routers→services, no raw SQL), a real XSS-safe markdown path
(`safeMarkdown.tsx`), Fernet vault, multi-layer agent-safety gates, and the 2026-05-17 DB-wipe
guard. The real findings cluster in the **langgraph worker** (retry/checkpoint edge cases) and a
few **BE correctness/perf** spots. The "security High"s are, on verification, **deliberate
single-operator design choices**, not accidental holes.

## Status update — 2026-06-19 (end of day)

Actioned the same day the review landed. Several headline findings were **already fixed**
before the review's snapshot (the review read a slightly stale tree).

**DONE + pushed:**
- FE **HIGH-1 aria-activedescendant (#8)** + **HIGH-2 TaskOutputs 50-parallel → lazy-load-on-expand (#9)** — already fixed by **#2502** (`36a4883`) before this snapshot. ✅
- FE MED nits — GanttView unmount guard, TaskDetail ESC-listener `[]`-deps, TaskOutputs unique key, WildcardSSE subscriber cleanup — **#2507** (`02f0ab7`). ✅
- BE — `decisions.py` silent-drop → WARNING-log; `budget_gate._ALERT_SENT` prune (bounded) — **#2508** (`344b100`, +2 regression tests). Parked in-progress (tests run in the operator BE pytest batch).

**BUILT — in-progress, pending the operator's pytest run to close:**
- TIER1 **#1** (worker retry brief double-injection) → **#2498**
- TIER1 **#2** (`_stub_turn` checkpoint mutation) → **#2499**
- TIER1 **#4** (GET next-autorun commit + TOCTOU) → **#2500**
- TIER2 **#6** (update_task 3-commit boundary) + **#7** (full-lane load / N+1) → **#2501**
- TIER3 gate — `require_operator_proof` on kill/revive/grant-consent + activation → **#2503** (`b54514b` Fix1)

**Stale / already-fixed (no action needed):**
- Security minor info-leaks `ingest.py:607` / `agent_gallery.py:254` — verified no `str(exc)` present anymore (already fixed). ✅
- Security M-1 (secrets → langgraph env) — FALSE (verified clean; see below).

**Still open, no task yet (operator picks if/when):**
- TIER1 **#3** (env-var validation at `WorkerConfig.__init__`) — confirm whether #2498's "retry-loop robustness" already covers it.
- TIER2 **#5** (`os.environ` session-id race), **#10 / C-2** (sync `probe_model.invoke` in lifespan — easy `await ainvoke` win).
- TIER3 **network-binding** (`db`/`web`/`langgraph` → 127.0.0.1 + `POSTGRES_PASSWORD`) — tracked under **#2503** (config/devops part), operator-gated.
- `shell_run.py` `docker compose exec` allowlist narrowing (optional; mitigated by DESTRUCTIVE-tier always-HALT).

## TIER 1 — real bugs worth fixing soon (correctness / data integrity)

| # | Where | Issue | Verdict |
|---|---|---|---|
| 1 | `langgraph/worker.py:809` | Transient-retry re-sends `initial_state` → `add_messages` re-injects the brief → specialist sees brief twice → **possible double tool-execution + audit skew** | Lead-confirmed pattern; real |
| 2 | `langgraph/nodes.py:443` (`_stub_turn`) | Mutates `ToolMessage.content` **in-place** on objects shared with the checkpointed `state["messages"]` → potential **durable checkpoint history loss** across HITL resume/auditor retry | Mutation confirmed; impact needs a focused trace; fix is cheap (clone, don't mutate) |
| 3 | `langgraph/worker.py:792` | `RuntimeError` on malformed `LANGGRAPH_TRANSIENT_RETRIES` escapes the loop → task **stuck in IN_PROGRESS forever** + worker degradation | Real; fix = validate at `WorkerConfig.__init__` |
| 4 | `api/src/routers/tasks.py:638,716` | `GET /api/tasks/next-autorun` calls `session.commit()` (RFC-unsafe GET) + TOCTOU on concurrent polls → **duplicate HITL-timeout/budget stamps & duplicate push notifications** | Confirmed GET-commits; the langgraph worker is the caller; fix = POST + `for_update(skip_locked)` |

## TIER 2 — real, lower urgency (perf / latent / a11y)

| # | Where | Issue |
|---|---|---|
| 5 | `langgraph/worker.py:506` | `os.environ["LANGGRAPH_SESSION_ID"]` mutation = latent session-id race (safe today only because the worker loop is serial) |
| 6 | `api/src/routers/tasks.py:2545/2563/2592` | Three sequential commits in `update_task`; hard crash between #2 and #3 leaves partial state, no compensation (low likelihood) |
| 7 | `api/src/routers/tasks.py:1147` + `1104/2112` | Full-lane load (no LIMIT) on reorder + N+1 `session.get()` blocker-chain walk — matters at hundreds-of-tasks scale |
| 8 | `web/components/MilestoneCombobox.tsx:173` | Missing `aria-activedescendant` — keyboard/SR users can't track the highlighted option (note: this is the file just touched in #2496) |
| 9 | `web/components/TaskOutputs.tsx:126` | Up to 50 parallel blob fetches on mount (no concurrency cap) → latency/connection-stall; fix = lazy-load or promise-pool |
| 10 | `langgraph/graph.py:249` | Sync `probe_model.invoke()` in the async lifespan — startup-only (~1-3s), trivial fix `await ainvoke` (easy win) |

## TIER 3 — security: by-design for single-operator + the real config hardening

The operator-proof gate (`operator_auth.py`) is **fail-open-when-unset by deliberate design**
(#1799), and `kill`/`revive`/`grant-consent` (`projects.py:916/962/1013`) and `X-Project-Id`
(`session_project.py`, documented "ADVISORY and SPOOFABLE") are intentional single-operator
choices — NOT accidental holes. Real hardening, if/when wanted:

- **Activate the gate**: set `OPERATOR_ACTION_KEY` in `.env` (then it's fail-closed); optionally
  add `require_operator_proof` to kill/revive/grant-consent (mirror `resources.py`).
- **Network binding (Lead finding, `docker-compose.yml`)**: `api`(8456) is correctly bound to
  `127.0.0.1` ✓ — BUT `db`(5432, default `postgres/postgres`), `web`(5431), `langgraph`(8465)
  bind to `0.0.0.0`, and `web:5431` proxies `/api/*` to the api (partially bypassing the
  localhost-only api). **On an untrusted network this is the most concrete exposure.** Fix:
  bind db/web/langgraph to `127.0.0.1` and/or set a strong `POSTGRES_PASSWORD`.
- Minor info-leaks: `ingest.py:607`, `agent_gallery.py:254` echo internal error strings → static
  detail. `shell_run.py:62` allows `docker compose exec` (flagged by 2 reviewers; mitigated by the
  DESTRUCTIVE-tier always-HALT-for-review gate) → narrow the allowlist if desired.

## False alarms / down-graded by Lead Mode-B verification

- **Security M-1 (secrets leak into langgraph env) → FALSE.** `docker-compose.yml` base correctly
  omits `OPERATOR_ACTION_KEY` + `CREDENTIALS_MASTER_KEY` from the langgraph service. (Check the
  dev/prod overlays too, but the base is clean.)
- **langgraph C-1 (`nodes.py:944` sync fallback) → Critical→Low.** Reviewer's own note: "not
  reachable in production today." Latent only.
- **langgraph C-2 → Critical→Low/Med.** Startup-only, not live traffic.
- **Security H-3 (X-Project-Id spoofable) → informational/by-design**, and the api is
  localhost-bound.
- **FE Board infinite-loop hypothesis → dismissed** (the `filterKey` guard makes the
  during-render setState a safe one-shot — verified).
- **FE HIGH-3 (`useAsyncData` no-dep effect) → Low** — intentional + documented; just add a comment.

## Recommendation

Fix order if we act: **TIER 1 (#1-#4)** first (langgraph retry/checkpoint + the GET-commit), then
TIER 2 as convenient, then the TIER 3 config hardening (cheap: `.env` + compose binding). Each is
its own scoped change → separate follow-up tasks, operator picks which to open.
