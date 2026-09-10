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


def _blocking_pier(started: threading.Event | None = None):
    """A run_pier_sync stub that blocks until its cancel event is set."""

    def stub(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        if started is not None:
            started.set()
        if cancel_event is not None:
            cancel_event.wait(timeout=10)
        return 1, "cancelled by user"

    return stub


def _wait_batch(service: DeepSweService, batch_id: str, *, entry_index: int, status: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = service.get_batch(batch_id)
        if state is not None and state["models"][entry_index]["status"] == status:
            return state
        time.sleep(0.02)
    return service.get_batch(batch_id)


def test_batch_cancel_stops_batch_before_next_model(tmp_path: Path, monkeypatch) -> None:
    """A cancel mid-batch must stop the current run and never start the next model."""
    service = _service(tmp_path)
    monkeypatch.setattr(DeepSweService, "BATCH_POLL_SEC", 0.05)
    started = threading.Event()
    monkeypatch.setattr(
        "iqradar.benchmarks.deep_swe.run_pier_sync",
        _blocking_pier(started),
    )
    state = service.submit_batch(
        model_ids=["model-a", "model-b"],
        model_names=["openai/model-a", "openai/model-b"],
        n_tasks=1,
        sample_seed=0,
    )
    batch_id = state["batch_id"]

    assert started.wait(5)
    assert service.cancel_batch(batch_id) is True

    final = _wait_batch(service, batch_id, entry_index=0, status="failed")
    assert final is not None
    assert final["status"] == "cancelled"
    assert final["cancel_requested"] is True
    # The first run was cancelled (failed); the second model was never started.
    assert final["models"][0]["status"] == "failed"
    assert final["models"][1]["run_id"] is None
    assert final["models"][1]["status"] == "pending"


def test_batch_cancel_does_not_publish_partial_results(tmp_path: Path, monkeypatch) -> None:
    """A batch cancelled after some models completed must leave the dashboard
    untouched. Batches no longer publish on completion at all, so a cancelled
    batch keeps snapshot_id null just like a completed one.
    """
    service = _service(tmp_path)
    monkeypatch.setattr(DeepSweService, "BATCH_POLL_SEC", 0.05)
    second_started = threading.Event()

    def fake(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        if "model-b" in model:
            second_started.set()
            if cancel_event is not None:
                cancel_event.wait(timeout=10)
            return 1, "cancelled by user"
        return 0, ""

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake)
    state = service.submit_batch(
        model_ids=["model-a", "model-b"],
        model_names=["openai/model-a", "openai/model-b"],
        n_tasks=1,
        sample_seed=0,
    )
    batch_id = state["batch_id"]

    assert second_started.wait(5)
    assert service.cancel_batch(batch_id) is True

    final = _wait_batch(service, batch_id, entry_index=1, status="failed")
    assert final is not None
    assert final["status"] == "cancelled"
    # model-a completed, but the batch was cancelled; nothing is published.
    assert final["models"][0]["status"] == "completed"
    assert final["models"][1]["status"] == "failed"
    assert final["snapshot_id"] is None


def test_cancel_batch_is_idempotent_for_already_cancelled_batch(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(DeepSweService, "BATCH_POLL_SEC", 0.05)
    started = threading.Event()
    monkeypatch.setattr(
        "iqradar.benchmarks.deep_swe.run_pier_sync",
        _blocking_pier(started),
    )
    state = service.submit_batch(
        model_ids=["model-a"],
        model_names=["openai/model-a"],
        n_tasks=1,
        sample_seed=0,
    )
    batch_id = state["batch_id"]

    assert started.wait(5)
    assert service.cancel_batch(batch_id) is True
    # A second cancel of an already-cancelled batch is a no-op success, so
    # repeated 中断 clicks never surface "batch is not active".
    assert service.cancel_batch(batch_id) is True
    assert service.get_batch(batch_id)["status"] == "cancelled"


def test_cancel_batch_returns_false_for_finished_batch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    batch_id = "finished"
    service._save_batch(
        {
            "batch_id": batch_id,
            "status": "completed",
            "n_tasks": 1,
            "sample_seed": 0,
            "created_at": "2026-08-15T00:00:00+00:00",
            "completed_at": "2026-08-15T01:00:00+00:00",
            "current_model": "model-a",
            "error": None,
            "snapshot_id": None,
            "models": [
                {
                    "model_id": "model-a",
                    "model_name": "openai/model-a",
                    "status": "completed",
                    "run_id": None,
                }
            ],
        }
    )

    assert service.cancel_batch(batch_id) is False
    assert service.cancel_batch("does-not-exist") is False


def test_cancel_orphaned_run_kills_pier(tmp_path: Path, monkeypatch) -> None:
    """A run whose worker died (server restart) has no cancel event left; the
    cancel must fall back to terminating the orphaned pier process."""
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(model_id="model-a", n_tasks=1, sample_seed=0)
    store.save(run.updated(status="running"))
    service = DeepSweService(runs=store, config=_config(tmp_path))
    killed: list[str] = []
    monkeypatch.setattr(service, "_kill_runner", lambda run: killed.append(run.run_id) or True)

    assert service.cancel(run.run_id) is True
    assert killed == [run.run_id]


def test_reconcile_batch_finalises_orphaned_batch(tmp_path: Path) -> None:
    """A batch left ``running`` by a dead worker is finalised from the run store."""
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(model_id="model-a", n_tasks=1, sample_seed=0)
    store.save(run.updated(status="completed"))
    service = DeepSweService(runs=store, config=_config(tmp_path))
    batch_id = "deadbeef"
    service._save_batch(
        {
            "batch_id": batch_id,
            "status": "running",
            "n_tasks": 1,
            "sample_seed": 0,
            "created_at": "2026-08-15T00:00:00+00:00",
            "completed_at": None,
            "current_model": "model-a",
            "error": None,
            "snapshot_id": None,
            "models": [
                {
                    "model_id": "model-a",
                    "model_name": "openai/model-a",
                    "status": "running",
                    "run_id": run.run_id,
                }
            ],
        }
    )

    reconciled = service.get_batch(batch_id)

    assert reconciled is not None
    assert reconciled["status"] == "completed"
    assert reconciled["models"][0]["status"] == "completed"
    assert reconciled["completed_at"] is not None
