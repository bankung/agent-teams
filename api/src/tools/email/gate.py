"""3-layer safety gate for email tools (Kanban #1604).

Layers:
  1. Daily-units cap (per project_id, per UTC day) — module-level dict counter.
  2. Audit JSONL append at `_scratch/email-tools-audit.jsonl`.
  3. Bulk threshold (per call) — refuses N>threshold unless ?force=true.

Owned by #1604 (Gmail). Imported by #1608 (Outlook) — interface is FROZEN by
Lead spec; do not redesign without coordinating with both Kanban tickets.

Karpathy cut: in-memory counter only. Lost on restart, intentional — production-
scale would swap to Redis-backed slowapi limiter. The cap is a SAFETY limit
against runaway bugs, not a billing budget (Gmail per-project daily quota is
80M units; per-user 6000 units/min — see research note 2026-05-27).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Daily cap counter — module-level dict keyed by (project_id, date_str).
# Persists for the lifetime of the api container process only.
_DAILY_UNITS: dict[tuple[int, str], int] = {}

# Audit log path. Configurable via EMAIL_TOOLS_AUDIT_PATH env var; defaults to
# /repo/_scratch/email-tools-audit.jsonl (the _scratch bind-mount in the container).
_AUDIT_PATH = Path(os.environ.get("EMAIL_TOOLS_AUDIT_PATH", "/repo/_scratch/email-tools-audit.jsonl"))


def _today() -> str:
    return datetime.datetime.now(datetime.UTC).date().isoformat()


def check_and_increment(project_id: int, units: int) -> tuple[bool, dict]:
    """Layer 1 — daily cap. Returns (allowed, info_dict).

    info_dict: {current_units, cap, reset_at_utc_midnight}.
    If allowed, increments counter atomically; if not, leaves counter unchanged.
    """
    cap = int(os.environ.get("EMAIL_TOOLS_DAILY_UNITS_CAP", "5000"))
    key = (project_id, _today())
    current = _DAILY_UNITS.get(key, 0)
    reset = (
        datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=1)
    ).isoformat() + "T00:00:00Z"
    info = {"current_units": current, "cap": cap, "reset_at_utc_midnight": reset}
    if current + units > cap:
        return False, info
    _DAILY_UNITS[key] = current + units
    info["current_units"] = _DAILY_UNITS[key]
    return True, info


def usage(project_id: int) -> dict:
    """Returns {date, units_consumed, cap, remaining} for today."""
    cap = int(os.environ.get("EMAIL_TOOLS_DAILY_UNITS_CAP", "5000"))
    date = _today()
    consumed = _DAILY_UNITS.get((project_id, date), 0)
    return {
        "date": date,
        "units_consumed": consumed,
        "cap": cap,
        "remaining": cap - consumed,
    }


def log_audit(
    provider: str,
    project_id: int,
    action: str,
    units: int,
    success: bool,
    error_code: str | None = None,
) -> None:
    """Layer 2 — append JSONL row to _AUDIT_PATH.

    Schema: {ts, project_id, provider, action, units, success, error_code?}
    """
    row = {
        "ts": datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat() + "Z",
        "project_id": project_id,
        "provider": provider,
        "action": action,
        "units": units,
        "success": success,
    }
    if error_code:
        row["error_code"] = error_code
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError as exc:
        # Audit is observability, not correctness — a disk hiccup must never
        # raise out of log_audit and turn a successful email action into a 500.
        logger.warning("gate.log_audit: write failed (best-effort guard): %s", exc)


def check_bulk_threshold(count: int, force: bool = False) -> tuple[bool, dict]:
    """Layer 3 — bulk threshold. Returns (allowed, info_dict).

    info_dict: {count, threshold}.
    Allowed if count <= threshold OR force is True.
    """
    threshold = int(os.environ.get("EMAIL_TOOLS_BULK_THRESHOLD", "100"))
    info = {"count": count, "threshold": threshold}
    return (count <= threshold) or force, info
