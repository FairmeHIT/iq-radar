"""评测记录的「重测网关失败题」与「回撤发布」端到端行为。

覆盖目标 2 的两条新链路：
- ``DeepSweService.retry_run_gateway_failures``：对一个已完成的 api-eval run
  起线程重测网关失败题，结束后若已发布则自动重发布；
- ``DeepSweService.unpublish_run`` + ``DashboardPublisher.delete_publication``：
  删除该 run 的快照、修复 current 指针；
- ``GET /api/deepswe-runs`` 列表附带每条 run 的 ``snapshot_id``。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iqradar.benchmarks import api_eval as api_eval_module
from iqradar.benchmarks.api_eval import ApiEvalBackend
from iqradar.config.schema import BenchmarkConfig
from iqradar.deepswe.runs import DeepSweRun, FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService
from iqradar.publication.service import DashboardPublisher
from iqradar.reporting.repository import FileDashboardRepository

PRICES_YAML = """
prices:
  m:
    currency: USD
    input_usd_per_1m: 1.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 2.0
quota:
  weekly_budget_usd: 20.0
"""


def _dataset(tmp_path: Path, count: int = 3) -> Path:
    dataset = tmp_path / "q.jsonl"
    lines = [
        json.dumps({"task_id": f"t{i}", "prompt": f"p{i}", "reference": "ans"})
        for i in range(count)
    ]
    dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dataset


def _config(tmp_path: Path, dataset: Path) -> BenchmarkConfig:
    return BenchmarkConfig(
        type="api-eval",
        repo_url="https://example.com/dataset",
        local_path=tmp_path / "local",
        tasks_path=dataset,
        default_timeout_sec=3600,
        default_concurrency=1,
        artifact_root=tmp_path / "jobs",
    )


def _backend(tmp_path: Path, dataset: Path) -> ApiEvalBackend:
    return ApiEvalBackend(_config(tmp_path, dataset), name="gpqa-diamond")


def _run(
    run_id: str,
    *,
    status: str = "completed",
    benchmark: str = "gpqa-diamond",
    model_id: str = "m",
    n_tasks: int = 3,
) -> DeepSweRun:
    return DeepSweRun(
        run_id=run_id,
        status=status,
        model_id=model_id,
        n_tasks=n_tasks,
        sample_seed=0,
        created_at=datetime.now(UTC),
        jobs_dir=None,
        benchmark=benchmark,
        effort="high",
    )


def _service(tmp_path: Path, dataset: Path) -> tuple[DeepSweService, ApiEvalBackend]:
    store = FileDeepSweRunStore(tmp_path / "runs")
    backend = _backend(tmp_path, dataset)
    service = DeepSweService(runs=store, backends={"gpqa-diamond": backend})
    return service, backend


def _publisher(tmp_path: Path) -> tuple[DashboardPublisher, FileDashboardRepository]:
    prices_path = tmp_path / "prices.yaml"
    prices_path.write_text(PRICES_YAML, encoding="utf-8")
    repo = FileDashboardRepository(tmp_path / "reporting")
    return DashboardPublisher(repo, prices_path), repo


def _run_main_round(
    backend: ApiEvalBackend, tmp_path: Path, monkeypatch, *, run_id: str = "r1"
) -> None:
    """跑一轮主评测：t0 通过、t1 网关超时、t2 模型答错。"""
    calls: list[str] = []

    def fake_gateway(base_url, api_key, model, prompt, **kwargs):
        calls.append(prompt)
        if prompt == "p1":
            return (None, {}, "TimeoutError:read timed out")
        if prompt == "p2":
            return ("wrong", {"input_tokens": 1, "output_tokens": 1}, None)
        return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

    monkeypatch.setattr(api_eval_module, "gateway_complete", fake_gateway)
    backend.run(
        run_id=run_id,
        model_name="m",
        n_tasks=3,
        sample_seed=0,
        log_path=tmp_path / "run.log",
    )


def _read_statuses(backend: ApiEvalBackend, run_id: str) -> dict[str, str]:
    text = (backend.jobs_root / run_id / "results.jsonl").read_text(encoding="utf-8")
    return {
        json.loads(line)["task_id"]: json.loads(line)["status"]
        for line in text.splitlines()
        if line.strip()
    }


# --- retry_run_gateway_failures -------------------------------------------


def test_retry_run_gateway_failures_recovers_and_republishes(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = _dataset(tmp_path)
    service, backend = _service(tmp_path, dataset)
    publisher, repo = _publisher(tmp_path)

    store = service._runs  # noqa: SLF001 — test-only access
    store.save(_run("r1"))
    _run_main_round(backend, tmp_path, monkeypatch)
    assert _read_statuses(backend, "r1") == {"t0": "passed", "t1": "runner_error", "t2": "failed"}
    assert service.retryable_infrastructure_failure_count(service.get("r1")) == 1

    # 先发布一次（让大盘持有 r1 的快照）
    publisher.publish_records("r1", service.records("r1"))
    assert publisher.publication_for_job("r1") is not None

    # 重测：t1 网关失败题应恢复，t2 答错不动；已发布 → 自动重发布
    calls: list[str] = []

    def fake_gateway(base_url, api_key, model, prompt, **kwargs):
        calls.append(prompt)
        return ("ans", {"input_tokens": 2, "output_tokens": 2}, None)

    monkeypatch.setattr(api_eval_module, "gateway_complete", fake_gateway)
    service.retry_run_gateway_failures("r1", model_name="m", publisher=publisher)

    # 等待后台线程结束：run 状态回到 completed
    deadline = time.time() + 30
    while service.get("r1").status == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert service.get("r1").status == "completed"

    statuses = _read_statuses(backend, "r1")
    assert statuses == {"t0": "passed", "t1": "passed", "t2": "failed"}
    # t1 恰好被重测一次；t0/t2 不重测
    assert calls.count("p1") == 1
    assert calls.count("p0") == 0
    assert calls.count("p2") == 0
    # 大盘仍持有 r1 的快照（自动重发布）
    assert publisher.publication_for_job("r1") is not None
    assert service.retryable_infrastructure_failure_count(service.get("r1")) == 0


def test_retry_run_gateway_failures_rejects_active_run(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = _dataset(tmp_path)
    service, _backend = _service(tmp_path, dataset)
    service._runs.save(_run("r1", status="running"))  # noqa: SLF001
    # 占住 active 名额，避免被「另一个 run 在跑」挡掉
    event = threading.Event()
    service._cancel_events["r1"] = event  # noqa: SLF001
    with pytest.raises(ValueError):
        service.retry_run_gateway_failures("r1", model_name="m")


def test_retry_run_gateway_failures_rejects_non_api_eval(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    service, _backend = _service(tmp_path, dataset)
    # 用 deep-swe（docker）benchmark 的 run：应拒绝
    service._runs.save(_run("r1", benchmark="deep-swe"))  # noqa: SLF001
    with pytest.raises(ValueError):
        service.retry_run_gateway_failures("r1", model_name="m")


# --- unpublish_run --------------------------------------------------------


def test_unpublish_run_deletes_snapshot(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    service, backend = _service(tmp_path, dataset)
    publisher, repo = _publisher(tmp_path)
    service._runs.save(_run("r1"))  # noqa: SLF001

    # 手写一条 record 让 publish_records 有数据
    from iqradar.publication.service import merge_records  # noqa: F401

    backend_run_dir = backend.jobs_root / "r1"
    backend_run_dir.mkdir(parents=True, exist_ok=True)
    (backend_run_dir / "results.jsonl").write_text(
        json.dumps(
            {
                "task_id": "t0",
                "status": "passed",
                "model_id": "m",
                "effort": "high",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    publisher.publish_records("r1", service.records("r1"))
    assert publisher.publication_for_job("r1") is not None

    result = service.unpublish_run("r1", publisher=publisher)
    assert result is not None
    assert publisher.publication_for_job("r1") is None


def test_unpublish_run_returns_none_when_not_published(tmp_path: Path) -> None:
    dataset = _dataset(tmp_path)
    service, _backend = _service(tmp_path, dataset)
    publisher, _repo = _publisher(tmp_path)
    service._runs.save(_run("r1"))  # noqa: SLF001
    assert service.unpublish_run("r1", publisher=publisher) is None
