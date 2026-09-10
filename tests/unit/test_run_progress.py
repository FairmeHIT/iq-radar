"""题目进度（测试页“总共 N 题、已完成 M 题”）单测。

覆盖各后端的 ``progress()`` 实现（pier/harbor 的 job 级统计解析、
api-eval 的实时计数与 results.jsonl 回退）以及 ``DeepSweService.progress``
的 total 补齐/回退合并逻辑。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from iqradar.benchmarks.api_eval import ApiEvalBackend
from iqradar.benchmarks.base import pier_job_progress
from iqradar.benchmarks.deep_swe import DeepSweBackend
from iqradar.benchmarks.terminal_bench_2 import TerminalBench2Backend
from iqradar.config.schema import BenchmarkConfig
from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import DeepSweRun, FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


# ---------------------------------------------------------------------------
# pier / harbor job-level result.json 解析
# ---------------------------------------------------------------------------

def _write_pier_result(job_dir: Path, payload: dict) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestPierJobProgress:
    def test_parses_live_stats(self, tmp_path: Path) -> None:
        path = _write_pier_result(
            tmp_path,
            {
                "finished_at": None,
                "n_total_trials": 10,
                "stats": {
                    "n_completed_trials": 4,
                    "n_errored_trials": 4,
                    "n_running_trials": 1,
                    "n_pending_trials": 5,
                },
            },
        )
        assert pier_job_progress(path) == {
            "total": 10,
            "completed": 4,
            "running": 1,
            "pending": 5,
        }

    def test_finished_job_keeps_only_totals(self, tmp_path: Path) -> None:
        path = _write_pier_result(
            tmp_path,
            {
                "finished_at": "2026-09-05T00:00:00Z",
                "n_total_trials": 10,
                "stats": {"n_completed_trials": 10, "n_errored_trials": 2},
            },
        )
        assert pier_job_progress(path) == {"total": 10, "completed": 10}

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert pier_job_progress(tmp_path / "absent.json") is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "result.json"
        path.write_text('{"stats": {"n_comp', encoding="utf-8")
        assert pier_job_progress(path) is None

    def test_missing_total_returns_none(self, tmp_path: Path) -> None:
        path = _write_pier_result(
            tmp_path,
            {"finished_at": None, "stats": {"n_completed_trials": 3}},
        )
        assert pier_job_progress(path) is None

    def test_completed_is_clamped_to_total(self, tmp_path: Path) -> None:
        path = _write_pier_result(
            tmp_path,
            {"n_total_trials": 3, "stats": {"n_completed_trials": 99}},
        )
        assert pier_job_progress(path) == {"total": 3, "completed": 3}


# ---------------------------------------------------------------------------
# 后端 progress 实现
# ---------------------------------------------------------------------------

def _deep_swe_backend(tmp_path: Path) -> DeepSweBackend:
    return DeepSweBackend(
        DeepSweConfig(
            local_path=tmp_path / "ds",
            tasks_path=tmp_path / "tasks",
            env_file=tmp_path / "ds" / ".env",
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=7200,
            n_concurrent=1,
        ),
        name="deep-swe",
    )


def _trial(job_dir: Path, name: str, *, finished: bool = True) -> None:
    trial = job_dir / name
    trial.mkdir(parents=True, exist_ok=True)
    if finished:
        (trial / "result.json").write_text("{}", encoding="utf-8")


class TestBackendProgress:
    def test_deep_swe_uses_pier_stats(self, tmp_path: Path) -> None:
        backend = _deep_swe_backend(tmp_path)
        _write_pier_result(
            tmp_path / "jobs" / "run-1",
            {
                "n_total_trials": 10,
                "stats": {"n_completed_trials": 4, "n_running_trials": 1},
            },
        )
        assert backend.progress("run-1") == {
            "total": 10,
            "completed": 4,
            "running": 1,
        }

    def test_deep_swe_falls_back_to_trial_dir_count(self, tmp_path: Path) -> None:
        backend = _deep_swe_backend(tmp_path)
        job_dir = tmp_path / "jobs" / "run-2"
        _trial(job_dir, "task-a__h1")
        _trial(job_dir, "task-b__h2")
        _trial(job_dir, "task-c__h3", finished=False)  # 进行中的 trial 不计
        assert backend.progress("run-2") == {"completed": 2}

    def test_deep_swe_empty_job_dir_returns_none(self, tmp_path: Path) -> None:
        backend = _deep_swe_backend(tmp_path)
        (tmp_path / "jobs" / "run-3").mkdir(parents=True)
        assert backend.progress("run-3") is None
        assert backend.progress("absent-run") is None

    def test_terminal_bench_2_uses_harbor_stats(self, tmp_path: Path) -> None:
        backend = TerminalBench2Backend(
            BenchmarkConfig(
                type="terminal-bench-2",
                local_path=tmp_path / "tb2",
                tasks_path=tmp_path / "tasks",
                artifact_root=tmp_path / "jobs",
                env_file=tmp_path / ".env",
                repo_url="https://example.com/tb2",
                default_timeout_sec=7200,
                default_concurrency=4,
            ),
            name="terminal-bench-2",
        )
        _write_pier_result(
            tmp_path / "jobs" / "run-1",
            {
                "n_total_trials": 74,
                "stats": {"n_completed_trials": 13, "n_running_trials": 4},
            },
        )
        assert backend.progress("run-1") == {
            "total": 74,
            "completed": 13,
            "running": 4,
        }

    def test_terminal_bench_2_falls_back_to_trial_dirs(self, tmp_path: Path) -> None:
        backend = TerminalBench2Backend(
            BenchmarkConfig(
                type="terminal-bench-2",
                local_path=tmp_path / "tb2",
                tasks_path=tmp_path / "tasks",
                artifact_root=tmp_path / "jobs",
                env_file=tmp_path / ".env",
                repo_url="https://example.com/tb2",
                default_timeout_sec=7200,
                default_concurrency=4,
            ),
            name="terminal-bench-2",
        )
        job_dir = tmp_path / "jobs" / "run-2"
        _trial(job_dir, "music-harmony__XBs3PqL")
        _trial(job_dir, "html-js-filter__5RXG3v", finished=False)
        assert backend.progress("run-2") == {"completed": 1}


# ---------------------------------------------------------------------------
# api-eval：实时计数 + 日志行前缀 + results.jsonl 回退
# ---------------------------------------------------------------------------

def _api_eval_backend(tmp_path: Path, *, concurrency: int = 1) -> ApiEvalBackend:
    dataset = tmp_path / "q.jsonl"
    dataset.write_text(
        "\n".join(
            json.dumps({"task_id": f"t{i}", "prompt": f"p{i}", "reference": "r"})
            for i in range(3)
        )
        + "\n",
        encoding="utf-8",
    )
    return ApiEvalBackend(
        BenchmarkConfig(
            type="api-eval",
            repo_url="https://example.com/ds",
            local_path=tmp_path / "local",
            tasks_path=dataset,
            default_timeout_sec=3600,
            default_concurrency=concurrency,
            artifact_root=tmp_path / "jobs",
        ),
        name="demo",
    )


class TestApiEvalProgress:
    def test_sequential_run_tracks_live_progress_and_log_prefix(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        backend = _api_eval_backend(tmp_path)
        observed: list[dict | None] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            # 每次调用时窥探进度：第 k 题作答中，已完成 k-1 题
            observed.append(backend.progress("r1"))
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(
            "iqradar.benchmarks.api_eval.gateway_complete", fake_gateway
        )
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=3,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )

        assert returncode == 0
        # 作答第 1 题时已完成 0，作答第 2 题时已完成 1 …
        assert observed == [
            {"total": 3, "completed": 0},
            {"total": 3, "completed": 1},
            {"total": 3, "completed": 2},
        ]
        assert backend.progress("r1") == {"total": 3, "completed": 3}

        log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
        assert "[1/3] t" in log_text
        assert "[3/3] t" in log_text

    def test_parallel_run_tracks_progress_and_logs_as_items_finish(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        backend = _api_eval_backend(tmp_path, concurrency=3)

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(
            "iqradar.benchmarks.api_eval.gateway_complete", fake_gateway
        )
        returncode, _ = backend.run(
            run_id="r2",
            model_name="openai/m",
            n_tasks=3,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            n_concurrent=3,
        )

        assert returncode == 0
        assert backend.progress("r2") == {"total": 3, "completed": 3}
        log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
        assert log_text.count("\n") == 3
        assert "[3/3]" in log_text

    def test_progress_falls_back_to_results_jsonl_when_finished(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        backend = _api_eval_backend(tmp_path)

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(
            "iqradar.benchmarks.api_eval.gateway_complete", fake_gateway
        )
        backend.run(
            run_id="r3",
            model_name="openai/m",
            n_tasks=3,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )

        # 新后端实例 = 服务重启后内存进度丢失：回退数 results.jsonl 行数
        restarted = _api_eval_backend(tmp_path)
        assert restarted.progress("r3") == {"total": 3, "completed": 3}

    def test_progress_none_for_unfinished_run_without_memory(
        self, tmp_path: Path
    ) -> None:
        backend = _api_eval_backend(tmp_path)
        run_dir = tmp_path / "jobs" / "r4"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"task_id": "t0"}\n', encoding="utf-8"
        )
        # 有部分结果但未结束（无 result.json）：无法断定进度
        assert backend.progress("r4") is None


# ---------------------------------------------------------------------------
# DeepSweService.progress 合并逻辑
# ---------------------------------------------------------------------------

def _service_with_run(
    tmp_path: Path, run_id: str, *, status: str = "running", n_tasks: int = 10
) -> DeepSweService:
    store = FileDeepSweRunStore(tmp_path / "runs")
    store.save(
        DeepSweRun(
            run_id=run_id,
            status=status,  # type: ignore[arg-type]
            model_id="model-a",
            n_tasks=n_tasks,
            sample_seed=0,
            created_at=datetime.now(UTC),
            jobs_dir=str(tmp_path / "jobs" / run_id),
        )
    )
    return DeepSweService(runs=store, config=_deep_swe_config(tmp_path))


def _deep_swe_config(tmp_path: Path) -> DeepSweConfig:
    (tmp_path / "tasks").mkdir(exist_ok=True)
    return DeepSweConfig(
        local_path=tmp_path / "ds",
        tasks_path=tmp_path / "tasks",
        env_file=tmp_path / "ds" / ".env",
        jobs_root=tmp_path / "jobs",
        default_timeout_sec=7200,
        n_concurrent=1,
    )


class TestServiceProgress:
    def test_merges_backend_progress(self, tmp_path: Path) -> None:
        service = _service_with_run(tmp_path, "run-1")
        _write_pier_result(
            tmp_path / "jobs" / "run-1",
            {
                "n_total_trials": 10,
                "stats": {
                    "n_completed_trials": 4,
                    "n_running_trials": 1,
                    "n_pending_trials": 5,
                },
            },
        )
        assert service.progress("run-1") == {
            "total": 10,
            "completed": 4,
            "running": 1,
            "pending": 5,
        }

    def test_fills_total_from_run_n_tasks(self, tmp_path: Path) -> None:
        service = _service_with_run(tmp_path, "run-2", n_tasks=5)
        job_dir = tmp_path / "jobs" / "run-2"
        _trial(job_dir, "task-a__h1")
        _trial(job_dir, "task-b__h2")
        # 后端只给 completed（无 total）→ 用 run.n_tasks 补齐
        assert service.progress("run-2") == {"total": 5, "completed": 2}

    def test_active_run_without_artifacts_falls_back_to_zero(
        self, tmp_path: Path
    ) -> None:
        service = _service_with_run(tmp_path, "run-3")
        # Docker 环境构建阶段：无任何产物 → 0/N
        assert service.progress("run-3") == {"total": 10, "completed": 0}

    def test_finished_run_without_artifacts_returns_none(
        self, tmp_path: Path
    ) -> None:
        service = _service_with_run(tmp_path, "run-4", status="completed")
        assert service.progress("run-4") is None

    def test_unknown_run_returns_none(self, tmp_path: Path) -> None:
        service = _service_with_run(tmp_path, "run-5")
        assert service.progress("absent-run") is None
