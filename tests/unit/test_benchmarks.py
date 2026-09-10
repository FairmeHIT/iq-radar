"""Unit tests for the multi-benchmark framework.

Tests cover:
- Config schema validation
- Backend task sampling methods
- Command construction (DeepSWE pier, tbench CLI)
- Registry building
- Service dispatch
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from iqradar.benchmarks.base import BenchmarkBackend, process_alive
from iqradar.benchmarks.deep_swe import DeepSweBackend
from iqradar.benchmarks.registry import BENCHMARK_LABELS, build_backends, label
from iqradar.benchmarks.terminal_bench import TerminalBenchBackend
from iqradar.benchmarks.terminal_bench_2 import TerminalBench2Backend
from iqradar.settings.inference import InferenceEndpoint
from iqradar.config.loader import load_benchmark_config, anchor_benchmark_config_set
from iqradar.config.schema import BenchmarkConfig, BenchmarkConfigSet
from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import DeepSweRun, FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

class TestBenchmarkConfigSchema:
    def test_deep_swe_minimal(self) -> None:
        cfg = BenchmarkConfig(
            type="deep-swe", local_path=Path("/a"), tasks_path=Path("/b"),
            artifact_root=Path("/c"), repo_url="https://github.com/x/y",
            default_timeout_sec=7200, default_concurrency=1,
        )
        assert cfg.type == "deep-swe"
        assert cfg.env_file is None
        assert cfg.resolved_env_file() == Path("/a/.env")

    def test_terminal_bench_accepts_agent(self) -> None:
        cfg = BenchmarkConfig(
            type="terminal-bench",
            local_path=Path("/a"),
            tasks_path=Path("/b"),
            artifact_root=Path("/c"),
            agent="mini-swe-agent",
            repo_url="https://github.com/x/y",
            default_timeout_sec=7200, default_concurrency=1,
        )
        assert cfg.agent == "mini-swe-agent"

    def test_terminal_bench_with_import_path_agent(self) -> None:
        cfg = BenchmarkConfig(
            type="terminal-bench",
            local_path=Path("/a"),
            tasks_path=Path("/b"),
            artifact_root=Path("/c"),
            agent="my_module:MyAgent",
            repo_url="https://github.com/x/y",
            default_timeout_sec=7200, default_concurrency=1,
        )
        assert cfg.agent == "my_module:MyAgent"

    def test_benchmark_config_set(self) -> None:
        config_set = BenchmarkConfigSet(
            benchmarks={
                "deep-swe": BenchmarkConfig(
                    type="deep-swe", local_path=Path("/a"), tasks_path=Path("/b"),
                    artifact_root=Path("/c"), repo_url="https://github.com/x/y",
                    default_timeout_sec=7200, default_concurrency=1,
                ),
            }
        )
        assert "deep-swe" in config_set.benchmarks


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

class TestLabels:
    def test_benchmark_label(self) -> None:
        assert label("deep-swe", "deep-swe") == "DeepSWE"
        assert label("terminal-bench", "terminal-bench") == "Terminal-Bench"
        assert label("unknown", "deep-swe") is not None  # uses TYPE_LABELS fallback

    def test_benchmark_labels_keys(self) -> None:
        assert "deep-swe" in BENCHMARK_LABELS
        assert "terminal-bench" in BENCHMARK_LABELS


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestBuildBackends:
    def test_builds_from_config_set(self, tmp_path: Path) -> None:
        config_set = BenchmarkConfigSet(
            benchmarks={
                "deep-swe": BenchmarkConfig(
                    type="deep-swe",
                    local_path=tmp_path / "ds",
                    tasks_path=tmp_path / "tasks",
                    artifact_root=tmp_path / "jobs",
                    repo_url="https://github.com/x/y",
                    default_timeout_sec=7200, default_concurrency=1,
                ),
                "terminal-bench": BenchmarkConfig(
                    type="terminal-bench",
                    local_path=tmp_path / "tb",
                    tasks_path=tmp_path / "tb-tasks",
                    artifact_root=tmp_path / "tb-jobs",
                    agent="mini-swe-agent",
                    repo_url="https://github.com/x/y",
                    default_timeout_sec=7200, default_concurrency=1,
                ),
            }
        )
        (tmp_path / "ds").mkdir()
        (tmp_path / "tasks").mkdir()
        (tmp_path / "tb").mkdir()
        (tmp_path / "tb-tasks").mkdir()

        backends = build_backends(config_set, tmp_path)
        assert "deep-swe" in backends
        assert "terminal-bench" in backends
        assert isinstance(backends["deep-swe"], DeepSweBackend)
        assert isinstance(backends["terminal-bench"], TerminalBenchBackend)

    def test_registry_anchors_relative_paths(self, tmp_path: Path) -> None:
        """build_backends anchors relative paths from the project root."""
        (tmp_path / "ds").mkdir()
        (tmp_path / "tasks").mkdir()
        config_set = BenchmarkConfigSet(
            benchmarks={
                "deep-swe": BenchmarkConfig(
                    type="deep-swe",
                    local_path=Path("ds"),
                    tasks_path=Path("tasks"),
                    artifact_root=Path("jobs"),
                    repo_url="https://github.com/x/y",
                    default_timeout_sec=7200, default_concurrency=1,
                ),
            }
        )
        backends = build_backends(config_set, tmp_path)
        assert backends["deep-swe"].jobs_root == tmp_path / "jobs"


# ---------------------------------------------------------------------------
# DeepSweBackend command construction
# ---------------------------------------------------------------------------

class TestDeepSweBackend:
    def test_runner_marker(self) -> None:
        backend = DeepSweBackend(
            DeepSweConfig(
                local_path=Path("/ds"),
                tasks_path=Path("/tasks"),
                env_file=Path("/ds/.env"),
                jobs_root=Path("/jobs"),
                default_timeout_sec=7200,
                n_concurrent=1,
            ),
            name="deep-swe",
        )
        assert "--job-name run-abc" in backend.runner_marker("run-abc")

    def test_job_finished_checks_result_json(self, tmp_path: Path) -> None:
        backend = DeepSweBackend(
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
        job_dir = tmp_path / "jobs" / "run-x"
        job_dir.mkdir(parents=True)
        # No result.json -> not finished
        assert backend.job_finished("run-x") is False
        # result.json without finished_at -> not finished
        (job_dir / "result.json").write_text("{}", encoding="utf-8")
        assert backend.job_finished("run-x") is False
        # result.json with finished_at -> finished
        (job_dir / "result.json").write_text(
            '{"finished_at": "2026-08-15T00:00:00"}', encoding="utf-8"
        )
        assert backend.job_finished("run-x") is True


# ---------------------------------------------------------------------------
# TerminalBenchBackend
# ---------------------------------------------------------------------------

class TestTerminalBenchBackend:
    def test_runner_marker(self) -> None:
        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=Path("/tb"),
                tasks_path=Path("/tb-tasks"),
                artifact_root=Path("/jobs"),
                env_file=Path("/tb/.env"),
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        marker = backend.runner_marker("run-abc")
        assert "--run-id run-abc" in marker

    def test_list_task_ids(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task-a").mkdir()
        (tasks_dir / "task-b").mkdir()
        (tasks_dir / "README.md").write_text("readme", encoding="utf-8")
        (tasks_dir / ".hidden").mkdir()

        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=tmp_path / "tb",
                tasks_path=tasks_dir,
                artifact_root=tmp_path / "jobs",
                env_file=tmp_path / ".env",
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        ids = backend.list_task_ids()
        assert "task-a" in ids
        assert "task-b" in ids
        assert "README.md" not in ids
        assert ".hidden" not in ids

    def test_sample_task_ids(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        for i in range(20):
            (tasks_dir / f"task-{i}").mkdir()

        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=tmp_path / "tb",
                tasks_path=tasks_dir,
                artifact_root=tmp_path / "jobs",
                env_file=tmp_path / ".env",
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        sampled = backend.sample_task_ids(5, 42)
        assert len(sampled) == 5
        sampled2 = backend.sample_task_ids(5, 42)
        assert sampled == sampled2

    def test_import_records_from_results_json(self, tmp_path: Path) -> None:
        jobs_root = tmp_path / "jobs"
        run_dir = jobs_root / "run-x"
        run_dir.mkdir(parents=True)
        results = {
            "results": [
                {
                    "task_id": "task-a",
                    "trial_name": "trial-0",
                    "is_resolved": True,
                    "failure_mode": None,
                    "total_input_tokens": 1500,
                    "total_output_tokens": 300,
                    "trial_started_at": "2026-08-15T00:00:00+00:00",
                    "trial_ended_at": "2026-08-15T00:05:00+00:00",
                },
                {
                    "task_id": "task-b",
                    "trial_name": "trial-1",
                    "is_resolved": False,
                    "failure_mode": "timeout",
                    "total_input_tokens": 500,
                    "total_output_tokens": 100,
                    "trial_started_at": "2026-08-15T00:06:00+00:00",
                    "trial_ended_at": "2026-08-15T00:10:00+00:00",
                },
            ]
        }
        (run_dir / "results.json").write_text(
            json.dumps(results), encoding="utf-8"
        )
        # task-a 有 Compat agent 落盘的 mini trajectory：usage 应以轨迹为准
        #（results.json 的 installed-agent 字段是上游硬编码/近似值）。
        trajectory_dir = run_dir / "task-a" / "trial-0" / "agent-logs"
        trajectory_dir.mkdir(parents=True)
        (trajectory_dir / "mini-swe-agent.trajectory.json").write_text(
            json.dumps(
                {
                    "info": {"model_stats": {"api_calls": 6}},
                    "messages": [
                        {
                            "role": "assistant",
                            "extra": {
                                "response": {
                                    "usage": {
                                        "prompt_tokens": 2000,
                                        "completion_tokens": 120,
                                        "prompt_tokens_details": {"cached_tokens": 1500},
                                    }
                                }
                            },
                        },
                        {
                            "role": "assistant",
                            "extra": {
                                "response": {
                                    "usage": {
                                        "prompt_tokens": 800,
                                        "completion_tokens": 60,
                                        "prompt_tokens_details": {"cached_tokens": 500},
                                    }
                                }
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=tmp_path / "tb",
                tasks_path=tmp_path / "tasks",
                artifact_root=jobs_root,
                env_file=tmp_path / ".env",
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        records = backend.import_records(
            run_id="run-x", model_id="model-a", base_url_hash="abc123"
        )
        assert len(records) == 2
        r0 = records[0]
        assert r0.benchmark.name == "terminal-bench"
        assert r0.result.status == "passed"
        # 轨迹存在：steps/cached 来自 trajectory，tokens 以轨迹累加为准。
        assert r0.usage.agent_steps == 6
        assert r0.usage.cached_input_tokens == 2000
        assert r0.usage.input_tokens == 2800
        assert r0.usage.output_tokens == 180
        r1 = records[1]
        assert r1.result.status == "failed"
        # 无轨迹：回退 results.json 字段，steps/cached 为 0（大盘显示 N/A）。
        assert r1.usage.input_tokens == 500
        assert r1.usage.output_tokens == 100
        assert r1.usage.agent_steps == 0
        assert r1.usage.cached_input_tokens == 0

    def test_tb_command_uses_agent_import_path(self) -> None:
        """Custom ``module:Class`` agents must go through ``--agent-import-path``
        and the model name must keep the gateway id (``openai/`` + nested-slash
        model) so the harness can pass it to ``mini -m`` verbatim."""
        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=Path("/tb"),
                tasks_path=Path("/tb-tasks"),
                artifact_root=Path("/jobs"),
                env_file=Path("/tb/.env"),
                agent="iqradar.benchmarks.tb_agents:MiniSweAgentCompat",
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        cmd = backend._tb_command(
            run_id="run-abc",
            model_name="gateway/deepseek-v4-flash",
            task_ids=["task-a"],
            base_url="http://gateway/v1",
            api_key="k",
        )
        assert "--agent" not in cmd
        assert "--agent-import-path" in cmd
        agent_index = cmd.index("--agent-import-path")
        assert cmd[agent_index + 1] == (
            "iqradar.benchmarks.tb_agents:MiniSweAgentCompat"
        )
        model_index = cmd.index("--model")
        assert cmd[model_index + 1] == "openai/gateway/deepseek-v4-flash"
        assert "--no-rebuild" in cmd
        assert "--no-cleanup" in cmd
        assert "--cleanup" not in cmd

    def test_tb_command_defaults_to_compat_agent(self) -> None:
        """Without an explicit ``agent`` the backend must default to the
        nested-slash-tolerant compat agent (stock mini-swe-agent crashes on
        ``openai/<provider>/<model>`` names)."""
        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=Path("/tb"),
                tasks_path=Path("/tb-tasks"),
                artifact_root=Path("/jobs"),
                env_file=Path("/tb/.env"),
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        cmd = backend._tb_command(
            run_id="run-abc",
            model_name="gateway/deepseek-v4-flash",
            task_ids=["task-a"],
            base_url="http://gateway/v1",
            api_key="k",
        )
        assert "--agent-import-path" in cmd
        agent_index = cmd.index("--agent-import-path")
        assert cmd[agent_index + 1] == (
            "iqradar.benchmarks.tb_agents:MiniSweAgentCompat"
        )

    def test_tb_command_n_concurrent_override(self) -> None:
        """Per-run ``n_concurrent`` must override the config default in the
        tb CLI command line; ``None`` falls back to ``default_concurrency``."""
        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=Path("/tb"),
                tasks_path=Path("/tb-tasks"),
                artifact_root=Path("/jobs"),
                env_file=Path("/tb/.env"),
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )
        kwargs: dict[str, Any] = dict(
            run_id="run-abc",
            model_name="gateway/deepseek-v4-flash",
            task_ids=["task-a"],
            base_url="http://gateway/v1",
            api_key="k",
        )

        def value_of(cmd: list[str]) -> str:
            return cmd[cmd.index("--n-concurrent") + 1]

        assert value_of(backend._tb_command(**kwargs)) == "1"
        assert value_of(backend._tb_command(**kwargs, n_concurrent=3)) == "3"

    def test_run_injects_pythonpath_for_import_path_agent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The tb subprocess must be able to import iqradar custom agents:
        PYTHONPATH must include the iqradar src directory."""
        from iqradar.benchmarks.terminal_bench import _iqradar_src_dir

        monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.test/v1")
        monkeypatch.setenv("GATEWAY_API_KEY", "sk-test")
        monkeypatch.setattr(
            "iqradar.shared.container_net._is_docker_desktop", lambda: False
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task-a").mkdir()
        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=tmp_path / "tb",
                tasks_path=tasks_dir,
                artifact_root=tmp_path / "jobs",
                env_file=tmp_path / ".env",
                agent="my_module:MyAgent",
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )

        captured: dict[str, object] = {}

        class _FakePopen:
            def __init__(self, command, **kwargs) -> None:
                captured["command"] = command
                captured["env"] = kwargs.get("env")
                self.pid = 12345

            def wait(self, timeout: float | None = None) -> int:
                return 0

        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench.ensure_images", lambda tags: 0
        )
        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench.subprocess.Popen", _FakePopen
        )
        returncode, error = backend.run(
            run_id="run-abc",
            model_name="openai/gateway/deepseek-v4-flash",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run-abc.log",
        )
        assert returncode == 0
        assert error == ""
        assert "--agent-import-path" in captured["command"]
        pythonpath = str(captured["env"].get("PYTHONPATH", "")).split(os.pathsep)
        assert str(_iqradar_src_dir()) in pythonpath
        assert captured["env"]["OPENAI_BASE_URL"] == "http://gateway.test/v1"
        assert captured["env"]["OPENAI_API_KEY"] == "sk-test"

    def test_run_skips_pythonpath_for_stock_agent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Stock agent names must not add iqradar to the tb subprocess
        PYTHONPATH (nothing custom to import)."""
        monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.test/v1")
        monkeypatch.setenv("GATEWAY_API_KEY", "sk-test")
        monkeypatch.setattr(
            "iqradar.shared.container_net._is_docker_desktop", lambda: False
        )
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task-a").mkdir()
        backend = TerminalBenchBackend(
            BenchmarkConfig(
                type="terminal-bench",
                local_path=tmp_path / "tb",
                tasks_path=tasks_dir,
                artifact_root=tmp_path / "jobs",
                env_file=tmp_path / ".env",
                agent="mini-swe-agent",
                repo_url="https://github.com/x/y",
                default_timeout_sec=7200, default_concurrency=1,
            ),
            name="terminal-bench",
        )

        captured: dict[str, object] = {}

        class _FakePopen:
            def __init__(self, command, **kwargs) -> None:
                captured["env"] = kwargs.get("env")
                self.pid = 12345

            def wait(self, timeout: float | None = None) -> int:
                return 0

        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench.ensure_images", lambda tags: 0
        )
        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench.subprocess.Popen", _FakePopen
        )
        backend.run(
            run_id="run-abc",
            model_name="openai/gpt-4o",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run-abc.log",
        )
        assert "PYTHONPATH" not in captured["env"]


# ---------------------------------------------------------------------------
# Terminal-Bench compat agent (tb_agents)
# ---------------------------------------------------------------------------

class TestTerminalBenchCompatAgent:
    """The compat agent only matters when terminal-bench is installed
    (bench dependency group); skip otherwise."""

    def test_provider_of_splits_on_first_slash(self) -> None:
        pytest.importorskip("terminal_bench")
        from iqradar.benchmarks.tb_agents import provider_of

        assert provider_of("openai/gpt-4o") == "openai"
        assert provider_of("openai/gateway/deepseek-v4-flash") == "openai"
        assert provider_of("gateway/deepseek-v4-flash") == "gateway"

    def test_compat_agent_accepts_nested_slash_model_name(self) -> None:
        """The exact model name that crashed the stock agent must construct
        cleanly (provider = first segment, model name preserved verbatim)."""
        pytest.importorskip("terminal_bench")
        from iqradar.benchmarks.tb_agents import MiniSweAgentCompat

        agent = MiniSweAgentCompat(
            model_name="openai/gateway/deepseek-v4-flash"
        )
        assert agent._provider == "openai"
        assert agent._model_name == "openai/gateway/deepseek-v4-flash"

    def test_stock_agent_still_works_for_one_slash(self) -> None:
        pytest.importorskip("terminal_bench")
        from terminal_bench.agents.installed_agents.mini_swe_agent.mini_swe_agent import (
            MiniSweAgent,
        )

        stock = MiniSweAgent(model_name="openai/gpt-4o")
        assert stock._provider == "openai"

    def test_compat_agent_is_stock_subclass(self) -> None:
        pytest.importorskip("terminal_bench")
        from terminal_bench.agents.base_agent import BaseAgent
        from iqradar.benchmarks.tb_agents import MiniSweAgentCompat

        assert issubclass(MiniSweAgentCompat, BaseAgent)

    def test_compat_agent_persists_mini_trajectory(self) -> None:
        """运行命令必须带 -o 把 trajectory 写进挂载的 /agent-logs，并先清掉
        旧文件；导入器据此采集 agent_steps 与 cached tokens。"""
        pytest.importorskip("terminal_bench")
        from iqradar.benchmarks.tb_agents import MiniSweAgentCompat

        agent = MiniSweAgentCompat(model_name="openai/gpt-4o")
        commands = agent._run_agent_commands("fix the bug")
        assert len(commands) == 1
        command = commands[0].command
        assert command.startswith("rm -f /agent-logs/mini-swe-agent.trajectory.json")
        assert "-o /agent-logs/mini-swe-agent.trajectory.json" in command
        assert "mini -m openai/gpt-4o" in command
        assert "--exit-immediately" in command


# ---------------------------------------------------------------------------
# Service dispatch
# ---------------------------------------------------------------------------

class TestServiceDispatch:
    def test_submit_deep_swe_uses_backend(self, tmp_path: Path, monkeypatch: Any) -> None:
        store = FileDeepSweRunStore(tmp_path / "runs")
        (tmp_path / "tasks").mkdir()  # preflight expects a real dataset
        config = DeepSweConfig(
            local_path=tmp_path / "ds",
            tasks_path=tmp_path / "tasks",
            env_file=tmp_path / "ds" / ".env",
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=7200,
            n_concurrent=1,
        )
        service = DeepSweService(runs=store, config=config)
        # Stub the backend's run method to return immediately
        monkeypatch.setattr(
            service.backends["deep-swe"],
            "run",
            lambda *a, **kw: (0, None),
        )
        run = service.submit(
            model_id="model-a",
            model_name="openai/model-a",
            n_tasks=2,
            sample_seed=0,
            base_url="http://gateway/v1",
            benchmark="deep-swe",
        )
        assert run.benchmark == "deep-swe"
        assert run.status == "queued" or run.status == "running"

    def test_submit_unknown_benchmark_raises(self, tmp_path: Path) -> None:
        store = FileDeepSweRunStore(tmp_path / "runs")
        config = DeepSweConfig(
            local_path=tmp_path / "ds",
            tasks_path=tmp_path / "tasks",
            env_file=tmp_path / "ds" / ".env",
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=7200,
            n_concurrent=1,
        )
        service = DeepSweService(runs=store, config=config)
        with pytest.raises(ValueError, match="unknown benchmark"):
            service.submit(
                model_id="model-a",
                model_name="openai/model-a",
                n_tasks=2,
                sample_seed=0,
                base_url="http://gateway/v1",
                benchmark="does-not-exist",
            )

    def test_benchmark_names_lists_backends(self, tmp_path: Path) -> None:
        store = FileDeepSweRunStore(tmp_path / "runs")
        config = DeepSweConfig(
            local_path=tmp_path / "ds",
            tasks_path=tmp_path / "tasks",
            env_file=tmp_path / "ds" / ".env",
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=7200,
            n_concurrent=1,
        )
        service = DeepSweService(runs=store, config=config)
        assert "deep-swe" in service.benchmark_names
        assert len(service.benchmark_names) == 1

    def test_base_url_property(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)
        monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
        store = FileDeepSweRunStore(tmp_path / "runs")
        config = DeepSweConfig(
            local_path=tmp_path / "ds",
            tasks_path=tmp_path / "tasks",
            env_file=tmp_path / "ds" / ".env",
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=7200,
            n_concurrent=1,
        )
        service = DeepSweService(runs=store, config=config)
        # No env file with OPENAI_BASE_URL -> base_url returns empty
        assert service.base_url == ""


# ---------------------------------------------------------------------------
# Boundary checks
# ---------------------------------------------------------------------------

class TestBenchmarkImports:
    def test_benchmarks_do_not_import_reporting(self) -> None:
        """Verification that the benchmarks package does not import reporting
        or publication modules. This is a static check; the actual test in
        tests/architecture/ handles it via AST analysis."""
        import iqradar.benchmarks  # noqa: F401
        # The architecture test does the real check, this is just a smoke test
        # that the package loads cleanly.
        assert True


# ---------------------------------------------------------------------------
# Configuration file loading (integration-light)
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_load_benchmark_yaml(self) -> None:
        """Parse the project's benchmark.yaml and verify the docker blocks."""
        cfg = load_benchmark_config(Path("configs/benchmark.yaml"))
        assert "deep-swe" in cfg.benchmarks
        assert "terminal-bench-2" in cfg.benchmarks
        assert cfg.benchmarks["deep-swe"].type == "deep-swe"
        assert cfg.benchmarks["terminal-bench-2"].type == "terminal-bench-2"

    def test_load_benchmark_yaml_includes_api_eval(self) -> None:
        cfg = load_benchmark_config(Path("configs/benchmark.yaml"))
        assert "gpqa-diamond" in cfg.benchmarks
        assert cfg.benchmarks["gpqa-diamond"].type == "api-eval"

    def test_benchmark_yaml_tasks_live_under_data_datasets(self) -> None:
        cfg = load_benchmark_config(Path("configs/benchmark.yaml"))
        for name, bench in cfg.benchmarks.items():
            rel = bench.tasks_path.as_posix()
            assert rel.startswith("data/datasets/"), (
                f"{name}.tasks_path={rel!r} is not under data/datasets/"
            )

    def test_benchmark_yaml_docker_benches_have_test_tasks(self) -> None:
        cfg = load_benchmark_config(Path("configs/benchmark.yaml"))
        for name in ("deep-swe",):
            tasks = cfg.benchmarks[name].test_tasks
            assert tasks, f"{name} is missing test_tasks"
            assert len(tasks) >= 10, f"{name} test_tasks too small: {len(tasks)}"

    def test_anchor_benchmark_config(self) -> None:
        cfg = load_benchmark_config(Path("configs/benchmark.yaml"))
        anchored = anchor_benchmark_config_set(cfg, Path("/project"))
        # deep-swe local_path is "checkouts/deep-swe" -> anchored to
        # /project/checkouts/deep-swe
        assert anchored.benchmarks["deep-swe"].local_path == Path("/project/checkouts/deep-swe")
        assert anchored.benchmarks["deep-swe"].tasks_path == Path(
            "/project/data/datasets/deep-swe"
        )


# ---------------------------------------------------------------------------
# Container gateway URL syncing (Docker Desktop for WSL 2)
# ---------------------------------------------------------------------------

class TestContainerGatewayUrl:
    def test_rewrite_unreachable_host(self) -> None:
        from iqradar.benchmarks.base import _rewrite_unreachable_host

        ip = "172.23.175.33"
        assert _rewrite_unreachable_host("http://172.17.0.1:8080/v1", ip) == (
            "http://172.23.175.33:8080/v1"
        )
        assert _rewrite_unreachable_host("http://localhost:8080/v1", ip) == (
            "http://172.23.175.33:8080/v1"
        )
        assert _rewrite_unreachable_host("http://127.0.0.1:8080/v1", ip) == (
            "http://172.23.175.33:8080/v1"
        )
        # Already-correct host stays unchanged
        assert _rewrite_unreachable_host("http://172.23.175.33:8080/v1", ip) == (
            "http://172.23.175.33:8080/v1"
        )

    def test_sync_rewrites_env_file(self, tmp_path: Path, monkeypatch: Any) -> None:
        from iqradar.benchmarks import base as base_mod
        from iqradar.shared import container_net as net_mod

        env_file = tmp_path / ".env"
        env_file.write_text(
            "OPENAI_BASE_URL=http://172.17.0.1:8080/v1\n"
            "OPENAI_API_KEY=sk-test\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(net_mod, "_is_docker_desktop", lambda: True)
        monkeypatch.setattr(net_mod, "wsl_eth0_ip", lambda: "172.23.175.33")

        base_mod.sync_container_gateway_url(env_file)

        content = env_file.read_text(encoding="utf-8")
        assert "OPENAI_BASE_URL=http://172.23.175.33:8080/v1" in content
        assert "OPENAI_API_KEY=sk-test" in content

    def test_sync_noop_on_native_docker(self, tmp_path: Path, monkeypatch: Any) -> None:
        from iqradar.benchmarks import base as base_mod
        from iqradar.shared import container_net as net_mod

        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_BASE_URL=http://172.17.0.1:8080/v1\n", encoding="utf-8")
        monkeypatch.setattr(net_mod, "_is_docker_desktop", lambda: False)

        base_mod.sync_container_gateway_url(env_file)

        # Native Linux Docker: 172.17.0.1 is correct, must not be rewritten
        assert "172.17.0.1" in env_file.read_text(encoding="utf-8")

    def test_container_reachable_url_rewrites(self, monkeypatch: Any) -> None:
        from iqradar.benchmarks import base as base_mod
        from iqradar.shared import container_net as net_mod

        monkeypatch.setattr(net_mod, "_is_docker_desktop", lambda: True)
        monkeypatch.setattr(net_mod, "wsl_eth0_ip", lambda: "172.23.175.33")

        assert base_mod.container_reachable_url("http://172.17.0.1:8080/v1") == (
            "http://172.23.175.33:8080/v1"
        )
        # Already-correct and empty URLs pass through unchanged
        assert base_mod.container_reachable_url("http://172.23.175.33:8080/v1") == (
            "http://172.23.175.33:8080/v1"
        )
        assert base_mod.container_reachable_url("") == ""

    def test_pier_env_file_rewrites_host(self, tmp_path: Path, monkeypatch: Any) -> None:
        from iqradar.deepswe import runner as runner_mod
        from iqradar.deepswe.runner import DeepSweConfig

        env_file = tmp_path / "deep-swe" / ".env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("OPENAI_BASE_URL=http://172.17.0.1:8080/v1\n", encoding="utf-8")
        config = DeepSweConfig(
            local_path=tmp_path / "deep-swe",
            tasks_path=tmp_path / "tasks",
            env_file=env_file,
            jobs_root=tmp_path / "jobs",
            default_timeout_sec=1,
            n_concurrent=1,
        )
        monkeypatch.setattr(runner_mod, "container_reachable_url", lambda u: (
            "http://172.23.175.33:8080/v1" if u == "http://172.17.0.1:8080/v1" else u
        ))

        resolved = runner_mod._pier_env_file(config, "job-1")

        # 临时 env-file 生成在可写的 jobs_root 下，主机已替换
        assert resolved != env_file
        assert "OPENAI_BASE_URL=http://172.23.175.33:8080/v1" in resolved.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# TerminalBench2Backend (Terminal-Bench 2.0 / Harbor)
# ---------------------------------------------------------------------------

class TestTerminalBench2Backend:
    def _backend(
        self,
        tmp_path: Path,
        *,
        tasks_path: Path | None = None,
        artifact_root: Path | None = None,
        agent: str | None = None,
        test_tasks: list[str] | None = None,
    ) -> TerminalBench2Backend:
        return TerminalBench2Backend(
            BenchmarkConfig(
                type="terminal-bench-2",
                local_path=tmp_path / "tb2",
                tasks_path=tasks_path or (tmp_path / "tasks"),
                artifact_root=artifact_root or (tmp_path / "jobs"),
                env_file=tmp_path / ".env",
                agent=agent,
                test_tasks=test_tasks,
                repo_url="https://github.com/harbor-framework/terminal-bench.git",
                default_timeout_sec=7200,
                default_concurrency=4,
            ),
            name="terminal-bench-2",
        )

    def test_config_accepts_agent(self) -> None:
        cfg = BenchmarkConfig(
            type="terminal-bench-2",
            local_path=Path("/a"),
            tasks_path=Path("/b"),
            artifact_root=Path("/c"),
            agent="mini-swe-agent",
            repo_url="https://github.com/harbor-framework/terminal-bench.git",
            default_timeout_sec=7200,
            default_concurrency=4,
        )
        assert cfg.type == "terminal-bench-2"
        assert cfg.agent == "mini-swe-agent"

    def test_label_and_registry(self, tmp_path: Path) -> None:
        assert label("terminal-bench-2", "terminal-bench-2") == "Terminal-Bench 2.0"
        assert "terminal-bench-2" in BENCHMARK_LABELS
        for d in ("tb2", "tasks"):
            (tmp_path / d).mkdir()
        cfg = BenchmarkConfigSet(
            benchmarks={
                "terminal-bench-2": BenchmarkConfig(
                    type="terminal-bench-2",
                    local_path=tmp_path / "tb2",
                    tasks_path=tmp_path / "tasks",
                    artifact_root=tmp_path / "jobs",
                    repo_url="https://github.com/harbor-framework/terminal-bench.git",
                    default_timeout_sec=7200,
                    default_concurrency=4,
                ),
            }
        )
        backs = build_backends(cfg, tmp_path)
        assert isinstance(backs["terminal-bench-2"], TerminalBench2Backend)

    def test_runner_marker(self) -> None:
        backend = TerminalBench2Backend(
            BenchmarkConfig(
                type="terminal-bench-2",
                local_path=Path("/tb2"),
                tasks_path=Path("/tasks"),
                artifact_root=Path("/jobs"),
                repo_url="https://github.com/harbor-framework/terminal-bench.git",
                default_timeout_sec=7200,
                default_concurrency=4,
            ),
            name="terminal-bench-2",
        )
        assert "--job-name run-abc" in backend.runner_marker("run-abc")

    def test_job_finished_requires_finished_at(self, tmp_path: Path) -> None:
        # Harbor writes result.json at startup with finished_at=null while
        # trials are still running; job_finished must NOT flip to True until
        # finished_at is set (regression: runs were marked "completed" the
        # instant harbor began, leaving harbor grinding in the background).
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        job_dir = tmp_path / "jobs" / "run-x"
        job_dir.mkdir(parents=True)
        # No result.json -> not finished
        assert backend.job_finished("run-x") is False
        # result.json without finished_at (live progress file) -> not finished
        (job_dir / "result.json").write_text(
            json.dumps(
                {
                    "finished_at": None,
                    "n_total_trials": 74,
                    "stats": {"n_running_trials": 4, "n_pending_trials": 70},
                }
            ),
            encoding="utf-8",
        )
        assert backend.job_finished("run-x") is False
        # result.json with finished_at -> finished
        (job_dir / "result.json").write_text(
            '{"finished_at": "2026-09-05T15:22:43Z"}', encoding="utf-8"
        )
        assert backend.job_finished("run-x") is True

    def test_run_log_source_reads_harbor_job_log(self, tmp_path: Path) -> None:
        # The "运行日志" (run) source must read harbor's <job_dir>/job.log,
        # not the empty harbor stdout captured into runs/<run_id>/pier.log.
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        job_dir = tmp_path / "jobs" / "run-y"
        job_dir.mkdir(parents=True)
        (job_dir / "job.log").write_text("harbor progress line\n", encoding="utf-8")
        pier_log = tmp_path / "runs" / "run-y" / "pier.log"
        pier_log.parent.mkdir(parents=True)
        pier_log.write_text("", encoding="utf-8")  # harbor stdout, always empty

        path = backend._log_path("run-y", "run")

        assert path == job_dir / "job.log"
        assert path is not None and path.is_file()
        assert backend.log_content("run-y", "run", tail=0)["content"] == (
            "harbor progress line"
        )

    def test_run_log_source_none_before_job_dir_exists(self, tmp_path: Path) -> None:
        # Early image-build phase: harbor has not created the job dir yet, so
        # the run source is absent and the frontend shows its build hint.
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        assert backend._log_path("run-z", "run") is None
        assert backend.log_sources("run-z") == []

    def test_list_task_ids_requires_task_toml(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        (tasks / "music-harmony").mkdir()
        (tasks / "music-harmony" / "task.toml").write_text(
            'version = "1.0"\n', encoding="utf-8"
        )
        (tasks / "no-toml").mkdir()  # dir without task.toml -> excluded
        (tasks / "README.md").write_text("readme", encoding="utf-8")
        backend = self._backend(tmp_path, tasks_path=tasks)
        assert backend.list_task_ids() == ["music-harmony"]

    def test_list_task_ids_fixed_subset(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        for name in ("a", "b", "c"):
            d = tasks / name
            d.mkdir()
            (d / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")
        # zzz is absent on disk -> dropped from the fixed subset
        backend = self._backend(tmp_path, tasks_path=tasks, test_tasks=["b", "zzz"])
        assert backend.list_task_ids() == ["b"]

    def test_sample_task_ids_deterministic(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        for i in range(12):
            d = tasks / f"t-{i}"
            d.mkdir()
            (d / "task.toml").write_text('version = "1.0"\n', encoding="utf-8")
        backend = self._backend(tmp_path, tasks_path=tasks)
        s1 = backend.sample_task_ids(4, 7)
        s2 = backend.sample_task_ids(4, 7)
        assert s1 == s2
        assert len(s1) == 4

    def test_harbor_command(self, tmp_path: Path) -> None:
        backend = self._backend(
            tmp_path,
            tasks_path=tmp_path / "tasks",
            artifact_root=tmp_path / "jobs",
        )
        endpoint = InferenceEndpoint(
            base_url="http://gw:8080/v1", api_key="sk-x"
        )
        cmd = backend._harbor_command(
            run_id="run-1",
            model_name="deepseek-v4-flash",
            task_ids=["music-harmony", "cad-model"],
            endpoint=endpoint,
        )
        assert cmd[0:2] == ["harbor", "run"]
        assert "--path" in cmd
        assert str(tmp_path / "tasks") in cmd
        assert "--agent" in cmd and "mini-swe-agent" in cmd
        assert "--model" in cmd and "openai/deepseek-v4-flash" in cmd
        assert "--no-force-build" in cmd
        assert "--no-delete" in cmd
        assert "--n-concurrent" in cmd and "4" in cmd
        assert "--job-name" in cmd and "run-1" in cmd
        assert cmd.count("--include-task-name") == 2
        joined = " ".join(cmd)
        assert "OPENAI_BASE_URL=http://gw:8080/v1" in joined
        assert "OPENAI_API_KEY=sk-x" in joined
        assert "MSWEA_API_KEY=sk-x" in joined

    def test_harbor_command_n_concurrent_override(self, tmp_path: Path) -> None:
        """The per-run ``n_concurrent`` (the test page's concurrency field)
        must override the benchmark.yaml ``default_concurrency`` (4) baked
        into the harbor command line; ``None`` falls back to the default.
        (Regression: the page field was silently ignored, so a requested
        concurrency of 1 still ran 4 trials in parallel.)"""
        backend = self._backend(
            tmp_path,
            tasks_path=tmp_path / "tasks",
            artifact_root=tmp_path / "jobs",
        )
        endpoint = InferenceEndpoint(base_url="http://gw:8080/v1", api_key="sk-x")
        kwargs: dict[str, Any] = dict(
            run_id="run-1",
            model_name="m",
            task_ids=["t"],
            endpoint=endpoint,
        )

        def value_of(cmd: list[str]) -> str:
            return cmd[cmd.index("--n-concurrent") + 1]

        assert value_of(backend._harbor_command(**kwargs)) == "4"
        assert value_of(backend._harbor_command(**kwargs, n_concurrent=1)) == "1"
        assert value_of(backend._harbor_command(**kwargs, n_concurrent=8)) == "8"

    # --- resume (harbor job resume) ------------------------------------------

    def _interrupted_job(self, tmp_path: Path, *, model: str = "openai/m") -> Path:
        """Stage an interrupted harbor job: one finished trial + config.json."""
        job = tmp_path / "jobs" / "old-run"
        trial = job / "music-harmony__abc1234"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text("{}", encoding="utf-8")
        (job / "config.json").write_text(
            json.dumps(
                {
                    "job_name": "old-run",
                    "agents": [{"name": "mini-swe-agent", "model_name": model}],
                }
            ),
            encoding="utf-8",
        )
        return job

    def _fake_popen(self, captured: dict[str, object]):
        class _FakePopen:
            def __init__(self, command, **kwargs) -> None:
                captured["command"] = command
                self.pid = 4242

            def wait(self, timeout: float | None = None) -> int:
                return 0

        return _FakePopen

    def test_harbor_resume_command(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        cmd = backend._harbor_resume_command("old-run")
        assert cmd == [
            "harbor",
            "job",
            "resume",
            "--job-path",
            str(tmp_path / "jobs" / "old-run"),
            "--filter-error-type",
            "CancelledError",
        ]

    def test_has_partial_results(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        assert backend.has_partial_results("old-run") is False
        self._interrupted_job(tmp_path)
        assert backend.has_partial_results("old-run") is True
        # a job dir without finished trials is not resumable
        (tmp_path / "jobs" / "empty-job").mkdir()
        assert backend.has_partial_results("empty-job") is False

    def test_run_resumes_interrupted_job(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """run(resume_run_id=...) must spawn `harbor job resume` against the old
        job dir, symlink the new run id to it (so progress/records/marker all
        resolve to the resumed job), and skip sampling/gates entirely."""
        self._interrupted_job(tmp_path)
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench_2.subprocess.Popen",
            self._fake_popen(captured),
        )
        returncode, error = backend.run(
            run_id="new-run",
            model_name="m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "logs" / "new-run.log",
            resume_run_id="old-run",
        )
        assert (returncode, error) == (0, "")
        assert captured["command"] == [
            "harbor",
            "job",
            "resume",
            "--job-path",
            str(tmp_path / "jobs" / "old-run"),
            "--filter-error-type",
            "CancelledError",
        ]
        link = tmp_path / "jobs" / "new-run"
        assert link.is_symlink()
        assert os.readlink(link) == "old-run"
        # every read path resolves through the link into the old job dir
        assert (link / "config.json").is_file()
        assert backend.has_partial_results("new-run") is True
        # the resumed harbor process carries --job-path, not --job-name
        assert backend.runner_marker("new-run") == (
            f"--job-path {tmp_path / 'jobs' / 'old-run'}"
        )
        # non-resumed runs keep the --job-name marker
        assert backend.runner_marker("plain-run") == "--job-name plain-run"

    def test_run_resume_rejects_model_mismatch(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """harbor reuses the old job's model config; a submission with a
        different model must fail fast instead of silently mis-attributing
        records."""
        self._interrupted_job(tmp_path, model="openai/other-model")
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "iqradar.benchmarks.terminal_bench_2.subprocess.Popen",
            self._fake_popen(captured),
        )
        returncode, error = backend.run(
            run_id="new-run",
            model_name="m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "logs" / "new-run.log",
            resume_run_id="old-run",
        )
        assert returncode == 1
        assert "cannot resume" in error and "other-model" in error
        assert "command" not in captured  # nothing spawned
        assert not (tmp_path / "jobs" / "new-run").exists()

    def test_run_resume_rejects_missing_job(self, tmp_path: Path) -> None:
        backend = self._backend(tmp_path, artifact_root=tmp_path / "jobs")
        returncode, error = backend.run(
            run_id="new-run",
            model_name="m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "logs" / "new-run.log",
            resume_run_id="nope",
        )
        assert returncode == 1
        assert "cannot resume nope" in error

    def test_harbor_command_cn_overrides(self, tmp_path: Path) -> None:
        # Known debian base -> TUNA sources + uv mounts via extra compose.
        tasks = tmp_path / "tasks"
        env = tasks / "music-harmony" / "environment"
        env.mkdir(parents=True)
        (env / "Dockerfile").write_text(
            "FROM python:3.11-slim\n", encoding="utf-8"
        )
        backend = self._backend(
            tmp_path, tasks_path=tasks, artifact_root=tmp_path / "jobs"
        )
        endpoint = InferenceEndpoint(base_url="http://gw:8080/v1", api_key="sk-x")
        cmd = backend._harbor_command(
            run_id="run-1",
            model_name="m",
            task_ids=["music-harmony"],
            endpoint=endpoint,
        )
        assert "--agent-setup-timeout-multiplier" in cmd
        i = cmd.index("--extra-docker-compose")
        compose = Path(cmd[i + 1])
        assert compose.is_file()
        text = compose.read_text(encoding="utf-8")
        assert "debian-trixie.sources:/etc/apt/sources.list.d/debian.sources:ro" in text
        assert ":/etc/uv/uv.toml:ro" in text
        sources = tmp_path / "jobs" / ".overrides" / "debian-trixie.sources"
        assert sources.is_file()
        assert "trixie" in sources.read_text(encoding="utf-8")
        assert "pypi.tuna.tsinghua.edu.cn" in (
            tmp_path / "jobs" / ".overrides" / "uv.toml"
        ).read_text(encoding="utf-8")

    def test_harbor_command_cn_overrides_fallback(self, tmp_path: Path) -> None:
        # Unknown base (alpine) and mixed families (debian + ubuntu) must not
        # mount anything: only the generous setup-timeout flag stays.
        tasks = tmp_path / "tasks"
        for name, from_line in (
            ("alpine-task", "FROM alpine:3.20\n"),
            ("deb-task", "FROM python:3.12-slim\n"),
            ("ubuntu-task", "FROM ubuntu:24.04\n"),
        ):
            env = tasks / name / "environment"
            env.mkdir(parents=True)
            (env / "Dockerfile").write_text(from_line, encoding="utf-8")
        backend = self._backend(
            tmp_path, tasks_path=tasks, artifact_root=tmp_path / "jobs"
        )
        endpoint = InferenceEndpoint(base_url="http://gw:8080/v1", api_key="sk-x")
        for task_ids in (["alpine-task"], ["deb-task", "ubuntu-task"]):
            cmd = backend._harbor_command(
                run_id="run-1",
                model_name="m",
                task_ids=task_ids,
                endpoint=endpoint,
            )
            assert "--agent-setup-timeout-multiplier" in cmd
            assert "--extra-docker-compose" not in cmd
        # ubuntu alone maps to its own suite + mount point
        cmd = backend._harbor_command(
            run_id="run-2", model_name="m", task_ids=["ubuntu-task"], endpoint=endpoint
        )
        i = cmd.index("--extra-docker-compose")
        text = Path(cmd[i + 1]).read_text(encoding="utf-8")
        assert ":/etc/apt/sources.list.d/ubuntu.sources:ro" in text
        assert "noble" in (tmp_path / "jobs" / ".overrides" / "ubuntu-noble.sources").read_text(
            encoding="utf-8"
        )

    def test_harbor_command_mirror_proxy_preferred(self, tmp_path: Path, monkeypatch) -> None:
        # With the mirror proxy alive even MIXED families get the suite-
        # agnostic apt.conf.d proxy mount (the full-eval case).
        from iqradar.benchmarks import terminal_bench_2 as tb2

        tasks = tmp_path / "tasks"
        for name, from_line in (
            ("deb-task", "FROM python:3.12-slim\n"),
            ("ubuntu-task", "FROM ubuntu:24.04\n"),
            ("alpine-task", "FROM alpine:3.20\n"),
        ):
            env = tasks / name / "environment"
            env.mkdir(parents=True)
            (env / "Dockerfile").write_text(from_line, encoding="utf-8")
        backend = self._backend(
            tmp_path, tasks_path=tasks, artifact_root=tmp_path / "jobs"
        )
        monkeypatch.setattr(tb2, "mirror_proxy_alive", lambda: True)
        endpoint = InferenceEndpoint(base_url="http://gw:8080/v1", api_key="sk-x")
        cmd = backend._harbor_command(
            run_id="run-1",
            model_name="m",
            task_ids=["deb-task", "ubuntu-task", "alpine-task"],
            endpoint=endpoint,
        )
        i = cmd.index("--extra-docker-compose")
        compose = Path(cmd[i + 1]).read_text(encoding="utf-8")
        assert ":/etc/apt/apt.conf.d/99iqradar-tuna-proxy:ro" in compose
        assert ":/etc/uv/uv.toml:ro" in compose
        apt_conf = tmp_path / "jobs" / ".overrides" / "apt-tuna-proxy.conf"
        assert "Acquire::http::Proxy" in apt_conf.read_text(encoding="utf-8")
        assert f":{3142}/" in apt_conf.read_text(encoding="utf-8")

    def _write_trial(
        self,
        trial_dir: Path,
        task_name: str,
        reward: float,
        *,
        exception=None,
        cache_tokens: int = 0,
    ) -> None:
        trial_dir.mkdir(parents=True)
        (trial_dir / "agent").mkdir()
        (trial_dir / "agent" / "oracle.txt").write_text("x", encoding="utf-8")
        (trial_dir / "result.json").write_text(
            json.dumps(
                {
                    "task_name": task_name,
                    "trial_name": trial_dir.name,
                    "agent_result": {
                        "n_input_tokens": 1500,
                        "n_output_tokens": 300,
                        "n_cache_tokens": cache_tokens,
                        "cost_usd": None,
                    },
                    "verifier_result": {"rewards": {"reward": reward}},
                    "exception_info": exception,
                    "started_at": "2026-09-04T07:24:17.176503Z",
                    "finished_at": "2026-09-04T07:26:50.901297Z",
                }
            ),
            encoding="utf-8",
        )

    def test_import_records_reward_one_passed(self, tmp_path: Path) -> None:
        jobs = tmp_path / "jobs"
        run_dir = jobs / "run-x"
        self._write_trial(
            run_dir / "music-harmony__bhJ2oMX", "terminal-bench/music-harmony", 1.0
        )
        backend = self._backend(tmp_path, artifact_root=jobs)
        recs = backend.import_records(
            run_id="run-x", model_id="m", base_url_hash="h"
        )
        assert len(recs) == 1
        r = recs[0]
        assert r.benchmark.name == "terminal-bench-2"
        assert r.benchmark.task_id == "music-harmony"
        assert r.result.status == "passed"
        assert r.result.verifier_passed is True
        assert r.usage.input_tokens == 1500
        assert r.usage.output_tokens == 300
        assert r.usage.wall_time_sec == pytest.approx(153.72, rel=1e-2)
        assert r.run_id == "terminal-bench-2__music-harmony__m__high"

    def test_import_records_reward_zero_failed(self, tmp_path: Path) -> None:
        jobs = tmp_path / "jobs"
        self._write_trial(
            jobs / "run-y" / "cad-model__abc123", "terminal-bench/cad-model", 0.0
        )
        backend = self._backend(tmp_path, artifact_root=jobs)
        recs = backend.import_records(
            run_id="run-y", model_id="m", base_url_hash="h"
        )
        assert len(recs) == 1
        assert recs[0].result.status == "failed"
        assert recs[0].result.verifier_passed is False

    def test_import_records_glob_fallback(self, tmp_path: Path) -> None:
        # harbor appended a timestamp suffix -> _job_dir matches run_id* prefix
        jobs = tmp_path / "jobs"
        self._write_trial(
            jobs / "run-z-2026" / "t__h", "terminal-bench/t", 1.0
        )
        backend = self._backend(tmp_path, artifact_root=jobs)
        recs = backend.import_records(
            run_id="run-z", model_id="m", base_url_hash="h"
        )
        assert len(recs) == 1
        assert recs[0].benchmark.task_id == "t"

    def test_import_records_collects_agent_steps_from_trajectory(
        self, tmp_path: Path
    ) -> None:
        """harbor 的 agent_result 没有步数；agent_steps 取自 trial agent/ 目录
        下 harbor 落盘的 mini trajectory（api_calls）。cached 仍走 agent_result
        （harbor 自己已解析）。"""
        jobs = tmp_path / "jobs"
        trial_dir = jobs / "run-x" / "music-harmony__bhJ2oMX"
        self._write_trial(
            trial_dir, "terminal-bench/music-harmony", 1.0, cache_tokens=320
        )
        (trial_dir / "agent" / "mini-swe-agent.trajectory.json").write_text(
            json.dumps(
                {
                    "info": {"model_stats": {"api_calls": 9}},
                    "messages": [
                        {
                            "role": "assistant",
                            "extra": {
                                "response": {
                                    "usage": {
                                        "prompt_tokens": 4000,
                                        "completion_tokens": 200,
                                        "prompt_tokens_details": {"cached_tokens": 3200},
                                    }
                                }
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        backend = self._backend(tmp_path, artifact_root=jobs)
        recs = backend.import_records(
            run_id="run-x", model_id="m", base_url_hash="h"
        )
        assert len(recs) == 1
        r = recs[0]
        assert r.usage.agent_steps == 9
        # cached 来自 agent_result.n_cache_tokens（harbor 解析的轨迹汇总）。
        assert r.usage.cached_input_tokens == 320
        assert r.usage.input_tokens == 1500

    def test_import_records_without_trajectory_keeps_zero_steps(
        self, tmp_path: Path
    ) -> None:
        jobs = tmp_path / "jobs"
        self._write_trial(jobs / "run-x" / "t__h", "terminal-bench/t", 1.0)
        backend = self._backend(tmp_path, artifact_root=jobs)
        recs = backend.import_records(
            run_id="run-x", model_id="m", base_url_hash="h"
        )
        assert recs[0].usage.agent_steps == 0

class TestMirrorProxy:
    def test_upstream_target_mapping(self) -> None:
        from iqradar.shared.mirror_proxy import (
            UPSTREAM_HOST,
            upstream_target,
        )

        for host in (
            "deb.debian.org",
            "security.debian.org",
            "archive.ubuntu.com",
            "security.ubuntu.com",
        ):
            assert upstream_target(host) == UPSTREAM_HOST
        assert upstream_target("evil.example.com") is None
        assert upstream_target("DEB.DEBIAN.ORG") == UPSTREAM_HOST

    def test_start_and_probe_lifecycle(self) -> None:
        import threading

        from iqradar.shared import mirror_proxy as mp

        with socket.socket() as probe:
            probe.settimeout(0.2)
            taken = probe.connect_ex(("127.0.0.1", 45342)) == 0
        assert not taken
        assert mp.mirror_proxy_alive("127.0.0.1", 45342) is False
        assert mp.start_mirror_proxy("127.0.0.1", 45342) is True
        try:
            assert mp.mirror_proxy_alive("127.0.0.1", 45342) is True
            # idempotent second start
            assert mp.start_mirror_proxy("127.0.0.1", 45342) is True
            # 403 for non-mirror hosts through a real request
            import http.client

            conn = http.client.HTTPConnection("127.0.0.1", 45342, timeout=5)
            conn.request("GET", "/", headers={"Host": "evil.example.com"})
            response = conn.getresponse()
            assert response.status == 403
            conn.close()
        finally:
            pass  # daemon-threaded server dies with the test process
