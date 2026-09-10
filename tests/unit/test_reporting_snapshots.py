"""Dashboard snapshot deletion and source-existence annotation.

Deleting a test-page record leaves its published snapshot behind (snapshots are
immutable), so the dashboard grew orphaned entries. These tests cover the
repair machinery: ``DELETE /api/snapshots/<id>`` must remove the snapshot,
repoint ``current.json`` at the newest remaining snapshot (or drop it when none
are left), and clean the ``publications/<source_job_id>.json`` mapping only
when it still points at the deleted snapshot. ``GET /api/snapshots`` must
annotate each entry with whether the source run/batch still exists."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from iqradar.api.app import _deepswe_source_exists
from iqradar.reporting.api import create_reporting_blueprint
from iqradar.reporting.repository import FileDashboardRepository
from iqradar.reporting.service import DashboardService


def _make_snapshot(root: Path, snapshot_id: str, source_job_id: str, published_at: str) -> None:
    snapshot_root = root / "snapshots" / snapshot_id
    snapshot_root.mkdir(parents=True)
    (snapshot_root / "summary.json").write_text(json.dumps({"summaries": []}))
    (snapshot_root / "runs.jsonl").write_text("")
    (snapshot_root / "manifest.json").write_text(
        json.dumps({"source_job_id": source_job_id, "published_at": published_at})
    )


@pytest.fixture()
def reporting_root(tmp_path: Path) -> Path:
    root = tmp_path / "reporting"
    root.mkdir()
    _make_snapshot(root, "snap-old", "run-old", "2026-09-01T00:00:00+00:00")
    _make_snapshot(root, "snap-new", "multi-abcd1234", "2026-09-02T00:00:00+00:00")
    (root / "current.json").write_text(json.dumps({"snapshot_id": "snap-new"}))
    publications = root / "publications"
    publications.mkdir()
    (publications / "multi-abcd1234.json").write_text(json.dumps({"snapshot_id": "snap-new"}))
    # A mapping that points at a different (re-published) snapshot must survive.
    (publications / "run-old.json").write_text(json.dumps({"snapshot_id": "snap-republished"}))
    return root


def test_list_snapshots_annotates_source_existence(reporting_root: Path) -> None:
    service = DashboardService(
        FileDashboardRepository(reporting_root),
        source_exists=lambda ids: ids == ["multi-abcd1234"],
    )
    entries = {e["snapshot_id"]: e["source_exists"] for e in service.list_snapshots()}
    assert entries == {"snap-old": False, "snap-new": True}


def test_list_snapshots_without_resolver_omits_annotation(reporting_root: Path) -> None:
    service = DashboardService(FileDashboardRepository(reporting_root))
    for entry in service.list_snapshots():
        assert "source_exists" not in entry


def test_delete_non_current_snapshot_keeps_pointer(reporting_root: Path) -> None:
    repo = FileDashboardRepository(reporting_root)
    result = repo.delete_snapshot("snap-old")
    assert result is not None
    assert result["snapshot_id"] == "snap-old"
    assert result["current_snapshot_id"] == "snap-new"
    assert not (reporting_root / "snapshots" / "snap-old").exists()
    # The mapping pointed at another snapshot, so it must be preserved.
    assert (reporting_root / "publications" / "run-old.json").is_file()


def test_delete_current_snapshot_repoints_and_cleans_mapping(reporting_root: Path) -> None:
    repo = FileDashboardRepository(reporting_root)
    result = repo.delete_snapshot("snap-new")
    assert result is not None
    assert result["source_job_id"] == "multi-abcd1234"
    assert result["current_snapshot_id"] == "snap-old"
    assert json.loads((reporting_root / "current.json").read_text())["snapshot_id"] == "snap-old"
    assert not (reporting_root / "publications" / "multi-abcd1234.json").exists()


def test_delete_last_snapshot_removes_current_pointer(reporting_root: Path) -> None:
    repo = FileDashboardRepository(reporting_root)
    repo.delete_snapshot("snap-new")
    result = repo.delete_snapshot("snap-old")
    assert result is not None
    assert result["current_snapshot_id"] is None
    assert not (reporting_root / "current.json").exists()
    assert repo.delete_snapshot("snap-old") is None


def test_delete_snapshot_rejects_invalid_ids(reporting_root: Path) -> None:
    repo = FileDashboardRepository(reporting_root)
    with pytest.raises(ValueError):
        repo.delete_snapshot("../escape")


def test_deepswe_source_exists_matches_all_three_id_shapes(tmp_path: Path) -> None:
    deepswe_root = tmp_path / "deepswe"
    runs_root = deepswe_root / "runs"
    run_id = "a" * 32
    (runs_root / run_id).mkdir(parents=True)
    (runs_root / run_id / "state.json").write_text("{}")
    (deepswe_root / "batches").mkdir()
    (deepswe_root / "batches" / ("b" * 8 + "c" * 24 + ".json")).write_text("{}")
    (deepswe_root / "multi-batches").mkdir()
    (deepswe_root / "multi-batches" / ("d" * 32 + ".json")).write_text("{}")
    assert _deepswe_source_exists(runs_root, [run_id]) is True
    assert _deepswe_source_exists(runs_root, ["e" * 32]) is False
    assert _deepswe_source_exists(runs_root, ["batch-" + "b" * 8]) is True
    assert _deepswe_source_exists(runs_root, ["batch-" + "f" * 8]) is False
    assert _deepswe_source_exists(runs_root, ["multi-" + "d" * 8]) is True
    assert _deepswe_source_exists(runs_root, ["weird-id"]) is False


def test_deepswe_source_exists_requires_every_merged_source(tmp_path: Path) -> None:
    """累积快照带多个来源：全部存在才算源未删，任一被删即提示。"""
    deepswe_root = tmp_path / "deepswe"
    runs_root = deepswe_root / "runs"
    run_id = "a" * 32
    (runs_root / run_id).mkdir(parents=True)
    (runs_root / run_id / "state.json").write_text("{}")
    (deepswe_root / "batches").mkdir()
    (deepswe_root / "batches" / ("b" * 8 + "c" * 24 + ".json")).write_text("{}")
    both = [run_id, "batch-" + "b" * 8]
    assert _deepswe_source_exists(runs_root, both) is True
    assert _deepswe_source_exists(runs_root, [*both, "e" * 32]) is False
    # 缺省（无来源）视为存在，与前端「缺省视为存在」的旧后端兼容约定一致。
    assert _deepswe_source_exists(runs_root, []) is True


def test_snapshot_routes_end_to_end(reporting_root: Path) -> None:
    service = DashboardService(
        FileDashboardRepository(reporting_root),
        source_exists=lambda ids: ids == ["multi-abcd1234"],
    )
    app = Flask(__name__)
    app.register_blueprint(create_reporting_blueprint(service))
    client = app.test_client()

    listed = client.get("/api/snapshots").get_json()["data"]
    assert {e["snapshot_id"]: e["source_exists"] for e in listed} == {
        "snap-old": False,
        "snap-new": True,
    }
    assert client.delete("/api/snapshots/missing").status_code == 404
    assert client.delete("/api/snapshots/bad~id").status_code == 400

    response = client.delete("/api/snapshots/snap-new")
    assert response.status_code == 200
    assert response.get_json()["data"]["current_snapshot_id"] == "snap-old"
    remaining = client.get("/api/snapshots").get_json()["data"]
    assert [e["snapshot_id"] for e in remaining] == ["snap-old"]
