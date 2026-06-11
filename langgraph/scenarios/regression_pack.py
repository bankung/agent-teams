"""T5 Regression Pack — end-to-end harness smoke against the live worker.

Invocation (inside agent-teams-langgraph container):
    python /repo/langgraph/scenarios/regression_pack.py
    python /repo/langgraph/scenarios/regression_pack.py --api http://api:8456
    python /repo/langgraph/scenarios/regression_pack.py --project 661
    python /repo/langgraph/scenarios/regression_pack.py --only S1,S3
    python /repo/langgraph/scenarios/regression_pack.py --timeout-per-scenario 900
    python /repo/langgraph/scenarios/regression_pack.py --dry-run

Scenarios (run sequentially — the worker is serial):

  S1  read-tool loop         assigned_role=2, calls git_status, EXPECT ps=5
  S2  two-tool multi-turn    assigned_role=2, calls git_status + git_diff, EXPECT ps=5
  S3  no-tool answer         assigned_role=2, direct definition, EXPECT ps=5
  S4  HITL decision          no role, "HITL demo —" prefix, EXPECT ps=4 pause then
                              operator answers a decision option, EXPECT ps=5 DONE
  S5  write-tier gate        assigned_role=2, file_write tool, EXPECT ps=4 halt with
                              halt_reason='decision' and question_payload NON-NULL (its
                              text is LLM-authored — only presence is contractual);
                              pack answers 'reject'; task then reaches ps=4
                              halt_reason='operator_rejected' (final).
  S6  missing-role escalation no role, git_status wording, EXPECT ps=4 via
                              general fallback -> auditor escalate path

Design notes:
  - Answer for HITL (S4/S5) is submitted via PATCH with new_answer (no separate /answer
    endpoint).
  - S5 real flow (empirically verified 2026-06-10, task #2143):
      1. Worker calls file_write -> check_permission returns HALT.
      2. Worker raises halt_for_review, records tool-call row with success=False,
         error_code='halt_for_review', permission_decision='halt'.
      3. Task halts ps=4, halt_reason='decision', question_payload asks human
         authorization for the file_write operation.
      4. Pack answers 'reject' via PATCH new_answer.
      5. Worker resumes, sees 'reject' -> sets halt_reason='operator_rejected',
         status_change_reason='Halted for review: tool_permission_review: file_write
         tier=write'. Task stays ps=4 (non-resumable terminal state).
      6. Target file is never created at any point.
  - S4 title MUST start with exactly "HITL demo —" (em-dash U+2014) — nodes.py L1017.
  - S4 decision options are ["staging", "prod"]; answer must be one of those exactly.
  - S4/S5 post-answer polling: ps=4 with halt in {question, decision} is NOT terminal
    while the answer is pending consumption — keep polling until ps changes or halt
    changes to a non-HITL value.
  - S6: routes to general_node -> halt_reason="error"; auditor sees non-clean state
    (halt_reason != None) -> LLM path -> classifies ESCALATE -> request_user_input fires
    -> ps=4 with halt_reason in {question, decision}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_API = "http://api:8456"
DEFAULT_PROJECT = 661
DEFAULT_TIMEOUT = 900  # seconds per scenario
POLL_INTERVAL = 12     # seconds between GET polls
RUN_PREFIX = "[T5RP "  # used to find and clean prior-run tasks

# Proven description from task #1953 (decisions.md 2026-06-10 evidence).
_GIT_STATUS_DESC = (
    "Call the git_status tool to inspect the repository working tree, then reply in ONE sentence "
    "stating whether there are uncommitted changes. "
    "You MUST actually invoke the git_status tool — do not answer from assumption."
)

# Terminal process_status codes (worker won't touch a task in these states).
_TERMINAL_PS = {4, 5, 6}

# HITL halt reasons that the worker DOES auto-resume on (worker._needs_resume check).
_HITL_HALT_REASONS = {"question", "decision"}


# ---------------------------------------------------------------------------
# Run ID
# ---------------------------------------------------------------------------

def _run_id() -> str:
    """Short time-based run id safe for task titles and filenames."""
    ts = datetime.now(timezone.utc)
    return ts.strftime("%m%dT%H%M")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _headers(project_id: int) -> dict[str, str]:
    return {
        "X-Project-Id": str(project_id),
        "Content-Type": "application/json",
    }


def _get(client: httpx.Client, api: str, project_id: int, path: str, **kwargs: Any) -> httpx.Response:
    """GET with small retry on transient failures."""
    url = f"{api}{path}"
    for attempt in range(3):
        try:
            resp = client.get(url, headers=_headers(project_id), **kwargs)
            return resp
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise
            print(f"  [retry {attempt+1}] GET {path}: {exc}")
            time.sleep(3)
    raise RuntimeError("unreachable")


def _post(client: httpx.Client, api: str, project_id: int, path: str, body: dict[str, Any]) -> httpx.Response:
    url = f"{api}{path}"
    return client.post(url, headers=_headers(project_id), json=body)


def _patch(client: httpx.Client, api: str, project_id: int, path: str, body: dict[str, Any]) -> httpx.Response:
    url = f"{api}{path}"
    return client.request("PATCH", url, headers=_headers(project_id), json=body)


def _delete(client: httpx.Client, api: str, project_id: int, task_id: int) -> None:
    url = f"{api}/api/tasks/{task_id}"
    try:
        resp = client.delete(url, headers=_headers(project_id))
        # 204 = deleted, 404 = already gone — both OK (idempotent)
        if resp.status_code not in (204, 404):
            print(f"  WARNING: DELETE task {task_id} returned {resp.status_code}")
    except httpx.HTTPError as exc:
        print(f"  WARNING: DELETE task {task_id} HTTP error: {exc}")


# ---------------------------------------------------------------------------
# Pre-clean: find and soft-delete prior T5RP tasks
# ---------------------------------------------------------------------------

def _find_prior_t5rp_tasks(client: httpx.Client, api: str, project_id: int) -> list[int]:
    """Return ids of all tasks whose title starts with RUN_PREFIX on this project.

    The list endpoint is windowed at max 500. We page with offset until we get
    fewer than limit rows. For DONE tasks (ps=5) the default order uses id ASC
    which covers everything. We also use include_cancelled=true to catch
    cancelled T5RP tasks.
    """
    ids: list[int] = []
    seen: set[int] = set()
    limit = 500

    # Sweep non-DONE tasks (all active statuses) with offset pagination.
    for offset in range(0, 100_000, limit):
        resp = _get(client, api, project_id,
                    f"/api/tasks?limit={limit}&offset={offset}&include_cancelled=true")
        if resp.status_code != 200:
            print(f"  WARNING: list tasks returned {resp.status_code}")
            break
        batch = resp.json()
        for t in batch:
            if t["id"] not in seen and t.get("title", "").startswith(RUN_PREFIX):
                ids.append(t["id"])
                seen.add(t["id"])
        if len(batch) < limit:
            break

    # Sweep DONE tasks with done_lane keyset pagination.
    before_updated_at: str | None = None
    before_id: int | None = None
    while True:
        cursor = ""
        if before_updated_at and before_id:
            cursor = f"&before_updated_at={before_updated_at}&before_id={before_id}"
        resp = _get(client, api, project_id,
                    f"/api/tasks?limit={limit}&process_status=5&order=done_lane{cursor}")
        if resp.status_code != 200:
            break
        batch = resp.json()
        for t in batch:
            if t["id"] not in seen and t.get("title", "").startswith(RUN_PREFIX):
                ids.append(t["id"])
                seen.add(t["id"])
        if len(batch) < limit:
            break
        last = batch[-1]
        before_updated_at = last.get("updated_at")
        before_id = last.get("id")
        if not before_updated_at or not before_id:
            break

    return ids


def pre_clean(client: httpx.Client, api: str, project_id: int) -> None:
    """Soft-delete all prior T5RP tasks on this project."""
    ids = _find_prior_t5rp_tasks(client, api, project_id)
    if not ids:
        print("  pre-clean: no prior T5RP tasks found")
        return
    print(f"  pre-clean: found {len(ids)} prior T5RP task(s): {ids}")
    for tid in ids:
        _delete(client, api, project_id, tid)
    print(f"  pre-clean: deleted {len(ids)} task(s)")


# ---------------------------------------------------------------------------
# Task creation helper
# ---------------------------------------------------------------------------

def _create_task(
    client: httpx.Client,
    api: str,
    project_id: int,
    title: str,
    description: str,
    *,
    assigned_role: int | None = None,
) -> int:
    """POST /api/tasks and return the new task id."""
    body: dict[str, Any] = {
        "project_id": project_id,
        "title": title,
        "description": description,
        "run_mode": "auto_pickup",
        "task_kind": "ai",
        "task_type": "chore",
        "priority": 2,
    }
    if assigned_role is not None:
        body["assigned_role"] = assigned_role
    resp = _post(client, api, project_id, "/api/tasks", body)
    if resp.status_code != 201:
        raise RuntimeError(f"POST /api/tasks failed {resp.status_code}: {resp.text[:300]}")
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Poll until terminal or timeout
# ---------------------------------------------------------------------------

def _poll_until_terminal(
    client: httpx.Client,
    api: str,
    project_id: int,
    task_id: int,
    timeout_sec: float,
    stop_early: set[int] | None = None,
) -> dict[str, Any] | None:
    """Poll GET /api/tasks/{id} until process_status in terminal set or timeout.

    `stop_early` allows stopping on a non-terminal ps (e.g. ps=4 for HITL check).
    Returns the task dict on match; None on timeout.
    """
    check = _TERMINAL_PS | (stop_early or set())
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            resp = _get(client, api, project_id, f"/api/tasks/{task_id}")
        except httpx.HTTPError as exc:
            print(f"  poll: GET task {task_id} error: {exc}; retrying...")
            time.sleep(POLL_INTERVAL)
            continue
        if resp.status_code != 200:
            print(f"  poll: GET task {task_id} returned {resp.status_code}; retrying...")
            time.sleep(POLL_INTERVAL)
            continue
        task = resp.json()
        ps = task.get("process_status")
        if ps in check:
            return task
        time.sleep(POLL_INTERVAL)
    return None


def _poll_post_answer(
    client: httpx.Client,
    api: str,
    project_id: int,
    task_id: int,
    timeout_sec: float,
) -> dict[str, Any] | None:
    """Poll after submitting a HITL answer, treating ps=4+HITL-halt as non-terminal.

    The worker consumes the answer on its next poll cycle (~10s).  During that
    window the task sits at ps=4, halt in {question, decision} — the same shape
    as before we answered.  Standard _poll_until_terminal would stop immediately
    on ps=4, producing a false failure.

    This helper keeps polling until:
      - ps changes away from 4  (worker resumed: expect 5 or a new halt)
      - halt changes to a non-HITL value  (e.g. 'operator_rejected', 'error')
      - timeout
    Returns the task dict on any of those conditions; None on timeout.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            resp = _get(client, api, project_id, f"/api/tasks/{task_id}")
        except httpx.HTTPError as exc:
            print(f"  poll_post_answer: GET task {task_id} error: {exc}; retrying...")
            time.sleep(POLL_INTERVAL)
            continue
        if resp.status_code != 200:
            print(f"  poll_post_answer: GET task {task_id} returned {resp.status_code}; retrying...")
            time.sleep(POLL_INTERVAL)
            continue
        task = resp.json()
        ps = task.get("process_status")
        halt = task.get("halt_reason")
        # If the task has left the ps=4+HITL-halt holding pattern, we're done.
        if ps != 4:
            return task
        if halt not in _HITL_HALT_REASONS:
            return task
        time.sleep(POLL_INTERVAL)
    return None


# ---------------------------------------------------------------------------
# Tool-calls audit helper
# ---------------------------------------------------------------------------

def _get_tool_calls(
    client: httpx.Client, api: str, project_id: int, task_id: int
) -> list[dict[str, Any]] | None:
    """GET /api/tasks/{id}/tool-calls. Returns None if endpoint absent (404/410)."""
    try:
        resp = _get(client, api, project_id, f"/api/tasks/{task_id}/tool-calls")
    except httpx.HTTPError:
        return None
    if resp.status_code in (200,):
        return resp.json()
    return None


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

class ScenarioResult:
    def __init__(self, name: str, task_id: int | None, passed: bool, notes: str, elapsed: float) -> None:
        self.name = name
        self.task_id = task_id
        self.passed = passed
        self.notes = notes
        self.elapsed = elapsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.name,
            "task_id": self.task_id,
            "passed": self.passed,
            "notes": self.notes,
            "elapsed_sec": round(self.elapsed, 1),
        }


def run_s1(client: httpx.Client, api: str, project_id: int, run_id: str, timeout: float) -> ScenarioResult:
    """S1: read-tool loop — git_status, expect ps=5."""
    name = "S1"
    t0 = time.monotonic()
    task_id: int | None = None
    try:
        title = f"{RUN_PREFIX}{run_id}] S1 read-tool loop"
        task_id = _create_task(
            client, api, project_id, title, _GIT_STATUS_DESC, assigned_role=2
        )
        print(f"  S1: created task {task_id}")

        task = _poll_until_terminal(client, api, project_id, task_id, timeout)
        elapsed = time.monotonic() - t0

        if task is None:
            return ScenarioResult(name, task_id, False, f"timeout after {timeout}s", elapsed)

        ps = task.get("process_status")
        halt = task.get("halt_reason")

        checks: list[str] = []
        ok = True

        if ps != 5:
            ok = False
            checks.append(f"FAIL: expected ps=5 got ps={ps}")
        else:
            checks.append("ps=5 PASS")

        if halt is not None:
            ok = False
            checks.append(f"FAIL: expected halt=None got {halt!r}")
        else:
            checks.append("halt=None PASS")

        # Optional: audit row count.
        tcs = _get_tool_calls(client, api, project_id, task_id)
        if tcs is not None:
            git_rows = [tc for tc in tcs if tc.get("tool_name") == "git_status"]
            if len(git_rows) >= 1:
                checks.append(f"tool-calls: {len(git_rows)} git_status row(s) PASS")
            else:
                ok = False
                checks.append(f"FAIL: expected >=1 git_status tool-call row, got {len(git_rows)}")
        else:
            checks.append("tool-calls endpoint unavailable — skipped audit-row assert")

        return ScenarioResult(name, task_id, ok, "; ".join(checks), elapsed)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ScenarioResult(name, task_id, False, f"exception: {exc}", elapsed)


def run_s2(client: httpx.Client, api: str, project_id: int, run_id: str, timeout: float) -> ScenarioResult:
    """S2: two-tool multi-turn — git_status + git_diff, expect ps=5."""
    name = "S2"
    t0 = time.monotonic()
    task_id: int | None = None
    try:
        title = f"{RUN_PREFIX}{run_id}] S2 two-tool multi-turn"
        desc = (
            "First call the git_status tool to inspect the working tree, "
            "then call the git_diff tool to see the actual diff. "
            "After BOTH tool calls, reply in ONE sentence summarising the uncommitted changes. "
            "You MUST invoke both git_status and git_diff — do not answer from assumption."
        )
        task_id = _create_task(client, api, project_id, title, desc, assigned_role=2)
        print(f"  S2: created task {task_id}")

        task = _poll_until_terminal(client, api, project_id, task_id, timeout)
        elapsed = time.monotonic() - t0

        if task is None:
            return ScenarioResult(name, task_id, False, f"timeout after {timeout}s", elapsed)

        ps = task.get("process_status")
        halt = task.get("halt_reason")

        checks: list[str] = []
        ok = True

        if ps != 5:
            ok = False
            checks.append(f"FAIL: expected ps=5 got ps={ps}")
        else:
            checks.append("ps=5 PASS")

        if halt is not None:
            ok = False
            checks.append(f"FAIL: expected halt=None got {halt!r}")
        else:
            checks.append("halt=None PASS")

        tcs = _get_tool_calls(client, api, project_id, task_id)
        if tcs is not None:
            tools_used = {tc.get("tool_name") for tc in tcs}
            if len(tcs) >= 2:
                checks.append(f"tool-calls: {len(tcs)} rows (tools: {sorted(tools_used)}) PASS")
            else:
                ok = False
                checks.append(f"FAIL: expected >=2 tool-call rows, got {len(tcs)} ({sorted(tools_used)})")
        else:
            checks.append("tool-calls endpoint unavailable — skipped audit-row assert")

        return ScenarioResult(name, task_id, ok, "; ".join(checks), elapsed)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ScenarioResult(name, task_id, False, f"exception: {exc}", elapsed)


def run_s3(client: httpx.Client, api: str, project_id: int, run_id: str, timeout: float) -> ScenarioResult:
    """S3: no-tool answer — definition of HTTP 404, expect ps=5."""
    name = "S3"
    t0 = time.monotonic()
    task_id: int | None = None
    try:
        title = f"{RUN_PREFIX}{run_id}] S3 no-tool answer"
        desc = "Answer in ONE sentence: what is HTTP 404? Do not call any tools."
        task_id = _create_task(client, api, project_id, title, desc, assigned_role=2)
        print(f"  S3: created task {task_id}")

        task = _poll_until_terminal(client, api, project_id, task_id, timeout)
        elapsed = time.monotonic() - t0

        if task is None:
            return ScenarioResult(name, task_id, False, f"timeout after {timeout}s", elapsed)

        ps = task.get("process_status")
        halt = task.get("halt_reason")

        checks: list[str] = []
        ok = True

        if ps != 5:
            ok = False
            checks.append(f"FAIL: expected ps=5 got ps={ps}")
        else:
            checks.append("ps=5 PASS")

        if halt is not None:
            ok = False
            checks.append(f"FAIL: expected halt=None got {halt!r}")
        else:
            checks.append("halt=None PASS")

        final = task.get("status_change_reason") or ""
        if "404" in final or "not found" in final.lower() or "resource" in final.lower():
            checks.append("final_result mentions 404/not-found PASS")
        else:
            # Soft warning — the answer may phrase it differently; don't fail on text alone.
            checks.append(f"NOTE: final_result may not mention 404 ({final[:80]!r})")

        return ScenarioResult(name, task_id, ok, "; ".join(checks), elapsed)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ScenarioResult(name, task_id, False, f"exception: {exc}", elapsed)


def run_s4(client: httpx.Client, api: str, project_id: int, run_id: str, timeout: float) -> ScenarioResult:
    """S4: HITL decision — title starts with 'HITL demo —', no role.

    Expected flow:
      1. Worker picks up task, routes to general_node (no assigned_role).
      2. general_node sees "HITL demo —" prefix (HITL_DEMO_ENABLED=1), calls
         request_user_input({"question":"Deploy to staging or prod?","options":["staging","prod"]}).
      3. Task halts: ps=4, halt_reason in {question, decision}, question_payload non-null.
      4. Pack submits answer "staging" via PATCH new_answer.
      5. Worker resumes; general_node returns {"final_result": "decision resolved: staging"}.
      6. Task reaches ps=5 DONE.
    """
    name = "S4"
    t0 = time.monotonic()
    task_id: int | None = None
    try:
        # Title MUST start with this exact em-dash prefix (nodes.py L1017).
        title = f"{RUN_PREFIX}{run_id}] HITL demo — S4 decision test"
        desc = (
            "HITL demo — this task exercises the HITL decision loop. "
            "Deploy target: staging or prod?"
        )
        # No assigned_role — routes to general_node which detects the prefix.
        task_id = _create_task(client, api, project_id, title, desc)
        print(f"  S4: created task {task_id}")

        # Phase 1: wait for HITL halt (ps=4, halt in {question, decision}).
        task = _poll_until_terminal(
            client, api, project_id, task_id, timeout, stop_early={4}
        )
        elapsed_phase1 = time.monotonic() - t0

        if task is None:
            return ScenarioResult(name, task_id, False, f"phase1 timeout after {timeout}s", time.monotonic() - t0)

        ps = task.get("process_status")
        halt = task.get("halt_reason")
        qp = task.get("question_payload")

        checks: list[str] = []
        ok = True

        if ps != 4:
            ok = False
            checks.append(f"FAIL phase1: expected ps=4 got ps={ps}")
            return ScenarioResult(name, task_id, False, "; ".join(checks), time.monotonic() - t0)

        checks.append(f"phase1: ps=4 PASS (halt={halt!r})")

        if halt not in _HITL_HALT_REASONS:
            ok = False
            checks.append(f"FAIL: halt_reason {halt!r} not in {_HITL_HALT_REASONS}")
        else:
            checks.append(f"halt={halt!r} PASS")

        if not qp:
            ok = False
            checks.append("FAIL: question_payload is null")
        else:
            options = qp.get("options") or []
            checks.append(f"question_payload non-null, options={options}")

        if not ok:
            return ScenarioResult(name, task_id, False, "; ".join(checks), time.monotonic() - t0)

        # Phase 2: submit answer "staging" via PATCH new_answer.
        # validate_answer: options=["staging","prod"], answer="staging" -> valid.
        answer_val = "staging"
        patch_resp = _patch(
            client, api, project_id, f"/api/tasks/{task_id}",
            {"new_answer": answer_val, "new_answer_by": "t5rp-operator"},
        )
        if patch_resp.status_code != 200:
            ok = False
            checks.append(f"FAIL: PATCH new_answer returned {patch_resp.status_code}: {patch_resp.text[:200]}")
            return ScenarioResult(name, task_id, False, "; ".join(checks), time.monotonic() - t0)
        checks.append(f"PATCH new_answer={answer_val!r}: 200 PASS")

        # Phase 3: wait for DONE (ps=5) after resume.
        # Use _poll_post_answer so that ps=4+decision (answer pending consumption)
        # is NOT treated as terminal — the worker needs its next ~10s poll cycle.
        remaining = timeout - (time.monotonic() - t0)
        if remaining < 30:
            remaining = 30
        task2 = _poll_post_answer(client, api, project_id, task_id, remaining)
        elapsed = time.monotonic() - t0

        if task2 is None:
            ok = False
            checks.append(f"FAIL phase3: timeout waiting for ps=5 after resume")
            return ScenarioResult(name, task_id, False, "; ".join(checks), elapsed)

        ps2 = task2.get("process_status")
        halt2 = task2.get("halt_reason")
        final = task2.get("status_change_reason") or ""

        if ps2 != 5:
            ok = False
            checks.append(f"FAIL phase3: expected ps=5 got ps={ps2} halt={halt2!r}")
        else:
            checks.append(f"phase3: ps=5 PASS (final={final[:80]!r})")

        # general_node returns "decision resolved: staging" — verify it.
        if final.startswith("decision resolved"):
            checks.append(f"final_result starts with 'decision resolved' PASS ({final[:80]!r})")
        elif "decision resolved" in final or "staging" in final:
            checks.append(f"NOTE: final_result={final[:80]!r} (expected 'decision resolved: staging')")
        else:
            checks.append(f"NOTE: final_result={final[:80]!r} (expected 'decision resolved: staging')")

        return ScenarioResult(name, task_id, ok, "; ".join(checks), elapsed)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ScenarioResult(name, task_id, False, f"exception: {exc}", elapsed)


def run_s5(client: httpx.Client, api: str, project_id: int, run_id: str, timeout: float) -> ScenarioResult:
    """S5: write-tier gate — file_write tool halted, then operator rejects.

    Project 661 has halt_tiers=["write","network","destructive"].
    file_write is tier=write -> check_permission returns HALT.

    Real observed flow (task #2143, 2026-06-10):
      Phase 1: ps=4, halt_reason='decision', question_payload NON-NULL (its .question
               text is LLM/auditor-authored paraphrase — only presence is contractual;
               specific words like "file_write" or "authorize" may not appear).
               Tool-calls row: success=False, error_code='halt_for_review',
               permission_decision='halt'.  Target file NOT created.
      Phase 2: Pack answers 'reject' via PATCH new_answer.
      Post-reject terminal: ps=4, halt_reason='operator_rejected',
               status_change_reason='Halted for review: tool_permission_review:
               file_write tier=write'.  Target file still NOT created.
    """
    name = "S5"
    t0 = time.monotonic()
    task_id: int | None = None
    target_file = f"/repo/_scratch/t5rp-{run_id}.txt"
    try:
        title = f"{RUN_PREFIX}{run_id}] S5 write-tier gate"
        desc = (
            f"Use the file_write tool to create the file {target_file} "
            f"with the content 'gate-test'. You MUST call the file_write tool."
        )
        task_id = _create_task(client, api, project_id, title, desc, assigned_role=2)
        print(f"  S5: created task {task_id}")

        # Phase 1: wait for HITL halt (ps=4, halt='decision').
        task = _poll_until_terminal(
            client, api, project_id, task_id, timeout, stop_early={4}
        )
        elapsed_phase1 = time.monotonic() - t0

        if task is None:
            return ScenarioResult(name, task_id, False, f"phase1 timeout after {timeout}s", time.monotonic() - t0)

        ps = task.get("process_status")
        halt = task.get("halt_reason") or ""
        qp = task.get("question_payload") or {}

        checks: list[str] = []
        ok = True

        # a. ps=4 AND halt='decision' AND question_payload non-null (word-match is NOTE only).
        if ps != 4:
            ok = False
            checks.append(f"FAIL phase1: expected ps=4 got ps={ps}")
            return ScenarioResult(name, task_id, False, "; ".join(checks), time.monotonic() - t0)
        checks.append("phase1: ps=4 PASS")

        if halt != "decision":
            ok = False
            checks.append(f"FAIL: expected halt='decision' got {halt!r}")
        else:
            checks.append("halt='decision' PASS")

        if not qp:
            ok = False
            checks.append("FAIL: question_payload is null/empty")
        else:
            checks.append("question_payload non-null PASS")
            question_text = qp.get("question") or ""
            if "file_write" in question_text.lower() or "authorize" in question_text.lower():
                checks.append(f"question_payload mentions file_write/authorize PASS ({question_text[:80]!r})")
            else:
                checks.append(
                    f"NOTE: question_payload does not mention file_write/authorize "
                    f"(LLM-authored wording varies): {question_text[:80]!r}"
                )

        # b. tool-calls has file_write row with success=False, error_code='halt_for_review',
        #    permission_decision='halt'.
        tcs = _get_tool_calls(client, api, project_id, task_id)
        if tcs is not None:
            fw_rows = [tc for tc in tcs if tc.get("tool_name") == "file_write"]
            if fw_rows:
                fw = fw_rows[0]
                if not fw.get("success", True):
                    checks.append("tool-calls: file_write success=False PASS")
                else:
                    ok = False
                    checks.append("FAIL: tool-calls file_write success is not False")
                if fw.get("error_code") == "halt_for_review":
                    checks.append("tool-calls: error_code='halt_for_review' PASS")
                else:
                    ok = False
                    checks.append(f"FAIL: expected error_code='halt_for_review' got {fw.get('error_code')!r}")
                if fw.get("permission_decision") == "halt":
                    checks.append("tool-calls: permission_decision='halt' PASS")
                else:
                    ok = False
                    checks.append(f"FAIL: expected permission_decision='halt' got {fw.get('permission_decision')!r}")
            else:
                ok = False
                checks.append("FAIL: no file_write row in tool-calls")
        else:
            checks.append("tool-calls endpoint unavailable — skipped audit-row assert")

        # c. target file does NOT exist (gate held).
        if not os.path.exists(target_file):
            checks.append(f"target file not created (gate held) PASS")
        else:
            ok = False
            checks.append(f"FAIL: target file {target_file!r} exists — gate did not hold!")

        if not ok:
            return ScenarioResult(name, task_id, False, "; ".join(checks), time.monotonic() - t0)

        # d. Answer 'reject' and poll to post-reject terminal state.
        patch_resp = _patch(
            client, api, project_id, f"/api/tasks/{task_id}",
            {"new_answer": "reject", "new_answer_by": "t5rp-operator"},
        )
        if patch_resp.status_code != 200:
            ok = False
            checks.append(f"FAIL: PATCH new_answer='reject' returned {patch_resp.status_code}: {patch_resp.text[:200]}")
            return ScenarioResult(name, task_id, False, "; ".join(checks), time.monotonic() - t0)
        checks.append("PATCH new_answer='reject': 200 PASS")

        # Post-reject: use _poll_post_answer to skip past the answer-pending window
        # (ps=4+decision while worker hasn't consumed the answer yet).
        # Terminal: ps=4 halt_reason='operator_rejected' (non-resumable).
        remaining = timeout - (time.monotonic() - t0)
        if remaining < 30:
            remaining = 30
        task2 = _poll_post_answer(client, api, project_id, task_id, remaining)
        elapsed = time.monotonic() - t0

        if task2 is None:
            ok = False
            checks.append("FAIL phase2: timeout waiting for post-reject terminal state")
            return ScenarioResult(name, task_id, False, "; ".join(checks), elapsed)

        ps2 = task2.get("process_status")
        halt2 = task2.get("halt_reason") or ""
        scr2 = task2.get("status_change_reason") or ""

        # Observed terminal: ps=4, halt='operator_rejected'.
        if ps2 == 4 and halt2 == "operator_rejected":
            checks.append(f"phase2: ps=4 halt='operator_rejected' PASS (scr={scr2[:80]!r})")
        else:
            ok = False
            checks.append(f"FAIL phase2: expected ps=4 halt='operator_rejected', got ps={ps2} halt={halt2!r}")

        # File must still not exist after reject.
        if not os.path.exists(target_file):
            checks.append("target file still not created after reject PASS")
        else:
            ok = False
            checks.append(f"FAIL: target file {target_file!r} exists after reject!")

        return ScenarioResult(name, task_id, ok, "; ".join(checks), elapsed)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ScenarioResult(name, task_id, False, f"exception: {exc}", elapsed)


def run_s6(client: httpx.Client, api: str, project_id: int, run_id: str, timeout: float) -> ScenarioResult:
    """S6: missing-role escalation — regression of incident #2130.

    No assigned_role -> supervisor -> general_node (no HITL-demo prefix) ->
    returns halt_reason='error'. Auditor sees halt != None (non-clean) ->
    LLM path -> classifies ESCALATE -> request_user_input fires ->
    task halts ps=4, halt_reason in {question, decision}, question_payload non-null.

    Assert: ps=4, halt non-null, halt in {question, decision} (HITL escalate path).
    """
    name = "S6"
    t0 = time.monotonic()
    task_id: int | None = None
    try:
        title = f"{RUN_PREFIX}{run_id}] S6 missing-role escalation"
        # Use the proven S1 description but no assigned_role.
        task_id = _create_task(client, api, project_id, title, _GIT_STATUS_DESC)
        print(f"  S6: created task {task_id}")

        task = _poll_until_terminal(client, api, project_id, task_id, timeout)
        elapsed = time.monotonic() - t0

        if task is None:
            return ScenarioResult(name, task_id, False, f"timeout after {timeout}s", elapsed)

        ps = task.get("process_status")
        halt = task.get("halt_reason") or ""

        checks: list[str] = []
        ok = True

        if ps != 4:
            ok = False
            checks.append(f"FAIL: expected ps=4 (auditor escalation) got ps={ps}")
        else:
            checks.append("ps=4 PASS")

        if halt:
            checks.append(f"halt non-null PASS ({halt[:80]!r})")
        else:
            ok = False
            checks.append("FAIL: halt_reason is null/empty, expected non-null")

        # Auditor ESCALATE -> request_user_input -> halt_reason in {question, decision}.
        if halt in _HITL_HALT_REASONS:
            checks.append(f"halt in {{question, decision}} PASS (auditor escalation confirmed)")
        else:
            ok = False
            checks.append(
                f"FAIL: expected halt in {{question, decision}} (auditor escalate path), got {halt!r}"
            )

        qp = task.get("question_payload")
        if qp:
            checks.append("question_payload non-null PASS")
        else:
            ok = False
            checks.append("FAIL: question_payload is null")

        scr = task.get("status_change_reason") or ""
        if "fallback" in scr.lower() or "general" in scr.lower() or "auditor" in scr.lower():
            checks.append(f"status_change_reason mentions fallback/general/auditor PASS")
        else:
            checks.append(f"NOTE: status_change_reason={scr[:80]!r} (may not mention fallback explicitly)")

        return ScenarioResult(name, task_id, ok, "; ".join(checks), elapsed)

    except Exception as exc:
        elapsed = time.monotonic() - t0
        return ScenarioResult(name, task_id, False, f"exception: {exc}", elapsed)


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

ALL_SCENARIOS = {
    "S1": run_s1,
    "S2": run_s2,
    "S3": run_s3,
    "S4": run_s4,
    "S5": run_s5,
    "S6": run_s6,
}


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="T5 Regression Pack")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default: {DEFAULT_API})")
    parser.add_argument("--project", type=int, default=DEFAULT_PROJECT,
                        help=f"Project ID (default: {DEFAULT_PROJECT})")
    parser.add_argument("--only", default="",
                        help="Comma-separated list of scenarios to run (e.g. S1,S3)")
    parser.add_argument("--timeout-per-scenario", type=float, default=DEFAULT_TIMEOUT,
                        help=f"Seconds per scenario (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without making any API calls")
    args = parser.parse_args()

    api = args.api.rstrip("/")
    project_id = args.project
    timeout = args.timeout_per_scenario

    # Determine which scenarios to run.
    if args.only:
        requested = [s.strip().upper() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in requested if s not in ALL_SCENARIOS]
        if unknown:
            print(f"ERROR: unknown scenario(s): {unknown}. Valid: {sorted(ALL_SCENARIOS)}")
            return 1
        scenarios = requested
    else:
        scenarios = list(ALL_SCENARIOS.keys())

    run_id = _run_id()

    print("=" * 60)
    print("T5 Regression Pack")
    print(f"  run_id         : {run_id}")
    print(f"  api            : {api}")
    print(f"  project_id     : {project_id}")
    print(f"  scenarios      : {scenarios}")
    print(f"  timeout/scenario: {timeout}s")
    print(f"  dry_run        : {args.dry_run}")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] Plan:")
        for s in scenarios:
            fn = ALL_SCENARIOS[s]
            print(f"  {s}: {fn.__doc__.strip().splitlines()[0]}")
        print("\n[DRY RUN] Pre-clean: would soft-delete prior T5RP tasks on project", project_id)
        print("[DRY RUN] No API calls made.")
        return 0

    wall_start = time.monotonic()
    results: list[ScenarioResult] = []

    with httpx.Client(timeout=30.0) as client:
        # Pre-clean prior T5RP tasks.
        print("\nPre-clean...")
        try:
            pre_clean(client, api, project_id)
        except Exception as exc:
            print(f"  WARNING: pre-clean failed: {exc} (continuing)")

        # Run scenarios sequentially.
        for s_name in scenarios:
            fn = ALL_SCENARIOS[s_name]
            print(f"\n--- {s_name} ---")
            try:
                result = fn(client, api, project_id, run_id, timeout)
            except Exception as exc:
                result = ScenarioResult(s_name, None, False, f"unhandled exception: {exc}", 0.0)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"  {s_name}: {status} ({result.elapsed:.1f}s) task={result.task_id}")
            print(f"    {result.notes}")

    wall_elapsed = time.monotonic() - wall_start

    # Summary table.
    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"{'Scenario':<10} {'Task ID':<10} {'Result':<6} {'Seconds':>8}  Notes")
    print("-" * 60)
    all_pass = True
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            all_pass = False
        tid = str(r.task_id) if r.task_id else "-"
        short_notes = r.notes[:60] + ("..." if len(r.notes) > 60 else "")
        print(f"{r.name:<10} {tid:<10} {status:<6} {r.elapsed:>8.1f}  {short_notes}")
    print("-" * 60)
    print(f"{'Total':<10} {'':<10} {'PASS' if all_pass else 'FAIL':<6} {wall_elapsed:>8.1f}s")
    print("=" * 60)

    # Write JSON results.
    results_path = f"/repo/_scratch/t5rp-{run_id}-results.json"
    try:
        os.makedirs(os.path.dirname(results_path), exist_ok=True)
        payload = {
            "run_id": run_id,
            "api": api,
            "project_id": project_id,
            "scenarios": scenarios,
            "wall_elapsed_sec": round(wall_elapsed, 1),
            "all_pass": all_pass,
            "results": [r.to_dict() for r in results],
        }
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nResults JSON: {results_path}")
    except Exception as exc:
        print(f"\nWARNING: could not write results JSON: {exc}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
