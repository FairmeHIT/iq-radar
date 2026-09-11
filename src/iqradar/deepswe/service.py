from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, RLock, Thread
from uuid import uuid4

from iqradar.benchmarks.base import BenchmarkBackend
from iqradar.config.schema import PriceConfig
from iqradar.deepswe.importer import base_url_hash
from iqradar.metrics.evaluation import build_evaluation_report, classify_outcome
from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import DeepSweRun, FileDeepSweRunStore
from iqradar.schemas.run_record import RunRecord
from iqradar.shared.atomic_files import atomic_write_text

DEFAULT_BENCHMARK = "deep-swe"


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _positive_int(value: object) -> int:
    number = _non_negative_int(value)
    return number if number > 0 else 0


def _metric_from_question(question: dict[str, object], key: str) -> object:
    value = question.get(key)
    if value is not None:
        return value
    timing = question.get("timing")
    if isinstance(timing, dict):
        return timing.get(key)
    return None


class DeepSweService:
    def __init__(
        self,
        *,
        runs: FileDeepSweRunStore,
        config: DeepSweConfig | None = None,
        backends: dict[str, BenchmarkBackend] | None = None,
        prices: PriceConfig | None = None,
    ) -> None:
        self._runs = runs
        self._lock = RLock()
        self._cancel_events: dict[str, Event] = {}
        self._prices = prices
        if backends is not None:
            self._backends = dict(backends)
        elif config is not None:
            # Backward-compatible construction: a single deep-swe backend.
            from iqradar.benchmarks.deep_swe import DeepSweBackend

            self._backends = {
                DEFAULT_BENCHMARK: DeepSweBackend(
                    config,
                    name=DEFAULT_BENCHMARK,
                    run_log_path=runs.log_path,
                )
            }
        else:
            raise ValueError("either config or backends is required")
        self._batches_root = runs.root.parent / "batches"
        self._batches_root.mkdir(parents=True, exist_ok=True)
        self._multi_batches_root = runs.root.parent / "multi-batches"
        self._multi_batches_root.mkdir(parents=True, exist_ok=True)

    # --- backend access -----------------------------------------------------

    @property
    def backends(self) -> dict[str, BenchmarkBackend]:
        return self._backends

    @property
    def benchmark_names(self) -> list[str]:
        return list(self._backends)

    def _backend(self, benchmark: str) -> BenchmarkBackend:
        try:
            return self._backends[benchmark]
        except KeyError as caught:
            raise ValueError(f"unknown benchmark: {benchmark}") from caught

    def _backend_for_run(self, run: DeepSweRun) -> BenchmarkBackend:
        return self._backend(run.benchmark or DEFAULT_BENCHMARK)

    def _backend_base_url(self, benchmark: str) -> str:
        return self._backend(benchmark).base_url()

    @property
    def base_url(self) -> str:
        """Gateway base URL of the default backend (backward compatibility)."""
        backend = self._backends.get(DEFAULT_BENCHMARK) or next(
            iter(self._backends.values())
        )
        return backend.base_url()

    def cancel(self, run_id: str) -> bool:
        """Request cancellation of an active run. Returns False if not active."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.status not in {"queued", "running"}:
                return False
            cancel_event = self._cancel_events.get(run_id)
            if cancel_event is not None:
                cancel_event.set()
                return True
        # No worker thread for this run (e.g. the server restarted mid-run):
        # its cancel event is gone, so stop the orphaned runner process itself.
        return self._kill_runner(run)

    def submit(
        self,
        *,
        model_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        base_url: str,
        benchmark: str = DEFAULT_BENCHMARK,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        max_concurrent_runs: int = 1,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> DeepSweRun:
        if n_tasks < 1:
            raise ValueError("n_tasks must be at least 1")
        backend = self._backend(benchmark)  # validate benchmark exists
        error = backend.preflight()
        if error:
            raise ValueError(f"benchmark '{benchmark}' is not ready: {error}")
        with self._lock:
            self._reconcile_all()
            active = self._runs.active_runs()
            if len(active) >= max_concurrent_runs:
                raise ValueError("concurrent run limit reached")
            run = self._runs.submit(
                model_id=model_id,
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                base_url=base_url,
                benchmark=benchmark,
                effort=effort,
                n_concurrent=n_concurrent,
                retry_gateway_failures=retry_gateway_failures or None,
                gateway_retry_rounds=gateway_retry_rounds,
            )
            cancel_event = Event()
            self._cancel_events[run.run_id] = cancel_event
            log_path = self._runs.log_path(run.run_id)
            thread = Thread(
                target=self._execute,
                args=(
                    run.run_id,
                    model_name,
                    n_tasks,
                    sample_seed,
                    benchmark,
                    log_path,
                    cancel_event,
                    effort,
                    resume_run_id,
                    n_concurrent,
                    retry_gateway_failures,
                    gateway_retry_rounds,
                ),
                name=f"deepswe-{run.run_id}",
                daemon=True,
            )
            thread.start()
            return run

    def _execute(
        self,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        benchmark: str,
        log_path: Path,
        cancel_event: Event,
        effort: str,
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> None:
        run = self._runs.get(run_id)
        if run is not None:
            self._runs.save(run.updated(status="running"))
        try:
            backend = self._backend(benchmark)
            returncode, error = backend.run(
                run_id=run_id,
                model_name=model_name,
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                log_path=log_path,
                cancel_event=cancel_event,
                effort=effort,
                resume_run_id=resume_run_id,
                n_concurrent=n_concurrent,
                retry_gateway_failures=retry_gateway_failures,
                gateway_retry_rounds=gateway_retry_rounds,
            )
        except Exception as caught:  # pragma: no cover - defensive
            returncode, error = 1, f"{caught.__class__.__name__}: {caught}"
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
        current = self._runs.get(run_id)
        if current is None:
            return
        if returncode == 0:
            self._runs.save(
                current.updated(
                    status="completed",
                    completed_at=datetime.now(UTC),
                )
            )
            if resume_run_id and resume_run_id != run_id:
                # 接续成功后只保留新的完整 run 记录：旧的 interrupted 记录
                # 已被新 run 的完整结果取代，继续留在评测记录里只会造成重复。
                old = self._runs.get(resume_run_id)
                if old is not None and old.status not in {"queued", "running"}:
                    self._runs.delete(resume_run_id)
        else:
            self._runs.save(
                current.updated(
                    status="failed",
                    completed_at=datetime.now(UTC),
                    error=error,
                )
            )

    def get(self, run_id: str) -> DeepSweRun | None:
        run = self._runs.get(run_id)
        if run is not None:
            run = self._reconcile_active_run(run)
        return run

    def latest(self) -> DeepSweRun | None:
        self._reconcile_all()
        return self._runs.latest()

    def list_runs(self) -> list[DeepSweRun]:
        """All persisted runs, newest first (active runs reconciled first)."""
        self._reconcile_all()
        runs = self._runs.all_runs()
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    def resumable_partial_results(self, run: DeepSweRun) -> bool:
        """Whether *run* has on-disk partial results that can be continued.

        Service restarts mark active runs as failed with an ``interrupted``
        error, but several backends can resume from their artifacts: api-eval
        copies the completed ``results.jsonl`` prefix, while terminal-bench-2
        delegates to ``harbor job resume``. Exposing this as run metadata lets
        the test page show a real ``接续`` action instead of the unrelated
        records-table ``重测`` action.
        """
        if run.status not in {"failed", "cancelled"}:
            return False
        try:
            backend = self._backend_for_run(run)
        except ValueError:
            return False
        try:
            return bool(backend.has_partial_results(run.run_id))
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            return False

    def run_api_metrics(self, run: DeepSweRun) -> dict[str, float | int | None] | None:
        """Average latency/throughput metrics for an api-eval run list row."""
        try:
            backend = self._backend_for_run(run)
        except ValueError:
            return None
        if getattr(backend, "benchmark_type", None) != "api-eval":
            return None
        try:
            questions = getattr(backend, "run_questions")(
                run_id=run.run_id,
                n_tasks=run.n_tasks,
                sample_seed=run.sample_seed,
            )
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            return None
        if not questions:
            return None

        def avg(key: str) -> float | None:
            values = []
            for question in questions:
                value = question.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(float(value))
            return round(sum(values) / len(values), 3) if values else None

        output_tokens = sum(_non_negative_int(q.get("output_tokens")) for q in questions)
        input_tokens = sum(_non_negative_int(q.get("input_tokens")) for q in questions)
        cached_input_tokens = sum(_non_negative_int(q.get("cached_input_tokens")) for q in questions)
        generation_time = sum(
            float(timing)
            for q in questions
            if isinstance((timing := _metric_from_question(q, "generation_time_sec")), (int, float))
            and not isinstance(timing, bool)
        )
        return {
            "avg_wall_time_sec": avg("wall_time_sec"),
            "avg_first_token_sec": avg("first_token_sec"),
            "avg_first_content_sec": avg("first_content_sec"),
            "output_tokens_per_sec": round(output_tokens / generation_time, 3)
            if generation_time > 0 and output_tokens > 0
            else avg("output_tokens_per_sec"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
        }

    def retryable_infrastructure_failure_count(self, run: DeepSweRun) -> int | None:
        """Count api-eval questions eligible for the records-table retry button.

        The records row uses this to disable ``重测`` when a finished api-eval run
        has no infrastructure/call-chain failures. Passed questions and model
        failures (wrong answer, empty answer, timeout, etc.) are not counted.
        ``None`` means the count is unavailable for this run/backend.
        """
        try:
            backend = self._backend_for_run(run)
        except ValueError:
            return None
        if getattr(backend, "benchmark_type", None) != "api-eval":
            return None
        if run.status not in {"completed", "failed"}:
            return 0
        try:
            questions = getattr(backend, "run_questions")(
                run_id=run.run_id,
                n_tasks=run.n_tasks,
                sample_seed=run.sample_seed,
            )
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            return None
        count = 0
        for question in questions:
            status = str(question.get("status") or "failed")
            if status == "passed":
                continue
            error_type = question.get("error_type")
            outcome, category = classify_outcome(
                status, str(error_type) if error_type is not None else None
            )
            if outcome == "infrastructure_error" and not str(
                category or ""
            ).startswith("model_"):
                count += 1
        return count

    def delete_run(self, run_id: str, *, publisher: object | None = None) -> bool:
        """Delete a run's on-disk state/artifacts and clear dashboard publications.

        Active (queued/running) runs are refused — cancelling them first lets
        the runner process tear down cleanly instead of being orphaned by a
        mid-run rmtree. When a publisher is supplied, every dashboard snapshot
        that still contains this run as a source is removed before the run files
        are deleted, so deleted evaluation records cannot remain visible on the
        dashboard.
        """
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise ValueError("run not found")
            if run.status in {"queued", "running"}:
                raise ValueError("active run cannot be deleted; cancel it first")
            if publisher is not None:
                self.unpublish_run(run_id, publisher=publisher)
            return self._runs.delete(run_id)

    def delete_runs(self, run_ids: list[str], *, publisher: object | None = None) -> dict[str, list]:
        """Delete many runs, never raising on a single failure.

        Returns ``{"deleted": [...ids], "skipped": [{"run_id", "reason"}]}``.
        Each id is deleted independently so one bad id never aborts the rest.
        """
        deleted: list[str] = []
        skipped: list[dict[str, str]] = []
        for run_id in run_ids:
            try:
                self.delete_run(run_id, publisher=publisher)
                deleted.append(run_id)
            except ValueError as caught:
                skipped.append({"run_id": run_id, "reason": str(caught)})
        return {"deleted": deleted, "skipped": skipped}

    def records(self, run_id: str) -> list[RunRecord]:
        run = self._runs.get(run_id)
        if run is None:
            return []
        self._reconcile_active_run(run)
        backend = self._backend_for_run(run)
        return backend.import_records(
            run_id=run_id,
            model_id=run.model_id,
            base_url_hash=base_url_hash(run.base_url),
            effort=run.effort,
            prices=self._prices,
        )

    def evaluation_report(self, run_id: str) -> dict[str, object] | None:
        """Build and persist a report-ready question-level evaluation report.

        Reports are regenerated from native artifacts on every request, so a
        question retry is reflected immediately. The JSON copy under the run
        state directory is also convenient to archive with an evaluation
        report, while native prompts/responses remain available only for local
        runs and are never included in dashboard aggregates.
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        run = self._reconcile_active_run(run)
        backend = self._backend_for_run(run)
        records = self.records(run_id)
        expected = backend.question_catalog(
            n_tasks=run.n_tasks, sample_seed=run.sample_seed
        )
        raw_questions: list[dict[str, object]] = []
        if getattr(backend, "benchmark_type", None) == "api-eval":
            raw_questions = list(
                getattr(backend, "run_questions")(
                    run_id=run_id,
                    n_tasks=run.n_tasks,
                    sample_seed=run.sample_seed,
                )
            )
        report = build_evaluation_report(
            run=run.as_dict(),
            records=records,
            raw_questions=raw_questions,
            expected_questions=expected,
            expected_count=run.n_tasks,
        )
        atomic_write_text(
            self._runs.root / run_id / "evaluation-report.json",
            json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        )
        return report

    def publish(
        self,
        run_id: str,
        *,
        publisher: object,
        merge_with_current: bool = True,
    ):
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError("run not found")
        run = self._reconcile_active_run(run)
        if run.status != "completed":
            raise ValueError("only completed runs can be published")
        records = self.records(run_id)
        if not records:
            raise ValueError("completed run has no records to publish")
        return publisher.publish_records(
            run_id, records, merge_with_current=merge_with_current
        )

    def publish_runs(
        self,
        run_ids: list[str],
        *,
        publisher: object,
        merge_with_current: bool = True,
    ):
        """Publish several completed runs as one accumulated dashboard snapshot.

        Selected from the test page's 评测记录 list. Each run must be
        completed; records are imported from every run and handed to
        ``publisher.publish_runs``, which merges them with the current
        snapshot (radar-v3 accumulation) and writes one snapshot carrying a
        publication marker per run. Returns the ``Publication`` for the shared
        snapshot. Raises ``ValueError`` listing the offending ids when a run is
        missing, not completed, or has no importable records.
        """
        if not run_ids:
            raise ValueError("no run ids to publish")
        # Dedupe preserving order so a run selected twice is published once.
        ordered = list(dict.fromkeys(run_ids))
        records: list[RunRecord] = []
        missing: list[str] = []
        not_completed: list[str] = []
        no_records: list[str] = []
        for rid in ordered:
            run = self._runs.get(rid)
            if run is None:
                missing.append(rid)
                continue
            run = self._reconcile_active_run(run)
            if run.status != "completed":
                not_completed.append(rid)
                continue
            try:
                imported = self.records(rid)
            except (KeyError, OSError, ValueError, json.JSONDecodeError, TypeError) as caught:
                no_records.append(f"{rid} ({caught.__class__.__name__}: {caught})")
                continue
            if not imported:
                no_records.append(rid)
                continue
            records.extend(imported)
        problems: list[str] = []
        if missing:
            problems.append(f"not found: {', '.join(missing)}")
        if not_completed:
            problems.append(f"not completed: {', '.join(not_completed)}")
        if no_records:
            problems.append(f"no records to publish: {', '.join(no_records)}")
        if problems:
            raise ValueError("; ".join(problems))
        if not records:
            raise ValueError("no records to publish")
        publishable = [rid for rid in ordered if rid not in missing + not_completed + no_records]
        return publisher.publish_runs(
            publishable, records, merge_with_current=merge_with_current
        )

    def retry_run_gateway_failures(
        self,
        run_id: str,
        *,
        model_name: str,
        publisher: object = None,
    ) -> DeepSweRun | None:
        """重测一个已完成 api-eval run 中因网关超时/临时错误失败的题目。

        运行以独立线程执行（与 ``submit`` 一致）：先把 run 置为 ``running``，
        调后端 ``retry_gateway_failures`` 重测一轮（模型答错的题不重测），
        结束后若该 run 此前已发布，自动重新发布以让大盘反映重测后的结果。
        仅 api-eval run 可重测；运行中/未完成的 run 拒绝。返回最新的 run。
        """
        with self._lock:
            self._reconcile_all()
            if self._runs.active_runs():
                raise ValueError("another run is already active")
            run = self._runs.get(run_id)
            if run is None:
                raise ValueError("run not found")
            if run.status not in {"completed", "failed"}:
                raise ValueError("only finished runs can be retried")
            backend = self._backend_for_run(run)
            if getattr(backend, "benchmark_type", None) != "api-eval":
                raise ValueError("retry only supported for api-eval runs")
            cancel_event = Event()
            self._cancel_events[run_id] = cancel_event
            self._runs.save(
                run.updated(
                    status="running",
                    error=None,
                    completed_at=None,
                )
            )
            n_tasks = run.n_tasks
            sample_seed = run.sample_seed
            effort = run.effort
            n_concurrent = run.n_concurrent
            # 在派发 worker 前先标 active，消除「线程已起、_active 尚未 add」
            # 的窗口：重测的是已完成的 run，result.json 此刻仍在，否则
            # reconcile 会抢先标 completed（而重测还没真正开始）。
            backend.mark_active(run_id)
        thread = Thread(
            target=self._execute_retry,
            args=(
                run_id,
                model_name,
                n_tasks,
                sample_seed,
                effort,
                n_concurrent,
                cancel_event,
                publisher,
            ),
            name=f"deepswe-retry-{run_id}",
            daemon=True,
        )
        thread.start()
        return self._runs.get(run_id)

    def _execute_retry(
        self,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        effort: str,
        n_concurrent: int | None,
        cancel_event: Event,
        publisher: object | None,
    ) -> None:
        """Worker for :meth:`retry_run_gateway_failures`."""
        log_path = self._runs.log_path(run_id)
        was_published = self._run_publication(publisher, run_id) is not None
        status = "completed"
        error: str | None = None
        try:
            run = self._runs.get(run_id)
            if run is None:
                return
            backend = self._backend_for_run(run)
            returncode, err = backend.retry_gateway_failures(
                run_id=run_id,
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                model_name=model_name,
                n_concurrent=n_concurrent,
                max_rounds=1,
                effort=effort,
                cancel_event=cancel_event,
                log_path=log_path,
            )
            if returncode != 0:
                # 取消不当作失败：run 仍视为已完成，保留已重测成果
                status = "completed"
            # 已发布的 run 自动重发布，让大盘反映重测后的结果
            if publisher is not None and was_published:
                try:
                    publisher.publish_records(
                        run_id,
                        self.records(run_id),
                        merge_with_current=True,
                    )
                except (KeyError, OSError, ValueError):
                    # 重发布失败不影响重测本身；前端可手动重发布
                    pass
        except Exception as caught:  # pragma: no cover - defensive
            status = "failed"
            error = f"{caught.__class__.__name__}: {caught}"
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
                run = self._runs.get(run_id)
                if run is not None:
                    # 兜底释放 mark_active 置的活跃标记（retry_gateway_failures
                    # 自身也会 discard，此处幂等，防 worker 异常时残留）。
                    self._backend_for_run(run).release_active(run_id)
                    if run.status == "running":
                        self._finish(run, status=status, error=error)

    def run_questions(self, run_id: str) -> list[dict[str, object]]:
        """逐题结果列表（仅 api-eval），供评测记录的题目级重测 UI。"""
        with self._lock:
            self._reconcile_all()
            run = self._runs.get(run_id)
            if run is None:
                raise ValueError("run not found")
            backend = self._backend_for_run(run)
            if getattr(backend, "benchmark_type", None) != "api-eval":
                raise ValueError("questions only available for api-eval runs")
        questions = backend.run_questions(
            run_id=run_id, n_tasks=run.n_tasks, sample_seed=run.sample_seed
        )
        return list(questions)

    def retry_run_questions(
        self,
        run_id: str,
        *,
        model_name: str,
        task_ids: list[str],
        publisher: object = None,
    ) -> DeepSweRun | None:
        """重测指定题目（评测记录的「单题重测 / 全部重测」入口）。

        与 :meth:`retry_run_gateway_failures` 同一套线程/发布语义，但不筛
        失败类型：*task_ids* 里的题无论成败都会重跑（全部重测即传全部题）。
        仅 api-eval、已完成/失败且当前无活跃 run 时可重测。
        """
        clean_ids = [str(task_id) for task_id in dict.fromkeys(task_ids) if str(task_id)]
        if not clean_ids:
            raise ValueError("task_ids is required")
        with self._lock:
            self._reconcile_all()
            if self._runs.active_runs():
                raise ValueError("another run is already active")
            run = self._runs.get(run_id)
            if run is None:
                raise ValueError("run not found")
            if run.status not in {"completed", "failed"}:
                raise ValueError("only finished runs can be retried")
            backend = self._backend_for_run(run)
            if getattr(backend, "benchmark_type", None) != "api-eval":
                raise ValueError("retry only supported for api-eval runs")
            cancel_event = Event()
            self._cancel_events[run_id] = cancel_event
            self._runs.save(
                run.updated(
                    status="running",
                    error=None,
                    completed_at=None,
                )
            )
            n_tasks = run.n_tasks
            sample_seed = run.sample_seed
            effort = run.effort
            backend.mark_active(run_id)
        thread = Thread(
            target=self._execute_retry_questions,
            args=(
                run_id,
                model_name,
                n_tasks,
                sample_seed,
                effort,
                clean_ids,
                cancel_event,
                publisher,
            ),
            name=f"deepswe-retryq-{run_id}",
            daemon=True,
        )
        thread.start()
        return self._runs.get(run_id)

    def _execute_retry_questions(
        self,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        effort: str,
        task_ids: list[str],
        cancel_event: Event,
        publisher: object | None,
    ) -> None:
        """Worker for :meth:`retry_run_questions`."""
        log_path = self._runs.log_path(run_id)
        was_published = self._run_publication(publisher, run_id) is not None
        status = "completed"
        error: str | None = None
        try:
            run = self._runs.get(run_id)
            if run is None:
                return
            backend = self._backend_for_run(run)
            returncode, err = backend.retry_questions(
                run_id=run_id,
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                model_name=model_name,
                task_ids=task_ids,
                effort=effort,
                cancel_event=cancel_event,
                log_path=log_path,
            )
            if returncode != 0:
                # 取消不当作失败：run 仍视为已完成，保留已重测成果
                status = "completed"
            # 已发布的 run 自动重发布，让大盘反映重测后的结果
            if publisher is not None and was_published:
                try:
                    publisher.publish_records(
                        run_id,
                        self.records(run_id),
                        merge_with_current=True,
                    )
                except (KeyError, OSError, ValueError):
                    pass
        except Exception as caught:  # pragma: no cover - defensive
            status = "failed"
            error = f"{caught.__class__.__name__}: {caught}"
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
                run = self._runs.get(run_id)
                if run is not None:
                    self._backend_for_run(run).release_active(run_id)
                    if run.status == "running":
                        self._finish(run, status=status, error=error)

    @staticmethod
    def _run_publication(publisher: object | None, run_id: str):
        if publisher is None:
            return None
        try:
            return publisher.publication_for_job(run_id)
        except (KeyError, OSError, ValueError):
            return None

    def unpublish_run(
        self,
        run_id: str,
        *,
        publisher: object,
    ) -> dict[str, object] | None:
        """回撤一个 run 的发布：删除所有包含该 run 来源的快照。

        radar-v3 大盘快照会累积历史记录；该 run 可能被后续快照继续携带，
        因此 publisher 会按 source_job_ids 清理所有相关快照并修复 current。
        未发布过的 run 返回 None（由 API 层映射为 404）。
        """
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError("run not found")
        try:
            return publisher.delete_publication(run_id)  # type: ignore[attr-defined]
        except (KeyError, OSError, ValueError):
            return None

    # --- multi-model batches ------------------------------------------------

    BATCH_POLL_SEC = 20

    def submit_batch(
        self,
        *,
        model_ids: list[str],
        model_names: list[str],
        n_tasks: int,
        sample_seed: int,
        benchmark: str = DEFAULT_BENCHMARK,
        effort: str = "high",
        n_concurrent: int | None = None,
        max_concurrent: int = 1,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> dict[str, object]:
        """Queue runs for several models, executed in a thread.

        Each model runs ``n_tasks`` tasks as its own DeepSWE run; the batch is
        finalised as ``completed`` once every model has finished. Completing a
        batch does not publish to the dashboard — the user publishes selected
        records from the 评测记录 list. *max_concurrent* caps how many model
        runs execute in parallel (1 = the legacy sequential behaviour). Batch
        state is persisted under ``data/deepswe/batches/<batch_id>.json``.
        """
        if not model_ids:
            raise ValueError("at least one model is required")
        if len(model_ids) != len(model_names):
            raise ValueError("model ids and names must be paired")
        if n_tasks < 1:
            raise ValueError("n_tasks must be at least 1")
        if not 1 <= max_concurrent <= 16:
            raise ValueError("max_concurrent must be between 1 and 16")
        backend = self._backend(benchmark)  # validate
        error = backend.preflight()
        if error:
            raise ValueError(f"benchmark '{benchmark}' is not ready: {error}")
        with self._lock:
            self._reconcile_all()
            if self._runs.active() is not None:
                raise ValueError("another deep-swe run is already active")
            batch_id = uuid4().hex
            state: dict[str, object] = {
                "batch_id": batch_id,
                "status": "running",
                "n_tasks": n_tasks,
                "sample_seed": sample_seed,
                "created_at": datetime.now(UTC).isoformat(),
                "completed_at": None,
                "current_model": None,
                "error": None,
                "snapshot_id": None,
                "benchmark": benchmark,
                "effort": effort,
                "n_concurrent": n_concurrent,
                "max_concurrent": max_concurrent,
                # 网关失败重测（仅 api-eval 生效）：随批次持久化，接续时沿用
                "retry_gateway_failures": retry_gateway_failures,
                "gateway_retry_rounds": gateway_retry_rounds,
                "models": [
                    {
                        "model_id": model_id,
                        "model_name": model_name,
                        "status": "pending",
                        "run_id": None,
                    }
                    for model_id, model_name in zip(model_ids, model_names)
                ],
            }
            self._save_batch(state)
            thread = Thread(
                target=self._execute_batch,
                args=(batch_id, n_tasks, sample_seed, effort, max_concurrent),
                name=f"deepswe-batch-{batch_id}",
                daemon=True,
            )
            thread.start()
            return state

    def _execute_batch(
        self,
        batch_id: str,
        n_tasks: int,
        sample_seed: int,
        effort: str,
        max_concurrent: int = 1,
    ) -> None:
        """Run queued models with at most *max_concurrent* runs in flight.

        ``max_concurrent == 1`` reproduces the legacy sequential behaviour. The
        cancel endpoint mutates the batch file on disk while this worker runs,
        so the worker never keeps a private snapshot across mutations: it
        reloads the authoritative state under the service lock before every
        decision, and a cancel that lands between iterations still stops the
        whole batch. Completing a batch does not publish: the user publishes
        selected records from the 评测记录 list.
        """
        state = self._load_batch(batch_id)
        if state is None:
            return
        benchmark = str(state.get("benchmark", DEFAULT_BENCHMARK))
        models: list = state["models"]  # type: ignore[union-attr]
        pending = list(range(len(models)))
        in_flight: dict[str, int] = {}
        # Skip models already completed in a previous attempt (batch resume);
        # their runs are re-derived at publish time from the authoritative state.
        for index, entry in enumerate(models):
            if entry.get("status") == "completed" and entry.get("run_id"):
                run = self._runs.get(str(entry["run_id"]))
                if run is not None and run.status == "completed":
                    pending.remove(index)
        cancelled = False
        try:
            while pending or in_flight:
                with self._lock:
                    state = self._load_batch(batch_id)
                    if state is None:
                        return
                    if state.get("cancel_requested") or state.get("status") in ("cancelled", "failed"):
                        cancelled = True
                        break
                    if state.get("status") == "completed":
                        # 批次已被 reconcile 终态化为 completed（例如两次轮询之间
                        # 前端 get_batch 触发 reconcile，把已结束的 run 同步进
                        # entry）。这不是取消：停止调度，交给终态化阶段重算
                        # completed runs 并发布，避免快照丢失。
                        break
                    models = state["models"]  # type: ignore[union-attr]
                    while pending and len(in_flight) < max_concurrent:
                        index = pending.pop(0)
                        entry = models[index]
                        prev_status = entry.get("status")
                        # Task-level resume: if the model entry has a failed
                        # prior run with partial results, pass it so the backend
                        # skips already completed items.
                        resume_run_id: str | None = None
                        prev_run_id = entry.get("run_id")
                        if prev_run_id and prev_status == "failed":
                            prev_run = self._runs.get(str(prev_run_id))
                            if prev_run is not None:
                                backend = self._backend_for_run(prev_run)
                                if hasattr(backend, "has_partial_results") and hasattr(backend, "benchmark_type"):
                                    try:
                                        if backend.has_partial_results(str(prev_run_id)):
                                            resume_run_id = str(prev_run_id)
                                    except Exception:
                                        pass  # safe to ignore; run starts fresh
                        run = self.submit(
                            model_id=entry["model_id"],
                            model_name=entry["model_name"],
                            n_tasks=n_tasks,
                            sample_seed=sample_seed,
                            base_url=self._backend_base_url(benchmark),
                            benchmark=benchmark,
                            effort=effort,
                            resume_run_id=resume_run_id,
                            n_concurrent=state.get("n_concurrent"),
                            max_concurrent_runs=max_concurrent,
                            retry_gateway_failures=bool(
                                state.get("retry_gateway_failures")
                            ),
                            gateway_retry_rounds=state.get("gateway_retry_rounds"),
                        )
                        in_flight[run.run_id] = index
                        entry["run_id"] = run.run_id
                        entry["status"] = "running"
                        if not state.get("current_model"):
                            state["current_model"] = entry["model_id"]
                        self._save_batch(state)
                if cancelled:
                    break
                time.sleep(self.BATCH_POLL_SEC)
                with self._lock:
                    state = self._load_batch(batch_id)
                    if state is None:
                        return
                    models = state["models"]  # type: ignore[union-attr]
                    for run_id in list(in_flight):
                        run = self.get(run_id)
                        if run is None:
                            continue
                        if run.status in ("completed", "failed"):
                            index = in_flight.pop(run_id)
                            models[index]["status"] = run.status
                            self._save_batch(state)
                    # Heartbeat: keep batch updated_at fresh so the orphan-
                    # reconcile never misfires during a long model execution.
                    if state.get("status") == "running":
                        self._save_batch(state)
            # Finalise against the authoritative state: a batch cancelled
            # while the last run was finishing must stay cancelled. Batches no
            # longer auto-publish on completion — completing a batch only
            # records its runs as done; the user publishes selected records
            # from the test page's 评测记录 list (POST /api/deepswe-runs/
            # publish-batch), so a finished batch always leaves snapshot_id
            # null until records are published explicitly.
            with self._lock:
                state = self._load_batch(batch_id) or state
                cancelled = cancelled or bool(state.get("cancel_requested"))
            for run_id in in_flight:
                self.cancel(run_id)
            with self._lock:
                state = self._load_batch(batch_id) or state
                state["status"] = "cancelled" if cancelled else "completed"  # type: ignore[union-attr]
                state["completed_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
                self._save_batch(state)
        except Exception as caught:
            with self._lock:
                state = self._load_batch(batch_id) or {}
                state["status"] = "failed"  # type: ignore[union-attr]
                state["error"] = f"{caught.__class__.__name__}: {caught}"  # type: ignore[union-attr]
                state["completed_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
                self._save_batch(state)

    def cancel_batch(self, batch_id: str) -> bool:
        """Request cancellation of a running batch.

        Marks the batch cancelled immediately (so a batch whose worker thread
        died with a server restart is also finalised), flags the worker to
        stop before the next model, and cancels the currently active model
        run. Cancelling an already-cancelled batch is an idempotent no-op
        success so repeated clicks never surface a spurious error.
        """
        with self._lock:
            state = self._load_batch(batch_id)
            if state is None:
                return False
            if state.get("status") == "cancelled":
                return True
            if state.get("status") != "running":
                return False
            state["cancel_requested"] = True  # type: ignore[union-attr]
            state["status"] = "cancelled"  # type: ignore[union-attr]
            state["completed_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
            self._save_batch(state)
            active_run_ids = [
                str(entry["run_id"])
                for entry in state.get("models", [])  # type: ignore[union-attr]
                if entry.get("status") == "running" and entry.get("run_id")
            ]
        for run_id in active_run_ids:
            self.cancel(run_id)
        return True

    def resume_batch(
        self,
        batch_id: str,
    ) -> dict[str, object] | None:
        """Resume a failed/cancelled batch from its first incomplete model.

        Models already completed (in a previous interrupted attempt) are left
        untouched and skipped by the new worker; models with a failed run that
        has partial results are resumed at task level by the backend. A fully
        completed batch is not resumable (nothing left to run, and batches no
        longer auto-publish so there is no publication to recover). Returns the
        refreshed batch state, None when the batch is not resumable.
        """
        with self._lock:
            state = self._load_batch(batch_id)
            if state is None:
                return None
            # 先 reconcile：服务器重启后批次和它的 run 可能仍停在
            # "running"（worker 线程已随进程死亡）。reconcile 会把粘连的
            # run 标记为 interrupted、必要时把批次终态化，再判断能否接续。
            state = self._reconcile_batch(state)
            if state.get("status") == "running":
                return state  # already running: idempotent no-op
            if self._runs.active() is not None:
                raise ValueError("another deep-swe run is already active")
            models = state.get("models")
            if not isinstance(models, list) or not models:
                return None
            # 全部 completed 的批次无事可做：模型都跑完了，且批次不再自动
            # 发布，没有可恢复的发布动作。
            all_completed = all(entry.get("status") == "completed" for entry in models)
            if all_completed:
                return None
            benchmark = str(state.get("benchmark") or DEFAULT_BENCHMARK)
            error = self._backend(benchmark).preflight()
            if error:
                raise ValueError(f"benchmark '{benchmark}' is not ready: {error}")
            # Flip the terminal state back to running and clear the finish
            # markers so the next worker+reconcile treat it as active again.
            state["status"] = "running"
            state["completed_at"] = None
            state["error"] = None
            state.pop("cancel_requested", None)
            self._save_batch(state)
            n_tasks = int(state.get("n_tasks") or 0)
            sample_seed = int(state.get("sample_seed") or 0)
            effort = str(state.get("effort") or "high")
            max_concurrent = int(state.get("max_concurrent") or 1)
        thread = Thread(
            target=self._execute_batch,
            args=(batch_id, n_tasks, sample_seed, effort, max_concurrent),
            name=f"deepswe-batch-{batch_id}",
            daemon=True,
        )
        thread.start()
        return self._load_batch(batch_id)

    def get_batch(self, batch_id: str) -> dict[str, object] | None:
        state = self._load_batch(batch_id)
        if state is None:
            return None
        return self._reconcile_batch(state)

    def latest_batch(self) -> dict[str, object] | None:
        latest: dict[str, object] | None = None
        latest_created = ""
        for path in self._batches_root.glob("*.json"):
            state = self._load_batch(path.stem)
            if state is None:
                continue
            created = str(state.get("created_at", ""))
            if created > latest_created:
                latest = state
                latest_created = created
        if latest is None:
            return None
        return self._reconcile_batch(latest)

    def _reconcile_batch(self, state: dict[str, object] | None) -> dict[str, object] | None:
        """Refresh a batch from the run store and finalise orphaned batches.

        Every model entry is synced to its run's real status (so a cancelled
        batch never shows a stuck ``running`` chip). A batch whose worker died
        with a server restart stays ``running`` forever otherwise: when no run
        is in flight and the batch has been idle past ``RECONCILE_AGE_SEC`` it
        is finalised — cancelled when a cancel was requested, completed when
        every run finished, failed otherwise.
        """
        if state is None:
            return state
        entries = state.get("models")
        if not isinstance(entries, list):
            return state
        has_pending = False
        has_active_run = False
        changed = False
        for entry in entries:
            run_id = entry.get("run_id")
            if not run_id:
                if entry.get("status") in {"pending", "running"}:
                    has_pending = True
                continue
            run = self._runs.get(str(run_id))
            if run is not None:
                run = self._reconcile_active_run(run)
            real = run.status if run is not None else "failed"
            if entry.get("status") != real:
                entry["status"] = real
                changed = True
            if real in {"queued", "running"}:
                has_active_run = True
        if state.get("status") != "running":
            if changed:
                self._save_batch(state)
            return state
        if has_active_run:
            return state  # a run is genuinely in flight
        if state.get("cancel_requested"):
            state["status"] = "cancelled"
            changed = True
        elif not has_pending:
            # entry 状态被本次 reconcile 修正（run 已结束而 entry 没跟上）
            # 说明 worker 已死——活着的 worker 会在轮询间隔内自己同步状态，
            # 立即终态化。entry 本来就全部 completed 且无变化时，worker 可
            # 能正处于发布阶段（导入 records + 写快照，无心跳），立即终态
            # 化会与发布竞争：批次提前显示 completed 而 snapshot_id 迟迟为
            # 空（2026-08-30 批次 2e5bb1b0 大盘丢数据的形态）。等 idle 超过
            # 阈值再兜底；正常路径由 worker 自己标 completed。
            if changed or self._batch_idle_sec(state) >= self.RECONCILE_AGE_SEC:
                state["status"] = "completed"
                changed = True
        elif self._batch_idle_sec(state) >= self.RECONCILE_AGE_SEC:
            state["status"] = "failed"
            state["error"] = state.get("error") or "interrupted: server restarted mid-batch"
            changed = True
        if changed:
            if state.get("status") != "running":
                state["completed_at"] = state.get("completed_at") or datetime.now(UTC).isoformat()
            self._save_batch(state)
        return state

    @staticmethod
    def _batch_idle_sec(state: dict[str, object]) -> float:
        raw = state.get("updated_at") or state.get("created_at") or ""
        try:
            updated = datetime.fromisoformat(str(raw))
        except ValueError:
            return 0.0
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return (datetime.now(UTC) - updated).total_seconds()

    def _save_batch(self, state: dict[str, object]) -> None:
        state["updated_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
        batch_id = str(state["batch_id"])
        atomic_write_text(
            self._batches_root / f"{batch_id}.json",
            json.dumps(state, ensure_ascii=True, indent=2) + "\n",
        )

    def _load_batch(self, batch_id: str) -> dict[str, object] | None:
        path = self._batches_root / f"{batch_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # --- restart reconciliation -------------------------------------------

    RECONCILE_AGE_SEC = 60

    def _reconcile_all(self) -> None:
        for run in self._runs.all_runs():
            self._reconcile_active_run(run)

    def _reconcile_active_run(self, run: DeepSweRun) -> DeepSweRun:
        """Transition runs orphaned by a server restart.

        A run left ``queued``/``running`` whose worker thread died with the
        previous server process is reconciled from the filesystem: once the
        backend's job-level result is finalized the run is marked completed;
        if the runner process is gone without a finished result the run is
        marked failed. Runs younger than ``RECONCILE_AGE_SEC`` are left alone
        so a freshly submitted run whose runner is still spawning is never
        misclassified.

        When the runner is still alive (worker thread holds ``_active``, or a
        pier process is still running) the run is left ``running`` even if a
        job-level ``result.json`` already exists: this matters for the gateway
        retry path, which re-runs questions on an *already finished* run whose
        ``result.json`` is only removed once the retry thread begins — without
        this guard ``job_finished`` would prematurely mark the run completed
        before the retry has actually run.
        """
        if run.status not in {"queued", "running"}:
            return run
        backend = self._backend_for_run(run)
        if backend.runner_alive(run.run_id):
            return run
        if backend.job_finished(run.run_id):
            return self._finish(run, status="completed")
        age = (datetime.now(UTC) - run.created_at).total_seconds()
        if age < self.RECONCILE_AGE_SEC:
            return run
        return self._finish(
            run,
            status="failed",
            error="interrupted: server restarted mid-run",
        )

    def _finish(
        self,
        run: DeepSweRun,
        *,
        status: str,
        error: str | None = None,
    ) -> DeepSweRun:
        updated = run.updated(
            status=status,  # type: ignore[arg-type]
            completed_at=datetime.now(UTC),
            error=error,
        )
        self._runs.save(updated)
        return updated

    def _runner_alive(self, run: DeepSweRun) -> bool:
        return self._backend_for_run(run).runner_alive(run.run_id)

    def _runner_pid(self, run: DeepSweRun) -> int | None:
        """PID of the runner process for ``run``, or None."""
        marker = self._backend_for_run(run).runner_marker(run.run_id)
        if not marker:
            return None
        try:
            output = subprocess.run(
                ["ps", "-ef"], capture_output=True, text=True, timeout=5
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in output.splitlines():
            if marker in line:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
        return None

    def _kill_runner(self, run: DeepSweRun) -> bool:
        """Stop the process group for a run (used for orphaned runs).

        A run whose worker thread died (server restart) has no cancel event
        left to set, so the batch/run cancel falls back to terminating the
        runner process group directly. Returns False when no such process is
        found.
        """
        pid = self._runner_pid(run)
        if pid is None:
            return False
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                time.sleep(0.2)
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        return True

    # --- live logs ---------------------------------------------------------

    LOG_SOURCES = ("pier", "job", "trial", "agent", "verifier", "llm")  # legacy (deep-swe)

    def progress(self, run_id: str) -> dict[str, int] | None:
        """题目级进度（测试页实时日志/评测进程展示用）。

        后端能给出进度时（pier/harbor 的 job 级统计、api-eval 的实时计数）
        以其为准，缺失的 total 用 run 的 ``n_tasks`` 补齐；后端完全给不出
        （如 Docker 环境构建阶段）时，活跃 run 回退 ``0/n_tasks``，已结束的
        run 返回 None。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        raw = self._backend_for_run(run).progress(run_id)
        if raw is None:
            if run.status in {"queued", "running"}:
                return {"total": run.n_tasks, "completed": 0}
            return None
        total = _positive_int(raw.get("total")) or run.n_tasks
        completed = min(_non_negative_int(raw.get("completed")), total)
        progress: dict[str, int] = {"total": total, "completed": completed}
        for key in ("running", "pending"):
            value = _non_negative_int(raw.get(key))
            if value:
                progress[key] = value
        return progress

    def log_sources(self, run_id: str) -> list[dict[str, object]]:
        run = self._runs.get(run_id)
        if run is not None:
            return self._backend_for_run(run).log_sources(run_id)
        # Backward compatibility: unknown/legacy run ids resolve against the
        # default benchmark's file layout.
        backend = self._backends.get(DEFAULT_BENCHMARK) or next(
            iter(self._backends.values())
        )
        return backend.log_sources(run_id)

    def log_content(
        self,
        run_id: str,
        source: str,
        *,
        tail: int = 2000,
    ) -> dict[str, object]:
        run = self._runs.get(run_id)
        if run is not None:
            return self._backend_for_run(run).log_content(run_id, source, tail=tail)
        backend = self._backends.get(DEFAULT_BENCHMARK) or next(
            iter(self._backends.values())
        )
        return backend.log_content(run_id, source, tail=tail)

    # --- multi-benchmark concurrent batches --------------------------------

    MULTI_POLL_SEC = 5

    def submit_multi_bench(
        self,
        *,
        items: list[dict[str, str]],
        max_concurrent: int,
        n_tasks: int,
        sample_seed: int,
        effort: str = "high",
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> dict[str, object]:
        """Queue one run per (benchmark, model) item, running up to
        *max_concurrent* in parallel.

        Only api-eval benchmarks are accepted: their runs stay in-process
        (ThreadPoolExecutor) so concurrent runs contend for the model gateway,
        not for Docker. Completing a multi-bench batch does not publish; the
        user publishes selected records from the 评测记录 list. State persists
        under ``data/deepswe/multi-batches/<batch_id>.json``.
        """
        if not items:
            raise ValueError("at least one item is required")
        if not 1 <= max_concurrent <= 16:
            raise ValueError("max_concurrent must be between 1 and 16")
        if n_tasks < 1:
            raise ValueError("n_tasks must be at least 1")
        for entry in items:
            benchmark = str(entry.get("benchmark", ""))
            if benchmark not in self._backends:
                raise ValueError(f"unknown benchmark: {benchmark}")
            if self._backends[benchmark].category != "api-eval":
                raise ValueError(
                    f"benchmark '{benchmark}' is not api-eval; "
                    "multi-bench concurrency is api-eval only"
                )
            if not entry.get("model_id") or not entry.get("model_name"):
                raise ValueError("each item needs model_id and model_name")
            # 每项可选独立 n_tasks（按基准粒度覆盖批量级值）；缺省回退。
            item_n_tasks = entry.get("n_tasks")
            if item_n_tasks is None:
                continue
            if isinstance(item_n_tasks, bool) or not isinstance(item_n_tasks, int) or not (
                1 <= item_n_tasks <= self._backends[benchmark].max_tasks()
            ):
                raise ValueError(
                    f"benchmark '{benchmark}' n_tasks must be an integer "
                    f"between 1 and {self._backends[benchmark].max_tasks()}"
                )
        with self._lock:
            self._reconcile_all_multi()
            if self._runs.active_runs():
                raise ValueError("another run is already active")
            batch_id = uuid4().hex
            state: dict[str, object] = {
                "batch_id": batch_id,
                "kind": "multi-bench",
                "status": "running",
                "max_concurrent": max_concurrent,
                "n_tasks": n_tasks,
                "sample_seed": sample_seed,
                "effort": effort,
                "n_concurrent": n_concurrent,
                "created_at": datetime.now(UTC).isoformat(),
                "completed_at": None,
                "cancel_requested": False,
                "snapshot_id": None,
                "error": None,
                # 网关失败重测（多基准均为 api-eval）：随批次持久化，接续时沿用
                "retry_gateway_failures": retry_gateway_failures,
                "gateway_retry_rounds": gateway_retry_rounds,
                "items": [
                    {
                        "benchmark": e["benchmark"],
                        "model_id": e["model_id"],
                        "model_name": e["model_name"],
                        # 每项 n_tasks：独立配置时存 per-item，否则回退批量级。
                        "n_tasks": e.get("n_tasks", n_tasks),
                        "status": "pending",
                        "run_id": None,
                    }
                    for e in items
                ],
            }
            self._save_multi_batch(state)
            thread = Thread(
                target=self._execute_multi_bench,
                args=(
                    batch_id,
                    max_concurrent,
                    effort,
                    n_concurrent,
                    n_tasks,
                    sample_seed,
                ),
                name=f"multi-bench-{batch_id}",
                daemon=True,
            )
            thread.start()
            return state

    def _execute_multi_bench(
        self,
        batch_id: str,
        max_concurrent: int,
        effort: str,
        n_concurrent: int | None,
        n_tasks: int,
        sample_seed: int,
    ) -> None:
        """Run queued items with at most *max_concurrent* runs in flight.

        Each run executes in its own ``_execute`` thread (started by
        ``submit``); this loop only schedules and polls. Reloading state
        under the lock before every decision lets a cancel land between
        iterations and still stop the whole batch. Completing the batch does
        not publish; the user publishes selected records from 评测记录.
        """
        state = self._load_multi_batch(batch_id)
        if state is None:
            return
        items: list = state["items"]  # type: ignore[union-attr]
        pending = list(range(len(items)))
        in_flight: dict[str, int] = {}
        for index, entry in enumerate(items):
            if entry.get("status") == "completed" and entry.get("run_id"):
                run = self._runs.get(str(entry["run_id"]))
                if run is not None and run.status == "completed":
                    pending.remove(index)
        cancelled = False
        try:
            while pending or in_flight:
                with self._lock:
                    state = self._load_multi_batch(batch_id)
                    if state is None:
                        return
                    if state.get("cancel_requested") or state.get("status") != "running":
                        cancelled = True
                        break
                    items = state["items"]
                    while pending and len(in_flight) < max_concurrent:
                        index = pending.pop(0)
                        entry = items[index]
                        resume_run_id: str | None = None
                        prev_run_id = entry.get("run_id")
                        if prev_run_id and entry.get("status") == "failed":
                            prev_run = self._runs.get(str(prev_run_id))
                            backend = self._backends.get(str(entry["benchmark"]))
                            if (
                                prev_run is not None
                                and backend is not None
                                and hasattr(backend, "has_partial_results")
                                and backend.has_partial_results(prev_run_id)
                            ):
                                resume_run_id = str(prev_run_id)
                        run = self.submit(
                            model_id=entry["model_id"],
                            model_name=entry["model_name"],
                            # 每项 n_tasks：旧 batch（无该字段）回退 *batch 级传入值*。
                            n_tasks=int(entry.get("n_tasks") or n_tasks),
                            sample_seed=sample_seed,
                            base_url=self._backend_base_url(str(entry["benchmark"])),
                            benchmark=str(entry["benchmark"]),
                            effort=effort,
                            n_concurrent=n_concurrent,
                            max_concurrent_runs=max_concurrent,
                            resume_run_id=resume_run_id,
                            retry_gateway_failures=bool(
                                state.get("retry_gateway_failures")
                            ),
                            gateway_retry_rounds=state.get("gateway_retry_rounds"),
                        )
                        in_flight[run.run_id] = index
                        entry["run_id"] = run.run_id
                        entry["status"] = "running"
                        self._save_multi_batch(state)
                if cancelled:
                    break
                time.sleep(self.MULTI_POLL_SEC)
                with self._lock:
                    state = self._load_multi_batch(batch_id)
                    if state is None:
                        return
                    items = state["items"]
                    for run_id in list(in_flight):
                        run = self.get(run_id)
                        if run is None:
                            continue
                        if run.status in ("completed", "failed"):
                            index = in_flight.pop(run_id)
                            items[index]["status"] = run.status
                            self._save_multi_batch(state)
            with self._lock:
                state = self._load_multi_batch(batch_id) or state
                cancelled = cancelled or bool(state.get("cancel_requested"))
            for run_id in in_flight:
                self.cancel(run_id)
            # Batches no longer auto-publish on completion — finalise the batch
            # and leave snapshot_id null until records are published explicitly
            # from the test page's 评测记录 list.
            with self._lock:
                state = self._load_multi_batch(batch_id) or state
                state["status"] = "cancelled" if cancelled else "completed"  # type: ignore[union-attr]
                state["completed_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
                self._save_multi_batch(state)
        except Exception as caught:
            with self._lock:
                state = self._load_multi_batch(batch_id) or {}
                state["status"] = "failed"  # type: ignore[union-attr]
                state["error"] = f"{caught.__class__.__name__}: {caught}"  # type: ignore[union-attr]
                state["completed_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
                self._save_multi_batch(state)

    def cancel_multi_batch(self, batch_id: str) -> bool:
        with self._lock:
            state = self._load_multi_batch(batch_id)
            if state is None:
                return False
            if state.get("status") == "cancelled":
                return True
            if state.get("status") != "running":
                return False
            state["cancel_requested"] = True  # type: ignore[union-attr]
            state["status"] = "cancelled"  # type: ignore[union-attr]
            state["completed_at"] = datetime.now(UTC).isoformat()  # type: ignore[union-attr]
            self._save_multi_batch(state)
            active_run_ids = [
                str(e["run_id"])
                for e in state.get("items", [])  # type: ignore[union-attr]
                if e.get("status") == "running" and e.get("run_id")
            ]
        for run_id in active_run_ids:
            self.cancel(run_id)
        return True

    def resume_multi_batch(
        self,
        batch_id: str,
    ) -> dict[str, object] | None:
        with self._lock:
            state = self._load_multi_batch(batch_id)
            if state is None:
                return None
            state = self._reconcile_multi_batch(state)
            if state.get("status") == "running":
                return state
            if self._runs.active_runs():
                raise ValueError("another run is already active")
            items = state.get("items")
            if not isinstance(items, list) or not items:
                return None
            # 全部 completed 的批次无事可做：runs 都跑完了，且批次不再自动
            # 发布，没有可恢复的发布动作。
            all_completed = all(e.get("status") == "completed" for e in items)
            if all_completed:
                return None
            for entry in items:
                benchmark = str(entry.get("benchmark", ""))
                if benchmark in self._backends:
                    err = self._backends[benchmark].preflight()
                    if err:
                        raise ValueError(
                            f"benchmark '{benchmark}' is not ready: {err}"
                        )
            state["status"] = "running"
            state["completed_at"] = None
            state["error"] = None
            state.pop("cancel_requested", None)
            self._save_multi_batch(state)
            n_tasks = int(state.get("n_tasks") or 0)
            sample_seed = int(state.get("sample_seed") or 0)
            max_concurrent = int(state.get("max_concurrent") or 1)
            effort = str(state.get("effort") or "high")
            n_concurrent = state.get("n_concurrent")
        thread = Thread(
            target=self._execute_multi_bench,
            args=(batch_id, max_concurrent, effort, n_concurrent, n_tasks, sample_seed),
            name=f"multi-bench-{batch_id}",
            daemon=True,
        )
        thread.start()
        return self._load_multi_batch(batch_id)

    def get_multi_batch(self, batch_id: str) -> dict[str, object] | None:
        state = self._load_multi_batch(batch_id)
        if state is None:
            return None
        return self._reconcile_multi_batch(state)

    def latest_multi_batch(self) -> dict[str, object] | None:
        latest: dict[str, object] | None = None
        latest_created = ""
        for path in self._multi_batches_root.glob("*.json"):
            state = self._load_multi_batch(path.stem)
            if state is None:
                continue
            created = str(state.get("created_at", ""))
            if created > latest_created:
                latest = state
                latest_created = created
        if latest is None:
            return None
        return self._reconcile_multi_batch(latest)

    def _reconcile_all_multi(self) -> None:
        for path in self._multi_batches_root.glob("*.json"):
            state = self._load_multi_batch(path.stem)
            if state is not None:
                self._reconcile_multi_batch(state)

    def _reconcile_multi_batch(
        self, state: dict[str, object] | None
    ) -> dict[str, object] | None:
        if state is None:
            return state
        entries = state.get("items")
        if not isinstance(entries, list):
            return state
        has_pending = False
        has_active_run = False
        changed = False
        for entry in entries:
            run_id = entry.get("run_id")
            # has_pending: any item not yet finished (covers both unsubmitted
            # items with run_id=None and in-flight items still running).
            if entry.get("status") in {"pending", "running"}:
                has_pending = True
            if not run_id:
                continue
            run = self._runs.get(str(run_id))
            if run is not None:
                run = self._reconcile_active_run(run)
            real = run.status if run is not None else "failed"
            if entry.get("status") != real:
                entry["status"] = real
                changed = True
            if real in {"queued", "running"}:
                has_active_run = True
        if state.get("status") != "running":
            if changed:
                self._save_multi_batch(state)
            return state
        if has_active_run:
            return state
        if state.get("cancel_requested"):
            state["status"] = "cancelled"
            changed = True
        elif not has_pending:
            if changed or self._batch_idle_sec(state) >= self.RECONCILE_AGE_SEC:
                state["status"] = "completed"
                changed = True
        elif self._batch_idle_sec(state) >= self.RECONCILE_AGE_SEC:
            state["status"] = "failed"
            state["error"] = state.get("error") or "interrupted: server restarted mid-batch"
            changed = True
        if changed:
            if state.get("status") != "running":
                state["completed_at"] = state.get("completed_at") or datetime.now(UTC).isoformat()
            self._save_multi_batch(state)
        return state

    def _save_multi_batch(self, state: dict[str, object]) -> None:
        state["updated_at"] = datetime.now(UTC).isoformat()
        batch_id = str(state["batch_id"])
        atomic_write_text(
            self._multi_batches_root / f"{batch_id}.json",
            json.dumps(state, ensure_ascii=True, indent=2) + "\n",
        )

    def _load_multi_batch(self, batch_id: str) -> dict[str, object] | None:
        path = self._multi_batches_root / f"{batch_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
