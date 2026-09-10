from __future__ import annotations

from math import floor

from pydantic import Field

from iqradar.config.schema import QuotaConfig, StrictConfigModel


class QuotaMetrics(StrictConfigModel):
    quota_percent_per_task: float = Field(ge=0)
    estimated_tasks_per_week: int = Field(ge=0)
    estimated_passes_per_week: int = Field(ge=0)
    output_quota_percent_per_task: float | None = Field(default=None, ge=0)


def calculate_quota(
    *,
    avg_cost_usd: float,
    pass_rate: float,
    avg_output_tokens: float,
    quota: QuotaConfig,
) -> QuotaMetrics:
    if avg_cost_usd < 0:
        raise ValueError("avg_cost_usd must be non-negative")
    if not 0 <= pass_rate <= 1:
        raise ValueError("pass_rate must be between 0 and 1")
    if avg_output_tokens < 0:
        raise ValueError("avg_output_tokens must be non-negative")

    quota_percent_per_task = avg_cost_usd / quota.weekly_budget_usd * 100
    estimated_tasks_per_week = floor(quota.weekly_budget_usd / avg_cost_usd) if avg_cost_usd else 0
    output_quota_percent_per_task = (
        avg_output_tokens / quota.weekly_output_token_budget * 100
        if quota.weekly_output_token_budget
        else None
    )
    return QuotaMetrics(
        quota_percent_per_task=quota_percent_per_task,
        estimated_tasks_per_week=estimated_tasks_per_week,
        estimated_passes_per_week=floor(estimated_tasks_per_week * pass_rate),
        output_quota_percent_per_task=output_quota_percent_per_task,
    )
