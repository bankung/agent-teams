# Code map — langgraph/ + ops (2026-06-10)

## Totals

| Metric | Value |
|---|---|
| Prod files | 27 (.py excl. `__init__` stubs + scenarios) |
| Prod LOC | 8,455 |
| Test LOC | 11,362 |
| Nodes wired in graph | 8 (supervisor + 5 specialists + general + auditor) |
| Tool count | 8 (file_edit, file_write, git_diff, git_status, git_commit, shell_run, http_get, http_post) |
| LLM providers defined | 5 (anthropic, openai, ollama, deepseek, google) |
| Env knobs (distinct `os.getenv` names) | 25 |
| Compose services | 4 (db, api, web, langgraph) |
| Compose files | 4 (base, dev overlay, prod overlay, images) |
| Hooks (`.claude/hooks/`) | 31 (.ps1 / .sh files) |

---

## Modules

| module | LOC | purpose (1 line) | key deps | public surface | tests | status | evidence |
|---|---|---|---|---|---|---|---|
| `langgraph/graph.py` | 384 | FastAPI app + StateGraph boot, `/ok` + `/invoke` endpoints, lifespan wires checkpointer + worker | `nodes`, `worker`, `llm`, `state`, `AsyncPostgresSaver`, `psycopg` | `app` (FastAPI), `graph` (compiled StateGraph, also exported for `langgraph.json`) | `test_database_uri_validation.py` | active | graph.py:157 `_build_graph` |
| `langgraph/nodes.py` | 1,661 | All 8 graph nodes + routing functions + compaction + auditor | `llm`, `tools`, `hitl`, `audit`, `config`, `gemini_schema` | `supervisor_node`, `{5 specialist}_node`, `general_node`, `auditor_node`, `route_from_supervisor`, `route_from_auditor` | `test_nodes_compaction.py`, `test_nodes_prompt.py`, `test_supervisor_routing.py`, `test_specialist_*` | active | nodes.py:978–982 factory bindings |
| `langgraph/worker.py` | 1,479 | Background Kanban poll loop — task pickup, HITL resume, finalize PATCH, usage reporting | `hitl`, `approval_evaluator`, `content_safety`, `agent_context_sanitizer`, `llm`, `config`, `httpx` | `run_worker_loop(graph_module)`, `classify_exception`, `WorkerConfig` | `test_worker*.py` (10 files) | active | worker.py:192 `run_worker_loop` |
| `langgraph/llm.py` | 463 | Multi-provider chat-model factory + safety prelude + prompt-cache bundle builder | `langchain_anthropic`, `langchain_openai`, `langchain_ollama`, `langchain_google_genai` | `make_chat_model`, `resolve_provider`, `resolve_model`, `build_system_message`, `build_cached_system_content` | `test_llm.py` (562 LOC), `test_safety_prelude.py` | active | llm.py:251–274 defaults + provider list |
| `langgraph/state.py` | 67 | `AgentState` TypedDict — all fields flowing through the graph | `langchain_core.messages` | `AgentState`, `HaltReason` | `test_state.py` | active | state.py:30 |
| `langgraph/config.py` | 74 | Three shared helpers: `resolve_api_base`, `resolve_project_id`, `resolve_session_id`, `utc_now` | stdlib only | same four functions | (inline across worker tests) | active | config.py:22 `DEFAULT_API_BASE` |
| `langgraph/hitl.py` | 271 | HITL engine glue: `request_user_input` (interrupt wrapper), `validate_answer`, `resume_graph`, error hierarchy | `langgraph.types.Command/interrupt` | `request_user_input`, `validate_answer`, `resume_graph`, `HITLError` subclasses | `test_hitl.py` (1,065 LOC) | active | hitl.py:149 |
| `langgraph/content_safety.py` | 164 | L17 task-content scanner + L23 agent-output sanitizer (mirrors api/src/services/content_moderation) | stdlib `re` | `scan_task_content`, `sanitize_agent_action` | `test_content_safety.py`, `test_worker_l17_gate.py` | active | content_safety.py:42 `_DESTRUCTIVE_PATTERNS` |
| `langgraph/agent_context_sanitizer.py` | 51 | L16 resume-context sanitizer — redacts SQL DDL/DML keywords + caps at 500 chars (mirrors api copy) | stdlib `re` | `sanitize_for_agent_context` | `test_agent_context_sanitizer.py`, `test_worker_sanitizer.py` | active | agent_context_sanitizer.py:39 |
| `langgraph/audit.py` | 186 | HTTP wrapper to POST tool-call audit rows to `/api/tasks/{id}/tool-calls` | `httpx`, `config` | `record_tool_invocation` | `test_auditor.py`, `test_auditor_demo_branches.py` | active | audit.py:65 |
| `langgraph/approval_evaluator.py` | 179 | Per-project HITL approval-policy rule engine (verbatim mirror of api/src copy) | stdlib `re` | `evaluate_policy(question_payload, policies) -> (action, default_answer, rule_name)` | `test_worker_policy_hook.py` | active | approval_evaluator.py:120 |
| `langgraph/gemini_schema.py` | 230 | Sanitizes langchain tool schemas for Gemini native — adds `items` to bare `array` nodes | stdlib `copy`, langchain | `sanitize_tools_for_gemini(tools) -> (tools, fixed_names)` | `test_gemini_schema.py` | active | gemini_schema.py:1–50; invoked only when `provider==google` |
| `langgraph/tools/__init__.py` | 68 | Public surface + trigger submodule registration side-effects | all tool submodules | `GLOBAL_REGISTRY`, `InvokeContext`, `PermissionDecision`, `ToolResult`, etc. | (via tool-level tests) | active | tools/__init__.py:44–47 |
| `langgraph/tools/base.py` | 298 | `Tool` ABC, `ToolResult`, `ToolInput`, `InvokeContext`, `Tier` enum, `MAX_TOOL_LOOP_ITERATIONS=5` | `pydantic` | all base classes | `test_base.py` | active | tools/base.py:49–299 |
| `langgraph/tools/registry.py` | 87 | `ToolRegistry` singleton — `register` decorator, `get`, `all_tools_as_langchain` | `tools/base` | `GLOBAL_REGISTRY`, `ToolRegistry`, `ToolNotFoundError` | `test_registry.py` | active | tools/registry.py:87 `GLOBAL_REGISTRY` |
| `langgraph/tools/permission_gate.py` | 96 | Pure gate fn: `tools_config` + `Tool` → `AUTO_ALLOW\|HALT\|REJECT`; master kill-switch `tools_enabled` | `tools/base` | `check_permission`, `PermissionDecision` | `test_permission_gate.py` | active | permission_gate.py:56 |
| `langgraph/tools/sandbox.py` | 259 | Post-flight guards: output cap (100 KB), fs-boundary check, timeout enforcement via `asyncio.wait_for` | `tools/base` | `apply_sandbox`, `fs_boundary_check`, `apply_output_cap`, `check_hard_kill_drift` | `test_sandbox.py` | active | sandbox.py:1–259 |
| `langgraph/tools/fs/file_edit.py` | 155 | `FileEditTool` (tier=WRITE) — exact-string replacement in files | `tools/base` | registered as `file_edit` | `test_file_edit.py` | active | |
| `langgraph/tools/fs/file_write.py` | 97 | `FileWriteTool` (tier=WRITE) — create/overwrite files | `tools/base` | registered as `file_write` | `test_file_write.py` | active | |
| `langgraph/tools/vcs/git_commit.py` | 152 | `GitCommitTool` (tier=WRITE, retry_safe=False) — stage + commit | `tools/vcs/_run_git` | registered as `git_commit` | `test_git_commit.py` | active | |
| `langgraph/tools/vcs/git_diff.py` | 61 | `GitDiffTool` (tier=READ) | `tools/vcs/_run_git` | registered as `git_diff` | `test_git_diff.py` | active | |
| `langgraph/tools/vcs/git_status.py` | 50 | `GitStatusTool` (tier=READ) | `tools/vcs/_run_git` | registered as `git_status` | `test_git_status.py` | active | |
| `langgraph/tools/shell/shell_run.py` | 252 | `ShellRunTool` (tier=DESTRUCTIVE) — arbitrary subprocess with timeout + output cap | `tools/base`, `asyncio` | registered as `shell_run` | `test_shell_run.py` | active | |
| `langgraph/tools/http/http_get.py` | 142 | `HttpGetTool` (tier=NETWORK) — GET with host allowlist check | `tools/http/_common` | registered as `http_get` | `test_http.py` | active | |
| `langgraph/tools/http/http_post.py` | 174 | `HttpPostTool` (tier=NETWORK, retry_safe=False) — POST with host allowlist | `tools/http/_common` | registered as `http_post` | `test_http.py` | active | |
| `langgraph/tools/iteration_limit.py` | 10 | Stub re-exporting `MAX_TOOL_LOOP_ITERATIONS` (moved into `base.py` during Phase 1 minimization) | `tools/base` | `MAX_TOOL_LOOP_ITERATIONS` | (implicit) | vestigial — import kept for backward compat | tools/iteration_limit.py:1 |
| `langgraph/scenarios/regression_pack.py` | 1,001 | End-to-end regression runner (S1–S6 scenarios, CLI `main()`) hitting the live API+engine | `httpx` (sync) | `main()`, `run_s1` ... `run_s6`, `ScenarioResult` | (self-contained) | active | regression_pack.py:886 `main` |

---

## Oversized files

### `langgraph/nodes.py` — 1,661 LOC

| Region | Lines | Content |
|---|---|---|
| Module docstring + imports | 1–72 | Role code constants, imports from tools/hitl/llm |
| Compaction constants | 87–125 | `DEFAULT_CONTEXT_TOKEN_BUDGET`, `CONTEXT_RECENT_TURNS_KEPT`, resolver |
| Supervisor node + router | 126–166 | `supervisor_node`, `route_from_supervisor` |
| Specialist factory + _SYSTEM_PROMPT | 167–323 | `make_specialist_node`, `_role_from_agent_name` |
| Compaction internals | 328–481 | `_estimate_tokens`, `_total_tokens`, `_split_turns`, `_stub_turn`, `_compact_messages` |
| Tool-use loop | 482–753 | `_run_tool_use_loop`, `_ToolCallOutcome`, `_handle_one_tool_call`, `_audit`, `_bind_tools_safely`, `_ainvoke_model`, `_extract_usage`, `_stringify_content` |
| Per-task config fetch | 905–969 | `_fetch_tools_config`, `_resolve_paths` |
| Factory bindings | 972–982 | `backend_specialist_node = make_specialist_node("dev-backend")` × 5 |
| `general_node` with demo branches | 985–1100 | HITL demo, AUDITOR retry demo, AUDITOR escalate demo (env-gated: `HITL_DEMO_ENABLED=1`) |
| Auditor constants + heuristic pre-filter | 1103–1197 | `AUDITOR_RETRY_CAP_DEFAULT=3`, `_heuristic_clean` |
| Auditor LLM helpers | 1200–1317 | `_build_pass_report`, `_parse_llm_verdict`, `_normalise_llm_verdict`, `_build_specialist_excerpt` |
| `auditor_node` | 1319–1549 | Main async auditor; heuristic-skip → LLM classify → AUTO_RESOLVE/ESCALATE/PASS |
| Auditor post-resume + router | 1552–1661 | `_apply_escalation_resume`, `route_from_auditor` |

### `langgraph/worker.py` — 1,479 LOC

| Region | Lines | Content |
|---|---|---|
| Module docstring + imports + constants | 1–95 | Lifecycle constants, `_REASON_MAX`, `_HALT_REASON_MAX`, retry defaults |
| Exception taxonomy | 96–151 | `classify_exception` → `(kind, short_class)` |
| `WorkerConfig` | 153–184 | Validated at startup; requires `LANGGRAPH_PROJECT_ID` |
| `run_worker_loop` | 192–236 | Outer asyncio loop; error-isolation + CancelledError handling |
| `_poll_once` | 243–646 | One poll tick: L17 gate → prereq gate → IN_PROGRESS flip → session_run create → graph invoke with retry → finalize → usage PATCH |
| `_build_finalize_body` | 654–805 | Pure helper mapping final_state → PATCH body (DONE / HITL-BLOCKED / non-HITL-BLOCKED) |
| Session run helpers | 808–932 | `_create_session_run`, `_patch_session_run_usage`, `_patch_task` |
| Project field cache | 967–1115 | `_fetch_project_field` (10s TTL), `_fetch_project_policies`, `_fetch_project_required_binaries` |
| HITL resume | 1118–1479 | `_last_valid_answer`, `_needs_resume`, `_maybe_resume_hitl_task`, `_resume_hitl_task`, `_build_resume_halt_body`, `_stamped_resume_context` |

---

## Cross-cutting observations (facts only)

### Graph topology (wired in graph.py)

```
START → supervisor → {frontend|backend|devops|tester|reviewer|general} → auditor → END
                                                                ↑                 |
                                                                └── auto_resolve ←┘  (capped at 3)
```
- Six specialist nodes are all produced by `make_specialist_node` factory (nodes.py:207); the only per-role parameter is `agent_name` threaded into `build_cached_system_content`.
- `general_node` is NOT produced by the factory; it contains the three env-gated demo branches (HITL demo, AUDITOR retry demo, AUDITOR escalate demo).
- `auditor_node` sits after EVERY specialist; heuristic pre-filter skips LLM when `final_result > 20 chars + halt_reason is None + no tool error`.

### Provider matrix

| Provider | Key env var | Model env var | Model default | Status | Notes |
|---|---|---|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | **active default** | prompt caching wired (`cache_control: ephemeral`); `max_retries=1` |
| `openai` | `OPENAI_API_KEY` | `OPENAI_MODEL` | `gpt-4o` | active | `OPENAI_BASE_URL` enables OpenAI-compat endpoints; `max_retries=1` |
| `ollama` | none | `OLLAMA_MODEL` | `llama3.2` | active (local) | no API key; `bind_tools` soft-fails; `LANGGRAPH_OLLAMA_NUM_CTX` default 32768 |
| `deepseek` | `DEEPSEEK_API_KEY` | `LANGGRAPH_DEEPSEEK_MODEL` | `deepseek-chat` | **dormant by decision** | Kanban #1838 cancelled; code path still present but caller-less at policy level |
| `google` | `GOOGLE_API_KEY` | `GOOGLE_MODEL` | `gemini-flash-latest` | active | native `ChatGoogleGenerativeAI`; Gemini schema sanitizer fires at bind time (`gemini_schema.py`) |

DeepSeek note: `make_chat_model()` and `resolve_provider()` still support `deepseek` fully. Kanban #1838 decided not to use it; the path is untested in the CI regression pack but not removed.

### Safety layer inventory

| Layer | Module | Trigger point | What it guards |
|---|---|---|---|
| L16 | `agent_context_sanitizer.py` | `worker._poll_once` (line ~449) before graph invoke | SQL DDL/DML keywords in `halt_reason` / `status_change_reason` reaching agent prompt |
| L17 | `content_safety.scan_task_content` | `worker._poll_once` before IN_PROGRESS flip | Static regex scan of task title/desc/AC for destructive intent; zero token spend path |
| L22 | `llm.build_system_message` / `build_cached_system_content` | Every LLM call in nodes.py (specialist + auditor) | Safety prelude prepended to ALL system messages; `safety_prelude.txt` file; startup aborts if file missing |
| L23 | `content_safety.sanitize_agent_action` | `worker._build_finalize_body` + `_resume_hitl_task` | Redacts destructive SQL echoed in `final_result` or interrupt prompt before it reaches operator-trusted fields |

Mirror duplication pattern: `content_safety.py`, `agent_context_sanitizer.py`, and `approval_evaluator.py` are all intentional verbatim mirrors of `api/src/services/` counterparts. Drift between pairs is a known documented risk.

### Env-knob sprawl — 25 distinct `os.getenv` names in core prod files

```
DATABASE_URI                    LANGGRAPH_LLM_PROVIDER
ANTHROPIC_API_KEY               ANTHROPIC_MODEL
OPENAI_API_KEY                  OPENAI_MODEL
OPENAI_BASE_URL                 DEEPSEEK_API_KEY
LANGGRAPH_DEEPSEEK_MODEL        LANGGRAPH_DEEPSEEK_BASE_URL
GOOGLE_API_KEY                  GOOGLE_MODEL
OLLAMA_MODEL                    OLLAMA_BASE_URL
LANGGRAPH_OLLAMA_NUM_CTX        LANGGRAPH_PROJECT_ID
LANGGRAPH_SESSION_ID            LANGGRAPH_KANBAN_API_BASE
LANGGRAPH_POLL_INTERVAL_SEC     LANGGRAPH_CONTEXT_TOKEN_BUDGET
LANGGRAPH_TRANSIENT_RETRIES     LANGGRAPH_RETRY_BACKOFF_SEC
LANGGRAPH_WORKING_PATH          LANGGRAPH_REPO_ROOT
LANGGRAPH_DB_NAME_ALLOWLIST     HITL_DEMO_ENABLED
```

Compose sets all except `LANGGRAPH_WORKING_PATH`, `LANGGRAPH_REPO_ROOT`, `LANGGRAPH_TRANSIENT_RETRIES`, `LANGGRAPH_RETRY_BACKOFF_SEC`, and `LANGGRAPH_DB_NAME_ALLOWLIST` (those have code-level defaults only; not surfaced in `docker-compose.yml`).

### Tool tier map

| Tool | Tier | retry_safe | Notes |
|---|---|---|---|
| `git_diff`, `git_status` | READ | true | |
| `file_edit`, `file_write`, `git_commit` | WRITE | false for git_commit; true for fs tools | |
| `http_get`, `http_post` | NETWORK | false for http_post | host allowlist enforced by sandbox |
| `shell_run` | DESTRUCTIVE | — | Arbitrary subprocess; permission gate defaults REJECT unless explicitly in `halt_tiers` |

All 8 tools are always registered in `GLOBAL_REGISTRY` at import time regardless of provider. The Ollama soft-disable applies only to `bind_tools` — the tools remain in the registry and can be exercised directly.

### Compose service summary

| Service | Profile | Build | Ports | Key env surface | Notes |
|---|---|---|---|---|---|
| `db` | always | `postgres:16-alpine` | 5432 | `POSTGRES_PASSWORD`, `POSTGRES_DB` | `max_connections=20`, `shared_buffers=32MB` (hardcoded in command) |
| `api` | always | `./api/Dockerfile` | 8456 | 40+ vars (LLM, backup, email, VAPID, SMTP, OAuth) | `--reload` in dev; bind-mounts repo at `/repo` |
| `web` | always | `./web/Dockerfile` | 5431 | 3 vars | dev overlay adds source bind-mount + anon node_modules volume |
| `langgraph` | `langgraph` profile only | `./langgraph/Dockerfile` | 8465→8000 | 26 vars | bind-mounts repo at `/repo` in dev; no bind-mount in images stack |

The `langgraph` service is gated behind `profiles: ["langgraph"]` in the base compose — it does NOT start with a plain `docker compose up`. It must be started explicitly with `--profile langgraph`.

### Defined but never externally imported

- `langgraph/tools/iteration_limit.py` — 10 LOC stub. `MAX_TOOL_LOOP_ITERATIONS` was moved into `tools/base.py`; this file remains for backward compat but no production code imports from it directly.
- `langgraph/scenarios/regression_pack.py` — standalone CLI (`main()`); not imported by the FastAPI app or the worker. Invoked manually or from CI.

### Hooks surface (`.claude/hooks/` — 31 files)

Not langgraph-specific; these are all repo-wide Claude Code hooks. Selected relevant ones:
- `block-raw-sql-dml.ps1` — prevents direct SQL DML from Claude Code bash calls (L4 complement)
- `block-pytest-on-live-db.ps1` — prevents pytest against non-test DB
- `block-spawn-on-killed-project.ps1` — reads `_runtime/lead_project_id.txt`
- `secretary-email-action-gate.ps1` — email write gate (Layer-0 role enforcement)
- `approval-policies-gate.ps1` — enforces approval policies at tool-use level

### `_runtime/` contents (5 files)

- `lead_project_id.txt` — active project binding (integer)
- `secretary-email-policy.json` — secretary email permission rules
- `email-actions.jsonl` — email action audit log
- `email_attachments/` — temp dir for attachment staging
- `posttooluse-bash-hygiene-debug.log` — debug log for hygiene hook

---

## Open questions

- `langgraph/tools/http/__init__.py` (61 LOC) contains a note about http tools being "registered only when the active LLM provider supports tool-use (Anthropic + OpenAI; ollama is feature-flagged OFF)". The actual conditional-registration mechanism needs verification — the `__init__.py` imports unconditionally in `tools/__init__.py:47`. Whether the Ollama exclusion happens at bind-time (`_bind_tools_safely`) rather than registration-time is likely but not confirmed from this read.
- `langgraph/gemini_schema.py` line 50+ (not read past the header) — the exact walk algorithm and which tools it fixes is unverified.
- `LANGGRAPH_WORKING_PATH` is set via env in `nodes._resolve_paths` but is not declared in `docker-compose.yml`. The compose file has `LANGGRAPH_REPO_ROOT` absent too. For a single-project dogfood, these likely default to `None` / `/repo` at runtime — but it is unclear which projects have `working_path` set in the DB.
- `approval_evaluator.py` and `api/src/services/approval_evaluator.py` are declared as verbatim mirrors, but there is no automated sync check (tests run against their own copy in isolation). Drift is a latent risk.

## Followups

- Map the `api/src/services/` counterparts for the three mirrored modules to confirm current sync status.
- Document the `tools/http/__init__.py` conditional logic explicitly (registration vs bind-time disable).
- The `scenarios/regression_pack.py` S1–S6 scenarios are undocumented; a scenario index would help reviewers assess coverage.

## Standards insights (proposal only — Lead applies)

- The three mirrored modules (`content_safety`, `agent_context_sanitizer`, `approval_evaluator`) represent a recurring pattern: langgraph container has no shared source tree with api. A `langgraph-common` or shared-package approach would eliminate the known drift risk without changing the container boundary.
- `LANGGRAPH_WORKING_PATH` and `LANGGRAPH_REPO_ROOT` are wired in code but absent from compose defaults — could cause silent `None` behaviour in multi-project setups. Worth standardising as required vars in the service definition.
- `HITL_DEMO_ENABLED=1` is compose-default in dev, meaning the demo branches in `general_node` are live for any task with a matching title prefix on a dev deployment. The security rationale is documented (Kanban #1107), but the branches live in the production code path rather than in a separate test module.
