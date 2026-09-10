from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from iqradar.shared.atomic_files import atomic_write_text

RunStatus = Literal["queued", "running", "completed", "failed"]


@dataclass(frozen=True)
class DeepSweRun:
    run_id: str
    status: RunStatus
    model_id: str
    n_tasks: int
    sample_seed: int
    base_url: str = ""
    created_at: datetime = datetime.min.replace(tzinfo=UTC)
    completed_at: datetime | None = None
    error: str | None = None
    jobs_dir: str | None = None
    benchmark: str = "deep-swe"
    effort: str = "high"
    n_concurrent: int | None = None
    # 网关失败重测配置（仅 api-eval 生效；旧记录缺省 None）
    retry_gateway_failures: bool | None = None
    gateway_retry_rounds: int | None = None

    def updated(self, **changes: object) -> DeepSweRun:
        values = dict(self.__dict__)
        values.update(changes)
        return DeepSweRun(**values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "model_id": self.model_id,
            "n_tasks": self.n_tasks,
            "sample_seed": self.sample_seed,
            "base_url": self.base_url,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "jobs_dir": self.jobs_dir,
            "benchmark": self.benchmark,
            "effort": self.effort,
            "n_concurrent": self.n_concurrent,
            "retry_gateway_failures": self.retry_gateway_failures,
            "gateway_retry_rounds": self.gateway_retry_rounds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeepSweRun:
        return cls(
            run_id=str(data["run_id"]),
            status=str(data.get("status", "queued")),  # type: ignore[arg-type]
            model_id=str(data["model_id"]),
            n_tasks=int(data["n_tasks"]),
            sample_seed=int(data.get("sample_seed", 0)),
            base_url=str(data.get("base_url", "")),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            completed_at=(
                datetime.fromisoformat(str(data["completed_at"]))
                if data.get("completed_at")
                else None
            ),
            error=data.get("error"),
            jobs_dir=data.get("jobs_dir"),
            benchmark=str(data.get("benchmark", "deep-swe")),
            effort=str(data.get("effort", "high")),
            n_concurrent=(
                int(data["n_concurrent"])
                if data.get("n_concurrent") is not None
                else None
            ),
            retry_gateway_failures=(
                bool(data["retry_gateway_failures"])
                if data.get("retry_gateway_failures") is not None
                else None
            ),
            gateway_retry_rounds=(
                int(data["gateway_retry_rounds"])
                if data.get("gateway_retry_rounds") is not None
                else None
            ),
        )


class FileDeepSweRunStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def submit(
        self,
        *,
        model_id: str,
        n_tasks: int,
        sample_seed: int,
        base_url: str = "",
        jobs_dir: Path | None = None,
        benchmark: str = "deep-swe",
        effort: str = "high",
        n_concurrent: int | None = None,
        retry_gateway_failures: bool | None = None,
        gateway_retry_rounds: int | None = None,
    ) -> DeepSweRun:
        run = DeepSweRun(
            run_id=uuid4().hex,
            status="queued",
            model_id=model_id,
            n_tasks=n_tasks,
            sample_seed=sample_seed,
            base_url=base_url,
            created_at=datetime.now(UTC),
            jobs_dir=str(jobs_dir) if jobs_dir else None,
            benchmark=benchmark,
            effort=effort,
            n_concurrent=n_concurrent,
            retry_gateway_failures=retry_gateway_failures,
            gateway_retry_rounds=gateway_retry_rounds,
        )
        self.save(run)
        return run

    def save(self, run: DeepSweRun) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run.run_id):
            raise ValueError("invalid deep-swe run ID")
        run_root = self.root / run.run_id
        run_root.mkdir(parents=True, exist_ok=True)
        run_root.chmod(0o700)
        atomic_write_text(
            run_root / "state.json",
            json.dumps(run.as_dict(), ensure_ascii=True, indent=2) + "\n",
        )

    def get(self, run_id: str) -> DeepSweRun | None:
        path = self.root / run_id / "state.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return DeepSweRun.from_dict(data) if isinstance(data, dict) else None

    def latest(self) -> DeepSweRun | None:
        runs = [
            run
            for run_id in self.root.iterdir()
            if (run := self.get(run_id.name)) is not None
        ]
        return max(runs, key=lambda run: run.created_at) if runs else None

    def all_runs(self) -> list[DeepSweRun]:
        return [
            run
            for run_id in self.root.iterdir()
            if (run := self.get(run_id.name)) is not None
        ]

    def active(self) -> DeepSweRun | None:
        runs = self.active_runs()
        return max(runs, key=lambda run: run.created_at) if runs else None

    def active_runs(self) -> list[DeepSweRun]:
        """All runs still queued or running (oldest first)."""
        runs = [
            run
            for run_id in self.root.iterdir()
            if (run := self.get(run_id.name)) is not None
            and run.status in {"queued", "running"}
        ]
        return sorted(runs, key=lambda run: run.created_at)

    def records_path(self, run_id: str) -> Path:
        return self.root / run_id / "records.jsonl"

    def log_path(self, run_id: str) -> Path:
        return self.root / run_id / "pier.log"

    def delete(self, run_id: str) -> bool:
        """Remove a run's on-disk state and artifacts.

        Returns True when the run directory existed and was removed, False
        when it was already absent. The run id is validated against the same
        safe pattern used by :meth:`save` so a crafted path can never escape
        the runs root. The service layer decides whether a run may be deleted
        (active runs are refused before this is called).
        """
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
            raise ValueError("invalid deep-swe run ID")
        run_root = self.root / run_id
        if not run_root.exists():
            return False
        shutil.rmtree(run_root)
        return True
