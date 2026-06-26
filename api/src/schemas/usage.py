"""Pydantic schemas for the usage rollup endpoints (Kanban #2135, #2356)."""

from __future__ import annotations

from pydantic import BaseModel


class UsageDailyRow(BaseModel):
    """One aggregated (date, provider, model) bucket."""

    date: str  # "YYYY-MM-DD"
    provider: str  # 'google' | 'anthropic' | 'ollama' | 'unknown' | …
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: str  # Decimal serialised to "0.0000" string (4dp, no float drift)


class UsageDailyResponse(BaseModel):
    """Response shape for GET /api/usage/daily."""

    days: int
    today: str  # server's UTC current date "YYYY-MM-DD" used for total_today_usd
    rows: list[UsageDailyRow]
    total_today_usd: str  # sum over today UTC
    total_month_usd: str  # sum over current UTC calendar month


# ---------------------------------------------------------------------------
# GET /api/usage/monthly — billing-cycle cost rollup (Kanban #2356).
# Money is serialised as 4dp strings (no float drift), matching usage_daily.
# ---------------------------------------------------------------------------


class UsageMonthlyTaskRow(BaseModel):
    """Per-task spend within one billing cycle, summed across both modes.

    `task_id`/`task_title` are None for the single "unattributed" bucket
    (rows with no task_id). `task_title` is also None when the task row was
    hard-deleted (the cost fact outlives its task).
    """

    task_id: int | None
    task_title: str | None
    mode_a_cost_usd: str  # usage_events spend attributed to this task
    mode_b_cost_usd: str  # session_runs spend attributed to this task
    total_cost_usd: str  # mode_a + mode_b


class UsageMonthlyCycle(BaseModel):
    """One billing cycle [cycle_start, cycle_end] (both inclusive dates).

    Mode A = usage_events (interactive Claude-Code hook capture).
    Mode B = session_runs (headless langgraph metering). DISJOINT sources —
    `total_cost_usd` = mode_a_cost_usd + mode_b_cost_usd (intended total, not
    double-counted).
    """

    cycle_start: str  # YYYY-MM-DD, inclusive (= cut-off day D)
    cycle_end: str  # YYYY-MM-DD, inclusive last day (next_start - 1 day)
    mode_a_cost_usd: str
    mode_a_input_tokens: int
    mode_a_output_tokens: int
    mode_b_cost_usd: str
    mode_b_input_tokens: int
    mode_b_output_tokens: int
    total_cost_usd: str
    tasks: list[UsageMonthlyTaskRow]


class UsageMonthlyResponse(BaseModel):
    """Response shape for GET /api/usage/monthly.

    `cycles` is zero-filled — one entry per requested cycle in the window,
    most-recent first, even cycles with zero spend.
    """

    months: int
    cycle_day: int  # resolved cut-off day actually used
    cycles: list[UsageMonthlyCycle]  # most-recent first
    total_cost_usd: str  # sum across all cycles in the window


# ---------------------------------------------------------------------------
# GET /api/usage/sessions — per-session cost aggregate over usage_events
# (Kanban #2728). Money is serialised as 4dp strings (no float drift),
# matching usage_daily/usage_monthly.
# ---------------------------------------------------------------------------


class UsageSessionAgentRow(BaseModel):
    """Per-(agent, model) spend within one session. agent_name NULL = Lead/main."""

    agent_name: str | None
    model: str
    cost_usd: str  # 4dp string
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    event_count: int


class UsageSessionRow(BaseModel):
    """One session_ext_id aggregate, with a per-agent breakdown."""

    session_ext_id: str
    total_cost_usd: str  # 4dp string; sum across this session's agents
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cache_hit_ratio: float  # cache_read / (input + cache_creation + cache_read); 0.0 if denom 0; round 4dp
    event_count: int
    first_occurred_at: str  # ISO 8601
    last_occurred_at: str  # ISO 8601
    agents: list[UsageSessionAgentRow]


class UsageSessionsResponse(BaseModel):
    """Response for GET /api/usage/sessions. Sessions most-recent first (by last_occurred_at)."""

    sessions: list[UsageSessionRow]
    limit: int
    offset: int
    returned: int  # len(sessions)
    total_cost_usd: str  # sum across returned sessions
