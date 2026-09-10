from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

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


def _completed_run(service: DeepSweService) -> str:
    runs = service._runs
    run = runs.submit(model_id="model-a", n_tasks=1, sample_seed=0)
    from datetime import UTC, datetime

    runs.save(run.updated(status="completed", completed_at=datetime.now(UTC)))
    return run.run_id


def test_batch_completion_does_not_publish(tmp_path: Path, monkeypatch) -> None:
    """Completing a batch must not publish: snapshot_id stays null until the
    user publishes selected records from the test page's 评测记录 list.

    Regression guard for the old auto-publish behaviour, which published at
    batch completion and left the dashboard holding whatever the worker
    happened to merge.
    """
    service = _service(tmp_path)
    monkeypatch.setattr(DeepSweService, "BATCH_POLL_SEC", 0.05)

    def fake(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        return 0, ""

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake)

    state = service.submit_batch(
        model_ids=["model-a"],
        model_names=["openai/model-a"],
        n_tasks=1,
        sample_seed=0,
    )
    batch_id = state["batch_id"]

    final = _wait_batch_status(service, batch_id, "completed")
    assert final is not None
    assert final["status"] == "completed"
    # No auto-publish: the dashboard is untouched until a manual publish.
    assert final["snapshot_id"] is None
    assert final["models"][0]["status"] == "completed"


def test_resume_completed_batch_is_noop(tmp_path: Path) -> None:
    """A fully-completed batch has nothing to resume: models are done and
    batches no longer auto-publish, so there is no publication to recover.
    """
    service = _service(tmp_path)
    run_id = _completed_run(service)
    batch_id = "already-done"
    service._save_batch(
        {
            "batch_id": batch_id,
            "status": "completed",
            "n_tasks": 1,
            "sample_seed": 0,
            "created_at": "2026-08-29T05:56:04+00:00",
            "completed_at": "2026-08-30T12:31:29+00:00",
            "current_model": "model-a",
            "error": None,
            # snapshot_id null is now the normal completed state, not a
            # recoverable publish failure.
            "snapshot_id": None,
            "benchmark": "deep-swe",
            "effort": "high",
            "models": [
                {
                    "model_id": "model-a",
                    "model_name": "openai/model-a",
                    "status": "completed",
                    "run_id": run_id,
                }
            ],
        }
    )

    assert service.resume_batch(batch_id) is None


def test_publish_runs_merges_selected_runs_into_one_snapshot(tmp_path: Path) -> None:
    """publish_runs publishes several completed runs as ONE accumulated
    snapshot and writes a publication marker per run, so the 评测记录 list
    shows every selected run as published and a per-run unpublish resolves to
    the shared snapshot.
    """
    service = _service(tmp_path)
    run_a = _completed_run(service)
    run_b = _completed_run(service)

    calls: list[tuple[str, list, bool]] = []

    class FakePublisher:
        def publish_runs(self, run_ids, records, *, merge_with_current=True):  # noqa: ANN001
            calls.append((tuple(run_ids), list(records), merge_with_current))
            return SimpleNamespace(snapshot_id="snap-multi", source_job_id=run_ids[0])

    # service.records is imported per run; stub it so publish_runs has data.
    service.records = lambda run_id: [SimpleNamespace(run_id=run_id)]  # type: ignore[method-assign]
    publication = service.publish_runs([run_a, run_b], publisher=FakePublisher())

    assert publication.snapshot_id == "snap-multi"
    assert calls == [((run_a, run_b), [SimpleNamespace(run_id=run_a), SimpleNamespace(run_id=run_b)], True)]
