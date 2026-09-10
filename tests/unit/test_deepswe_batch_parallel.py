from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


@pytest.fixture(autouse=True)
def _inference_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-test")


def _config(tmp_path: Path) -> DeepSweConfig:
    (tmp_path / "tasks").mkdir(exist_ok=True)  # preflight expects a real dataset
    return DeepSweConfig(
        local_path=tmp_path / "deep-swe",
        tasks_path=tmp_path / "tasks",
        env_file=tmp_path / "deep-swe" / ".env",
        jobs_root=tmp_path / "jobs",
        default_timeout_sec=7200,
        n_concurrent=1,
    )


def _service(tmp_path: Path) -> DeepSweService:
    return DeepSweService(runs=FileDeepSweRunStore(tmp_path / "runs"), config=_config(tmp_path))


def _wait_batch_status(service: DeepSweService, batch_id: str, status: str) -> dict | None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = service.get_batch(batch_id)
        if state is not None and state["status"] == status:
            return state
        time.sleep(0.02)
    return service.get_batch(batch_id)


def test_parallel_batch_starts_models_concurrently(tmp_path: Path, monkeypatch) -> None:
    """max_concurrent=2 时两个模型的 run 要同时启动，而不是等第一个跑完。

    用一个共享 gate 把所有 run_pier_sync 阻塞住：如果调度是串行的，第二个
    模型永远不会在 gate 打开前被调用；只有并行调度才会在 gate 仍关闭时
    就让两个模型都进入 running。批次完成不再自动发布（snapshot_id 保持空）。
    """
    service = _service(tmp_path)
    monkeypatch.setattr(DeepSweService, "BATCH_POLL_SEC", 0.05)
    gate = threading.Event()
    lock = threading.Lock()
    started: list[str] = []

    def fake(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        with lock:
            started.append(model)
        gate.wait(timeout=10)
        return 0, ""

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake)

    state = service.submit_batch(
        model_ids=["model-a", "model-b"],
        model_names=["openai/model-a", "openai/model-b"],
        n_tasks=1,
        sample_seed=0,
        max_concurrent=2,
    )
    batch_id = state["batch_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with lock:
            if len(started) == 2:
                break
        time.sleep(0.02)
    with lock:
        assert len(started) == 2, "both models must start while the first is still blocked"

    gate.set()
    final = _wait_batch_status(service, batch_id, "completed")
    assert final is not None
    assert final["status"] == "completed"
    assert final["max_concurrent"] == 2
    assert final["models"][0]["status"] == "completed"
    assert final["models"][1]["status"] == "completed"
    # No auto-publish on completion.
    assert final["snapshot_id"] is None


def test_parallel_batch_cancel_stops_all_in_flight_runs(tmp_path: Path, monkeypatch) -> None:
    """并行批次中断时，所有 in-flight 的 run 都要被取消且不发布。"""
    service = _service(tmp_path)
    monkeypatch.setattr(DeepSweService, "BATCH_POLL_SEC", 0.05)
    started = threading.Event()
    lock = threading.Lock()
    count = 0

    def fake(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        nonlocal count
        with lock:
            count += 1
            if count >= 2:
                started.set()
        if cancel_event is not None:
            cancel_event.wait(timeout=10)
        return 1, "cancelled by user"

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake)

    state = service.submit_batch(
        model_ids=["model-a", "model-b"],
        model_names=["openai/model-a", "openai/model-b"],
        n_tasks=1,
        sample_seed=0,
        max_concurrent=2,
    )
    batch_id = state["batch_id"]

    assert started.wait(5)
    assert service.cancel_batch(batch_id) is True

    final = _wait_batch_status(service, batch_id, "cancelled")
    assert final is not None
    assert final["status"] == "cancelled"
    # 两个模型都进入过 running 且被取消：run 被取消后 reconcile 会同步为 failed。
    assert final["models"][0]["run_id"] is not None
    assert final["models"][1]["run_id"] is not None


def test_submit_batch_rejects_out_of_range_max_concurrent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError):
        service.submit_batch(
            model_ids=["model-a"],
            model_names=["openai/model-a"],
            n_tasks=1,
            sample_seed=0,
            max_concurrent=0,
        )
    with pytest.raises(ValueError):
        service.submit_batch(
            model_ids=["model-a"],
            model_names=["openai/model-a"],
            n_tasks=1,
            sample_seed=0,
            max_concurrent=17,
        )
