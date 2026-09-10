from __future__ import annotations

from typing import Any, Callable

from iqradar.reporting.repository import FileDashboardRepository
from iqradar.schemas.run_record import RunRecord


class DashboardService:
    def __init__(
        self,
        repository: FileDashboardRepository,
        *,
        source_exists: Callable[[list[str]], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._source_exists = source_exists

    def summary(self, *, snapshot_id: str | None = None) -> dict[str, Any] | None:
        snapshot = self._repository.load_snapshot(snapshot_id)
        return snapshot.summary if snapshot else None

    def dashboard(self, *, limit: int = 100, snapshot_id: str | None = None) -> dict[str, Any] | None:
        snapshot = self._repository.load_snapshot(snapshot_id)
        if snapshot is None:
            return None
        summaries = snapshot.summary.get("summaries", [])
        return {
            "snapshot_id": snapshot.snapshot_id,
            "summary": snapshot.summary,
            "iq_radar": [_iq_series(item) for item in summaries],
            "quota_radar": [_quota_series(item) for item in summaries],
            "runs": [record.model_dump(mode="json") for record in snapshot.runs[:limit]],
        }

    def list_snapshots(self) -> list[dict[str, Any]]:
        snapshots = self._repository.list_snapshots()
        if self._source_exists is not None:
            for entry in snapshots:
                # 累积快照带全部来源（radar-v3）；单源旧快照退化为单元素
                # 列表。全部来源仍存在于测试页才算"源未删"，任何一个被
                # 清理都提示，因为该来源的数据仍留在快照里。
                ids = entry.get("source_job_ids") or [entry.get("source_job_id")]
                sources = [str(item) for item in ids if item]
                entry["source_exists"] = (
                    bool(self._source_exists(sources)) if sources else True
                )
        return snapshots

    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self._repository.delete_snapshot(snapshot_id)

    def runs(
        self,
        *,
        model: str | None,
        effort: str | None,
        status: str | None,
        limit: int,
    ) -> list[RunRecord]:
        return [
            record
            for record in self._repository.load_runs()
            if (model is None or record.model.name == model)
            and (effort is None or record.model.effort_requested == effort)
            and (status is None or record.result.status == status)
        ][:limit]

    def iq_radar(self) -> list[dict[str, Any]] | None:
        summary = self.summary()
        return [_iq_series(item) for item in summary.get("summaries", [])] if summary else None

    def quota_radar(self) -> list[dict[str, Any]] | None:
        summary = self.summary()
        return [_quota_series(item) for item in summary.get("summaries", [])] if summary else None


def _iq_series(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": summary["model"],
        "effort": summary["effort"],
        # 累积快照可含多个基准：同一模型/强度会有多行 series，前端用
        # benchmark 区分重名。
        "benchmark": summary["benchmark"]["name"],
        "axes": {
            "iq": _clamp(summary["iq"] / 150 * 100),
            "pass_rate": _clamp(summary["pass_rate_percent"]),
            "stability": _clamp((1 - (summary["confidence"]["upper"] - summary["confidence"]["lower"])) * 100),
            "speed": _clamp(summary["tasks_per_hour"]),
            "cost_efficiency": _inverse_score(summary["avg_cost_usd"]),
        },
    }


def _quota_series(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": summary["model"],
        "effort": summary["effort"],
        "benchmark": summary["benchmark"]["name"],
        "axes": {
            "quota_remaining_friendliness": _clamp(100 - summary["quota_percent_per_task"]),
            "tasks_per_week": _clamp(summary["estimated_tasks_per_week"]),
            "passes_per_week": _clamp(summary["estimated_passes_per_week"]),
            "cost_per_pass_efficiency": _inverse_score(summary.get("cost_per_pass_usd")),
            "token_efficiency": _inverse_score(summary["avg_output_tokens"] / 1000),
        },
    }


def _inverse_score(value: float | int | None) -> float:
    return 0.0 if value is None else _clamp(100 / (1 + float(value)))


def _clamp(value: float | int) -> float:
    return round(min(100.0, max(0.0, float(value))), 4)
