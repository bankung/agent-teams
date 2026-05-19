# Prompt caching on langgraph specialist nodes

**Decision date:** 2026-05-19
**Source task:** Kanban #1186 (B+C path locked by operator)
**Touches:** `langgraph/llm.py`, `langgraph/nodes.py`, `api/src/services/cost_tracker.py`

## What changed

`langgraph/nodes.py::backend_specialist_node` and any future specialist that calls `make_chat_model()` now bundle four sources into the system message — safety prelude + `CLAUDE.md` + the team playbook (`.claude/teams/<team>.md`) + the agent definition (`.claude/agents/<agent_name>.md`) — and attach `cache_control: {"type": "ephemeral"}` on that bundle.

On Anthropic the SystemMessage content is a list of two text blocks:
- the stable bundle (cached, ~11,500 tokens, well above the 1024-token Sonnet minimum)
- the per-task `role_brief` (NOT cached)

On OpenAI / Ollama the bundle collapses to a flat string (`cache_control` is Anthropic-only — provider-gated in `build_cached_system_content`).

The per-task `HumanMessage` (task brief) lives outside the cached system block by construction. The cache key is byte-identical across loop iterations within Anthropic's 5-minute TTL — break-even after iteration 2, ~78% input-cost savings by iteration 10 (measured in `test_compute_cost_cache_savings_vs_uncached_amortization`).

## Cost-tracker integration (#1186 C)

`api/src/services/cost_tracker.py::compute_cost(...)` now accepts two optional kwargs:

- `cache_read_input_tokens: int = 0` — priced at 0.10× the regular input rate
- `cache_creation_input_tokens: int = 0` — priced at 1.25× the regular input rate

Callers passing only `input_tokens + output_tokens` (no cache fields) get the existing behavior. 5 new tests cover the cache pricing math + the backward-compat path + the 10-iter amortization benchmark.

## Discipline for future agent / playbook authors

- Keep agent definitions and team playbooks **stable across calls**. No per-call dynamic substitution (no `{{operator}}`, no per-task placeholders) inside the bundled content — those go in `role_brief` or `HumanMessage` so they sit outside the cached block.
- Sizing matters. CLAUDE.md alone is ~5.2K tokens; growth beyond ~20K starts to feel the cache-write premium every time the bundle changes. When a CLAUDE.md / playbook / agent-def edit ships, the FIRST call after the change pays the 1.25× write cost — all subsequent calls inside the TTL recover at 0.10×.
- Bundle is process-local (`_BUNDLE_CACHE` in `langgraph/llm.py`). Worker restarts re-load from disk. Acceptable for short-lived worker processes; revisit if a long-lived worker is introduced.

## Verification status (as of close)

| AC | Status | Source |
|---|---|---|
| AC1 cache_control on stable context | passed | `langgraph/tests/test_langgraph_cache.py::test_cache_control_on_stable_block` |
| AC2 cache_read_input_tokens > 0 on 2nd call | passed (mocked); live deferred | `test_simulated_cache_hit_lowers_cost` + smoke followup filed |
| AC3 ≥40% input cost reduction | passed (measured 77.5% on 10-iter) | `api/tests/test_cost_tracker.py::test_compute_cost_cache_savings_vs_uncached_amortization` |
| AC4 dynamic content NOT cached | passed | `test_dynamic_brief_outside_cached_block` (two different briefs → byte-identical stable block) |
| AC5 documentation | passed | this file |

## Open items (carried into followups)

- **Live cache hit verification.** Mocked API in unit tests proves the plumbing. A live verification requires `ANTHROPIC_API_KEY` in the langgraph container + a runtime smoke task with ≥2 iterations; smoke followup task filed separately.
- **Auditor node not yet bundled.** `auditor_node` still uses the string-only `build_system_message`. The auditor is single-shot per task (no loop) so amortization is poor — bundle wire-up there only if profiling shows amortization is worth the 1.25× write premium.
- **Pre-existing 15 test failures** in `tests/tools/test_http.py` + `test_registry.py` — unrelated to #1186 (caused by `LANGGRAPH_LLM_PROVIDER=ollama` excluding http tools at registry-build time). Worth a separate task; not blocking.
