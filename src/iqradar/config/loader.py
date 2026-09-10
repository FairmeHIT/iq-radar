from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel

from iqradar.config.schema import (
    BenchmarkConfig,
    BenchmarkConfigSet,
    ConfigEnvironmentError,
    ModelConfigSet,
    PriceConfig,
)

ConfigModel = TypeVar("ConfigModel", bound=BaseModel)


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a YAML mapping: {path}")
    return data


def _load_config(path: Path, schema: type[ConfigModel]) -> ConfigModel:
    return schema.model_validate(_load_yaml(path))


def load_model_config(path: Path) -> ModelConfigSet:
    return _load_config(path, ModelConfigSet)


def load_price_config(path: Path) -> PriceConfig:
    return _load_config(path, PriceConfig)


def parse_price_config(content: bytes | str) -> PriceConfig:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("Price config must contain a YAML mapping")
    return PriceConfig.model_validate(data)


def load_benchmark_config(path: Path) -> BenchmarkConfigSet:
    return _load_config(path, BenchmarkConfigSet)


def anchor_benchmark_config(
    benchmark: BenchmarkConfig,
    project_root: Path,
) -> BenchmarkConfig:
    return benchmark.model_copy(
        update={
            "local_path": _anchor_path(benchmark.local_path, project_root),
            "tasks_path": _anchor_path(benchmark.tasks_path, project_root),
            "artifact_root": _anchor_path(benchmark.artifact_root, project_root),
            "env_file": _anchor_path(benchmark.resolved_env_file(), project_root),
        }
    )


def anchor_benchmark_config_set(
    config: BenchmarkConfigSet,
    project_root: Path,
) -> BenchmarkConfigSet:
    return config.model_copy(
        update={
            "benchmarks": {
                name: anchor_benchmark_config(benchmark, project_root)
                for name, benchmark in config.benchmarks.items()
            }
        }
    )


def _anchor_path(path: Path, project_root: Path) -> Path:
    return path if path.is_absolute() else project_root / path


__all__ = [
    "ConfigEnvironmentError",
    "anchor_benchmark_config",
    "anchor_benchmark_config_set",
    "load_benchmark_config",
    "load_model_config",
    "load_price_config",
    "parse_price_config",
]
