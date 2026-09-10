from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from iqradar.config.schema import EffortName, StrictConfigModel


class ConfidenceInterval(StrictConfigModel):
    method: Literal["wilson"] = "wilson"
    level: float = 0.95
    lower: float = Field(ge=0, le=1)
    upper: float = Field(ge=0, le=1)


class BenchmarkSummary(StrictConfigModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ModelEffortSummary(StrictConfigModel):
    benchmark: BenchmarkSummary
    model: str = Field(min_length=1)
    effort: EffortName
    tasks_total: int = Field(ge=0)
    #: records that reached a scoring verdict (excludes runner_error /
    #: verifier_error); pass_rate and IQ are computed over this denominator.
    tasks_scored: int = Field(default=0, ge=0)
    #: harness/verifier failures excluded from the IQ denominator (gateway
    #: unreachable, agent crash, verifier crash). Kept out of pass rate so
    #: infrastructure outages are not scored as the model failing tasks.
    infra_error_count: int = Field(default=0, ge=0)
    tasks_passed: int = Field(ge=0)
    tasks_failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    pass_rate_percent: float = Field(ge=0, le=100)
    iq: float = Field(ge=0, le=150)
    avg_cost_usd: float = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    avg_wall_time_sec: float = Field(ge=0)
    tasks_per_hour: float = Field(ge=0)
    avg_input_tokens: float = Field(ge=0)
    avg_output_tokens: float = Field(ge=0)
    output_tokens_per_min: float = Field(ge=0)
    avg_agent_steps: float = Field(ge=0)
    agent_steps_per_hour: float = Field(ge=0)
    cost_per_pass_usd: float | None = Field(default=None, ge=0)
    cost_per_iq_point_usd: float | None = Field(default=None, ge=0)
    quota_percent_per_task: float = Field(ge=0)
    estimated_tasks_per_week: int = Field(ge=0)
    estimated_passes_per_week: int = Field(ge=0)
    output_quota_percent_per_task: float | None = Field(default=None, ge=0)
    confidence: ConfidenceInterval


class AggregateSummary(StrictConfigModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    summaries: list[ModelEffortSummary]
