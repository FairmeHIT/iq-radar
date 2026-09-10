"""跨快照累积发布（radar-v3）：publish_records 与当前快照合并而非覆盖。

回归背景：radar-v2 时代每次发布都会用本次 records 整体替换 current.json，
跨批次/跨基准的模型无法同时出现在大盘上。radar-v3 起 records 按 ``run_id``
（``<benchmark>__<task>__<model>__<effort>``，确定性键）合并去重、新
``created_at`` 获胜，因此重跑同一任务会替换旧结果，其余数据持续累积。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iqradar.publication.service import DashboardPublisher, merge_records
from iqradar.reporting.repository import FileDashboardRepository
from iqradar.reporting.service import DashboardService
from iqradar.schemas.run_record import RunRecord

PRICES_YAML = """
prices:
  model-a:
    currency: USD
    input_usd_per_1m: 1.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 2.0
  model-b:
    currency: USD
    input_usd_per_1m: 1.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 2.0
quota:
  weekly_budget_usd: 20.0
"""


def _record(
    run_id: str,
    *,
    model: str,
    task_id: str,
    created_at: datetime,
    wall_time_sec: float = 60.0,
    status: str = "passed",
    benchmark: str = "deep-swe",
) -> RunRecord:
    return RunRecord.model_validate(
        {
            "run_id": run_id,
            "benchmark": {
                "name": benchmark,
                "version": "local",
                "task_id": task_id,
                "repo": "unknown",
                "language": "python",
                "task_path": f"tasks/{task_id}",
            },
            "model": {
                "provider": "openai-compatible",
                "base_url_hash": "sha256:abc",
                "name": model,
                "effort_requested": "high",
                "effort_effective": False,
            },
            "result": {
                "status": status,
                "verifier_passed": status == "passed",
                "exit_code": 0,
                "error_type": None,
                "error_message_redacted": None,
            },
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_input_tokens": 0,
                "agent_steps": 1,
                "wall_time_sec": wall_time_sec,
                "usage_estimated": False,
            },
            "cost": {
                "currency": "USD",
                "input_cost": 0.0,
                "cached_input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            },
            "artifacts": {"patch_path": None, "log_path": None, "verifier_path": None},
            "created_at": created_at,
        }
    )


@pytest.fixture()
def publisher(tmp_path: Path) -> tuple[DashboardPublisher, FileDashboardRepository]:
    prices_path = tmp_path / "prices.yaml"
    prices_path.write_text(PRICES_YAML, encoding="utf-8")
    repo = FileDashboardRepository(tmp_path / "reporting")
    return DashboardPublisher(repo, prices_path), repo


def _run_ids(repo: FileDashboardRepository) -> list[str]:
    return [record.run_id for record in repo.load_runs()]


def test_second_publish_accumulates_previous_snapshot(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    pub.publish_records(
        "run-a",
        [_record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base)],
    )
    pub.publish_records(
        "run-b",
        [_record("deep-swe__t2__model-b__high", model="model-b", task_id="t2", created_at=base + timedelta(hours=1))],
    )

    summary = repo.load_summary()
    assert summary is not None
    models = {item["model"] for item in summary["summaries"]}
    assert models == {"model-a", "model-b"}
    assert summary["summaries"][0]["tasks_total"] + summary["summaries"][1]["tasks_total"] == 2
    # 最近运行在前：新发布的 run-b 记录排最前。
    assert _run_ids(repo) == ["deep-swe__t2__model-b__high", "deep-swe__t1__model-a__high"]

    current_id = repo.current_snapshot_id()
    assert current_id is not None and current_id.startswith("run-b-")
    manifest = repo.snapshot_manifest(current_id)
    assert manifest is not None
    assert manifest["source_job_id"] == "run-b"
    assert manifest["source_job_ids"] == ["run-a", "run-b"]
    # 列表接口也带出全部来源。
    listed = {s["snapshot_id"]: s for s in repo.list_snapshots()}
    assert listed[current_id]["source_job_ids"] == ["run-a", "run-b"]


def test_republish_same_task_replaces_older_record(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    same_run_id = "deep-swe__t1__model-a__high"
    pub.publish_records(
        "run-a",
        [_record(same_run_id, model="model-a", task_id="t1", created_at=base, wall_time_sec=60.0)],
    )
    # 重跑同一任务：run_id 相同、created_at 更新 → 替换旧记录而不是重复累积。
    pub.publish_records(
        "run-a2",
        [_record(same_run_id, model="model-a", task_id="t1", created_at=base + timedelta(hours=2), wall_time_sec=120.0)],
    )

    runs = repo.load_runs()
    assert len(runs) == 1
    assert runs[0].usage.wall_time_sec == 120.0
    summary = repo.load_summary()
    assert summary is not None
    assert summary["summaries"][0]["tasks_total"] == 1
    current_id = repo.current_snapshot_id()
    manifest = repo.snapshot_manifest(str(current_id))
    assert manifest is not None
    assert manifest["source_job_ids"] == ["run-a", "run-a2"]


def test_replace_mode_publishes_only_new_records(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    pub.publish_records(
        "run-a",
        [_record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base)],
    )
    pub.publish_records(
        "run-b",
        [_record("deep-swe__t2__model-b__high", model="model-b", task_id="t2", created_at=base)],
        merge_with_current=False,
    )

    summary = repo.load_summary()
    assert summary is not None
    assert {item["model"] for item in summary["summaries"]} == {"model-b"}
    assert _run_ids(repo) == ["deep-swe__t2__model-b__high"]


def test_radar_series_carry_benchmark_for_multi_benchmark_snapshots(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    pub.publish_records(
        "run-a",
        [
            _record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base),
            _record("api-eval-demo__q1__model-a__high", model="model-a", task_id="q1", created_at=base, benchmark="api-eval-demo"),
        ],
    )
    service = DashboardService(repo)
    data = service.dashboard()
    assert data is not None
    benchmarks = {series["benchmark"] for series in data["iq_radar"]}
    assert benchmarks == {"deep-swe", "api-eval-demo"}


def test_merge_records_handles_naive_and_aware_datetimes() -> None:
    # aware 00:00Z vs naive 12:00（按 UTC 解释）→ naive 记录更新，排最前。
    aware = datetime(2026, 9, 1, tzinfo=UTC)
    naive = datetime(2026, 9, 1, 12, 0)
    older = _record("r-old", model="m", task_id="t", created_at=aware)
    newer = _record("r-new", model="m", task_id="t", created_at=naive)
    merged = merge_records([older], [newer])
    assert [r.run_id for r in merged] == ["r-new", "r-old"]


def test_publish_runs_creates_one_snapshot_with_per_run_markers(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    """发布选中多条 run：合成一个累积快照，每条 run 都写 publication 标记，
    评测记录列表据此把每条 run 显示为已发布。"""
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    records = [
        _record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base),
        _record("deep-swe__t2__model-b__high", model="model-b", task_id="t2", created_at=base + timedelta(hours=1)),
    ]
    publication = pub.publish_runs(["run-a", "run-b"], records)

    # Exactly one snapshot, and it is the current one.
    listed = repo.list_snapshots()
    assert len(listed) == 1
    assert listed[0]["snapshot_id"] == publication.snapshot_id
    assert repo.current_snapshot_id() == publication.snapshot_id
    # Manifest carries both runs as sources (for the source-deleted annotation).
    manifest = repo.snapshot_manifest(publication.snapshot_id)
    assert manifest is not None
    assert "run-a" in manifest["source_job_ids"]
    assert "run-b" in manifest["source_job_ids"]
    # A publication marker per run: the records list shows each as published.
    assert pub.publication_for_job("run-a").snapshot_id == publication.snapshot_id
    assert pub.publication_for_job("run-b").snapshot_id == publication.snapshot_id


def test_publish_runs_unpublish_cleans_all_shared_markers(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    """回撤一条已发布 run 删除其所有来源快照后，同快照的其余 per-run 标记也要清掉，
    否则评测记录会显示已发布但快照已不存在。"""
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    records = [
        _record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base),
        _record("deep-swe__t2__model-b__high", model="model-b", task_id="t2", created_at=base),
    ]
    publication = pub.publish_runs(["run-a", "run-b"], records)
    assert pub.publication_for_job("run-a") is not None
    assert pub.publication_for_job("run-b") is not None

    # Unpublish run-a deletes the shared snapshot; both markers must go.
    assert pub.delete_publication("run-a") is not None
    assert pub.publication_for_job("run-a") is None
    assert pub.publication_for_job("run-b") is None
    assert repo.current_snapshot_id() is None


def test_publish_runs_accumulates_with_current_snapshot(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    """先单独发布 run-a，再发布选中 [run-b, run-c]：第二次发布合并进当前快照，
    三条记录同时出现在大盘，且 run-b/run-c 各自标记指向新快照。"""
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    pub.publish_records(
        "run-a",
        [_record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base)],
    )
    second = pub.publish_runs(
        ["run-b", "run-c"],
        [
            _record("deep-swe__t2__model-b__high", model="model-b", task_id="t2", created_at=base + timedelta(hours=1)),
            _record("deep-swe__t3__model-c__high", model="model-c", task_id="t3", created_at=base + timedelta(hours=2)),
        ],
    )
    summary = repo.load_summary()
    assert summary is not None
    assert {item["model"] for item in summary["summaries"]} == {"model-a", "model-b", "model-c"}
    assert pub.publication_for_job("run-b").snapshot_id == second.snapshot_id
    assert pub.publication_for_job("run-c").snapshot_id == second.snapshot_id


def test_unpublish_source_removes_later_accumulated_snapshots(
    publisher: tuple[DashboardPublisher, FileDashboardRepository],
) -> None:
    """删除/回撤旧 run 后，后续累积快照也必须移除，避免大盘残留旧数据。"""
    pub, repo = publisher
    base = datetime(2026, 9, 1, tzinfo=UTC)
    pub.publish_records(
        "run-a",
        [_record("deep-swe__t1__model-a__high", model="model-a", task_id="t1", created_at=base)],
    )
    pub.publish_records(
        "run-b",
        [_record("deep-swe__t2__model-b__high", model="model-b", task_id="t2", created_at=base + timedelta(hours=1))],
    )
    assert {record.run_id for record in repo.load_runs()} == {
        "deep-swe__t1__model-a__high",
        "deep-swe__t2__model-b__high",
    }

    result = pub.delete_publication("run-a")

    assert result is not None
    assert repo.current_snapshot_id() is None
    assert repo.load_snapshot() is None
    assert pub.publication_for_job("run-a") is None
    assert pub.publication_for_job("run-b") is None
