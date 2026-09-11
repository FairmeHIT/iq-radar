from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from iqradar.config.schema import EffortName, ProviderName, StrictConfigModel

RunStatus = Literal[
    "passed",
    "failed",
    "timeout",
    "runner_error",
    "verifier_error",
    "budget_stopped",
    "skipped",
]


class BenchmarkInfo(StrictConfigModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    language: str = Field(min_length=1)
    task_path: str = Field(min_length=1)


class ModelInfo(StrictConfigModel):
    provider: ProviderName
    base_url_hash: str = Field(min_length=1)
    name: str = Field(min_length=1)
    effort_requested: EffortName
    effort_effective: bool


class ResultInfo(StrictConfigModel):
    status: RunStatus
    verifier_passed: bool
    exit_code: int | None = None
    error_type: str | None = None
    error_message_redacted: str | None = None


class UsageInfo(StrictConfigModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    agent_steps: int = Field(ge=0)
    wall_time_sec: float = Field(ge=0)
    first_token_sec: float | None = Field(default=None, ge=0)
    first_content_sec: float | None = Field(default=None, ge=0)
    generation_time_sec: float | None = Field(default=None, ge=0)
    output_tokens_per_sec: float | None = Field(default=None, ge=0)
    usage_estimated: bool = False


class CostInfo(StrictConfigModel):
    currency: str = Field(default="USD", min_length=1)
    input_cost: float = Field(ge=0)
    cached_input_cost: float = Field(default=0.0, ge=0)
    output_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)


class ArtifactPaths(StrictConfigModel):
    patch_path: str | None = None
    log_path: str | None = None
    verifier_path: str | None = None


class RunRecord(StrictConfigModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(min_length=1)
    benchmark: BenchmarkInfo
    model: ModelInfo
    result: ResultInfo
    usage: UsageInfo
    cost: CostInfo
    artifacts: ArtifactPaths
    created_at: datetime
