"""Capability Probe — measures LLM provider harness capability RATES on a pilot board.

Invocation (inside agent-teams-langgraph container):
    python -u /repo/langgraph/scenarios/capability_probe.py
    python -u /repo/langgraph/scenarios/capability_probe.py --api http://api:8456
    python -u /repo/langgraph/scenarios/capability_probe.py --project 691
    python -u /repo/langgraph/scenarios/capability_probe.py --only A,C
    python -u /repo/langgraph/scenarios/capability_probe.py --reps-scale 1.0
    python -u /repo/langgraph/scenarios/capability_probe.py --timeout-per-task 900
    python -u /repo/langgraph/scenarios/capability_probe.py --dry-run
    python -u /repo/langgraph/scenarios/capability_probe.py --label gemma4

Classes:
  A  no-tool-thai x3   Thai text, MUST call 0 tools, non-empty answer.
  B  json-strict x3    EXACTLY one JSON object, default_port==443.
  C  single-tool x5    call git_status, >=1 audit row.
  D  two-tool x3 phrasings x3 reps (9 tasks)  git_status+git_diff, >=2 audit rows.
  E  write-emission x3  file_write gate: ps=4+halt=decision+halt_for_review audit row;
                        probe rejects; terminal=ps=4+halt=operator_rejected.
  F  long-context needle x2  plant BLUE-LANTERN-7 mid-doc; verify recall.

Known bug #2194: auditor may bless no-tool narration as ps=5 (false DONE).
For tool classes (C/D/E): NEVER trust ps=5 alone; verify tool-call rows too.
If ps=5 but required audit rows missing -> failure_kind='false_done_detected' (#2194).

Design mirrors regression_pack.py: httpx client, create/poll/PATCH helpers, run-id
tagging, pre-clean, results JSON + markdown summary to _scratch/, stdout buffering
with python -u.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Re-use helpers from regression_pack (imported by name; they share a package)
# ---------------------------------------------------------------------------
# Ensure /repo/langgraph is on sys.path so `scenarios` is importable whether
# this file is run as a script or as a module.
_LANGGRAPH_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
if _LANGGRAPH_ROOT not in sys.path:
    sys.path.insert(0, _LANGGRAPH_ROOT)

from scenarios.regression_pack import (
    _HITL_HALT_REASONS,
    _TERMINAL_PS,
    _create_task,
    _delete,
    _get,
    _get_tool_calls,
    _headers,
    _patch,
    _poll_post_answer,
    _poll_until_terminal,
    _post,
    _run_id,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_API = "http://api:8456"
DEFAULT_PROJECT = 691
DEFAULT_TIMEOUT = 900       # seconds per task
DEFAULT_REPS_SCALE = 1.0
POLL_INTERVAL = 12          # seconds between polls
TASK_CAP = 30               # safety: never create more than this many tasks
RUN_PREFIX = "[CPROBE "     # used to find and clean prior CPROBE tasks

# Proven S1 wording (regression_pack decision.md evidence).
_GIT_STATUS_DESC = (
    "Call the git_status tool to inspect the repository working tree, then reply in ONE sentence "
    "stating whether there are uncommitted changes. "
    "You MUST actually invoke the git_status tool — do not answer from assumption."
)

# D-phrasing descriptions (two-tool).
_D_PHRASINGS: list[tuple[str, str]] = [
    (
        "D1",
        "Call the git_status tool and the git_diff tool to inspect the working tree, "
        "then answer in ONE sentence which file has the largest change. "
        "You MUST actually invoke both tools - do not guess.",
    ),
    (
        "D2",
        "First call the git_status tool. Then call the git_diff tool. "
        "Finally answer in ONE sentence: which file has the largest change? "
        "You MUST actually invoke both tools before answering - do not guess.",
    ),
    (
        "D3",
        "Using the git tools available to you, report in one sentence which file "
        "currently has the largest uncommitted change.",
    ),
]

# ---------------------------------------------------------------------------
# Per-task record
# ---------------------------------------------------------------------------

class TaskRecord:
    """Per-task measurement record."""

    def __init__(
        self,
        cls: str,
        phrasing: str,
        rep: int,
        task_id: int | None,
        outcome: str,          # PASS / FAIL / TIMEOUT
        failure_kind: str,     # '' / no_emission / wrong_answer / timeout / false_done_detected / runner_error
        wall_sec: float,
        answer_excerpt: str,   # 200 chars
        notes: str,
    ) -> None:
        self.cls = cls
        self.phrasing = phrasing
        self.rep = rep
        self.task_id = task_id
        self.outcome = outcome
        self.failure_kind = failure_kind
        self.wall_sec = wall_sec
        self.answer_excerpt = answer_excerpt
        self.notes = notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.cls,
            "phrasing": self.phrasing,
            "rep": self.rep,
            "task_id": self.task_id,
            "outcome": self.outcome,
            "failure_kind": self.failure_kind,
            "wall_sec": round(self.wall_sec, 1),
            "answer_excerpt": self.answer_excerpt,
        }


# ---------------------------------------------------------------------------
# Pre-clean: find and soft-delete prior CPROBE tasks
# ---------------------------------------------------------------------------

def _find_prior_cprobe_tasks(
    client: httpx.Client, api: str, project_id: int
) -> list[int]:
    """Return ids of all tasks whose title starts with RUN_PREFIX."""
    ids: list[int] = []
    seen: set[int] = set()
    limit = 500

    for offset in range(0, 100_000, limit):
        resp = _get(
            client, api, project_id,
            f"/api/tasks?limit={limit}&offset={offset}&include_cancelled=true",
        )
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

    # Sweep DONE lane with keyset pagination.
    before_updated_at: str | None = None
    before_id: int | None = None
    while True:
        cursor = ""
        if before_updated_at and before_id:
            cursor = f"&before_updated_at={before_updated_at}&before_id={before_id}"
        resp = _get(
            client, api, project_id,
            f"/api/tasks?limit={limit}&process_status=5&order=done_lane{cursor}",
        )
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
    ids = _find_prior_cprobe_tasks(client, api, project_id)
    if not ids:
        print("  pre-clean: no prior CPROBE tasks found")
        return
    print(f"  pre-clean: found {len(ids)} prior CPROBE task(s): {ids}")
    for tid in ids:
        _delete(client, api, project_id, tid)
    print(f"  pre-clean: deleted {len(ids)} task(s)")


# ---------------------------------------------------------------------------
# Cancel a task (PATCH ps=6)
# ---------------------------------------------------------------------------

def _cancel_task(
    client: httpx.Client, api: str, project_id: int, task_id: int, reason: str
) -> None:
    try:
        _patch(
            client, api, project_id, f"/api/tasks/{task_id}",
            {"process_status": 6, "status_change_reason": reason},
        )
    except Exception as exc:
        print(f"  WARNING: cancel task {task_id}: {exc}")


# ---------------------------------------------------------------------------
# Answer excerpt helper
# ---------------------------------------------------------------------------

def _excerpt(task: dict[str, Any]) -> str:
    # Prefer status_change_reason (model answer on completion), then halt_reason
    # (informative when task is halted/quota-killed), then description (last resort).
    raw = (
        task.get("status_change_reason")
        or task.get("halt_reason")
        or task.get("description")
        or ""
    )
    return raw[:200]


# ---------------------------------------------------------------------------
# Auto-reject unexpected HITL (safety: never wedge waiting for human)
# ---------------------------------------------------------------------------

def _auto_reject_unexpected_hitl(
    client: httpx.Client,
    api: str,
    project_id: int,
    task_id: int,
    task: dict[str, Any],
    notes: list[str],
) -> None:
    """If a task is stuck at ps=4 HITL unexpectedly, auto-reject it."""
    notes.append(
        f"UNEXPECTED HITL ps=4 halt={task.get('halt_reason')!r}; auto-rejecting"
    )
    _patch(
        client, api, project_id, f"/api/tasks/{task_id}",
        {"new_answer": "reject", "new_answer_by": "capability-probe"},
    )


# ---------------------------------------------------------------------------
# Class A: no-tool-thai
# ---------------------------------------------------------------------------

_A_DESC = (
    "สรุปความแตกต่างระหว่าง HTTP กับ HTTPS เป็นภาษาไทย 2 ประโยคเท่านั้น "
    "ห้ามใช้ tool ใดๆ"
)


def run_class_a(
    client: httpx.Client,
    api: str,
    project_id: int,
    run_id: str,
    reps: int,
    timeout: float,
    task_counter: list[int],
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for rep in range(1, reps + 1):
        if task_counter[0] >= TASK_CAP:
            print("  [TASK_CAP reached] skipping A-" + str(rep))
            break
        t0 = time.monotonic()
        task_id: int | None = None
        notes: list[str] = []
        try:
            title = f"{RUN_PREFIX}{run_id}] A-no-tool-thai-{rep}"
            task_id = _create_task(
                client, api, project_id, title, _A_DESC, assigned_role=2
            )
            task_counter[0] += 1
            print(f"  A-{rep}: created task {task_id}")

            task = _poll_until_terminal(
                client, api, project_id, task_id, timeout, stop_early={4}
            )
            elapsed = time.monotonic() - t0

            if task is None:
                _cancel_task(client, api, project_id, task_id, "probe timeout")
                records.append(TaskRecord(
                    "A", "A", rep, task_id, "TIMEOUT", "timeout",
                    elapsed, "", "timeout"
                ))
                continue

            ps = task.get("process_status")

            if ps == 4:
                _auto_reject_unexpected_hitl(
                    client, api, project_id, task_id, task, notes
                )
                records.append(TaskRecord(
                    "A", "A", rep, task_id, "FAIL", "no_emission",
                    elapsed, _excerpt(task), "; ".join(notes)
                ))
                continue

            answer = task.get("status_change_reason") or ""
            tcs = _get_tool_calls(client, api, project_id, task_id)
            tool_rows = tcs if tcs is not None else []

            ok = True
            fk = ""

            if ps != 5:
                ok = False
                fk = "wrong_answer"
                notes.append(f"FAIL ps={ps} expected 5")
            else:
                notes.append("ps=5 PASS")

            if tool_rows:
                ok = False
                fk = "no_emission"
                notes.append(f"FAIL: {len(tool_rows)} tool-call row(s) — expected 0")
            else:
                notes.append("zero tool-call rows PASS")

            if not answer.strip():
                ok = False
                fk = "wrong_answer"
                notes.append("FAIL: empty answer")
            else:
                notes.append(f"non-empty answer PASS ({answer[:60]!r})")

            records.append(TaskRecord(
                "A", "A", rep, task_id,
                "PASS" if ok else "FAIL", fk,
                elapsed, answer[:200], "; ".join(notes)
            ))

        except Exception as exc:
            elapsed = time.monotonic() - t0
            records.append(TaskRecord(
                "A", "A", rep, task_id, "FAIL", "runner_error",
                elapsed, "", f"runner_error: {exc}"
            ))
    return records


# ---------------------------------------------------------------------------
# Class B: json-strict
# ---------------------------------------------------------------------------

_B_DESC = (
    "respond with EXACTLY one JSON object, nothing else: "
    '{"protocol": ..., "default_port": ...} '
    "where protocol is the name of the secure web protocol and "
    "default_port is its standard port number. "
    "Do not include any text outside the JSON object."
)


def run_class_b(
    client: httpx.Client,
    api: str,
    project_id: int,
    run_id: str,
    reps: int,
    timeout: float,
    task_counter: list[int],
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for rep in range(1, reps + 1):
        if task_counter[0] >= TASK_CAP:
            print("  [TASK_CAP reached] skipping B-" + str(rep))
            break
        t0 = time.monotonic()
        task_id: int | None = None
        notes: list[str] = []
        try:
            title = f"{RUN_PREFIX}{run_id}] B-json-strict-{rep}"
            task_id = _create_task(
                client, api, project_id, title, _B_DESC, assigned_role=2
            )
            task_counter[0] += 1
            print(f"  B-{rep}: created task {task_id}")

            task = _poll_until_terminal(
                client, api, project_id, task_id, timeout, stop_early={4}
            )
            elapsed = time.monotonic() - t0

            if task is None:
                _cancel_task(client, api, project_id, task_id, "probe timeout")
                records.append(TaskRecord(
                    "B", "B", rep, task_id, "TIMEOUT", "timeout",
                    elapsed, "", "timeout"
                ))
                continue

            ps = task.get("process_status")

            if ps == 4:
                _auto_reject_unexpected_hitl(
                    client, api, project_id, task_id, task, notes
                )
                records.append(TaskRecord(
                    "B", "B", rep, task_id, "FAIL", "wrong_answer",
                    elapsed, _excerpt(task), "; ".join(notes)
                ))
                continue

            answer = task.get("status_change_reason") or ""
            ok = True
            fk = ""

            if ps != 5:
                ok = False
                fk = "wrong_answer"
                notes.append(f"FAIL ps={ps} expected 5")
            else:
                notes.append("ps=5 PASS")

            # Parse: json.loads on the FULL string (no stripping per spec).
            if ok:
                try:
                    obj = json.loads(answer)
                    if not isinstance(obj, dict):
                        raise ValueError("not a dict")
                    dp = obj.get("default_port")
                    try:
                        port = int(dp)  # type: ignore[arg-type]
                    except (TypeError, ValueError):
                        port = -1
                    if port == 443:
                        notes.append("default_port==443 PASS")
                    else:
                        ok = False
                        fk = "wrong_answer"
                        notes.append(f"FAIL default_port={dp!r} expected 443")
                    if "protocol" in obj:
                        notes.append(f"protocol={obj['protocol']!r}")
                    else:
                        ok = False
                        fk = "wrong_answer"
                        notes.append("FAIL: 'protocol' key missing")
                except (json.JSONDecodeError, ValueError) as e:
                    ok = False
                    fk = "wrong_answer"
                    notes.append(f"FAIL json.loads: {e} (answer={answer[:80]!r})")

            records.append(TaskRecord(
                "B", "B", rep, task_id,
                "PASS" if ok else "FAIL", fk,
                elapsed, answer[:200], "; ".join(notes)
            ))

        except Exception as exc:
            elapsed = time.monotonic() - t0
            records.append(TaskRecord(
                "B", "B", rep, task_id, "FAIL", "runner_error",
                elapsed, "", f"runner_error: {exc}"
            ))
    return records


# ---------------------------------------------------------------------------
# Class C: single-tool
# ---------------------------------------------------------------------------

def run_class_c(
    client: httpx.Client,
    api: str,
    project_id: int,
    run_id: str,
    reps: int,
    timeout: float,
    task_counter: list[int],
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for rep in range(1, reps + 1):
        if task_counter[0] >= TASK_CAP:
            print("  [TASK_CAP reached] skipping C-" + str(rep))
            break
        t0 = time.monotonic()
        task_id: int | None = None
        notes: list[str] = []
        try:
            title = f"{RUN_PREFIX}{run_id}] C-single-tool-{rep}"
            task_id = _create_task(
                client, api, project_id, title, _GIT_STATUS_DESC, assigned_role=2
            )
            task_counter[0] += 1
            print(f"  C-{rep}: created task {task_id}")

            task = _poll_until_terminal(
                client, api, project_id, task_id, timeout, stop_early={4}
            )
            elapsed = time.monotonic() - t0

            if task is None:
                _cancel_task(client, api, project_id, task_id, "probe timeout")
                records.append(TaskRecord(
                    "C", "C", rep, task_id, "TIMEOUT", "timeout",
                    elapsed, "", "timeout"
                ))
                continue

            ps = task.get("process_status")

            if ps == 4:
                _auto_reject_unexpected_hitl(
                    client, api, project_id, task_id, task, notes
                )
                records.append(TaskRecord(
                    "C", "C", rep, task_id, "FAIL", "no_emission",
                    elapsed, _excerpt(task), "; ".join(notes)
                ))
                continue

            tcs = _get_tool_calls(client, api, project_id, task_id)
            tool_rows = tcs if tcs is not None else []
            git_rows = [tc for tc in tool_rows if tc.get("tool_name") == "git_status"]

            ok = True
            fk = ""

            if ps != 5:
                # Check for false DONE (#2194): ps=5 but missing rows handled below.
                ok = False
                fk = "wrong_answer"
                notes.append(f"FAIL ps={ps} expected 5")
            else:
                if len(git_rows) >= 1:
                    notes.append(f"ps=5 + {len(git_rows)} git_status row(s) PASS")
                else:
                    # Bug #2194: auditor blessed a narration as DONE with no tool use.
                    ok = False
                    fk = "false_done_detected"
                    notes.append(
                        f"FAIL #2194 false_done: ps=5 but 0 git_status rows "
                        f"(total tool rows={len(tool_rows)})"
                    )

            if ok and len(git_rows) == 0:
                ok = False
                fk = "no_emission"
                notes.append("FAIL no git_status audit row")

            records.append(TaskRecord(
                "C", "C", rep, task_id,
                "PASS" if ok else "FAIL", fk,
                elapsed, _excerpt(task), "; ".join(notes)
            ))

        except Exception as exc:
            elapsed = time.monotonic() - t0
            records.append(TaskRecord(
                "C", "C", rep, task_id, "FAIL", "runner_error",
                elapsed, "", f"runner_error: {exc}"
            ))
    return records


# ---------------------------------------------------------------------------
# Class D: two-tool, 3 phrasings x3 reps
# ---------------------------------------------------------------------------

def run_class_d(
    client: httpx.Client,
    api: str,
    project_id: int,
    run_id: str,
    reps: int,
    timeout: float,
    task_counter: list[int],
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for phrasing_tag, phrasing_desc in _D_PHRASINGS:
        for rep in range(1, reps + 1):
            if task_counter[0] >= TASK_CAP:
                print(f"  [TASK_CAP reached] skipping D-{phrasing_tag}-{rep}")
                break
            t0 = time.monotonic()
            task_id: int | None = None
            notes: list[str] = []
            try:
                title = f"{RUN_PREFIX}{run_id}] D-{phrasing_tag}-{rep}"
                task_id = _create_task(
                    client, api, project_id, title, phrasing_desc, assigned_role=2
                )
                task_counter[0] += 1
                print(f"  D-{phrasing_tag}-{rep}: created task {task_id}")

                task = _poll_until_terminal(
                    client, api, project_id, task_id, timeout, stop_early={4}
                )
                elapsed = time.monotonic() - t0

                if task is None:
                    _cancel_task(client, api, project_id, task_id, "probe timeout")
                    records.append(TaskRecord(
                        "D", phrasing_tag, rep, task_id, "TIMEOUT", "timeout",
                        elapsed, "", "timeout"
                    ))
                    continue

                ps = task.get("process_status")

                if ps == 4:
                    _auto_reject_unexpected_hitl(
                        client, api, project_id, task_id, task, notes
                    )
                    records.append(TaskRecord(
                        "D", phrasing_tag, rep, task_id, "FAIL", "no_emission",
                        elapsed, _excerpt(task), "; ".join(notes)
                    ))
                    continue

                tcs = _get_tool_calls(client, api, project_id, task_id)
                tool_rows = tcs if tcs is not None else []
                tools_used = {tc.get("tool_name") for tc in tool_rows}
                has_git_status = "git_status" in tools_used
                has_git_diff = "git_diff" in tools_used

                ok = True
                fk = ""

                if ps != 5:
                    ok = False
                    fk = "wrong_answer"
                    notes.append(f"FAIL ps={ps} expected 5")
                else:
                    if len(tool_rows) >= 2 and has_git_status and has_git_diff:
                        notes.append(
                            f"ps=5 + {len(tool_rows)} rows "
                            f"(git_status={has_git_status}, git_diff={has_git_diff}) PASS"
                        )
                    else:
                        # Check #2194 false done.
                        if len(tool_rows) < 2:
                            ok = False
                            if len(tool_rows) == 0:
                                fk = "false_done_detected"
                                notes.append(
                                    f"FAIL #2194 false_done: ps=5 but 0 tool rows"
                                )
                            else:
                                fk = "no_emission"
                                notes.append(
                                    f"FAIL: {len(tool_rows)} tool row(s) but need >=2 "
                                    f"(git_status={has_git_status}, git_diff={has_git_diff})"
                                )
                        else:
                            ok = False
                            fk = "no_emission"
                            notes.append(
                                f"FAIL: missing required tools: "
                                f"git_status={has_git_status}, git_diff={has_git_diff}"
                            )

                records.append(TaskRecord(
                    "D", phrasing_tag, rep, task_id,
                    "PASS" if ok else "FAIL", fk,
                    elapsed, _excerpt(task), "; ".join(notes)
                ))

            except Exception as exc:
                elapsed = time.monotonic() - t0
                records.append(TaskRecord(
                    "D", phrasing_tag, rep, task_id, "FAIL", "runner_error",
                    elapsed, "", f"runner_error: {exc}"
                ))
    return records


# ---------------------------------------------------------------------------
# Class E: write-emission
# ---------------------------------------------------------------------------

def run_class_e(
    client: httpx.Client,
    api: str,
    project_id: int,
    run_id: str,
    reps: int,
    timeout: float,
    task_counter: list[int],
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for rep in range(1, reps + 1):
        if task_counter[0] >= TASK_CAP:
            print("  [TASK_CAP reached] skipping E-" + str(rep))
            break
        t0 = time.monotonic()
        task_id: int | None = None
        notes: list[str] = []
        target_file = f"/repo/_scratch/cprobe-{run_id}-{rep}.txt"
        try:
            title = f"{RUN_PREFIX}{run_id}] E-write-emission-{rep}"
            desc = (
                f"Use the file_write tool to create the file {target_file} "
                f"with the content 'probe'. You MUST call the file_write tool."
            )
            task_id = _create_task(
                client, api, project_id, title, desc, assigned_role=2
            )
            task_counter[0] += 1
            print(f"  E-{rep}: created task {task_id}")

            # Phase 1: wait for HITL halt (ps=4, halt='decision').
            task = _poll_until_terminal(
                client, api, project_id, task_id, timeout, stop_early={4}
            )
            elapsed_p1 = time.monotonic() - t0

            if task is None:
                _cancel_task(client, api, project_id, task_id, "probe timeout")
                records.append(TaskRecord(
                    "E", "E", rep, task_id, "TIMEOUT", "timeout",
                    time.monotonic() - t0, "", "phase1 timeout"
                ))
                continue

            ps = task.get("process_status")
            halt = task.get("halt_reason") or ""
            qp = task.get("question_payload") or {}
            question_text = qp.get("question") or ""

            ok = True
            fk = ""

            if ps != 4:
                # Reached terminal without halting — no emission at all.
                if ps == 5:
                    ok = False
                    fk = "no_emission"
                    notes.append(
                        f"FAIL no_emission: ps={ps} reached without halt "
                        f"(auditor may have blessed no-tool narration)"
                    )
                else:
                    ok = False
                    fk = "no_emission"
                    notes.append(f"FAIL no_emission: unexpected ps={ps}")
                elapsed = time.monotonic() - t0
                records.append(TaskRecord(
                    "E", "E", rep, task_id,
                    "FAIL", fk, elapsed,
                    _excerpt(task), "; ".join(notes)
                ))
                continue

            notes.append("phase1: ps=4 PASS")

            if halt != "decision":
                ok = False
                fk = "no_emission"
                notes.append(f"FAIL: halt={halt!r} expected 'decision'")
            else:
                notes.append("halt='decision' PASS")

            if "file_write" in question_text.lower() or "authorize" in question_text.lower():
                notes.append("question mentions file_write/authorize PASS")
            else:
                ok = False
                fk = "no_emission"
                notes.append(
                    f"FAIL: question_payload does not mention file_write/authorize "
                    f"({question_text[:80]!r})"
                )

            # Check audit row: error_code='halt_for_review'.
            tcs = _get_tool_calls(client, api, project_id, task_id)
            tool_rows = tcs if tcs is not None else []
            fw_rows = [
                tc for tc in tool_rows
                if tc.get("tool_name") == "file_write"
                and tc.get("error_code") == "halt_for_review"
            ]
            if fw_rows:
                notes.append(f"audit row error_code='halt_for_review' PASS")
            else:
                ok = False
                fk = "no_emission"
                notes.append(
                    f"FAIL: no file_write+halt_for_review audit row "
                    f"(total rows={len(tool_rows)})"
                )

            # File must NOT have been written.
            if os.path.exists(target_file):
                ok = False
                fk = "no_emission"
                notes.append(f"FAIL: target file {target_file!r} unexpectedly exists")
            else:
                notes.append("target file not created (gate held) PASS")

            if not ok:
                elapsed = time.monotonic() - t0
                records.append(TaskRecord(
                    "E", "E", rep, task_id,
                    "FAIL", fk, elapsed,
                    _excerpt(task), "; ".join(notes)
                ))
                continue

            # Phase 2: PATCH new_answer='reject'.
            patch_resp = _patch(
                client, api, project_id, f"/api/tasks/{task_id}",
                {"new_answer": "reject", "new_answer_by": "capability-probe"},
            )
            if patch_resp.status_code != 200:
                ok = False
                fk = "no_emission"
                notes.append(
                    f"FAIL PATCH reject returned {patch_resp.status_code}: "
                    f"{patch_resp.text[:100]}"
                )
                elapsed = time.monotonic() - t0
                records.append(TaskRecord(
                    "E", "E", rep, task_id,
                    "FAIL", fk, elapsed,
                    _excerpt(task), "; ".join(notes)
                ))
                continue
            notes.append("PATCH new_answer='reject': 200 PASS")

            # Phase 3: poll until halt='operator_rejected' (terminal).
            remaining = timeout - (time.monotonic() - t0)
            if remaining < 30:
                remaining = 30
            task2 = _poll_post_answer(client, api, project_id, task_id, remaining)
            elapsed = time.monotonic() - t0

            if task2 is None:
                ok = False
                fk = "timeout"
                notes.append("FAIL phase3: timeout waiting for operator_rejected terminal")
                records.append(TaskRecord(
                    "E", "E", rep, task_id,
                    "FAIL", fk, elapsed,
                    "", "; ".join(notes)
                ))
                continue

            ps2 = task2.get("process_status")
            halt2 = task2.get("halt_reason") or ""

            if ps2 == 4 and halt2 == "operator_rejected":
                notes.append("phase3: ps=4 halt='operator_rejected' PASS")
            else:
                ok = False
                fk = "no_emission"
                notes.append(
                    f"FAIL phase3: expected ps=4 halt='operator_rejected', "
                    f"got ps={ps2} halt={halt2!r}"
                )

            if os.path.exists(target_file):
                ok = False
                fk = "no_emission"
                notes.append(f"FAIL: target file exists after reject!")
            else:
                notes.append("target file absent post-reject PASS")

            records.append(TaskRecord(
                "E", "E", rep, task_id,
                "PASS" if ok else "FAIL", fk,
                elapsed, _excerpt(task2), "; ".join(notes)
            ))

        except Exception as exc:
            elapsed = time.monotonic() - t0
            records.append(TaskRecord(
                "E", "E", rep, task_id, "FAIL", "runner_error",
                elapsed, "", f"runner_error: {exc}"
            ))
    return records


# ---------------------------------------------------------------------------
# Class F: long-context needle
# ---------------------------------------------------------------------------

_NEEDLE = "The deployment codename for region 4 is BLUE-LANTERN-7."
_NEEDLE_QUESTION = (
    "Based only on the document above, answer in ONE sentence: "
    "what is the deployment codename for region 4? Do not use any tools."
)

# Filler paragraphs (each ~200 chars) assembled to target length.
_FILLER_UNIT = (
    "This document contains operational procedures and configuration notes for the "
    "distributed deployment infrastructure. Each region is assigned a unique operational "
    "codename for tracking purposes during the deployment cycle. "
)


def _build_needle_doc(target_chars: int) -> str:
    """Build a document with _NEEDLE planted at ~40% and question at end."""
    filler_40pct = int(target_chars * 0.40)
    filler_rest = target_chars - filler_40pct - len(_NEEDLE) - len(_NEEDLE_QUESTION) - 4
    if filler_rest < 0:
        filler_rest = 0

    def _repeat_to(n: int) -> str:
        if n <= 0:
            return ""
        reps = math.ceil(n / len(_FILLER_UNIT))
        return (_FILLER_UNIT * reps)[:n]

    return (
        _repeat_to(filler_40pct)
        + "\n\n"
        + _NEEDLE
        + "\n\n"
        + _repeat_to(filler_rest)
        + "\n\n"
        + _NEEDLE_QUESTION
    )


_F_DOCS = [
    ("F1", 10_000),
    ("F2", 18_000),   # 20K API cap on tasks.description; 18K keeps safely under it
]


def run_class_f(
    client: httpx.Client,
    api: str,
    project_id: int,
    run_id: str,
    timeout: float,
    task_counter: list[int],
) -> list[TaskRecord]:
    records: list[TaskRecord] = []
    for phrasing_tag, target_chars in _F_DOCS:
        rep = 1
        if task_counter[0] >= TASK_CAP:
            print(f"  [TASK_CAP reached] skipping {phrasing_tag}")
            break
        t0 = time.monotonic()
        task_id: int | None = None
        notes: list[str] = []
        try:
            doc = _build_needle_doc(target_chars)
            actual_chars = len(doc)
            title = f"{RUN_PREFIX}{run_id}] F-needle-{phrasing_tag}"
            task_id = _create_task(
                client, api, project_id, title, doc, assigned_role=2
            )
            task_counter[0] += 1
            print(
                f"  F-{phrasing_tag}: created task {task_id} "
                f"(doc={actual_chars} chars)"
            )

            task = _poll_until_terminal(
                client, api, project_id, task_id, timeout, stop_early={4}
            )
            elapsed = time.monotonic() - t0

            if task is None:
                _cancel_task(client, api, project_id, task_id, "probe timeout")
                records.append(TaskRecord(
                    "F", phrasing_tag, rep, task_id, "TIMEOUT", "timeout",
                    elapsed, "", "timeout"
                ))
                continue

            ps = task.get("process_status")

            if ps == 4:
                _auto_reject_unexpected_hitl(
                    client, api, project_id, task_id, task, notes
                )
                records.append(TaskRecord(
                    "F", phrasing_tag, rep, task_id, "FAIL", "wrong_answer",
                    elapsed, _excerpt(task), "; ".join(notes)
                ))
                continue

            answer = task.get("status_change_reason") or ""
            ok = True
            fk = ""

            if ps != 5:
                ok = False
                fk = "wrong_answer"
                notes.append(f"FAIL ps={ps} expected 5")
            else:
                notes.append("ps=5 PASS")

            if _NEEDLE.split("is ")[-1].strip(".") in answer or "BLUE-LANTERN-7" in answer:
                notes.append("BLUE-LANTERN-7 in answer PASS")
            else:
                ok = False
                fk = "wrong_answer"
                notes.append(f"FAIL: 'BLUE-LANTERN-7' not in answer ({answer[:100]!r})")

            records.append(TaskRecord(
                "F", phrasing_tag, rep, task_id,
                "PASS" if ok else "FAIL", fk,
                elapsed, answer[:200], "; ".join(notes)
            ))

        except Exception as exc:
            elapsed = time.monotonic() - t0
            records.append(TaskRecord(
                "F", phrasing_tag, rep, task_id, "FAIL", "runner_error",
                elapsed, "", f"runner_error: {exc}"
            ))
    return records


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------

def _summarize(
    records: list[TaskRecord],
    run_id: str,
    api: str,
    project_id: int,
    label: str,
    wall_elapsed: float,
    classes_run: list[str],
) -> dict[str, Any]:
    """Build the results JSON payload."""

    by_class: dict[str, list[TaskRecord]] = {}
    for r in records:
        by_class.setdefault(r.cls, []).append(r)

    class_stats: list[dict[str, Any]] = []
    false_done_detections: list[dict[str, Any]] = []
    runner_errors: list[dict[str, Any]] = []

    for cls in sorted(by_class):
        recs = by_class[cls]
        n_pass = sum(1 for r in recs if r.outcome == "PASS")
        n_total = len(recs)
        latencies = [r.wall_sec for r in recs if r.outcome != "TIMEOUT"]
        mean_lat = round(statistics.mean(latencies), 1) if latencies else None
        median_lat = round(statistics.median(latencies), 1) if latencies else None

        # False-done detections and runner_error collections.
        for r in recs:
            if r.failure_kind == "false_done_detected":
                false_done_detections.append({
                    "class": r.cls,
                    "phrasing": r.phrasing,
                    "rep": r.rep,
                    "task_id": r.task_id,
                    "notes": r.notes,
                })
            elif r.failure_kind == "runner_error":
                runner_errors.append({
                    "class": r.cls,
                    "phrasing": r.phrasing,
                    "rep": r.rep,
                    "task_id": r.task_id,
                    "notes": r.notes,
                })

        stat: dict[str, Any] = {
            "class": cls,
            "pass": n_pass,
            "total": n_total,
            "rate_pct": round(100 * n_pass / n_total, 1) if n_total else 0.0,
            "latency_mean_sec": mean_lat,
            "latency_median_sec": median_lat,
        }

        # Per-phrasing breakdown for D.
        if cls == "D":
            phrasings: dict[str, dict[str, Any]] = {}
            for r in recs:
                pb = phrasings.setdefault(r.phrasing, {"pass": 0, "total": 0})
                pb["total"] += 1
                if r.outcome == "PASS":
                    pb["pass"] += 1
            stat["phrasing_breakdown"] = {
                ph: {
                    "pass": v["pass"],
                    "total": v["total"],
                    "rate_pct": round(100 * v["pass"] / v["total"], 1) if v["total"] else 0.0,
                }
                for ph, v in phrasings.items()
            }

        class_stats.append(stat)

    return {
        "run_id": run_id,
        "label": label,
        "api": api,
        "project_id": project_id,
        "classes_run": classes_run,
        "wall_elapsed_sec": round(wall_elapsed, 1),
        "task_count": len(records),
        "class_stats": class_stats,
        "false_done_detections_2194": false_done_detections,
        "runner_errors": runner_errors,
        "records": [r.to_dict() for r in records],
    }


def _write_markdown(
    summary: dict[str, Any],
    run_id: str,
    label: str,
) -> str:
    lines: list[str] = [
        f"# Capability Probe — run {run_id}",
        f"",
        f"Label: `{label}`  |  Project: {summary['project_id']}  "
        f"|  Wall time: {summary['wall_elapsed_sec']}s",
        f"Tasks run: {summary['task_count']}  |  API: {summary['api']}",
        f"",
        f"## Per-class success rates",
        f"",
        f"| Class | Pass | Total | Rate | Latency mean | Latency median |",
        f"|-------|------|-------|------|-------------|----------------|",
    ]
    for st in summary["class_stats"]:
        cls = st["class"]
        lm = f"{st['latency_mean_sec']}s" if st["latency_mean_sec"] is not None else "—"
        lmed = f"{st['latency_median_sec']}s" if st["latency_median_sec"] is not None else "—"
        lines.append(
            f"| {cls} | {st['pass']} | {st['total']} | {st['rate_pct']}% "
            f"| {lm} | {lmed} |"
        )

    # D phrasing breakdown.
    for st in summary["class_stats"]:
        if st["class"] == "D" and "phrasing_breakdown" in st:
            lines += ["", "## Class D — phrasing breakdown", ""]
            lines += ["| Phrasing | Pass | Total | Rate |", "|----------|------|-------|------|"]
            for ph, pb in sorted(st["phrasing_breakdown"].items()):
                lines.append(
                    f"| {ph} | {pb['pass']} | {pb['total']} | {pb['rate_pct']}% |"
                )

    # Runner errors (probe-infrastructure failures — not model behavior).
    rerrs = summary.get("runner_errors", [])
    if rerrs:
        lines += ["", "## Runner errors (probe-infrastructure failures)", ""]
        lines += ["> runner_error = probe setup/poll failed; model behavior unknown for these tasks.", ""]
        for d in rerrs:
            lines.append(
                f"- **[runner_error]** Class {d['class']} phrasing {d['phrasing']} rep {d['rep']} "
                f"task_id={d['task_id']}: {d['notes']}"
            )
    else:
        lines += ["", "## Runner errors (probe-infrastructure failures)", "", "None detected."]

    # False-done detections.
    fdd = summary.get("false_done_detections_2194", [])
    if fdd:
        lines += ["", "## Bug #2194 false-done detections", ""]
        for d in fdd:
            lines.append(
                f"- Class {d['class']} phrasing {d['phrasing']} rep {d['rep']} "
                f"task_id={d['task_id']}: {d['notes']}"
            )
    else:
        lines += ["", "## Bug #2194 false-done detections", "", "None detected."]

    lines += ["", "## Per-task records", ""]
    lines += [
        "| Class | Phrasing | Rep | Task ID | Outcome | Failure Kind | Secs | Excerpt |",
        "|-------|----------|-----|---------|---------|--------------|------|---------|",
    ]
    for r in summary["records"]:
        exc = (r["answer_excerpt"] or "")[:60].replace("|", "\\|")
        # Visually flag runner_error rows in the table.
        fk_display = f"**{r['failure_kind']}**" if r["failure_kind"] == "runner_error" else (r["failure_kind"] or "—")
        lines.append(
            f"| {r['class']} | {r['phrasing']} | {r['rep']} | {r['task_id']} "
            f"| {r['outcome']} | {fk_display} | {r['wall_sec']} | {exc} |"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_CLASSES = ["A", "B", "C", "D", "E", "F"]

_CLASS_REPS = {
    "A": 3,
    "B": 3,
    "C": 5,
    "D": 3,   # 3 phrasings × 3 reps = 9 tasks
    "E": 3,
    "F": 2,   # 2 sizes (F1, F2) — reps not scaled
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capability Probe")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--project", type=int, default=DEFAULT_PROJECT)
    parser.add_argument("--only", default="",
                        help="Comma-separated classes to run (e.g. A,C)")
    parser.add_argument("--reps-scale", type=float, default=DEFAULT_REPS_SCALE,
                        help="Scale factor for reps (0.34 = 1 task for 3-rep classes)")
    parser.add_argument("--timeout-per-task", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--label", default="",
                        help="Provider label for result files (e.g. gemma4)")
    args = parser.parse_args()

    api = args.api.rstrip("/")
    project_id = args.project
    timeout = args.timeout_per_task
    label = args.label or "unlabeled"
    reps_scale = args.reps_scale

    # Determine which classes to run.
    if args.only:
        requested = [c.strip().upper() for c in args.only.split(",") if c.strip()]
        unknown = [c for c in requested if c not in ALL_CLASSES]
        if unknown:
            print(f"ERROR: unknown class(es): {unknown}. Valid: {ALL_CLASSES}")
            return 1
        classes_to_run = requested
    else:
        classes_to_run = list(ALL_CLASSES)

    run_id = _run_id()

    # Compute scaled reps.
    def _scale(base: int) -> int:
        scaled = max(1, math.floor(base * reps_scale))
        return scaled

    scaled_reps = {cls: _scale(_CLASS_REPS[cls]) for cls in ALL_CLASSES}

    # Dry-run plan.
    print("=" * 70)
    print("Capability Probe")
    print(f"  run_id          : {run_id}")
    print(f"  label           : {label}")
    print(f"  api             : {api}")
    print(f"  project_id      : {project_id}")
    print(f"  classes_to_run  : {classes_to_run}")
    print(f"  reps_scale      : {reps_scale}")
    print(f"  timeout/task    : {timeout}s")
    print(f"  dry_run         : {args.dry_run}")
    print(f"  task_cap        : {TASK_CAP}")
    print()
    print("  Plan:")

    total_planned = 0
    for cls in classes_to_run:
        reps = scaled_reps[cls]
        if cls == "D":
            tasks = len(_D_PHRASINGS) * reps
            print(f"    {cls}: {len(_D_PHRASINGS)} phrasings × {reps} reps = {tasks} tasks")
        elif cls == "F":
            tasks = len(_F_DOCS)
            print(f"    {cls}: {tasks} needle sizes (F1={_F_DOCS[0][1]} chars, F2={_F_DOCS[1][1]} chars)")
        else:
            tasks = reps
            print(f"    {cls}: {reps} reps = {tasks} task(s)")
        total_planned += tasks

    print(f"  Total planned   : {min(total_planned, TASK_CAP)} task(s) (cap={TASK_CAP})")
    print(f"  Results JSON    : /repo/_scratch/cprobe-{run_id}-results.json")
    print(f"  Summary MD      : /repo/_scratch/cprobe-{run_id}-summary.md")
    print("=" * 70)

    if args.dry_run:
        print("\n[DRY RUN] No API calls made.")
        return 0

    wall_start = time.monotonic()
    records: list[TaskRecord] = []
    task_counter = [0]  # mutable int for cap tracking

    with httpx.Client(timeout=30.0) as client:
        print("\nPre-clean...")
        try:
            pre_clean(client, api, project_id)
        except Exception as exc:
            print(f"  WARNING: pre-clean failed: {exc} (continuing)")

        for cls in classes_to_run:
            print(f"\n--- Class {cls} ---")
            reps = scaled_reps[cls]

            if cls == "A":
                recs = run_class_a(
                    client, api, project_id, run_id, reps, timeout, task_counter
                )
            elif cls == "B":
                recs = run_class_b(
                    client, api, project_id, run_id, reps, timeout, task_counter
                )
            elif cls == "C":
                recs = run_class_c(
                    client, api, project_id, run_id, reps, timeout, task_counter
                )
            elif cls == "D":
                recs = run_class_d(
                    client, api, project_id, run_id, reps, timeout, task_counter
                )
            elif cls == "E":
                recs = run_class_e(
                    client, api, project_id, run_id, reps, timeout, task_counter
                )
            elif cls == "F":
                recs = run_class_f(
                    client, api, project_id, run_id, timeout, task_counter
                )
            else:
                print(f"  SKIP unknown class {cls}")
                recs = []

            records.extend(recs)
            for r in recs:
                tag = f"{r.cls}-{r.phrasing}-{r.rep}" if r.phrasing != r.cls else f"{r.cls}-{r.rep}"
                print(
                    f"  {tag}: {r.outcome} "
                    f"({r.wall_sec:.1f}s) task={r.task_id} fk={r.failure_kind or '—'}"
                )

    wall_elapsed = time.monotonic() - wall_start

    summary = _summarize(
        records, run_id, api, project_id, label, wall_elapsed, classes_to_run
    )

    # Print summary table.
    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"  run_id={run_id}  label={label}  total_tasks={len(records)}"
          f"  wall={wall_elapsed:.1f}s")
    print()
    print(f"  {'Class':<8} {'Pass':>5} {'Total':>6} {'Rate':>7}  "
          f"{'Mean(s)':>8}  {'Median(s)':>10}")
    print("  " + "-" * 55)
    for st in summary["class_stats"]:
        lm = f"{st['latency_mean_sec']}" if st["latency_mean_sec"] is not None else "—"
        lmed = f"{st['latency_median_sec']}" if st["latency_median_sec"] is not None else "—"
        print(
            f"  {st['class']:<8} {st['pass']:>5} {st['total']:>6} "
            f"{st['rate_pct']:>6.1f}%  {lm:>8}  {lmed:>10}"
        )

    rerrs = summary.get("runner_errors", [])
    if rerrs:
        print(f"\n  WARNING: {len(rerrs)} runner_error(s) (probe-infrastructure failures):")
        for d in rerrs:
            print(f"    [runner_error] class={d['class']} phrasing={d['phrasing']} rep={d['rep']} task_id={d['task_id']}")
    else:
        print("\n  No runner errors.")

    fdd = summary.get("false_done_detections_2194", [])
    if fdd:
        print(f"\n  WARNING: {len(fdd)} bug #2194 false-done detection(s):")
        for d in fdd:
            print(f"    task_id={d['task_id']} class={d['class']} phrasing={d['phrasing']}")
    else:
        print("\n  No bug #2194 false-done detections.")

    print("=" * 70)

    # Write outputs.
    scratch = "/repo/_scratch"
    os.makedirs(scratch, exist_ok=True)

    json_path = f"{scratch}/cprobe-{run_id}-results.json"
    md_path = f"{scratch}/cprobe-{run_id}-summary.md"

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"\nResults JSON : {json_path}")
    except Exception as exc:
        print(f"\nWARNING: could not write results JSON: {exc}")

    try:
        md = _write_markdown(summary, run_id, label)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"Summary MD   : {md_path}")
    except Exception as exc:
        print(f"WARNING: could not write summary MD: {exc}")

    # Exit 0 on completed run (success rates are data, not failures).
    return 0


if __name__ == "__main__":
    sys.exit(main())
