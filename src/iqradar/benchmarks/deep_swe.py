from __future__ import annotations

import json
import random
import shutil
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Callable
from zoneinfo import ZoneInfo

from iqradar.benchmarks.base import BenchmarkBackend, pier_job_progress
from iqradar.deepswe.importer import import_pier_run
from iqradar.deepswe.runner import DeepSweConfig, run_pier_sync
from iqradar.settings.inference import (
    MISSING_INFERENCE_ENDPOINT,
    resolve_inference_endpoint,
)
from iqradar.prewarm.restore import ensure_images
from iqradar.schemas.run_record import RunRecord

_LOG_TZ = ZoneInfo("Asia/Shanghai")


class DeepSweBackend(BenchmarkBackend):
    """Executes DeepSWE batches through the local ``pier`` binary.

    Log sources and job layout follow pier's job directory
    (``<jobs_root>/<run_id>/`` with one trial directory per task).
    """

    benchmark_type = "deep-swe"
    log_source_names = ("pier", "job", "trial", "agent", "verifier", "llm")

    def __init__(
        self,
        config: DeepSweConfig,
        *,
        name: str = "deep-swe",
        run_log_path: Callable[[str], Path] | None = None,
        gateway_settings: object | None = None,
    ) -> None:
        self.name = name
        self._config = config
        self.jobs_root = config.jobs_root
        self.env_file = config.env_file
        self.default_timeout_sec = config.default_timeout_sec
        self.n_concurrent = config.n_concurrent
        self._gateway = gateway_settings
        self._run_log_path = run_log_path or (
            lambda run_id: self.jobs_root / run_id / "run.log"
        )
        # First-seen wall-clock time per trajectory message, keyed by
        # (trajectory path, message index), used to timestamp LLM responses.
        self._llm_first_seen: dict[tuple[str, int], datetime] = {}

    # --- execution ----------------------------------------------------------

    def base_url(self) -> str:
        return resolve_inference_endpoint(self._gateway).for_container().base_url

    def _gateway_overrides(self) -> dict[str, str]:
        """Project the UI 模型调用 pair into pier's OpenAI-compatible env file."""
        return resolve_inference_endpoint(self._gateway).for_container().agent_env()

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
        # Ensure ECR base images are available locally so pier's compose
        # build doesn't pull from the network.  Silently skip missing ones.
        task_names = list(self._config.test_tasks)
        if not task_names:
            task_names = [
                p.name for p in sorted(self._config.tasks_path.iterdir())
                if p.is_dir() and (p / "task.toml").is_file()
            ]
        ecr_tags = []
        for name in task_names:
            toml = self._config.tasks_path / name / "task.toml"
            if toml.is_file():
                try:
                    cfg = tomllib.load(toml.open("rb"))
                    ecr_tags.append(cfg["environment"]["docker_image"])
                except (KeyError, OSError, tomllib.TOMLDecodeError):
                    pass
        ensure_images(ecr_tags)
        overrides = self._gateway_overrides()
        if not overrides.get("OPENAI_BASE_URL"):
            return 1, MISSING_INFERENCE_ENDPOINT
        return run_pier_sync(
            self._config,
            model=model_name,
            n_tasks=n_tasks,
            sample_seed=sample_seed,
            job_name=run_id,
            log_path=log_path,
            cancel_event=cancel_event,
            gateway_overrides=overrides,
        )

    def import_records(
        self,
        *,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str = "high",
        prices: object = None,
    ) -> list[RunRecord]:
        return import_pier_run(
            tasks_path=self._config.tasks_path,
            jobs_root=self.jobs_root,
            run_id=run_id,
            model_id=model_id,
            base_url_hash=base_url_hash,
            effort=effort,
        )

    def preflight(self) -> str | None:
        """Runnable check for the service layer: pier binary + task dataset."""
        if shutil.which("pier") is None:
            return "pier executable not found (install pier)"
        tasks_path = self._config.tasks_path
        if not tasks_path.is_dir():
            return f"tasks_path does not exist: {tasks_path}"
        fixed = self._config.test_tasks
        if fixed and not any((tasks_path / task).is_dir() for task in fixed):
            return f"none of the configured test_tasks exist in {tasks_path}"
        return None

    def task_count(self) -> int | None:
        """Fixed test_tasks subset size, or the number of task directories."""
        if self._config.test_tasks:
            return len(self._config.test_tasks)
        try:
            return sum(
                1
                for path in self._config.tasks_path.iterdir()
                if path.is_dir() and (path / "task.toml").is_file()
            )
        except OSError:
            return None

    def question_catalog(
        self, *, n_tasks: int, sample_seed: int
    ) -> list[dict[str, object]]:
        """Expose sampled DeepSWE task IDs for coverage reports."""
        task_names = list(self._config.test_tasks)
        if not task_names:
            try:
                task_names = sorted(
                    path.name
                    for path in self._config.tasks_path.iterdir()
                    if path.is_dir() and (path / "task.toml").is_file()
                )
            except OSError:
                return []
        random.Random(sample_seed).shuffle(task_names)
        return [{"task_id": name} for name in task_names[: max(0, n_tasks)]]

    def progress(self, run_id: str) -> dict[str, int] | None:
        """题目进度：优先解析 pier 的 job 级 result.json 统计。

        Pier 运行中持续更新该文件（``n_total_trials`` + ``stats`` 计数），
        ``n_completed_trials`` 含出错的 trial。老版本 pier 不写 stats 时，
        退化为统计已产出 trial 级 result.json 的任务目录数。
        """
        job_dir = self.jobs_root / run_id
        parsed = pier_job_progress(job_dir / "result.json")
        if parsed is not None:
            return parsed
        if not job_dir.is_dir():
            return None
        try:
            completed = sum(
                1
                for path in job_dir.iterdir()
                if path.is_dir() and (path / "result.json").is_file()
            )
        except OSError:
            return None
        return {"completed": completed} if completed > 0 else None

    # --- restart reconciliation ---------------------------------------------

    def job_finished(self, run_id: str) -> bool:
        """True when pier's job-level result.json has been finalized.

        Pier writes ``jobs/<run_id>/result.json`` while a job is still running
        as a progress file (``finished_at`` null), so a plain file-existence
        check is not enough to conclude the job is done.
        """
        job_result = self.jobs_root / run_id / "result.json"
        if not job_result.is_file():
            return False
        try:
            data = json.loads(job_result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(isinstance(data, dict) and data.get("finished_at"))

    def runner_marker(self, run_id: str) -> str:
        return f"--job-name {run_id}"

    # --- live logs ----------------------------------------------------------

    # "llm" is derived from the running agent's raw trajectory
    # (mini-swe-agent.trajectory.json): it surfaces the model's own response
    # text (reasoning + content) so a long trial never looks dead.
    def _log_path(self, run_id: str, source: str) -> Path | None:
        if source == "pier":
            log_path = self._run_log_path(run_id)
            return log_path if log_path.is_file() else None
        job_dir = self.jobs_root / run_id
        if not job_dir.is_dir():
            return None
        if source == "job":
            path = job_dir / "job.log"
            return path if path.is_file() else None
        trial = self._latest_trial_dir(job_dir)
        if trial is None:
            return None
        if source == "trial":
            path = trial / "trial.log"
        elif source == "agent":
            path = trial / "agent" / "mini-swe-agent.txt"
        elif source == "verifier":
            path = trial / "verifier" / "run.log"
        elif source == "llm":
            path = trial / "agent" / "mini-swe-agent.trajectory.json"
        else:
            return None
        return path if path.is_file() else None

    def log_content(
        self,
        run_id: str,
        source: str,
        *,
        tail: int = 2000,
    ) -> dict[str, object]:
        if source == "llm":
            path = self._log_path(run_id, "llm")
            if path is None:
                return {"source": source, "path": None, "content": ""}
            return {
                "source": source,
                "path": str(path),
                "content": self._llm_content(path, tail=tail),
            }
        return super().log_content(run_id, source, tail=tail)

    def _llm_content(self, path: Path, tail: int = 2000) -> str:
        """Render recent model response text from a raw mini-swe-agent trajectory.

        Reads ``mini-swe-agent.trajectory.json`` (written live while the agent
        runs) and renders the last text-bearing assistant turns, each prefixed
        with a ``[HH:MM:SS]`` timestamp. The raw trajectory carries no
        per-message clocks, so the time of first observation by this server is
        used (accurate to the ~3s polling interval). Returns an empty string
        when the file is mid-write (invalid JSON) or has no assistant messages
        yet, so a poll can simply retry on the next tick.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return ""
        # Pure tool-call assistant turns carry no text; keep only turns that
        # actually contain reasoning or content so the panel shows real model
        # text. The message index is the stable identity for timestamps.
        seen: list[tuple[datetime, str, str]] = []
        for index, message in enumerate(messages):
            if not (
                isinstance(message, dict)
                and message.get("role") == "assistant"
            ):
                continue
            reasoning = (
                message.get("reasoning_content")
                or message.get("reasoning")
                or ""
            )
            content = message.get("content") or ""
            if not reasoning and not content:
                continue
            key = (str(path), index)
            first_seen = self._llm_first_seen.get(key)
            if first_seen is None:
                first_seen = datetime.now(_LOG_TZ)
                self._llm_first_seen[key] = first_seen
            seen.append((first_seen, reasoning, content))
        if not seen:
            return ""
        # tail <= 0 means "everything" (matching other log sources), capped at 50.
        limit = 50 if tail <= 0 else max(1, min(tail, 50))
        blocks: list[str] = []
        for first_seen, reasoning, content in seen[-limit:]:
            block = [f"[{first_seen.isoformat(timespec='seconds')}]"]
            if reasoning:
                block.append(f"[思考] {reasoning}")
            if content:
                block.append(f"[回复] {content}")
            blocks.append("\n".join(block))
        return "\n\n" + "\n\n".join(blocks) + "\n"

    @staticmethod
    def _latest_trial_dir(job_dir: Path) -> Path | None:
        candidates = [
            path
            for path in job_dir.iterdir()
            if path.is_dir() and (path / "result.json").is_file()
        ]
        if not candidates:
            candidates = [path for path in job_dir.iterdir() if path.is_dir()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)
