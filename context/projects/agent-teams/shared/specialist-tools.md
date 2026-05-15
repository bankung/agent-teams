# Specialist tool layer — design doc (Kanban #949 AC1)

## 1. Goals + non-goals

**Goal:** Enable the LangGraph supervisor + specialist nodes to *act*, not just plan. When `auto_pickup=true` or `auto_headless=true` mode is set on a Kanban task, the LangGraph engine will invoke a tool layer that can:
- Edit and write files (within a working path boundary)
- Issue git commands (diff, status, commit — NOT push, branch deletion, or force ops)
- Run shell commands (with a narrow allowlist + timeout)
- Make HTTP GET/POST calls (with per-project host allowlist)

Each tool is tagged by permission tier. Per-project config gates whether calls in that tier auto-execute, halt for review, or are blocked outright.

**Non-goals:**
- AI-as-judge for safety (the engine does NOT recursively evaluate its own output for safety — humans must audit the allowlist + per-tool config).
- Push/merge/force operations on git (destructive VCS ops stay human-only, mirroring CLAUDE.md rule).
- UI-level tool rendering (Kanban task detail may surface a "tool calls" tab/section in a future UI phase; this design specifies the data shape only).
- Automatic async tool chaining (tools return results; the LLM sees them and can invoke more tools — no orchestration layer auto-repeating based on heuristics).
- LLM-internal tool-use format switching (we lock on langchain-core's `.tool_choice` / `.tools` contract, not OpenAI Function Calling vs Anthropic tool_use).

---

## 2. Tool registry

**Where it lives:** `langgraph/tools/` directory — a new Python package.
- `langgraph/tools/__init__.py` — exports `ToolRegistry`, `Tool`, `ToolResult`
- `langgraph/tools/registry.py` — the `ToolRegistry` class
- `langgraph/tools/` subdirs per tool family:
  - `langgraph/tools/fs/` — file_edit, file_write
  - `langgraph/tools/vcs/` — git_diff, git_status, git_commit
  - `langgraph/tools/shell/` — shell_run
  - `langgraph/tools/http/` — http_get, http_post

**Registry design:** Class-based, decorated approach.

```python
# langgraph/tools/registry.py

class ToolRegistry:
    def __init__(self):
        self._tools = {}
    
    def register(self, tool_cls):
        """Decorator to register a Tool subclass."""
        tool = tool_cls()
        self._tools[tool.name] = tool
        return tool_cls
    
    def get(self, name: str) -> 'Tool':
        """Retrieve a tool by name."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Unknown tool: {name}")
        return self._tools[name]
    
    def list(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())
    
    def to_langchain_tools(self) -> list[BaseTool]:
        """Convert registry to langchain tool_choice format."""
        return [t.to_langchain() for t in self._tools.values()]

GLOBAL_REGISTRY = ToolRegistry()
```

**Tool base class:**

```python
# langgraph/tools/__init__.py

from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field

class ToolInput(BaseModel):
    """Base schema for tool inputs (Pydantic v2)."""
    pass

class ToolResult(BaseModel):
    """Standard return envelope for all tools."""
    success: bool = Field(..., description="True if the tool succeeded")
    error_code: str | None = Field(None, description="e.g. 'permission_denied', 'timeout', 'not_found'")
    error_msg: str | None = Field(None, description="Human-readable error message")
    output: str | None = Field(None, description="Tool-specific output (stdout, file diff, API response, etc.)")
    retry_safe: bool = Field(True, description="True if retrying is safe (not idempotent for writes)")
    duration_ms: int = Field(0, description="Time elapsed (milliseconds)")

class PermissionTier(str, Enum):
    """Classification of tool per its blast radius."""
    READ = "read"           # Read-only — filesystem, git status, HTTP GET
    WRITE = "write"         # Modifies local state — file edits, git commits
    NETWORK = "network"     # External network calls — HTTP POST
    DESTRUCTIVE = "destructive"  # Cannot be safely undone — git force, rm -rf

class Tool(ABC):
    """Base class for all specialist tools."""
    
    name: str  # e.g., "file_edit"
    description: str  # LLM-facing description
    input_schema: type[ToolInput]  # Pydantic model
    permission_tier: PermissionTier
    timeout_sec: int = 30  # Default; tool can override
    
    @abstractmethod
    async def invoke(self, input_dict: dict[str, Any], context: InvokeContext) -> ToolResult:
        """Execute the tool. Must be async. Context carries config + audit sink."""
        pass
    
    def to_langchain(self) -> Any:
        """Convert to langchain BaseTool for /invoke."""
        # Wraps .invoke() in a langchain-compatible wrapper
        pass

class InvokeContext:
    """Metadata passed to every tool invocation."""
    task_id: int
    project_id: int
    working_path: str  # Filesystem boundary (e.g., /repo/_sessions/task-123/)
    project_config: ProjectToolConfig  # Per-project permission tiers
    audit_logger: AuditLogger  # To log each invocation
```

**Discovery mechanism:** Explicit register calls at module load time.

```python
# langgraph/tools/fs/file_edit.py

@GLOBAL_REGISTRY.register
class FileEditTool(Tool):
    name = "file_edit"
    description = "Edit a file using exact string matching and replacement..."
    input_schema = FileEditInput
    permission_tier = PermissionTier.WRITE
    ...
```

All tools are imported + registered in `langgraph/tools/__init__.py` at startup (Python module load order).

---

## 3. Per-tool spec sheet

### 3.1. `file_edit`
- **Signature:** `file_edit(file_path: str, old_string: str, new_string: str, dry_run: bool = False) -> ToolResult`
- **Permission tier:** `write`
- **Allowlist mechanism:** None. Enforces boundary check: `file_path` must be under `context.working_path`. Raises `ToolResult(success=False, error_code='boundary_violation')` if outside.
- **Failure modes:**
  - `boundary_violation`: path outside working_path
  - `not_found`: file does not exist
  - `match_ambiguous`: old_string matches 0 or >1 locations
  - `io_error`: permission denied (e.g., read-only FS)
- **Dry-run behavior:** `dry_run=true` returns a ToolResult with the prospective diff without writing. Useful for the engine to preview before committing.

### 3.2. `file_write`
- **Signature:** `file_write(file_path: str, content: str, dry_run: bool = False) -> ToolResult`
- **Permission tier:** `write`
- **Allowlist mechanism:** Boundary check (same as file_edit).
- **Failure modes:**
  - `boundary_violation`, `io_error`, `size_exceeded` (output_size_cap exceeded)
- **Dry-run behavior:** Returns `output` with first 200 chars of content + `"... [<actual_size> bytes total]"`.

### 3.3. `git_diff`
- **Signature:** `git_diff(rev_range: str = "HEAD", file_paths: list[str] | None = None) -> ToolResult`
- **Permission tier:** `read`
- **Allowlist mechanism:** None. Runs git within the repo root (inherited from working_path).
- **Failure modes:**
  - `git_error`: bad rev range, no repo, etc.
  - `timeout`: diff too large (caught by timeouts, below)
- **Dry-run behavior:** N/A (read-only tool).

### 3.4. `git_status`
- **Signature:** `git_status(porcelain: bool = True) -> ToolResult`
- **Permission tier:** `read`
- **Allowlist mechanism:** None.
- **Failure modes:** `git_error`
- **Dry-run behavior:** N/A.
- **Note:** `porcelain=true` returns machine-readable format; LLM can parse easily.

### 3.5. `git_commit`
- **Signature:** `git_commit(message: str, allow_empty: bool = False) -> ToolResult`
- **Permission tier:** `write`
- **Allowlist mechanism:** None. Blocks `git push`, `git rebase -i`, `--force-*` via command-line regex.
- **Failure modes:**
  - `nothing_to_commit`: clean working tree (unless allow_empty=true)
  - `git_error`: merge conflict, detached head, no user config
  - `forbidden_flag`: attempted `--force`, `--amend`, `--rebase` in message (literal string match, case-insensitive)
- **Dry-run behavior:** Runs `git commit --dry-run` (reports what would be staged/committed without writing).

### 3.6. `shell_run`
- **Signature:** `shell_run(command: str, timeout_sec: int | None = None, capture_output: bool = True) -> ToolResult`
- **Permission tier:** `write` (default; some commands may be reclassified as `read` if clearly read-only)
- **Allowlist mechanism:** 
  - Per-project config `tools_config.shell_allowlist: list[str]` — each entry is a shell command prefix (e.g., `"docker compose exec -T api pytest"`, `"npm run"`, `"pip install"`).
  - Tool checks: is `command.startswith(<allowlisted_prefix>)` true for at least one entry? If no, `error_code='command_not_allowed'`.
  - Denylist (hard, no override): blocks `rm`, `dd`, `kill -9`, `git push`, `git force-*`, `sudo`, `sudo su`, raw `psql -c` (detect via hook, below).
- **Failure modes:**
  - `command_not_allowed`: prefix not in allowlist
  - `blocked_command`: matched denylist
  - `timeout`: exceeded timeout_sec
  - `nonzero_exit`: exit code != 0; stderr captured in output
  - `size_exceeded`: output exceeds size cap
- **Dry-run behavior:** N/A (cannot safely dry-run arbitrary shell).
- **Hook synergy:** The existing `.claude/hooks/block-raw-sql-dml.ps1` blocks `python -c "... DELETE ..."` at the Bash tool level (applies to Claude Code sessions). The specialist tool layer will inherit similar logic: a `PreInvoke` hook (internal to langgraph container, separate from the harness hook) detects destructive SQL in `shell_run` and rejects it with `error_code='destructive_sql_blocked'`.

### 3.7. `http_get`
- **Signature:** `http_get(url: str, headers: dict[str, str] | None = None, timeout_sec: int = 30) -> ToolResult`
- **Permission tier:** `network` (or `read` — TBD: does `http_get` count as "read" or separate "network"? Design: separate, so per-project config can gate external network calls independently.)
- **Allowlist mechanism:**
  - Per-project config `tools_config.http_get_hosts: list[str]` — each entry is a hostname (e.g., `"localhost:8456"`, `"api.github.com"`, `"pypi.org"`).
  - Tool checks: `url.netloc` in allowlist? If no, `error_code='host_not_allowed'`.
  - Hard denylist: blocks `file://`, `localhost:9999` (arbitrary high port), internal IP ranges (10.0.0.0/8, 192.168.0.0/16, 127.0.0.0/8) unless explicitly allowlisted.
- **Failure modes:**
  - `host_not_allowed`: domain not in allowlist
  - `connection_error`: DNS, timeout, etc.
  - `http_error`: 4xx/5xx status (output includes response body first 500 chars)
  - `size_exceeded`: response body exceeds cap
- **Dry-run behavior:** N/A.

### 3.8. `http_post`
- **Signature:** `http_post(url: str, data: dict | str, headers: dict[str, str] | None = None, timeout_sec: int = 30) -> ToolResult`
- **Permission tier:** `network` (same reasoning as http_get)
- **Allowlist mechanism:** Same host allowlist as http_get.
- **Failure modes:** Same as http_get.
- **Idempotence note:** Not all POST calls are idempotent. `ToolResult.retry_safe` defaults to `false` for POST. The engine should NOT auto-retry a failed POST without user review.

---

## 4. Permission model

**Tier taxonomy:**

| Tier | Definition | Examples | Auto-allow default? |
|---|---|---|---|
| `read` | No state change; diagnostics only. | git_status, git_diff, http_get | ✓ yes |
| `write` | Modifies local state; reversible. | file_edit, file_write, git_commit, shell_run (if allowlisted) | ✗ no (per-project) |
| `network` | External calls; may have side effects. | http_get, http_post | ✗ no (per-project) |
| `destructive` | Cannot be safely undone. | (reserved; no tools in this tier yet — prevents future edge cases) | ✗ no (always blocked) |

**Per-project config schema** — new column on `projects` table (or a related `project_tool_config` table for normalization). Design: JSONB column `tools_config` on `projects`:

```json
{
  "auto_allow_tiers": ["read"],
  "halt_tiers": ["write", "network"],
  "blocked_tiers": ["destructive"],
  "shell_allowlist": [
    "docker compose exec -T api pytest",
    "npm run build",
    "docker run --rm -v"
  ],
  "http_get_hosts": [
    "localhost:8456",
    "api.github.com",
    "pypi.org"
  ],
  "http_post_hosts": [
    "localhost:8456"
  ],
  "fs_boundary": "/repo/_sessions/{task_id}/",
  "shell_timeout_default_sec": 30,
  "http_timeout_default_sec": 15,
  "output_size_cap_bytes": 100000
}
```

**Auto-allow vs halt-and-ask decision flow:**

1. Tool is invoked with `context.project_config`.
2. Tool checks its `permission_tier` against `project_config.auto_allow_tiers`.
3. If tier in auto_allow → execute tool directly.
4. If tier in halt_tiers → return `ToolResult(success=false, error_code='permission_halt', output="<tool call details>")` + add entry to `tool_calls` audit table (below). The engine sees the halt and can:
   - Emit a message to the LLM explaining the hold (e.g., "I wanted to run `npm run build` but it requires approval").
   - Return a `halt_reason='tool_permission_pending'` (a new halt reason type).
   - The poll loop (Kanban #852) marks the task BLOCKED + `is_pending=true`.
5. If tier in blocked_tiers → reject outright with `error_code='tier_blocked'`.

**Halt mechanism:** The `halt_reason` field in AgentState already exists (Kanban #850). We add new reasons:
- `halt_reason='tool_permission_pending'` — engine waiting for user to approve a halted tool call.
- Kanban task UI then shows: "Tool approval pending: <tool_name>(<args>) — Approve / Reject / Cancel task". On Approve, the task is re-queued with a new flag `resume_tool_approval={tool_id}` (Kanban #852b — deferred, out of scope for AC1).

---

## 5. Audit trail

**`tool_calls` table schema** — new table in `public.*` schema (mirrors `session_runs`, below).

```sql
CREATE TABLE tool_calls (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL,
  project_id BIGINT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
  
  invoked_at TIMESTAMP NOT NULL DEFAULT NOW(),
  tool_name VARCHAR(64) NOT NULL,  -- e.g. "file_edit", "http_post"
  
  -- Input (input and args together as JSONB for flexibilit)
  input_json JSONB NOT NULL,  -- The full input dict passed to tool.invoke()
  
  -- Output
  success BOOLEAN NOT NULL,
  error_code VARCHAR(64),  -- 'permission_halt', 'timeout', 'not_found', etc.
  error_msg TEXT,
  output_summary TEXT,  -- First 500 chars of tool output
  output_size_bytes BIGINT,  -- Full output size (even if truncated)
  
  -- Metadata
  permission_tier VARCHAR(32) NOT NULL,  -- 'read', 'write', 'network', 'destructive'
  duration_ms INT NOT NULL,
  retry_safe BOOLEAN NOT NULL,
  
  -- Soft-delete (optional; mirrors tasks.status convention)
  status SMALLINT NOT NULL DEFAULT 1,  -- 1=live, 0=soft-deleted
  
  FOREIGN KEY (project_id) REFERENCES projects(id),
  INDEX (task_id),
  INDEX (invoked_at)
);
```

**Pruning policy:** Keep all rows indefinitely (audit trail is immutable history). Compress old rows after 90 days if storage becomes a concern (Kanban #949b future decision).

**Kanban task-detail render** — new section in the task detail API response + UI:
- API: new nested array `tool_calls[]` on the task response (or a separate `/api/tasks/{id}/tool-calls` sub-route).
- UI: new "Tools" tab or collapsible section under task details, showing:
  - Tool name + timestamp
  - Input (prettified JSON)
  - Success/Error status + reason
  - Output (first 200 chars)
  - A link to "View full output" if output_size > 200

---

## 6. Sandbox + safety

**Subprocess timeout:**
- Global default: `30 sec` (configured per-project in `tools_config.shell_timeout_default_sec`).
- Per-tool override: each tool can declare `timeout_sec = <value>` (e.g., `git_commit` might use 10s, `http_post` might use 15s).
- Enforcement: `asyncio.wait_for(proc.communicate(), timeout=<timeout>)` in `shell_run` and all async tool invocations.
- On timeout: `ToolResult(success=false, error_code='timeout', output='Timeout after <N> seconds')`

**Filesystem boundary:**
- Each task gets a `working_path` (e.g., `/repo/_sessions/{task_id}/`).
- All file tools (`file_edit`, `file_write`) normalize the input path and check: `os.path.abspath(path).startswith(os.path.abspath(working_path))`.
- If path is outside the boundary → `ToolResult(..., error_code='boundary_violation')`.
- Implementation: use Python's `pathlib.Path.resolve()` + startswith check (guards against symlink escapes).
- Ragged-edge case: if `working_path` contains a symlink, resolve the target and check. Log a warning if the resolved boundary differs from the nominal boundary.

**Output size cap:**
- Global cap: `100_000 bytes` (configured per-project in `tools_config.output_size_cap_bytes`).
- Applied to: `shell_run` stdout+stderr, `http_get` response body, `file_edit`/`file_write` diff output.
- Enforcement: stream the output; on size exceeded, truncate with `"... output truncated (total <N> bytes)"` + set `error_code='size_exceeded'` (with `success=true` if the tool itself succeeded — the truncation is a presentation issue, not tool failure).
- For git operations (diffs, status), if diff exceeds cap, return the first N bytes + note. The engine can iterate with smaller rev ranges.

---

## 7. Error handling

**Standard error envelope:**

```python
class ToolResult(BaseModel):
    success: bool
    error_code: str | None
    error_msg: str | None
    output: str | None
    retry_safe: bool
    duration_ms: int
```

**Error code taxonomy:**

| Code | Tier | Meaning |
|---|---|---|
| `permission_halt` | Tool-level | Requested tier not in auto_allow; halting for user review. |
| `tier_blocked` | Tool-level | Tier in blocked_tiers; rejecting outright. |
| `command_not_allowed` | Tool-specific | shell_run — prefix not in allowlist. |
| `blocked_command` | Tool-specific | shell_run — matched hard denylist (rm, sudo, etc.). |
| `boundary_violation` | Tool-specific | file_* — path outside working_path. |
| `not_found` | Tool-specific | file_edit — old_string not found; file_write — parent dir missing. |
| `host_not_allowed` | Tool-specific | http_* — domain not in allowlist. |
| `timeout` | Tool-specific | Subprocess exceeded timeout. |
| `nonzero_exit` | Tool-specific | shell_run — exited with code != 0. |
| `size_exceeded` | Tool-specific | Output exceeded cap. |
| `git_error` | Tool-specific | git operation failed (bad rev, merge conflict, detached head, etc.). |
| `io_error` | Tool-specific | Permission denied, disk full, etc. |
| `connection_error` | Tool-specific | http_* — DNS, refused, timeout. |
| `http_error` | Tool-specific | http_* — 4xx/5xx status. |
| `match_ambiguous` | Tool-specific | file_edit — old_string not unique. |

**Tool-internal vs harness errors:**
- **Tool-internal:** The tool was invoked correctly, but the operation failed (e.g., file not found, git merge conflict). These are caught inside the tool and returned as `ToolResult(success=false, error_code=...)` (not exceptions).
- **Harness errors:** The tool invocation itself failed (e.g., invalid input schema, unknown tool name). These are exceptions, caught by the specialist node's exception handler + wrapped in `halt_reason='tool_invoke_error: <exception>'` (halts the whole task for review).

**How errors flow back to the LLM:**
- Successful tool calls: `ToolResult` is serialized to JSON, added to the AgentState `messages` as an `AIMessage` or `ToolMessage`.
- Permission halt: `ToolResult` with `error_code='permission_halt'` is serialized and added to messages. The LLM sees it and can reason about it (e.g., "I wanted to commit, but it requires approval. Let me output the commit message for manual review").
- Other errors: `ToolResult` added to messages. The LLM can see the error and either:
  - Ask the user for clarification (e.g., "File not found. Did you mean ...?").
  - Retry with a corrected input (e.g., fixed file path).
  - Or halt with `halt_reason='user_review_needed'` if the error suggests human intervention.
- Harness errors (exception during tool invocation): the specialist node catches the exception, logs it, and returns `halt_reason='tool_invoke_error: <exception>'` (does NOT retry — this is a fatal error in the tool layer itself).

---

## 8. Dry-run mode

**Per-tool dry-run flag:**
- Each tool has an optional `dry_run` input parameter.
- For write tools (file_edit, file_write, git_commit): `dry_run=true` returns a ToolResult showing what would happen, without side effects.
  - `file_edit(file_path, old, new, dry_run=true)` → returns the diff that would be applied.
  - `git_commit(message, dry_run=true)` → returns `git commit --dry-run` output.
- For read-only tools: `dry_run` is not applicable (N/A).

**Engine-level dry-run (future, Kanban #949b):**
- A task-level flag `mode=dry_run` (separate from `run_mode`; orthogonal to `auto_headless` / `auto_pickup`).
- When set, before actually invoking tools, the specialist node:
  1. Calls `tool.invoke(..., dry_run=true)` for each tool the LLM "wants" to invoke.
  2. Chains these dry-run results back to the LLM with a message: "Here's what would happen; approve or refine?"
  3. Only on explicit approval does the engine re-run with `dry_run=false`.
- Out of scope for AC1; deferred to a future slice.

**Output format for dry-run:**
```
Dry-run: file_edit(path='src/foo.py', old='...', new='...')
Will apply this diff:
--- src/foo.py
+++ src/foo.py
@@ -10,5 +10,5 @@
 old line
-old text
+new text
 another line
```

---

## 9. Integration with existing engine

**Where in langgraph/ the tool registry is wired:**

1. **`langgraph/graph.py` lifespan:** After the graph is compiled, load and initialize the `ToolRegistry` (already done by module import in `__init__.py`).
2. **`langgraph/tools/context.py` (new file):** Define `InvokeContext` class that carries task_id, project_id, working_path, project_config, audit_logger. Instantiate in the specialist node before invoking tools.
3. **`langgraph/nodes.py` specialist nodes:** Updated to use tools.
   - Old flow: `model.invoke(prompt) → final_result` (text only).
   - New flow: `model.invoke(prompt_with_tools_visible) → while not halt: tool_call = parse_result(); invoke_tool(); append_to_messages()` (tool-use loop).
4. **`langgraph/llm.py`:** The `make_chat_model()` factory returns a BaseChatModel configured for tool-use.
   - langchain-core's `model.bind_tools(tool_list)` attaches the tool schema to the model.
   - `invoke()` result carries either text OR a `tool_call` object (depending on model vendor's tool-use format).

**LLM sees the tools:**
- We pass the registry's `to_langchain_tools()` output to `model.bind_tools(...)`.
- The LLM receives tool descriptions in its context and can emit tool-use calls.
- For Anthropic: native `tool_use` block in responses.
- For OpenAI: `function_calling` block in responses.
- LangChain abstracts both to a unified `AIMessage` + `ToolMessage` format.

**LLM result flow back:**
```python
# langgraph/nodes.py — updated specialist node (pseudocode)

def backend_specialist_node(state: AgentState) -> dict:
    model = make_chat_model().bind_tools(GLOBAL_REGISTRY.to_langchain_tools())
    context = InvokeContext(
        task_id=state['task_id'],
        project_id=state['project_id'],
        working_path=get_working_path(state['task_id']),
        project_config=fetch_project_config(state['project_id']),
        audit_logger=get_audit_logger(),
    )
    
    messages = state['messages']
    max_tool_iters = 5  # Guard against infinite loops
    
    for _ in range(max_tool_iters):
        response = model.invoke(messages)
        
        if not response.tool_calls:
            # LLM decided to stop using tools; return final result
            final_result = response.content if isinstance(response.content, str) else str(response.content)
            return {
                'messages': [response],
                'final_result': final_result,
            }
        
        # Process tool calls
        messages.append(response)  # Add AI message to history
        for tool_call in response.tool_calls:
            tool_name = tool_call['name']
            tool_input = tool_call['args']
            
            try:
                tool = GLOBAL_REGISTRY.get(tool_name)
                result = await tool.invoke(tool_input, context)  # Async
            except Exception as e:
                result = ToolResult(success=False, error_code='invoke_error', error_msg=str(e))
            
            # Add tool result to messages
            messages.append(ToolMessage(
                tool_call_id=tool_call['id'],
                content=result.model_dump_json(),
                name=tool_name,
            ))
        
        # Check for halt
        if any(result.error_code == 'permission_halt' for result in ...):
            return {
                'messages': messages,
                'halt_reason': 'tool_permission_pending',
                'final_result': '(waiting for tool approval)',
            }
    
    # Max iterations reached
    return {
        'messages': messages,
        'halt_reason': 'tool_iteration_limit',
        'final_result': '(exceeded max tool calls)',
    }
```

---

## 10. Decomposition into 5 sub-tasks

These sub-tasks are siblings (can run in parallel after this design is locked):

### Sub-task 1: Tool batch 1 (FS + Git + Shell) — Backend implementation
- **Scope:** Implement `file_edit`, `file_write`, `git_diff`, `git_status`, `git_commit`, `shell_run` tools.
- **Files:** `langgraph/tools/fs/*.py`, `langgraph/tools/vcs/*.py`, `langgraph/tools/shell/*.py`, `langgraph/tools/registry.py`, `langgraph/tools/__init__.py`
- **AC outline:**
  - All 6 tools implemented + tested with unit tests covering happy path + error cases (boundary violation, timeout, allowlist rejection).
  - Registry decorator pattern works; all 6 tools registered at module load.
  - Pydantic input schemas for each tool (Pydantic v2, consistent with api/src).
  - ToolResult envelope matches spec (success, error_code, output, retry_safe, duration_ms).
  - Filesystem boundary enforcement verified (symlink escapes tested).
  - Shell allowlist + denylist working.
  - ~25 unit tests (4-5 per tool).

### Sub-task 2: Tool batch 2 (HTTP) — Backend implementation
- **Scope:** Implement `http_get` and `http_post` tools.
- **Files:** `langgraph/tools/http/*.py`
- **AC outline:**
  - Both tools implemented + tested.
  - Host allowlist + hard denylist (RFC1918 ranges) enforced.
  - Response body truncation on size cap working.
  - Status code / connection error handling in spec.
  - ~10 unit tests.

### Sub-task 3: Permission model + per-project config
- **Scope:** Schema update on `projects` table (add `tools_config` JSONB column via Alembic migration); permission decision logic in tool layer.
- **Files:** `api/src/alembic/versions/<new_migration>.py`, `langgraph/tools/context.py`, `langgraph/tools/permission.py`
- **AC outline:**
  - Alembic migration (adds `projects.tools_config` JSONB, server default=`{}`).
  - `ProjectToolConfig` Pydantic model (mirrors schema above).
  - Permission decision flow implemented: auto_allow vs halt_tiers vs blocked_tiers logic in context.
  - FastAPI endpoint to fetch + update project tool config (`GET/PATCH /api/projects/{id}/tools-config`).
  - ~12 unit tests.

### Sub-task 4: Audit trail (tool_calls table + Kanban UI)
- **Scope:** Create `tool_calls` table (Alembic migration); add ORM model; add API endpoint to list tool calls per task; optionally add UI tab/section.
- **Files:** `api/src/models/tool_call.py`, `api/src/routers/tool_calls.py` (new), `api/src/alembic/versions/<new_migration>.py`, audit logger implementation
- **AC outline:**
  - Alembic migration creates `tool_calls` table per spec.
  - ORM model + SQLAlchemy relationship to Task.
  - `GET /api/tasks/{id}/tool-calls` endpoint returns list + pagination.
  - Audit logger (context.audit_logger) writes to table on each tool invocation.
  - ~8 unit tests + smoke test (invoke a tool, verify audit row written).
  - UI (optional AC2): Kanban task detail shows "Tools" tab with tool call history. Deferred if time-boxed.

### Sub-task 5: Sandbox + safety (subprocess, filesystem, output cap enforcement) + live smoke
- **Scope:** Integration tests; verify subprocess timeout, boundary checks, output cap truncation all work end-to-end.
- **Files:** `langgraph/tests/test_tools_sandbox.py` (new), possibly `langgraph/tests/test_tools_integration.py`
- **AC outline:**
  - Subprocess timeout test: spawn a sleep command, verify it times out + returns `error_code='timeout'`.
  - Boundary violation test: attempt to write outside working_path, verify rejected.
  - Output cap test: invoke a tool with huge output, verify truncation.
  - Real smoke: wire tools into the `backend_specialist_node`; invoke the graph with a task that uses a tool; verify tool_calls audit rows are written.
  - ~15 unit tests + 3 smoke test scenarios.

**Rough AC counts per sub-task (total ~70 ACs across the 5 subtasks):**
1. Tool batch 1: ~25 ACs
2. Tool batch 2: ~10 ACs
3. Permission model: ~12 ACs
4. Audit trail: ~8 ACs
5. Sandbox + smoke: ~15 ACs

---

## 11. Open questions

1. **Symlink handling in working_path boundary check:** If the task's working_path contains a symlink (e.g., `/repo/_sessions/task-123 → /tmp/actual-task-123`), should we:
   - A) Resolve the symlink once at startup and store the resolved path?
   - B) Always resolve on each file access (safer but slower)?
   - C) Reject symlinks outright and fail if working_path is not a "real" directory?
   **Lead input needed:** What's the expected working_path pattern? Will tasks ever use symlinks?

2. **Project config migration / default values:** When adding the `tools_config` JSONB column, what should the server default be?
   - A) `{}` (empty — all tiers default to halt/blocked)?
   - B) `{"auto_allow_tiers": ["read"], "halt_tiers": ["write", "network"]}` (permissive default)?
   - C) Project scaffold must explicitly set it at creation time?
   **Lead input needed:** Should existing projects gain tool capability by default, or should it be opt-in?

3. **Tool-use loop iteration limit:** The specialist node loops `while tool_calls exist` with a max of 5 iterations. Should this be:
   - A) Hardcoded to 5?
   - B) Configurable per-project in `tools_config`?
   - C) A model-level setting (max LLM turns)?
   **Lead input needed:** Expected max tool-call depth for a typical task?

4. **HTTP allowlist scope:** Should `http_get_hosts` and `http_post_hosts` be separate, or one unified `http_hosts`?
   - A) Separate (more granular — POST only to trusted endpoints)?
   - B) Unified (simpler config)?
   **Lead input needed:** Any POST-only endpoints vs GET-only endpoints in the roadmap?

5. **Shell denylist as code vs config:** Today, the hard denylist (rm, sudo, kill) is hardcoded in the tool. Should it be:
   - A) Hardcoded (immutable, for security)?
   - B) Configurable per-project (risky but flexible)?
   - C) A shared `context/standards/` list?
   **Lead input needed:** Is the denylist universal, or do some projects legitimately need to run rm/sudo?

6. **ToolResult retry_safe semantics:** For POST calls that fail, `retry_safe=false` (idempotent unknown). Should the engine:
   - A) Never auto-retry POST errors (manual review always)?
   - B) Offer a "retry?" prompt to the LLM?
   - C) Provide a `idempotent=true` input param the LLM can set?
   **Lead input needed:** How should the engine handle a failed POST—halt, ask LLM, or something else?

7. **Dry-run mode at engine level:** The design defers engine-level dry-run to Kanban #949b. Should AC1 include a stub `mode=dry_run` flag on task model, or leave it entirely for later?
   - A) Add the flag stub now (no behavior, just schema)?
   - B) Defer entirely (add in #949b)?
   **Lead input needed:** Any immediate need for dry-run visibility in the UI / task model?

8. **Tool invocation from specialist nodes only:** Should tools be invokable ONLY from specialist nodes, or also directly from `graph.py:/invoke` for testing?
   - A) Specialist nodes only (strict separation)?
   - B) Direct invocation allowed for debugging?
   **Lead input needed:** How will devops/tester/reviewer specialists eventually invoke tools (once #853 real nodes land)? Same loop or different?

9. **Audit logger implementation detail:** Should audit writes be:
   - A) Synchronous (block tool invocation until audit row written)?
   - B) Async (fire-and-forget to a channel, best-effort)?
   - C) Buffered (batched writes at end of graph run)?
   **Lead input needed:** How important is audit tail latency? Any risk of missing rows if the container crashes mid-audit?

10. **Output truncation encoding:** When output is truncated due to size cap, should we:
   - A) Return raw truncated output (may be mid-UTF8 sequence)?
   - B) Detect and avoid mid-character truncation (slower)?
   - C) Base64-encode to avoid encoding issues?
   **Lead input needed:** Will the UI / LLM need to re-construct truncated output, or is the 100KB cap sufficient for most cases?

11. **Langgraph provider abstraction:** The design assumes `make_chat_model()` returns a lanchain-core `BaseChatModel` with `bind_tools()` support. Do:
   - A) Anthropic SDK + langchain-anthropic (supports tool_use)?
   - B) OpenAI SDK + langchain-openai (supports function_calling)?
   - C) Ollama via langchain-ollama (tool-use support uncertain)?
   **Lead input needed:** Should tool-use be a feature-flag (disabled for Ollama)? Or is Ollama never used for auto_headless tasks?

---

## Reference: Existing patterns in agent-teams

This design is informed by:

- **Session audit model** (`api/src/models/session.py:session_runs`) — schema with task_id FK, invoked_at, duration_ms, result summary. We mirror this for tool_calls.
- **Pydantic v2 conventions** (`context/standards/pydantic/v2-conventions.md`) — all tool input schemas use Pydantic BaseModel with Field descriptions.
- **FastAPI routing** (`context/standards/fastapi/routing.md`) — per-resource routers, 404/409 semantics, Header extraction. Tool config PATCH follows this pattern.
- **Soft-delete** (`context/standards/postgresql/soft-delete.md`) — tool_calls.status column mirrors tasks.status (1=live, 0=soft-deleted).
- **Hook pattern** (`.claude/hooks/block-raw-sql-dml.ps1`) — PreToolUse hooks detect and reject patterns. We borrow this for `shell_run` denylist (and optionally for destructive SQL in tool context).
- **CLAUDE.md golden rules** — DB writes via FastAPI only (tool_calls audit writes through a service, not raw SQL), no .claude/ writes from subagents (tools are part of langgraph container, not subagents, so different scope — but same discipline applies).

---

## Implementation sequence (recommended)

1. **Lock this design** (via Lead + user review). Clarify the 11 open questions.
2. **Sub-task 1 (FS + Git + Shell):** Foundational; unblocks Sub-task 5 (smoke tests).
3. **Sub-task 2 (HTTP):** Parallel with Sub-task 1.
4. **Sub-task 3 (Permission model):** Parallel; required for Sub-task 1 tests to verify allowlist logic.
5. **Sub-task 4 (Audit trail):** Parallel; required for smoke tests to verify audit rows.
6. **Sub-task 5 (Sandbox + smoke):** Last; depends on all prior sub-tasks to be coded + integrated.

**Estimated effort (rough):**
- Sub-task 1: 2-3 days (dev-backend)
- Sub-task 2: 1 day (dev-backend)
- Sub-task 3: 1 day (dev-backend)
- Sub-task 4: 1 day (dev-backend) + 0.5 days (dev-frontend UI, optional)
- Sub-task 5: 1 day (dev-tester + dev-backend)

Total: ~6-7 days for full tooling layer (AC1–AC5 of umbrella #949).
