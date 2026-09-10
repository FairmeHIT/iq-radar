from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from iqradar.config.loader import (
    ConfigEnvironmentError,
    load_benchmark_config,
    load_model_config,
    load_price_config,
)
from iqradar.config.schema import ModelConfigSet


def write_yaml(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_model_config_validates_effort_and_reports_missing_env_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://secret-provider.example/v1")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODEL", "secret-model-name")

    path = write_yaml(
        tmp_path,
        "models.yaml",
        """
models:
  - id: my-model
    display_name: My Reasoning Model
    provider: openai-compatible
    env:
      base_url: LLM_BASE_URL
      api_key: LLM_API_KEY
      model: LLM_MODEL
    gateway_headers:
      x-portkey-provider: openai
    effort:
      supported: true
      parameter_path: reasoning_effort
      values:
        low: low
        medium: medium
        high: high
        max: max
""",
    )

    config = load_model_config(path)

    assert config.get("my-model").effort.values["high"] == "high"
    assert config.get("my-model").gateway_headers == {"x-portkey-provider": "openai"}
    assert config.required_env_names() == ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"]

    with pytest.raises(ConfigEnvironmentError) as error:
        config.require_env(os.environ)

    message = str(error.value)
    assert "LLM_API_KEY" in message
    assert "https://secret-provider.example/v1" not in message
    assert "secret-model-name" not in message


def test_model_config_rejects_unknown_effort_key(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "models.yaml",
        """
models:
  - id: my-model
    display_name: My Reasoning Model
    provider: openai-compatible
    env:
      base_url: LLM_BASE_URL
      api_key: LLM_API_KEY
      model: LLM_MODEL
    effort:
      supported: true
      parameter_path: reasoning_effort
      values:
        low: low
        medium: medium
        high: high
        turbo: turbo
""",
    )

    with pytest.raises(ValidationError) as error:
        load_model_config(path)

    assert "turbo" in str(error.value)


def test_model_config_supports_fixed_model_name_without_model_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://127.0.0.1:8787/v1")
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    path = write_yaml(
        tmp_path,
        "models.yaml",
        """
models:
  - id: example-35b
    display_name: Example 35B
    provider: openai-compatible
    model_name: gateway/example-35b
    env:
      base_url: GATEWAY_BASE_URL
    effort:
      supported: false
      values: {}
""",
    )

    config = load_model_config(path)

    assert config.get("example-35b").effective_model_name({}) == "gateway/example-35b"
    assert config.required_env_names() == ["GATEWAY_BASE_URL"]
    config.require_env(os.environ)


def test_bundled_model_catalog_defaults_to_deepseek_flash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://localhost:8080/v1")

    config = load_model_config(Path("configs/models.yaml"))

    assert config.models[0].id == "deepseek-v4-flash"
    assert config.models[0].effective_model_name({}) == "gateway/deepseek-v4-flash"


def test_model_config_set_from_env_names_creates_catalog() -> None:
    config = ModelConfigSet.from_env_names(
        ["gateway/deepseek-v4-flash", "z.ai/glm-5.2"]
    )

    assert [model.id for model in config.models] == [
        "gateway/deepseek-v4-flash",
        "z.ai/glm-5.2",
    ]
    assert config.models[0].effective_model_name({}) == "gateway/deepseek-v4-flash"
    assert config.models[0].env.base_url == "GATEWAY_BASE_URL"
    assert config.get("z.ai/glm-5.2").id == "z.ai/glm-5.2"


def test_model_config_set_from_env_names_dedupes_and_skips_blanks() -> None:
    config = ModelConfigSet.from_env_names([" a/b ", "", "a/b", "c/d"])

    assert [model.id for model in config.models] == ["a/b", "c/d"]


def test_model_config_set_from_env_names_rejects_empty() -> None:
    with pytest.raises(ValueError):
        ModelConfigSet.from_env_names([])


def test_model_config_rejects_authorization_gateway_header(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "models.yaml",
        """
models:
  - id: my-model
    display_name: My Reasoning Model
    provider: openai-compatible
    model_name: openai/my-model
    env:
      base_url: LLM_BASE_URL
    gateway_headers:
      Authorization: Bearer secret
    effort:
      supported: false
      values: {}
""",
    )

    with pytest.raises(ValidationError) as error:
        load_model_config(path)

    assert "Authorization" in str(error.value)


def test_load_price_config_validates_quota_and_prices(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "prices.yaml",
        """
prices:
  my-model:
    currency: USD
    input_usd_per_1m: 2.0
    cached_input_usd_per_1m: 0.25
    output_usd_per_1m: 20.0
quota:
  weekly_budget_usd: 20.0
  weekly_output_token_budget: null
""",
    )

    config = load_price_config(path)

    assert config.prices["my-model"].input_usd_per_1m == 2.0
    assert config.prices["my-model"].cached_input_usd_per_1m == 0.25
    assert config.quota.weekly_budget_usd == 20.0
    assert config.quota.weekly_output_token_budget is None


def test_load_price_config_rejects_negative_prices(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "prices.yaml",
        """
prices:
  my-model:
    currency: USD
    input_usd_per_1m: -1.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 20.0
quota:
  weekly_budget_usd: 20.0
  weekly_output_token_budget: null
""",
    )

    with pytest.raises(ValidationError):
        load_price_config(path)


def test_load_benchmark_config_validates_deep_swe_defaults(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "benchmark.yaml",
        """
benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: vendor/deep-swe
    tasks_path: vendor/deep-swe/tasks
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: data/artifacts
""",
    )

    config = load_benchmark_config(path)

    benchmark = config.benchmarks["deep-swe"]
    assert benchmark.repo_url == "https://github.com/datacurve-ai/deep-swe.git"
    assert benchmark.local_path == Path("vendor/deep-swe")
    assert benchmark.default_timeout_sec == 7200
    assert benchmark.default_concurrency == 1


def test_benchmark_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = write_yaml(
        tmp_path,
        "benchmark.yaml",
        """
benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: vendor/deep-swe
    tasks_path: vendor/deep-swe/tasks
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: data/artifacts
    unsafe_extra: true
""",
    )

    with pytest.raises(ValidationError) as error:
        load_benchmark_config(path)

    assert "unsafe_extra" in str(error.value)
