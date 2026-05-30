# Code-minimization plan — agent-teams

**As-of:** 2026-05-30 · **decay_class:** review-on-touch · **Status:** DRAFT plan, not yet actioned (saved as reference).
**Source:** 4 read-only minimization reviewers (api / web / langgraph / infra). Companion: `code-review-2026-05-30.md` (bugs/risks). Inspiration: large-core refactors that split a monolith loop into modules.
**Principle:** smallest surgical moves, ranked by (LOC saved ÷ risk). Structural splits reorganize (not delete) but cut the maintenance blast radius. Nothing here changes behavior unless noted.

## Hotspot summary (where the weight is)
| Subsystem | Biggest items | Note |
|---|---|---|
| infra | `.codex/` ~10,264 LOC mirror (88 files, no sync) ; 155 ephemeral project dirs + 78 `.deleted/` | largest duplication + working-tree noise |
| langgraph | `nodes.py` 1,287 (auditor ~486) ; ~5,746 total prod | prime god-file; split into modules |
| api | `tasks.py` 2,693 (`update_task` = 838-line fn) ; `test_routes_smoke.py` 4,565 | god-function + test monolith |
| web | `api.ts` 1,594 (mostly types, OK) ; `TaskDetail.tsx` 1,076 ; **modal chrome duped ×10** | dedup via shared components |

## Phase 1 — low-risk quick wins (no behavior change, ~400–500 net prod LOC + cleanup)
**Backend**
- Extract `_translate_task_integrity_error(exc, context)` — dedup IntegrityError blocks in create/update (~30).
- Promote `get_active_project_or_404` to `db.py`; delete 3 local `_resolve_project_or_404` copies (~25).
- Delete unused `routers/__init__.py` re-exports (5). Remove `_Snap` class → explicit kwargs (8). Replace stripped-by-`-O` bare `assert`s in `_compute_sort_order` (5).
- Extract `_apply_jsonb_serialization` helper (the ×6 "#801 pattern") (~30).

**Frontend** (highest LOC payoff)
- **`ModalShell` shared component** — collapses the 10× duplicated backdrop/panel/ESC/backdrop-click chrome; ALSO fixes the stale-closure ESC + the backdrop-vs-panel a11y bug. (**-110 to -160**).
- `extractErrorMessage(err)` util — replaces the 32× `err instanceof Error ? ...` (**-50**).
- Shared `PRIORITY_OPTIONS`/`ROLE_OPTIONS`/`REASON_MIN_CHARS` in `constants.ts` (3 dup defs) (**-28**).
- `PauseOverrideBlock` shared component (verbatim block in NewTaskModal + AiTaskModal) (**-30**).

**Langgraph**
- New `langgraph/config.py`: `DEFAULT_API_BASE` + `resolve_api_base()` + `resolve_project_id()` + `utc_now()` — kills 3 copies of api-base + 2 project-id + 2 UTC helpers (~55).
- Inline `tools/iteration_limit.py` (20-LOC file for 2 constants) into `tools/base.py` (-20).
- 4 stub specialist wrappers → one `make_stub_node()` factory (-20). Centralize VCS `asyncio.TimeoutError` catch in `_run_git` (-20).

**Infra** (file-count + maintenance, propose where humans-only)
- Prune ~34 session-specific task-ID curl entries from `settings.json` (covered by existing wildcards) + 7 redundant `Write/Edit current-state.md` entries.
- **Create the missing `context/standards/general/reward-hacking-patterns.md`** (extract catalogue from dev-reviewer.md) → fixes 7 dead refs + lets the 5 inline copies shrink to a pointer (~55). *(humans-only → propose to operator.)*
- `.gitignore` + git-rm the 155 `smoke-push-*`/`hitl-push-*` ephemeral dirs + `.deleted/` (DB is the authoritative record). Massive working-tree/glob noise reduction.
- Move `bin/tier-presets/AUDIT.md` → a `context/teams/dev/decisions.md` entry; delete the stray.

## Phase 2 — medium-risk structural (reorg, broad test coverage)
- **Split `langgraph/nodes.py` (1,287) → `nodes/{routing,specialist,kanban_client,auditor}.py`** with `nodes/__init__.py` re-exporting public names so `graph.py` import is unchanged. The auditor block (~486) becomes independently testable. *(The headline "shrink the core" move — directly analogous to the Hermes refactor. Also unblocks wiring gated verifier/synthesizer = backlog #1239/#1261 and the swarm idea — but those touch Mode B → respect the #1652 gate.)*
- Merge `budget_enforcer.py` + `budget_gate.py` → one `services/budget.py` with clear names (`check_pickup_budget` / `check_spawn_budget`) (~80).
- Split `TaskDetail.tsx` (1,076) — extract the 5 inline sub-components to siblings; reuse `AcceptanceCriteriaSection`/`CostStrip` in `TaskFocusView.tsx` (kills parallel impls). (1,076 → ~620 + dedup.)
- Split `test_routes_smoke.py` (4,565) into per-router files (test-maintenance blast radius; no prod change).

## Phase 3 — high-risk / big / operator-decision
- **`update_task` 838-line fn → service extraction** (validation pass + side-effect hooks into named services; router handler → ~150 lines). High value but source-text-locked strings + broad tests → careful.
- **`.codex/` mirror (~10,264 LOC):** generate from `.claude/` via a `bin/sync-codex.ps1` (md→toml + verbatim docs/hooks) run pre-push, OR delete if Codex-compat is roadmap-only. *(Operator decision — it's a product-surface choice.)*
- **Shared `agent-teams-common` package** for the verbatim-mirrored `approval_evaluator.py` + `agent_context_sanitizer.py` (langgraph copies of api) — removes ~230 LOC but needs pyproject + Docker build changes.
- DRAFT hooks (8 files, ~1,335 LOC, sem-*/data-*, never registered) + unregistered `.sh` twins (268) → delete or move to `_scratch/hooks-draft/`; re-create when those teams onboard.

## Rough totals
- **Phase 1 (low-risk):** ~400–500 net prod LOC removed + the biggest *working-tree* cleanup (≈230 ephemeral dirs, ~34 settings entries) + fixes the broken reward-hacking ref.
- **Phase 2:** reorganizes ~2,000 LOC (nodes.py + TaskDetail + test monolith) into focused modules; ~80 net deleted (budget merge).
- **Phase 3:** up to ~10k LOC surface removed if `.codex/` is generated/deleted; +~230 from the common package; +~1,600 if DRAFT/`.sh` hooks dropped.

## Suggested order
Phase 1 frontend `ModalShell` + backend dedup are the cheapest high-yield. The `nodes.py` split (Phase 2) is the most strategically valuable structural move (enables later engine work) but should follow once #1652 direction is set, since the auditor/specialist nodes are Mode-B. `.codex/` and `update_task` (Phase 3) need an explicit operator call.
