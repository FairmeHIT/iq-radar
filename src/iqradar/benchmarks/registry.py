from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from iqradar.benchmarks.api_eval import ApiEvalBackend
from iqradar.benchmarks.base import BenchmarkBackend
from iqradar.benchmarks.deep_swe import DeepSweBackend
from iqradar.benchmarks.terminal_bench import TerminalBenchBackend
from iqradar.benchmarks.terminal_bench_2 import TerminalBench2Backend
from iqradar.config.loader import anchor_benchmark_config_set
from iqradar.config.schema import BenchmarkConfig, BenchmarkConfigSet
from iqradar.deepswe.runner import DeepSweConfig

# Human-readable labels keyed by benchmark config key (not type).
BENCHMARK_LABELS: dict[str, str] = {
    "deep-swe": "DeepSWE",
    "terminal-bench": "Terminal-Bench",
    "terminal-bench-2": "Terminal-Bench 2.0",
    "gpqa-diamond": "GPQA Diamond",
    "aime-2024": "AIME 2024",
    "mmlu-pro": "MMLU-Pro",
    "arc-agi-2": "ARC-AGI-2",
    "chinese-simpleqa": "Chinese-SimpleQA",
    "hmmt-feb-2026": "HMMT 2026 Feb",
    "hle": "HLE (Humanity's Last Exam)",
    "livecodebench": "LiveCodeBench",
    "codeforcesRating": "Codeforces (Rating)",
    "imo-answerbench": "IMOAnswerBench",
}

# Benchmark type → default label (fallback when config key has no label).
TYPE_LABELS: dict[str, str] = {
    "deep-swe": "DeepSWE",
    "terminal-bench": "Terminal-Bench",
    "terminal-bench-2": "Terminal-Bench 2.0",
    "api-eval": "API Eval",
}


def build_backends(
    config_set: BenchmarkConfigSet,
    project_root: Path,
    *,
    run_log_path: Callable[[str], Path] | None = None,
    gateway_settings: object | None = None,
) -> dict[str, BenchmarkBackend]:
    """Construct one ``BenchmarkBackend`` per entry in the config set.

    The returned dict is keyed by the benchmark config key (e.g. ``"deep-swe"``,
    ``"terminal-bench"``). ``gateway_settings`` is the
    runtime gateway-config store from the test page (optional; backends fall
    back to their env-file/env-var resolution when None).
    """
    anchored = anchor_benchmark_config_set(config_set, project_root)
    backends: dict[str, BenchmarkBackend] = {}
    for name, benchmark in anchored.benchmarks.items():
        backends[name] = _build_backend(
            benchmark,
            name=name,
            run_log_path=run_log_path,
            gateway_settings=gateway_settings,
        )
    return backends


def _build_backend(
    benchmark: BenchmarkConfig,
    *,
    name: str,
    run_log_path: Callable[[str], Path] | None = None,
    gateway_settings: object | None = None,
) -> BenchmarkBackend:
    if benchmark.type == "deep-swe":
        return DeepSweBackend(
            _deep_swe_config(benchmark),
            name=name,
            run_log_path=run_log_path,
            gateway_settings=gateway_settings,
        )
    if benchmark.type == "terminal-bench":
        return TerminalBenchBackend(
            benchmark,
            name=name,
            run_log_path=run_log_path,
            gateway_settings=gateway_settings,
        )
    if benchmark.type == "terminal-bench-2":
        return TerminalBench2Backend(
            benchmark,
            name=name,
            run_log_path=run_log_path,
            gateway_settings=gateway_settings,
        )
    if benchmark.type == "api-eval":
        return ApiEvalBackend(
            benchmark,
            name=name,
            run_log_path=run_log_path,
            gateway_settings=gateway_settings,
        )
    raise ValueError(f"unsupported benchmark type: {benchmark.type}")


def _deep_swe_config(benchmark: BenchmarkConfig) -> DeepSweConfig:
    return DeepSweConfig(
        local_path=benchmark.local_path,
        tasks_path=benchmark.tasks_path,
        env_file=benchmark.resolved_env_file(),
        jobs_root=benchmark.artifact_root,
        default_timeout_sec=benchmark.default_timeout_sec,
        n_concurrent=benchmark.default_concurrency,
        test_tasks=tuple(benchmark.test_tasks or ()),
    )


def label(key: str, benchmark_type: str | None = None) -> str:
    """Human-readable label for a benchmark key or type."""
    if key in BENCHMARK_LABELS:
        return BENCHMARK_LABELS[key]
    if benchmark_type and benchmark_type in TYPE_LABELS:
        return TYPE_LABELS[benchmark_type]
    return key.replace("-", " ").title()