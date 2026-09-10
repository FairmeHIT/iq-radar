from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EffortName = Literal["low", "medium", "high", "max"]
ProviderName = Literal["openai-compatible"]
BenchmarkType = Literal[
    "deep-swe", "terminal-bench", "terminal-bench-2", "api-eval"
]

ALLOWED_EFFORTS = {"low", "medium", "high", "max"}


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfigEnvironmentError(RuntimeError):
    def __init__(self, missing_names: list[str]) -> None:
        self.missing_names = missing_names
        joined = ", ".join(missing_names)
        super().__init__(f"Missing required environment variables: {joined}")


class ModelEnvConfig(StrictConfigModel):
    base_url: str = Field(min_length=1)
    api_key: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)

    def names(self) -> list[str]:
        return [name for name in (self.base_url, self.api_key, self.model) if name]


class EffortConfig(StrictConfigModel):
    supported: bool
    parameter_path: str | None = None
    values: dict[EffortName, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_effort_mapping(self) -> EffortConfig:
        unknown = sorted(set(self.values) - ALLOWED_EFFORTS)
        if unknown:
            raise ValueError(f"Unknown effort values: {', '.join(unknown)}")
        if self.supported and not self.parameter_path:
            raise ValueError("parameter_path is required when effort is supported")
        return self


class ModelConfig(StrictConfigModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    provider: ProviderName
    model_name: str | None = Field(default=None, min_length=1)
    env: ModelEnvConfig
    gateway_headers: dict[str, str] = Field(default_factory=dict)
    effort: EffortConfig

    @model_validator(mode="after")
    def validate_model_source(self) -> ModelConfig:
        if not self.model_name and not self.env.model:
            raise ValueError("model_name or env.model is required")
        return self

    @field_validator("gateway_headers")
    @classmethod
    def validate_gateway_headers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for name, header_value in value.items():
            if not name.strip() or not header_value.strip():
                raise ValueError("gateway header names and values must be non-empty")
            if name.lower() == "authorization":
                raise ValueError("Authorization must be configured with env.api_key")
            normalized[name] = header_value
        return normalized

    def effective_model_name(self, environ: Mapping[str, str] | None = None) -> str:
        if self.model_name:
            return self.model_name
        if not self.env.model:
            raise ValueError("model_name or env.model is required")
        source = os.environ if environ is None else environ
        return source.get(self.env.model, self.id)


class ModelConfigSet(StrictConfigModel):
    models: list[ModelConfig] = Field(min_length=1)

    @classmethod
    def from_env_names(cls, names: Iterable[str]) -> ModelConfigSet:
        """Build a catalog from an env-provided model-name list.

        Each name becomes a model whose ``id``/``display_name``/``model_name``
        all equal the name; the gateway base URL comes from
        ``GATEWAY_BASE_URL``. This lets the test-page model list be maintained
        in ``.env`` (``IQRADAR_MODEL_NAMES``) instead of the YAML catalog.
        Blank entries and duplicates are dropped; an empty result raises.
        """
        seen: set[str] = set()
        models: list[ModelConfig] = []
        for raw_name in names:
            name = raw_name.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            models.append(
                ModelConfig(
                    id=name,
                    display_name=name,
                    provider="openai-compatible",
                    model_name=name,
                    env=ModelEnvConfig(base_url="GATEWAY_BASE_URL"),
                    effort=EffortConfig(supported=False, values={}),
                )
            )
        if not models:
            raise ValueError("No model names provided")
        return cls(models=models)

    @model_validator(mode="after")
    def validate_unique_model_ids(self) -> ModelConfigSet:
        ids = [model.id for model in self.models]
        duplicates = sorted({model_id for model_id in ids if ids.count(model_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate model ids: {', '.join(duplicates)}")
        return self

    def get(self, model_id: str) -> ModelConfig:
        for model in self.models:
            if model.id == model_id:
                return model
        for model in self.models:
            if model.effective_model_name() == model_id:
                return model
        raise KeyError(model_id)

    def required_env_names(self) -> list[str]:
        names = {name for model in self.models for name in model.env.names()}
        return sorted(names)

    def missing_env_names(self, environ: Mapping[str, str] | None = None) -> list[str]:
        source = os.environ if environ is None else environ
        return [name for name in self.required_env_names() if not source.get(name)]

    def require_env(self, environ: Mapping[str, str] | None = None) -> None:
        missing = self.missing_env_names(environ)
        if missing:
            raise ConfigEnvironmentError(missing)


class ModelPrice(StrictConfigModel):
    currency: str = Field(default="USD", min_length=1)
    input_usd_per_1m: float = Field(ge=0)
    cached_input_usd_per_1m: float = Field(default=0.0, ge=0)
    output_usd_per_1m: float = Field(ge=0)


class QuotaConfig(StrictConfigModel):
    weekly_budget_usd: float = Field(gt=0)
    weekly_output_token_budget: int | None = Field(default=None, gt=0)


class PriceConfig(StrictConfigModel):
    prices: dict[str, ModelPrice] = Field(min_length=1)
    quota: QuotaConfig


class BenchmarkConfig(StrictConfigModel):
    type: BenchmarkType = "deep-swe"
    repo_url: str = Field(min_length=1)
    local_path: Path
    tasks_path: Path
    default_timeout_sec: int = Field(gt=0)
    default_concurrency: int = Field(gt=0)
    artifact_root: Path
    # 单次网关调用超时（秒）；仅 api-eval 使用。缺省采用后端默认值
    # （DEFAULT_REQUEST_TIMEOUT_SEC）。复杂推理题（如 GPQA + effort=high）
    # 单次生成可能超过一分钟，默认 60s 会误截断，建议按基准调大。
    request_timeout_sec: int | None = Field(default=None, gt=0)
    # 单次 chat/completions 的输出 token 预算（max_tokens）；仅 api-eval 使用。
    # 缺省不传（用网关默认，如 8192）。ARC-AGI-2 这类长推理题常把整个预算
    # 耗在思考上导致 content 为空，需调大才能拿到最终答案。
    max_tokens: int | None = Field(default=None, gt=0)
    # Model API environment file (OPENAI_BASE_URL / OPENAI_API_KEY / MSWEA_API_KEY).
    # Defaults to ``<local_path>/.env`` when unset.
    env_file: Path | None = None
    # HuggingFace dataset id and (optional) mirror base URL used by the dataset
    # preparation scripts (api-eval benchmarks).
    dataset_name: str | None = Field(default=None, min_length=1)
    dataset_mirror: str | None = Field(default=None, min_length=1)
    # Agent identifier passed to the benchmark runner (pier agent import path for
    # deep-swe; tbench agent name or ``module:Class`` import path for
    # terminal-bench, e.g. the nested-slash-tolerant MiniSweAgentCompat).
    agent: str | None = Field(default=None, min_length=1)
    # Terminal-Bench: optional dataset registry URL; when unset tbench uses its
    # built-in default registry.
    tb_registry_url: str | None = Field(default=None, min_length=1)
    # Fixed task subset for this benchmark. When set, every run samples only
    # from these task ids (deep-swe: pier ``-i`` include filters; terminal-bench:
    # task-dir names) and the UI caps n_tasks at the pool size. Intended to pair
    # with a prewarm of exactly these tasks' images so test-time runs never hit
    # the network to build environments.
    test_tasks: list[str] | None = Field(default=None)

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> BenchmarkConfig:
        return self

    def resolved_env_file(self) -> Path:
        return self.env_file if self.env_file is not None else self.local_path / ".env"


class BenchmarkConfigSet(StrictConfigModel):
    benchmarks: dict[str, BenchmarkConfig] = Field(min_length=1)
