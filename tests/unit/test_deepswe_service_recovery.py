from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import DeepSweRun, FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


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


def _service_with_run(
    tmp_path: Path,
    run_id: str,
    *,
    created_seconds_ago: int = 120,
) -> tuple[DeepSweService, FileDeepSweRunStore, object]:
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = DeepSweRun(
        run_id=run_id,
        status="running",
        model_id="model-a",
        n_tasks=1,
        sample_seed=0,
        created_at=datetime.now(UTC) - timedelta(seconds=created_seconds_ago),
        jobs_dir=str(tmp_path / "jobs" / run_id),
    )
    store.save(run)
    service = DeepSweService(runs=store, config=_config(tmp_path))
    return service, store, run


def test_get_marks_completed_when_job_result_appears(tmp_path: Path) -> None:
    service, _store, _run = _service_with_run(tmp_path, "run-a")
    job_result = tmp_path / "jobs" / "run-a" / "result.json"
    job_result.parent.mkdir(parents=True)
    job_result.write_text(
        '{"finished_at": "2026-08-14T23:40:00"}', encoding="utf-8"
    )

    result = service.get("run-a")

    assert result is not None
    assert result.status == "completed"
    assert result.completed_at is not None


def test_get_does_not_complete_on_progress_result_file(
    tmp_path: Path, monkeypatch
) -> None:
    # Pier writes result.json early as a progress file (finished_at null).
    service, _store, _run = _service_with_run(tmp_path, "run-a2")
    job_result = tmp_path / "jobs" / "run-a2" / "result.json"
    job_result.parent.mkdir(parents=True)
    job_result.write_text('{"finished_at": null, "n_total_trials": 2}', encoding="utf-8")
    monkeypatch.setattr(service.backends["deep-swe"], "runner_alive", lambda run_id: True)

    assert service.get("run-a2").status == "running"


def test_get_marks_failed_when_pier_gone_and_no_result(
    tmp_path: Path, monkeypatch
) -> None:
    service, _store, _run = _service_with_run(tmp_path, "run-b")
    monkeypatch.setattr(service.backends["deep-swe"], "runner_alive", lambda run_id: False)

    result = service.get("run-b")

    assert result is not None
    assert result.status == "failed"
    assert "interrupted" in (result.error or "")


def test_get_leaves_run_running_when_pier_alive(
    tmp_path: Path, monkeypatch
) -> None:
    service, _store, _run = _service_with_run(tmp_path, "run-c")
    monkeypatch.setattr(service.backends["deep-swe"], "runner_alive", lambda run_id: True)

    assert service.get("run-c") is not None
    assert service.get("run-c").status == "running"


def test_recent_run_is_not_marked_failed(tmp_path: Path, monkeypatch) -> None:
    service, _store, _run = _service_with_run(tmp_path, "run-d", created_seconds_ago=5)
    monkeypatch.setattr(service.backends["deep-swe"], "runner_alive", lambda run_id: False)

    assert service.get("run-d").status == "running"


def test_submit_reconciles_stale_runs_before_active_check(
    tmp_path: Path, monkeypatch
) -> None:
    service, store, _run = _service_with_run(tmp_path, "run-e")
    monkeypatch.setattr(service.backends["deep-swe"], "runner_alive", lambda run_id: False)

    service.submit(
        model_id="model-a",
        model_name="openai/gateway/deepseek-v4-flash",
        n_tasks=1,
        sample_seed=0,
        base_url="http://gateway/v1",
    )

    stale = store.get("run-e")
    assert stale.status == "failed"
    assert "interrupted" in (stale.error or "")


def test_publish_reconciles_before_completed_check(
    tmp_path: Path, monkeypatch
) -> None:
    service, _store, _run = _service_with_run(tmp_path, "run-f")
    job_result = tmp_path / "jobs" / "run-f" / "result.json"
    job_result.parent.mkdir(parents=True)
    job_result.write_text('{"finished_at": "2026-08-14T23:40:00"}', encoding="utf-8")
    fake_publisher = type(
        "FakePublisher",
        (),
        {"publish_records": lambda self, run_id, records, *, merge_with_current=True: f"published:{run_id}"},
    )()

    # job result is finalized but no trial dirs -> no records, so expect the
    # "no records" error, proving the run passed the completed gate.
    try:
        service.publish("run-f", publisher=fake_publisher)
    except ValueError as error:
        assert "no records" in str(error)
    else:
        raise AssertionError("expected ValueError for empty records")
