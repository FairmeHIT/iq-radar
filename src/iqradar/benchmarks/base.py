from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from threading import Event

from iqradar.config.schema import PriceConfig
from iqradar.schemas.run_record import RunRecord
from iqradar.settings.inference import resolve_inference_endpoint
from iqradar.shared.container_net import (
    _is_docker_desktop,
    _rewrite_unreachable_host,
    container_reachable_url,
    sync_container_gateway_url,
    wsl_eth0_ip,
)


def env_value(env_file: Path, name: str) -> str:
    """Read a KEY=VALUE entry from a benchmark .env file (empty on failure)."""
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return _unquote(value.strip())
    return ""


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _to_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def pier_job_progress(job_result: Path) -> dict[str, int] | None:
    """Parse a pier/harbor job-level ``result.json`` into question progress.

    Pier (deep-swe) and harbor (terminal-bench-2) both write
    ``<jobs_root>/<run_id>/result.json`` live while the job runs. It carries
    ``n_total_trials`` plus ``stats`` counters where ``n_completed_trials``
    counts trials that *finished* (errored trials included), so
    ``total == completed + running + pending`` holds while the job is active.
    Returns ``None`` when the file is absent, unreadable, or predates the
    stats schema.
    """
    if not job_result.is_file():
        return None
    try:
        data = json.loads(job_result.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    total = _to_int(data.get("n_total_trials"))
    stats = data.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    completed = _to_int(stats.get("n_completed_trials"))
    if total <= 0:
        return None
    progress: dict[str, int] = {
        "total": total,
        "completed": min(max(completed, 0), total),
    }
    running = _to_int(stats.get("n_running_trials"))
    pending = _to_int(stats.get("n_pending_trials"))
    if running > 0:
        progress["running"] = running
    if pending > 0:
        progress["pending"] = pending
    return progress


def process_alive(marker: str) -> bool:
    """True when a process whose command line contains ``marker`` is running.

    Used to reconcile runs orphaned by a server restart. When the process
    table cannot be inspected the caller is assumed still running.
    """
    if not marker:
        return False
    try:
        output = subprocess.run(
            ["ps", "-ef"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True  # cannot inspect the process table; assume still running
    return marker in output


class BenchmarkBackend(ABC):
    """One executable benchmark harness (deep-swe / terminal-bench / api-eval).

    A backend owns the *execution* of a benchmark run and the *import* of its
    results into :class:`RunRecord` rows, plus the filesystem layout that feeds
    the live-log panel and restart reconciliation. Backends must not import
    ``iqradar.reporting`` or ``iqradar.publication`` (architecture boundary).
    """

    #: benchmark config key this backend serves (e.g. ``"deep-swe"``)
    name: str
    #: benchmark type discriminator (e.g. ``"terminal-bench-2"``)
    benchmark_type: str
    jobs_root: Path
    env_file: Path
    default_timeout_sec: int
    n_concurrent: int

    @property
    def category(self) -> str:
        """``"api-eval"`` for lightweight HTTP benchmarks (run-level
        concurrency safe), ``"docker"`` for containerised benchmarks
        (concurrent runs would contend for CPU/memory)."""
        return "api-eval" if self.benchmark_type == "api-eval" else "docker"

    def base_url(self) -> str:
        """模型调用 URL from the UI gateway settings (record hashing).

        Docker backends override this with :meth:`InferenceEndpoint.for_container`
        so the hashed URL matches the host the agent container actually dials.
        """
        return resolve_inference_endpoint(getattr(self, "_gateway", None)).base_url

    def max_tasks(self) -> int:
        """Upper bound on ``n_tasks`` accepted for a single run.

        Backends whose question/instance count is dynamic (e.g. api-eval) may
        override this; the default keeps the DeepSWE-oriented 117 ceiling.
        """
        return 117

    def preflight(self) -> str | None:
        """Return a human-readable readiness error, or ``None`` when runnable.

        Checked by the service before a run/batch is queued (and by
        ``resume_batch``) so a broken dataset path or a missing executable
        fails fast with an actionable message instead of a failed run record.
        """
        return None

    def runner_marker(self, run_id: str) -> str:
        """A distinctive substring that appears on the runner process command
        line while ``run_id`` is executing (used by restart reconciliation)."""
        return run_id

    def mark_active(self, run_id: str) -> None:
        """Mark *run_id* as having an in-flight worker (``runner_alive`` → True).

        Called by the service in the **submitting thread** (before the worker
        thread is spawned) so restart reconciliation never sees a window where
        the run is ``running`` but ``runner_alive`` is still False. Backends
        whose ``runner_alive`` is backed by an in-process set override this;
        Docker backends track liveness via the runner process and no-op here.
        """
        return None

    def release_active(self, run_id: str) -> None:
        """Drop the in-flight marker set by :meth:`mark_active` (idempotent)."""
        return None

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
        """Execute the benchmark for ``run_id``; returns (returncode, error).

        When *resume_run_id* is set, the backend should skip items already
        completed in the referenced run and continue from the last checkpoint.

        *n_concurrent* overrides the backend's configured task-level
        concurrency for this run; ``None`` falls back to the benchmark's
        ``default_concurrency``. All backends honour it: api-eval scales its
        thread pool, the Docker backends bake ``--n-concurrent`` into the
        spawned CLI command.

        *retry_gateway_failures*（网关失败重测）: after every question has
        finished, re-run only the questions whose failure was caused by an
        upstream-gateway timeout / transient error (model wrong answers are
        never retested), at most *gateway_retry_rounds* rounds. Currently
        implemented by the api-eval backend only; the Docker backends accept
        and ignore the flags (per-question gateway calls cannot be re-run in
        isolation there).
        """
        raise NotImplementedError

    def retry_gateway_failures(
        self,
        *,
        run_id: str,
        n_tasks: int,
        sample_seed: int,
        model_name: str,
        n_concurrent: int | None = None,
        max_rounds: int = 1,
        effort: str = "high",
        cancel_event: Event | None = None,
        log_path: Path,
    ) -> tuple[int, str]:
        """Re-run a finished run's questions that failed on the gateway.

        Only the api-eval backend implements this (per-question gateway calls
        are re-runnable in isolation). The Docker backends do not override it;
        the service layer refuses to retry a non-api-eval run before reaching
        here. Returns ``(returncode, error)``; ``1`` means cancelled.
        """
        raise NotImplementedError

    def has_partial_results(self, run_id: str) -> bool:
        """True when *run_id* has partial results that can be resumed."""
        return False

    def import_records(
        self,
        *,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str = "high",
        prices: PriceConfig | None = None,
    ) -> list[RunRecord]:
        raise NotImplementedError

    def job_finished(self, run_id: str) -> bool:
        """True when the run's job-level result file has been finalized."""
        return False

    def runner_alive(self, run_id: str) -> bool:
        return process_alive(self.runner_marker(run_id))

    def task_count(self) -> int | None:
        """Size of the runnable task pool for this benchmark.

        Backends with a fixed ``test_tasks`` subset report the subset size;
        otherwise the whole pool. ``None`` when the backend has no pool
        concept (e.g. api-eval). Used by the test page to cap ``n_tasks``.
        """
        return None

    def question_catalog(
        self, *, n_tasks: int, sample_seed: int
    ) -> list[dict[str, object]]:
        """Return the sampled question catalog when the backend can expose it.

        The evaluation report uses this to distinguish a failed question from
        a question that was requested but never executed. Docker harnesses may
        return an empty list when their native task catalog is not available.
        """
        return []

    def progress(self, run_id: str) -> dict[str, int] | None:
        """Question-level progress of *run_id* for the live log panel.

        Returns ``{"total": N, "completed": M, ...}`` where ``completed``
        counts questions that finished — passed, failed, or errored alike —
        and optional ``running``/``pending`` breakdowns. ``total`` may be
        omitted when the backend only knows the completed count (the service
        fills it from the run's ``n_tasks``). ``None`` when nothing about the
        run's progress can be determined (e.g. no artifacts on disk yet).
        """
        return None

    def log_sources(self, run_id: str) -> list[dict[str, object]]:
        return [
            {"name": name, "path": str(path) if path is not None else None}
            for name in self.log_source_names
            if (path := self._log_path(run_id, name)) is not None
        ]

    def log_content(
        self,
        run_id: str,
        source: str,
        *,
        tail: int = 2000,
    ) -> dict[str, object]:
        path = self._log_path(run_id, source)
        if path is None or not path.is_file():
            return {"source": source, "path": None, "content": ""}
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if tail > 0 and len(lines) > tail:
            lines = lines[-tail:]
        return {
            "source": source,
            "path": str(path),
            "content": "\n".join(lines),
        }

    @property
    def log_source_names(self) -> tuple[str, ...]:
        return ("run",)

    def _log_path(self, run_id: str, source: str) -> Path | None:
        return None

    @staticmethod
    def _tail_text(text: str, tail: int) -> str:
        lines = text.splitlines()
        if tail > 0 and len(lines) > tail:
            lines = lines[-tail:]
        return "\n".join(lines)
