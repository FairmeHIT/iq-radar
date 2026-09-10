"""Preflight readiness checks: every backend must report a clear, actionable
error (or ``None``) and the service must refuse to queue runs against a
broken dataset instead of creating a failed run record."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from iqradar.benchmarks.api_eval import ApiEvalBackend
from iqradar.benchmarks.deep_swe import DeepSweBackend
from iqradar.benchmarks.terminal_bench import TerminalBenchBackend
from iqradar.config.schema import BenchmarkConfig
from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


def _tb_config(tmp_path: Path, tasks_path: Path) -> BenchmarkConfig:
    return BenchmarkConfig(
        type="terminal-bench",
        local_path=tmp_path / "terminal-bench",
        tasks_path=tasks_path,
        artifact_root=tmp_path / "jobs",
        env_file=tmp_path / ".env",
        repo_url="https://github.com/x/y",
        default_timeout_sec=7200,
        default_concurrency=1,
    )


class TestTerminalBenchPreflight:
    def test_reports_missing_tasks_path(self, tmp_path: Path) -> None:
        backend = TerminalBenchBackend(
            _tb_config(tmp_path, tmp_path / "nope"), name="terminal-bench"
        )
        error = backend.preflight()
        assert error is not None
        assert "tasks_path does not exist" in error

    def test_reports_missing_tb_binary(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task-a").mkdir()
        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench.shutil.which", lambda name: None
        )
        backend = TerminalBenchBackend(_tb_config(tmp_path, tasks_dir), name="tb")
        error = backend.preflight()
        assert error is not None
        assert "tb executable not found" in error

    def test_reports_empty_tasks_dir(self, tmp_path: Path) -> None:
        backend = TerminalBenchBackend(
            _tb_config(tmp_path, tmp_path / "tasks"), name="tb"
        )
        (tmp_path / "tasks").mkdir()
        error = backend.preflight()
        assert error is not None
        assert "no tasks found" in error

    def test_ok_when_binary_and_tasks_exist(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task-a").mkdir()
        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench.shutil.which",
            lambda name: "/usr/local/bin/tb",
        )
        backend = TerminalBenchBackend(_tb_config(tmp_path, tasks_dir), name="tb")
        assert backend.preflight() is None


def _deep_swe_config(tmp_path: Path) -> DeepSweConfig:
    return DeepSweConfig(
        local_path=tmp_path / "deep-swe",
        tasks_path=tmp_path / "tasks",
        env_file=tmp_path / "deep-swe" / ".env",
        jobs_root=tmp_path / "jobs",
        default_timeout_sec=7200,
        n_concurrent=1,
    )


class TestDeepSwePreflight:
    def test_reports_missing_pier(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            "iqradar.benchmarks.deep_swe.shutil.which", lambda name: None
        )
        backend = DeepSweBackend(_deep_swe_config(tmp_path), name="deep-swe")
        error = backend.preflight()
        assert error is not None
        assert "pier executable not found" in error

    def test_reports_missing_tasks_path(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            "iqradar.benchmarks.deep_swe.shutil.which",
            lambda name: "/usr/local/bin/pier",
        )
        backend = DeepSweBackend(_deep_swe_config(tmp_path), name="deep-swe")
        error = backend.preflight()
        assert error is not None
        assert "tasks_path does not exist" in error

    def test_reports_test_tasks_absent(self, tmp_path: Path, monkeypatch: Any) -> None:
        (tmp_path / "tasks").mkdir()
        monkeypatch.setattr(
            "iqradar.benchmarks.deep_swe.shutil.which",
            lambda name: "/usr/local/bin/pier",
        )
        config = DeepSweConfig(
            local_path=tmp_path / "deep-swe",
            tasks_path=tmp_path / "tasks",
            env_file=tmp_path / "deep-swe" / ".env",
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=7200,
            n_concurrent=1,
            test_tasks=("missing-a", "missing-b"),
        )
        backend = DeepSweBackend(config, name="deep-swe")
        error = backend.preflight()
        assert error is not None
        assert "none of the configured test_tasks" in error

    def test_ok_when_pier_and_tasks_exist(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        (tmp_path / "tasks" / "task-a").mkdir(parents=True)
        monkeypatch.setattr(
            "iqradar.benchmarks.deep_swe.shutil.which",
            lambda name: "/usr/local/bin/pier",
        )
        backend = DeepSweBackend(_deep_swe_config(tmp_path), name="deep-swe")
        assert backend.preflight() is None


class TestApiEvalPreflight:
    def _backend(self, tmp_path: Path, dataset_path: Path) -> ApiEvalBackend:
        config = BenchmarkConfig(
            type="api-eval",
            repo_url="https://example.com/dataset",
            local_path=tmp_path / "local",
            tasks_path=dataset_path,
            default_timeout_sec=3600,
            default_concurrency=1,
            artifact_root=tmp_path / "jobs",
        )
        return ApiEvalBackend(config, name="aime-2024")

    def test_reports_missing_dataset_file(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, tmp_path / "nope.jsonl")
        error = backend.preflight()
        assert error is not None
        assert "tasks file does not exist" in error

    def test_reports_empty_dataset(self, tmp_path: Path) -> None:
        dataset = tmp_path / "empty.jsonl"
        dataset.write_text("")
        backend = self._backend(tmp_path, dataset)
        error = backend.preflight()
        assert error is not None
        assert "no items found" in error

    def test_ok_when_dataset_loads(self, tmp_path: Path) -> None:
        dataset = tmp_path / "aime-2024.jsonl"
        dataset.write_text(
            '{"task_id": "q1", "prompt": "1+1?", "reference": "2"}\n'
        )
        backend = self._backend(tmp_path, dataset)
        assert backend.preflight() is None


class TestServicePreflight:
    def test_submit_batch_refuses_broken_dataset(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """submit_batch must fail fast (ValueError) instead of queueing a batch
        that would produce one failed run record per model."""
        monkeypatch.setattr(
            "iqradar.benchmarks.deep_swe.shutil.which",
            lambda name: "/usr/local/bin/pier",
        )
        service = DeepSweService(
            runs=FileDeepSweRunStore(tmp_path / "runs"),
            config=_deep_swe_config(tmp_path),  # tasks_path never created
        )
        with pytest.raises(ValueError, match="not ready.*tasks_path does not exist"):
            service.submit_batch(
                model_ids=["m1"],
                model_names=["openai/m1"],
                n_tasks=2,
                sample_seed=1,
            )

    def test_submit_refuses_broken_dataset(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(
            "iqradar.benchmarks.deep_swe.shutil.which",
            lambda name: "/usr/local/bin/pier",
        )
        service = DeepSweService(
            runs=FileDeepSweRunStore(tmp_path / "runs"),
            config=_deep_swe_config(tmp_path),
        )
        with pytest.raises(ValueError, match="not ready"):
            service.submit(
                model_id="m1",
                model_name="openai/m1",
                n_tasks=2,
                sample_seed=1,
                base_url="http://gateway/v1",
            )
