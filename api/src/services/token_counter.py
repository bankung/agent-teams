"""Token counter + per-section measurement (CTX-3, Kanban #718).

V1 uses a local heuristic — no SDK calls, no rebuild required. The chars/4
approximation is documented at `count_tokens` and is tolerated by the
soft-warn budget contract (we never block on miscounts; provider errors if
the real model window is exceeded).

Public API:
- `count_tokens(text, model_hint='claude-opus-4-7') -> int`
- `measure_session_prompt(session_id, repo_root, *, include_card_id, db) -> dict`
- `check_budget(session_id, total_input_estimate, db) -> dict`

`measure_session_prompt` returns per-section token counts AND the four
session ceilings. Callers (router) compare Recent Activity tokens against
`recent_activity_ceiling_tokens` and surface `compact_recommended` as an
advisory flag (CTX-4 owns the actual compact runner).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.session import Session as SessionModel
from src.services.session_store import (
    SECTION_COMPACTED_HISTORY,
    SECTION_RECENT_ACTIVITY,
    get_section_text,
)


def count_tokens(text: str, model_hint: str = "claude-opus-4-7") -> int:
    """Estimate token count via local chars/4 heuristic.

    ~10-20% inaccuracy on English; worse on code/CJK. Soft-warn budget
    tolerates this. Switch to a real tokenizer (anthropic SDK or tiktoken) at
    the module-level when needed. `model_hint` is accepted for forward compat
    but unused in V1.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _read_card_text(session_id: int, card_id: int, repo_root: Path) -> str:
    """Return the card markdown body (empty string if missing)."""
    card_path = (
        Path(repo_root) / "_sessions" / str(session_id) / "cards" / f"{card_id}.md"
    )
    if not card_path.exists():
        return ""
    return card_path.read_text(encoding="utf-8")


async def measure_session_prompt(
    session_id: int,
    repo_root: Path,
    *,
    include_card_id: int | None = None,
    db: AsyncSession,
) -> dict:
    """Measure per-section token counts + return session ceilings.

    Shape:
        {
          "compacted_history_tokens": int,
          "recent_activity_tokens": int,
          "card_detail_tokens": int,
          "total_input_estimate": int,
          "ceilings": {
            "compacted_history": int,
            "recent_activity": int,
            "card_detail": int,
            "output_budget": int,
          },
        }

    Reads each section directly via `get_section_text` (rather than parsing
    the concatenated prompt) so per-section counts are precise.
    """
    sess = await db.scalar(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    if sess is None:
        raise ValueError(f"session id={session_id} not found")

    compacted_text = get_section_text(
        session_id, SECTION_COMPACTED_HISTORY, repo_root
    )
    recent_text = get_section_text(
        session_id, SECTION_RECENT_ACTIVITY, repo_root
    )
    card_text = (
        _read_card_text(session_id, include_card_id, repo_root)
        if include_card_id is not None
        else ""
    )

    compacted_tokens = count_tokens(compacted_text)
    recent_tokens = count_tokens(recent_text)
    card_tokens = count_tokens(card_text)
    total = compacted_tokens + recent_tokens + card_tokens

    return {
        "compacted_history_tokens": compacted_tokens,
        "recent_activity_tokens": recent_tokens,
        "card_detail_tokens": card_tokens,
        "total_input_estimate": total,
        "ceilings": {
            "compacted_history": int(sess.compacted_history_ceiling_tokens),
            "recent_activity": int(sess.recent_activity_ceiling_tokens),
            "card_detail": int(sess.card_detail_ceiling_tokens),
            "output_budget": int(sess.output_budget_tokens),
        },
    }


async def check_budget(
    session_id: int,
    total_input_estimate: int,
    db: AsyncSession,
) -> dict:
    """Compare a measured token total against `sessions.token_budget_per_run`.

    Returns:
        {
          "over_budget": bool,
          "budget": int | None,
          "current": int,
          "recommend_compact": bool,
        }

    NULL `token_budget_per_run` → `over_budget=False` always (no budget set).
    `recommend_compact` mirrors `over_budget` here for symmetry; the activity
    endpoint uses a different signal (Recent Activity tokens vs ceiling) and
    computes its own flag — this is for direct PATCH-time checks.
    """
    sess = await db.scalar(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    if sess is None:
        raise ValueError(f"session id={session_id} not found")

    budget = sess.token_budget_per_run
    if budget is None:
        return {
            "over_budget": False,
            "budget": None,
            "current": int(total_input_estimate),
            "recommend_compact": False,
        }
    over = total_input_estimate > budget
    return {
        "over_budget": over,
        "budget": int(budget),
        "current": int(total_input_estimate),
        "recommend_compact": over,
    }
