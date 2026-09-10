from __future__ import annotations

import json
import os
import random
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

from iqradar.benchmarks.base import BenchmarkBackend
from iqradar.benchmarks.mini_trajectory import (
    MINI_TRAJECTORY_FILENAME,
    read_mini_trajectory_usage,
)
from iqradar.config.schema import BenchmarkConfig
from iqradar.prewarm.restore import ensure_images
from iqradar.schemas.run_record import RunRecord
from iqradar.settings.inference import (
    MISSING_INFERENCE_ENDPOINT,
    resolve_inference_endpoint,
)


class TerminalBenchBackend(BenchmarkBackend):
    """Terminal-Bench (classic ``tbench`` CLI 0.2.x): runs interactive tasks
    in isolated Docker containers via the ``tb`` binary.

    The ``tb`` binary must be installed separately (``pip install terminal-bench``
    or ``uv tool install terminal-bench``). Tasks are discovered from the
    configured ``tasks_path`` directory (one subdirectory per task).

    Agent: ``iqradar.benchmarks.tb_agents:MiniSweAgentCompat`` by default — a
    drop-in for the built-in ``mini-swe-agent`` (installed inside the container
    via ``MSWEA_CONFIGURED=true`` + ``MSWEA_API_KEY``) that tolerates gateway
    model ids with nested slashes (``openai/gateway/deepseek-v4-flash``),
    which crash the stock agent's ``model_name.split("/")``.  Any custom
    ``module:Class`` import path is supported the same way.
    """

    benchmark_type = "terminal-bench"
    log_source_names = ("run", "agent", "results")

    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        name: str,
        run_log_path: Callable[[str], Path] | None = None,
        gateway_settings: object | None = None,
    ) -> None:
        self.name = name
        self._config = config
        self.jobs_root = config.artifact_root
        self.env_file = config.resolved_env_file()
        self.default_timeout_sec = config.default_timeout_sec
        self.n_concurrent = config.default_concurrency
        self._gateway = gateway_settings
        self._run_log_path = run_log_path or (
            lambda run_id: self.jobs_root / run_id / "run.log"
        )
        self._tasks_path = config.tasks_path
        self._tb_agent = config.agent or (
            "iqradar.benchmarks.tb_agents:MiniSweAgentCompat"
        )

    def base_url(self) -> str:
        return resolve_inference_endpoint(self._gateway).for_container().base_url

    # --- dataset helpers ----------------------------------------------------

    def list_task_ids(self) -> list[str]:
        """Discover task directories under ``tasks_path``.

        When the benchmark config declares ``test_tasks``, only those task ids
        (that exist on disk) are exposed — sampling and task_count then operate
        on the fixed subset.
        """
        if not self._tasks_path.is_dir():
            return []
        discovered = sorted(
            path.name
            for path in self._tasks_path.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and path.name != "README.md"
        )
        fixed = self._config.test_tasks
        if not fixed:
            return discovered
        fixed_set = set(fixed)
        return [task_id for task_id in discovered if task_id in fixed_set]

    def sample_task_ids(self, n_tasks: int, sample_seed: int) -> list[str]:
        """Deterministic sampling matching pier semantics."""
        all_ids = self.list_task_ids()
        random.Random(sample_seed).shuffle(all_ids)
        return all_ids[: max(0, n_tasks)]

    def task_count(self) -> int:
        return len(self.list_task_ids())

    def question_catalog(
        self, *, n_tasks: int, sample_seed: int
    ) -> list[dict[str, object]]:
        """Expose the sampled legacy terminal-bench task IDs for reports."""
        return [
            {"task_id": task_id}
            for task_id in self.sample_task_ids(n_tasks, sample_seed)
        ]

    def preflight(self) -> str | None:
        """Runnable check for the service layer: tb binary + task dataset."""
        if shutil.which("tb") is None:
            return "tb executable not found (install terminal-bench)"
        if not self._tasks_path.is_dir():
            return f"tasks_path does not exist: {self._tasks_path}"
        if not self.list_task_ids():
            return f"no tasks found in {self._tasks_path}"
        return None

    _BASE_IMAGES: list[str] = [
        "ghcr.io/laude-institute/t-bench/ubuntu-24-04:20250624",
        "ghcr.io/laude-institute/t-bench/ubuntu-24-04:latest",
        "ghcr.io/laude-institute/t-bench/python-3-13:20250620",
        "taichidev/taichi:v0.7.26",
    ]

    def _base_images(self) -> list[str]:
        return list(self._BASE_IMAGES)

    # --- execution ----------------------------------------------------------

    def run(
        self,
        *,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        log_path: Path,
        cancel_event: Event | None = None,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> tuple[int, str]:
        # retry_gateway_failures / gateway_retry_rounds 仅 api-eval 实现：
        # 容器化基准的每题网关调用无法单独重跑，这里接受并忽略。
        if not self._tasks_path.is_dir():
            return 1, f"tasks_path does not exist: {self._tasks_path}"
        task_ids = self.sample_task_ids(n_tasks, sample_seed)
        if not task_ids:
            return 1, f"no tasks found in {self._tasks_path}"
        # Ensure the task base images are available locally so the compose
        # build doesn't pull from the network.  Silently skip missing ones.
        ensure_images(self._base_images())
        endpoint = resolve_inference_endpoint(self._gateway).for_container()
        if not endpoint.base_url:
            return 1, MISSING_INFERENCE_ENDPOINT
        base_url = endpoint.base_url
        api_key = endpoint.api_key
        # Build the tb command
        command = self._tb_command(
            run_id=run_id,
            model_name=model_name,
            task_ids=task_ids,
            base_url=base_url,
            api_key=api_key,
            n_concurrent=n_concurrent,
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(endpoint.agent_env())
        # Custom agents (``module:Class`` import paths) are imported inside the
        # ``tb`` process; make iqradar importable there via PYTHONPATH.
        if ":" in self._tb_agent:
            env["PYTHONPATH"] = os.pathsep.join(
                [
                    str(_iqradar_src_dir()),
                    env.get("PYTHONPATH", ""),
                ]
            ).rstrip(os.pathsep)
        # tbench's ``dotenv.load_dotenv()`` reads .env from cwd;
        # pass env vars explicitly through the process environment.
        try:
            with log_path.open("w", encoding="utf-8") as log_handle:
                process = subprocess.Popen(
                    command,
                    cwd=str(self._tasks_path),
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                returncode = _wait_with_cancel(
                    process,
                    cancel_event=cancel_event,
                    timeout_sec=self.default_timeout_sec,
                )
            if cancel_event is not None and cancel_event.is_set():
                return 1, "cancelled by user"
            return (
                (0, "") if returncode == 0 else (returncode, "tb exited non-zero")
            )
        except subprocess.TimeoutExpired:
            return 1, "terminal-bench run timed out"
        except FileNotFoundError:
            return 1, "tb executable not found (install terminal-bench)"

    def _tb_command(
        self,
        *,
        run_id: str,
        model_name: str,
        task_ids: list[str],
        base_url: str,
        api_key: str,
        n_concurrent: int | None = None,
    ) -> list[str]:
        cmd = [
            "tb",
            "runs",
            "create",
            "--dataset-path",
            str(self._tasks_path),
            "--output-path",
            str(self.jobs_root),
            "--run-id",
            run_id,
            "--n-concurrent",
            # Per-run override (the test page's concurrency field) wins over
            # the benchmark.yaml ``default_concurrency`` baked in at init.
            str(n_concurrent or self.n_concurrent),
            "--model",
            _pier_model_name(model_name),
            "--no-rebuild",
            "--no-cleanup",
        ]
        for task_id in task_ids:
            cmd.extend(["--task-id", task_id])
        # agent: compat mini-swe-agent by default, or any custom import path
        if ":" in self._tb_agent:
            cmd.extend(["--agent-import-path", self._tb_agent])
        else:
            cmd.extend(["--agent", self._tb_agent])
        return cmd

    def runner_marker(self, run_id: str) -> str:
        return f"--run-id {run_id}"

    # --- import -------------------------------------------------------------

    def import_records(
        self,
        *,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str = "high",
        prices: object = None,
    ) -> list[RunRecord]:
        results_path = self.jobs_root / run_id / "results.json"
        if not results_path.is_file():
            return []
        try:
            results = json.loads(results_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records: list[RunRecord] = []
        trials = results.get("results") or []
        if not isinstance(trials, list):
            return []
        for trial in trials:
            records.append(
                self._trial_record(
                    trial=trial,
                    run_id=run_id,
                    model_id=model_id,
                    base_url_hash=base_url_hash,
                    effort=effort,
                )
            )
        return records

    def _trial_record(
        self,
        *,
        trial: dict[str, Any],
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str,
    ) -> RunRecord:
        task_id = str(trial.get("task_id") or "unknown")
        is_resolved = bool(trial.get("is_resolved"))
        failure_mode = str(trial.get("failure_mode") or "unset")
        status, error_type, error_message = self._trial_outcome(
            is_resolved, failure_mode
        )
        wall_time_sec = self._trial_wall_time(trial)
        # Locate the per-trial artifact directory for logs
        trial_name = str(trial.get("trial_name") or "1")
        trial_dir = self.jobs_root / run_id / task_id / trial_name
        # 上游 harness 对 installed-agent 硬编码 AgentResult(total_*=0)，usage
        # 从 Compat agent 落盘的 mini trajectory 采集（见 tb_agents.py）；
        # 缺轨迹时回退 results.json 字段（老 run 常为 None → 0）。
        trajectory = read_mini_trajectory_usage(
            trial_dir / "agent-logs" / MINI_TRAJECTORY_FILENAME
        )
        return RunRecord.model_validate(
            {
                "run_id": f"terminal-bench__{task_id}__{model_id}__{effort}",
                "benchmark": {
                    "name": "terminal-bench",
                    "version": "1.0",
                    "task_id": task_id,
                    "repo": "unknown",
                    "language": "unknown",
                    "task_path": str(trial_dir),
                },
                "model": {
                    "provider": "openai-compatible",
                    "base_url_hash": base_url_hash,
                    "name": model_id,
                    "effort_requested": effort,
                    "effort_effective": False,
                },
                "result": {
                    "status": status,
                    "verifier_passed": is_resolved,
                    "exit_code": 0,
                    "error_type": error_type,
                    "error_message_redacted": error_message,
                },
                "usage": {
                    "input_tokens": (
                        trajectory["input_tokens"]
                        if trajectory
                        else _int(trial.get("total_input_tokens"))
                    ),
                    "output_tokens": (
                        trajectory["output_tokens"]
                        if trajectory
                        else _int(trial.get("total_output_tokens"))
                    ),
                    "cached_input_tokens": (
                        trajectory["cached_input_tokens"] if trajectory else 0
                    ),
                    "agent_steps": trajectory["agent_steps"] if trajectory else 0,
                    "wall_time_sec": wall_time_sec,
                    "usage_estimated": False,
                },
                "cost": {
                    "currency": "USD",
                    "input_cost": 0.0,
                    "cached_input_cost": 0.0,
                    "output_cost": 0.0,
                    "total_cost": 0.0,
                },
                "artifacts": {
                    "patch_path": None,
                    "log_path": self._find_log(trial_dir, "recording"),
                    "verifier_path": self._find_log(trial_dir, "results"),
                },
                "created_at": datetime.now(UTC),
            }
        )

    @staticmethod
    def _trial_outcome(
        is_resolved: bool, failure_mode: str
    ) -> tuple[str, str | None, str | None]:
        if failure_mode and failure_mode not in ("none", "unset", "NONE", "UNSET"):
            return "failed", "runner_error", f"failure_mode={failure_mode}"
        if is_resolved:
            return "passed", None, None
        return "failed", "verifier_failed", None

    @staticmethod
    def _trial_wall_time(trial: dict[str, Any]) -> float:
        for start_key, end_key in [
            ("trial_started_at", "trial_ended_at"),
            ("agent_started_at", "agent_ended_at"),
        ]:
            started = trial.get(start_key)
            ended = trial.get(end_key)
            if started and ended:
                try:
                    return max(
                        0.0,
                        (
                            datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
                            - datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                        ).total_seconds(),
                    )
                except (ValueError, TypeError):
                    continue
        return 0.0

    @staticmethod
    def _find_log(trial_dir: Path, kind: str) -> str | None:
        if kind == "recording":
            for candidate in ("recording.webm", "recording.mp4", "agent.log"):
                path = trial_dir / candidate
                if path.is_file():
                    return str(path)
            return None
        if kind == "results":
            path = trial_dir / "results.json"
            return str(path) if path.is_file() else None
        return None

    # --- logs ---------------------------------------------------------------

    def _log_path(self, run_id: str, source: str) -> Path | None:
        if source == "run":
            path = self._run_log_path(run_id)
            return path if path.is_file() else None
        if source == "results":
            path = self.jobs_root / run_id / "results.json"
            return path if path.is_file() else None
        if source == "agent":
            # The agent (mini-swe-agent) runs inside a tmux session; its model
            # responses are captured in the post-agent pane snapshot.
            path = self._latest_trial_pane(run_id, "post-agent.txt")
            return path if path is not None and path.is_file() else None
        return None

    def _latest_trial_pane(self, run_id: str, filename: str) -> Path | None:
        """Return the most recently modified ``filename`` under any trial's
        ``panes/`` directory for ``run_id``."""
        run_dir = self.jobs_root / run_id
        if not run_dir.is_dir():
            return None
        candidates = list(run_dir.glob(f"*/**/panes/{filename}"))
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)


def _iqradar_src_dir() -> Path:
    """Absolute path of the iqradar ``src`` package directory.

    Computed from this file's location (``src/iqradar/benchmarks/``) so the
    ``tb`` subprocess can import custom agents via ``PYTHONPATH`` regardless of
    the current working directory.
    """
    return Path(__file__).resolve().parents[2]


def _pier_model_name(model_name: str) -> str:
    if model_name.startswith("openai/"):
        return model_name
    return f"openai/{model_name}"


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def _wait_with_cancel(
    process: subprocess.Popen,
    *,
    cancel_event: threading.Event | None,
    timeout_sec: int,
) -> int:
    """Wait for the tb process, terminating it on cancel or timeout.

    The process is spawned in its own session (start_new_session=True) so the
    whole tb / docker-compose tree can be killed as one group.
    """
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            return process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process_tree(process)
                process.wait()
                return process.returncode or 1
            if timeout_sec and time.monotonic() >= deadline:
                _terminate_process_tree(process)
                process.wait()
                raise subprocess.TimeoutExpired(
                    cmd=process.args,
                    timeout=timeout_sec,
                )


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Terminate the process group, escalating to SIGKILL after a grace period."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()