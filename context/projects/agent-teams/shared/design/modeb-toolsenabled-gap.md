# Mode-B `tools_enabled` UI-gap — design doc (Task #2660)

**Status:** gap CONFIRMED · design-only (implementation = separate follow-up) · 2026-06-25
**Author:** Lead (analysis by dev-analyst, key claims independently re-verified — see "Verification")
**Decision owner:** operator — the eligibility contract is **operator-locked** (multiboard.py:6); the recommended fix (Option B) changes it, so the direction needs operator sign-off before the follow-up implements.

## Verification (Lead, independent)
- `langgraph/multiboard.py:35-52` `is_eligible` read directly — requires `is_active` AND `auto_run_consent_at` AND `tools_config.tools_enabled` (45-47) AND NOT `is_paused` AND NOT `is_killed`. ✓
- `web/`-wide grep `tools_enabled|tools_config` → exactly 2 hits, both comments (`web/lib/api.ts:505`, `web/components/EditProjectModal.tsx:24`). No FE write path. ✓

---

## 1. The gap (CONFIRMED)
A UI-only operator **cannot** enable a new project for Mode-B (headless autonomous) execution.

- **Eligibility contract** (`langgraph/multiboard.py:35-52`, docstring 6-9): multi-board pickup requires ALL of `is_active` + `auto_run_consent_at != null` + `tools_config.tools_enabled == true` + NOT `is_paused` + NOT `is_killed`.
- **FE consent surface EXISTS** (`web/components/ProjectConsentBanner.tsx`, `ProjectConsentGrantModal.tsx`): "Enable headless auto-run" → `POST /api/projects/{id}/grant-consent` → sets `auto_run_consent_at`.
- **FE `tools_enabled` surface is MISSING**: `EditProjectModal.tsx:24` *explicitly excludes* `tools_config` ("separate flow — tool gate"); `NewProjectModal.tsx` omits it; `web/lib/api.ts` `ProjectUpdateBody` narrows it out. Whole-`web/` grep = 2 comment hits only. **Zero write path.**
- **Net:** consent via UI is necessary but NOT sufficient; `tools_enabled` (no UI) is also required, so a new board never becomes eligible. Workaround used for the #706 pilot: Lead PATCHed `tools_config.tools_enabled=true` via the API — not available to a UI-only user.

## 2. Single-board vs multi-board
- **Single-board** (`LANGGRAPH_PROJECT_ID` set): bypasses `is_eligible()` entirely — polls the pinned project regardless. But it needs an `.env` change + container recreate (devops, not UI) and runs only ONE board.
- **Multi-board** (`LANGGRAPH_PROJECT_ID` unset = the `docker-compose.yml:260` default): enforces `is_eligible()`, incl. `tools_enabled`.
- **Conclusion: no pure-UI path exists today.** (The pre-built `docker-compose.images.yml:154` variant defaults to single-board `=1`, distribution-path only.)

## 3. Root cause
- **A — implementation gap:** Kanban #943 shipped the Edit Project modal but deferred `tools_config` ("separate flow — tool gate"); the toggle UI was never built and no successor task was created. The intent is documented in 3 backend files (`api/src/schemas/project.py:261`, the `0100_projects_tools_config` migration, `langgraph/tools/permission_gate.py:67`).
- **B — semantic mismatch:** `tools_config.tools_enabled` conflates **tool-use capability** (does the agent need tools?) with **autonomous-execution intent** (which `auto_run_consent_at` already captures). A pure-Q&A / no-tools eval board must still flip a "tool switch" to be picked up — semantically incoherent (also flagged in `shared/gemini-harness-test-plan.md:36`).

## 4. Fix options

### Option A — build the FE tool-gate config UI
Add a `tools_enabled` toggle to `EditProjectModal.tsx` (+ re-add `tools_config` to `ProjectUpdateBody` in `web/lib/api.ts`); optionally a dedicated `ProjectToolsConfigModal.tsx`. API already supports `PATCH /api/projects/{id}`.
- **+** directly fixes the gap, no worker/contract change. **+** exposes full tier posture.
- **−** perpetuates the semantic mismatch (no-tools boards still flip a tool switch); tier config is complex for a non-technical operator. Scope: medium, FE-only.

### Option B — decouple eligibility from `tools_enabled` (recommended first step)
Delete the `tools_enabled` check from `is_eligible()` — consent alone signals auto-run intent; `tools_enabled` stays a per-call tool-permission default the gate still enforces.
- **Files:** `langgraph/multiboard.py:45-47` (delete 3 lines); `langgraph/tests/test_worker_multiboard.py` (update the `tools_enabled=False` eligibility assertions); `permission_gate.py` unchanged (still REJECTs tool calls when `tools_enabled=false` → safe over-block).
- **+** resolves the mismatch; pure-Q&A boards become eligible; smallest change (3 LOC + tests). **+** safety net intact (REJECT not silent-exec).
- **−** a consented board with `tools_enabled=false` is now picked up and its tool calls all REJECT (noisy logs — needs operator awareness). **−** changes the **operator-locked** contract → requires operator sign-off + doc/comment/migration updates.

### Option C — combined "Enable autonomous execution" flow (recommended follow-up UX)
One operator-gated action that sets `auto_run_consent_at` AND a safe `tools_config` posture together (extend `ProjectConsentGrantModal.tsx` or a new `ProjectAutoRunSetupModal.tsx` + a 2nd PATCH in `web/lib/api.ts`). Posture selector: "Q&A only" / "Standard tools" / "Custom" with safe defaults.
- **+** best UX — one deliberate action, no consent-without-tools / tools-without-consent split; surfaces tool posture semantically. **−** most FE work; still needs Option B to fully resolve the mismatch. Scope: medium-large.

## 5. Recommendation
**Option B first** (3-LOC eligibility decouple — unblocks already-consented boards immediately, fixes the architectural mismatch, safety preserved by the call-site permission gate), **then Option C** as the UX follow-up. Option A alone is not recommended (perpetuates the mismatch + clutters a generic edit modal). **Both require operator sign-off** because Option B alters the operator-locked contract.

**Follow-up implementation task (opened, manual — pending operator direction):**
**Task #2707** (opened, `run_mode=manual`): "[feature] Implement #2660 Mode-B tools_enabled UI-gap fix … operator confirms Option B+C vs A" — decouple `is_eligible` from `tools_enabled` (Option B) + combined "Enable autonomous execution" setup flow (Option C). Gated on the operator confirming Option B+C vs A before implementation.

## Open questions
None on the facts (all confirmed). Open **decision**: operator to confirm B (decouple the locked contract) vs A (build the toggle UI) before implementation.
