from __future__ import annotations

from pathlib import Path

from iqradar.deepswe.runs import FileDeepSweRunStore


def test_run_store_round_trips_state(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(
        model_id="model-a",
        n_tasks=10,
        sample_seed=0,
        base_url="https://models.example/v1",
    )

    assert run.status == "queued"
    loaded = store.get(run.run_id)
    assert loaded is not None
    assert loaded.run_id == run.run_id
    assert loaded.model_id == "model-a"
    assert loaded.n_tasks == 10
    assert loaded.sample_seed == 0

    updated = loaded.updated(status="running")
    store.save(updated)
    assert store.get(run.run_id).status == "running"  # type: ignore[union-attr]


def test_run_store_latest_and_active(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    first = store.submit(model_id="model-a", n_tasks=5, sample_seed=0)
    second = store.submit(model_id="model-b", n_tasks=5, sample_seed=1)

    assert store.latest().run_id == second.run_id
    assert store.active().run_id == second.run_id

    store.save(second.updated(status="completed"))
    assert store.active().run_id == first.run_id
    store.save(first.updated(status="failed"))
    assert store.active() is None


def test_run_store_active_runs_lists_all_queued_and_running(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    first = store.submit(model_id="model-a", n_tasks=5, sample_seed=0)
    second = store.submit(model_id="model-b", n_tasks=5, sample_seed=1)
    store.save(first.updated(status="running"))

    active = store.active_runs()
    assert {r.run_id for r in active} == {first.run_id, second.run_id}
    # oldest first (sorted by created_at)
    assert active[0].run_id == first.run_id

    store.save(second.updated(status="completed"))
    assert [r.run_id for r in store.active_runs()] == [first.run_id]
    store.save(first.updated(status="failed"))
    assert store.active_runs() == []


def test_run_store_records_and_log_paths_are_scoped(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(model_id="model-a", n_tasks=5, sample_seed=0)

    assert store.records_path(run.run_id).parent == tmp_path / "runs" / run.run_id
    assert store.log_path(run.run_id).parent == tmp_path / "runs" / run.run_id


def test_run_store_delete_removes_run_directory(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(model_id="model-a", n_tasks=5, sample_seed=0)
    # 写入 records / 日志等产物，确认 rmtree 一并清掉。
    (store.records_path(run.run_id)).parent.mkdir(parents=True, exist_ok=True)
    store.records_path(run.run_id).write_text("{}", encoding="utf-8")

    assert store.delete(run.run_id) is True
    assert store.get(run.run_id) is None
    assert not (tmp_path / "runs" / run.run_id).exists()


def test_run_store_delete_is_false_for_unknown_run(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")

    assert store.delete("does-not-exist") is False


def test_run_store_delete_rejects_path_traversal(tmp_path: Path) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")

    # 形如 ../escape 的 run id 不得越出 runs 根目录。
    import pytest

    with pytest.raises(ValueError):
        store.delete("../escape")
