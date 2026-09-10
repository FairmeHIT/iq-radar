from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from iqradar.deepswe.runner import DeepSweConfig, _wait_with_cancel
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


def test_wait_with_cancel_terminates_process_on_event(tmp_path: Path) -> None:
    process = subprocess.Popen(["sleep", "60"], start_new_session=True)
    cancel_event = threading.Event()
    started = time.monotonic()

    def cancel() -> None:
        time.sleep(0.5)
        cancel_event.set()

    threading.Thread(target=cancel, daemon=True).start()
    returncode = _wait_with_cancel(process, cancel_event=cancel_event, timeout_sec=30)

    assert process.poll() is not None
    assert time.monotonic() - started < 10
    assert returncode != 0


def test_cancel_returns_true_for_active_run(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    started = threading.Event()
    released = threading.Event()

    def fake_run_pier_sync(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        started.set()
        if cancel_event is not None:
            cancel_event.wait(timeout=10)
        released.set()
        return 1, "cancelled by user"

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake_run_pier_sync)
    run = service.submit(
        model_id="model-a",
        model_name="openai/model-a",
        n_tasks=1,
        sample_seed=0,
        base_url="http://gateway.example/v1",
    )

    assert started.wait(5)
    assert service.cancel(run.run_id) is True
    assert released.wait(5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if service.get(run.run_id).status == "failed":
            break
        time.sleep(0.05)
    assert service.get(run.run_id).status == "failed"


def test_cancel_returns_false_for_unknown_run(tmp_path: Path) -> None:
    service = _service(tmp_path)

    assert service.cancel("does-not-exist") is False


def test_cancel_returns_false_for_finished_run(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(model_id="model-a", n_tasks=1, sample_seed=0)
    store.save(run.updated(status="completed"))
    service = DeepSweService(runs=store, config=_config(tmp_path))

    assert service.cancel(run.run_id) is False
