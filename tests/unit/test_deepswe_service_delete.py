from __future__ import annotations

from pathlib import Path

import pytest

from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


def _config(tmp_path: Path) -> DeepSweConfig:
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


def _seed_run(service: DeepSweService, *, model_id: str = "model-a", status: str = "failed"):
    run = service._runs.submit(model_id=model_id, n_tasks=5, sample_seed=0)
    updated = run.updated(status=status)
    service._runs.save(updated)
    return updated


def test_list_runs_returns_newest_first(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _seed_run(service, model_id="model-a")
    second = _seed_run(service, model_id="model-b")

    runs = service.list_runs()

    assert [run.run_id for run in runs] == [second.run_id, first.run_id]


def test_delete_run_removes_non_active_run(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = _seed_run(service, status="failed")

    assert service.delete_run(run.run_id) is True
    assert service.get(run.run_id) is None


def test_delete_run_refuses_unknown_run(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="run not found"):
        service.delete_run("nope")


def test_delete_run_refuses_active_run(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run = service._runs.submit(model_id="model-a", n_tasks=5, sample_seed=0)

    with pytest.raises(ValueError, match="active run cannot be deleted"):
        service.delete_run(run.run_id)
    # 运行中的记录仍在，未被删除。
    assert service.get(run.run_id) is not None


def test_delete_runs_batches_independently(tmp_path: Path) -> None:
    service = _service(tmp_path)
    failed = _seed_run(service, status="failed")
    completed = _seed_run(service, status="completed")
    active = service._runs.submit(model_id="model-a", n_tasks=5, sample_seed=0)

    result = service.delete_runs([failed.run_id, completed.run_id, active.run_id, "nope"])

    assert set(result["deleted"]) == {failed.run_id, completed.run_id}
    skipped_by_id = {item["run_id"]: item["reason"] for item in result["skipped"]}
    assert "active" in skipped_by_id[active.run_id]
    assert "not found" in skipped_by_id["nope"]
    # 已删除的不可再取；运行中的仍在。
    assert service.get(failed.run_id) is None
    assert service.get(completed.run_id) is None
    assert service.get(active.run_id) is not None
