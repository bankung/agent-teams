"""Tool-loop iteration limit (Kanban #981).

Hardcoded to 5 for V1 (locked decision #949 Q3 → A). The specialist node
loops at most `MAX_TOOL_LOOP_ITERATIONS` times — each iteration is one
`model.invoke()` round. On the (N+1)-th iteration the loop exits early
and the node returns `halt_reason='tool_loop_max_iterations: 5'`.

Future configurability (per-project override) is explicitly OUT of scope
for V1. Locking the limit here documents the intent and gives #981+ a
single grep-target if the policy changes.
"""

from __future__ import annotations

# Locked Kanban #949 Q3 → A. Per-project override deferred to a future slice.
MAX_TOOL_LOOP_ITERATIONS: int = 5

# Halt reason emitted by `nodes.backend_specialist_node` when the limit fires.
# Verbatim — pinned by the test `test_specialist_audit_writer_iter_limit_halt`.
TOOL_LOOP_HALT_REASON: str = f"tool_loop_max_iterations: {MAX_TOOL_LOOP_ITERATIONS}"
