# Test plan — validate langgraph headless harness with a capable API model (Gemini Flash)

**Status:** spec (pre-implementation). Blocked on operator GCP setup (§3).
**Kanban:** agent-teams task (see board; this doc is the companion spec).
**Owner path:** Lead writes this doc; dev-backend implements §4; Lead/operator drive §3/§5/§6.

---

## 1. Goal & scope

**One-line goal:** prove that the agent-teams **own** LangGraph harness (`_run_tool_use_loop` in `langgraph/nodes.py`) can drive a **capable model over a real API** through the full backend-role tool-use loop end-to-end.

This single test collapses two questions into one run:
- "Does the real API connection work?" (provider plumbing)
- "Does our dev harness actually work with a capable model?" (the tool-use loop)

**Vehicle:** Google Gemini Flash via the **OpenAI-compatible endpoint**, funded by the GCP $300 free-trial credit (no GPU, no account upgrade, no per-token cost worry during the trial).

### In scope
- Add `OPENAI_BASE_URL` override to the openai provider branch so `ChatOpenAI` can point at Gemini's OpenAI-compat endpoint.
- Run the langgraph worker against an **isolated test project** with `tools_enabled=true`.
- Drive ONE backend-role task that requires a read-tier tool; prove the real tool-use loop ran.

### Out of scope (explicitly deferred per operator decision)
- Big-model-on-local test (low project value).
- The 4 stub specialist roles (frontend / devops / tester / reviewer) — only `backend_specialist_node` is implemented.
- Self-hosted 40B / Ollama-on-cloud-GPU (optional future data point only).

---

## 2. Why the prior Ollama-local test proved nothing (code evidence)

The local Ollama run was tool-less for **two independent reasons** — either alone forces the single-shot, no-tool path:

1. **Provider gate** — `_bind_tools_safely` (`langgraph/nodes.py:474`): ollama's `bind_tools` raises → returns `None` → `backend_specialist_node` falls to the single-shot path (`nodes.py:219-228`). Comment at `nodes.py:158-160` documents the intent.
2. **Project config** — agent-teams project (id=1) has `tools_config.tools_enabled = false` (confirmed via `GET /api/projects/by-name/agent-teams`). `_bind_tools_safely` (`nodes.py:490`) returns `None` whenever `tools_enabled` is falsy — **regardless of model**.

⇒ The local test never exercised `_run_tool_use_loop` (permission gate / sandbox / audit / multi-turn). Both confounds must be removed for the Gemini test to be meaningful: a capable model that binds tools **and** a project with `tools_enabled=true`.

---

## 3. Operator prerequisites — GCP setup (operator-owned; do BEFORE §4 implementation)

> ⚠️ **Cost-safety gotcha:** a GCP **Budget** only sends notification emails — it does **NOT** cap or stop spend. The real hard cap is the **API quota limit**. Do not rely on the budget alert alone.

1. Create a **dedicated GCP project** for this experiment (isolates blast radius; delete the whole project to clean up).
2. Enable the **Generative Language API** (AI Studio) on that project.
3. Generate an **API key** scoped to that project.
4. Set an **API quota limit** (requests/min + requests/day) on the Generative Language API — this is the hard stop.
5. Set a **budget alert** (e.g. $10) — know it only notifies.
6. Stay on the **free trial** (do NOT upgrade to paid) — "No billing during trial" provides a natural backstop (spend stops when credit/90 days runs out; no charge).
7. Hand the API key to Lead via `.env` (`OPENAI_API_KEY=<gemini key>`) — never commit it.

**Endpoint + model (verify current values in AI Studio):**
- `OPENAI_BASE_URL = https://generativelanguage.googleapis.com/v1beta/openai/`
- `OPENAI_MODEL = gemini-2.0-flash` (confirm the current Flash model id)

---

## 4. Code change spec (dev-backend; small, low-risk)

**File: `langgraph/llm.py`, openai branch (currently ~line 381-385).**
- Read `OPENAI_BASE_URL` from env (default empty).
- When non-empty, pass `base_url=<value>` to `ChatOpenAI(...)`; when empty, construct as today (real OpenAI default unchanged).
- Keep `max_retries=1`.

**File: `.env.example`** — add an `OPENAI_BASE_URL=` line under the LangGraph provider section with a one-line comment (empty = real OpenAI; set to Gemini's OpenAI-compat URL to drive Gemini Flash).

**File: `docker-compose.yml`, langgraph service env block** — add `OPENAI_BASE_URL: ${OPENAI_BASE_URL:-}` alongside the existing `OPENAI_*` vars (~line 234-236).

**Why this over a native `gemini` provider branch:** reuses the mature, tool-tested openai code path (`bind_tools`, content stringify) instead of a fresh branch that would hit Gemini-specific quirks. Smaller diff, lower risk for a first test. A native branch can come later if needed.

---

## 5. Isolated test target (Lead/devops)

- Create a **dedicated Kanban test project** (separate from agent-teams id=1) with:
  - `tools_config.tools_enabled = true`
  - `auto_allow_tiers` including `"read"` (so a read-tier tool auto-allows and the loop runs without a HITL halt).
- Point `LANGGRAPH_PROJECT_ID` at this test project for the run (the worker is single-project — this keeps the experiment off the live agent-teams board).

---

## 6. Smoke test procedure

1. Set `.env`: `LANGGRAPH_LLM_PROVIDER=openai`, `OPENAI_MODEL=gemini-2.0-flash`, `OPENAI_API_KEY=<gemini key>`, `OPENAI_BASE_URL=<gemini openai-compat url>`, `LANGGRAPH_PROJECT_ID=<test project id>`.
2. Rebuild + restart the langgraph container.
3. Create a **backend-role** task (`assigned_role=2`) on the test project whose brief **requires reading a file** (a read-tier tool) — e.g. "read file X and summarize its first section".
4. Let the worker pick it up (or set `run_mode` so it auto-picks).

### Proof-of-loop (the part that matters)
A clean DONE is **not enough** — a tool-less single-shot can also produce a DONE. Confirm the REAL loop ran:
- **Tool-invocation audit rows** exist for the task (≥1), AND/OR worker logs show `bind_tools` succeeded + ≥1 `tool_calls` round.
- The auditor verdict is recorded.
- Lead independently verifies via `curl GET /api/tasks/<id>` (don't trust the agent's self-report).

---

## 7. Teardown (zero main-process impact)

- Set `LANGGRAPH_PROJECT_ID` back to `1`, `LANGGRAPH_LLM_PROVIDER` back to `anthropic`.
- Delete / archive the test Kanban project.
- Delete the GCP project (or just let the trial lapse after 90 days).
- The `OPENAI_BASE_URL` code stays (inert when empty) — no runtime impact on the default anthropic path.
