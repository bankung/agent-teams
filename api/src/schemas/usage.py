"""Pydantic schemas for the usage rollup endpoint (Kanban #2135)."""

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
