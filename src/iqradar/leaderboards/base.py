from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from iqradar.leaderboards.schemas import LeaderboardEntry, SourceConfig, SourceStatus


class LeaderboardSource(ABC):
    """一个外部榜单数据源。"""

    def __init__(self, config: SourceConfig, cache_root: Path) -> None:
        self.config = config
        self.source_id = config.id
        self._cache_root = cache_root
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def display_name(self) -> str:
        return self.config.id

    @property
    def _cache_dir(self) -> Path:
        return self._cache_root / self.source_id

    @property
    def _cache_path(self) -> Path:
        return self._cache_dir / "data.json"

    def _status_path(self) -> Path:
        return self._cache_dir / "status.json"

    # --- 子类必须实现 ------------------------------------------------

    @abstractmethod
    async def fetch(self) -> list[LeaderboardEntry]:
        """拉取最新数据，返回归一化后的榜单条目。"""
        ...

    # --- 缓存管理 ---------------------------------------------------

    def load_cache(self) -> list[LeaderboardEntry] | None:
        """从本地缓存加载数据，过期返回 None。"""
        path = self._cache_path
        if not path.is_file():
            return None
        try:
            import json

            raw = json.loads(path.read_text(encoding="utf-8"))
            return [LeaderboardEntry.model_validate(item) for item in raw]
        except (OSError, ValueError, KeyError):
            return None

    def save_cache(self, entries: list[LeaderboardEntry]) -> None:
        """将数据写入本地缓存。"""
        import json

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        raw = [e.model_dump(mode="json") for e in entries]
        self._cache_path.write_text(
            json.dumps(raw, ensure_ascii=True, indent=2), encoding="utf-8"
        )
        self._save_status(entries)

    def _save_status(self, entries: list[LeaderboardEntry]) -> None:
        import json

        now = datetime.now(timezone.utc)
        status = {
            "source_id": self.source_id,
            "last_fetched": now.isoformat(),
            "entries_count": len(entries),
            "status": "ok",
        }
        self._status_path().write_text(
            json.dumps(status, ensure_ascii=True), encoding="utf-8"
        )

    def status(self) -> SourceStatus:
        """返回当前数据源状态。"""
        if not self.config.enabled:
            return SourceStatus(
                source_id=self.source_id,
                display_name=self.display_name,
                status="disabled",
            )
        sp = self._status_path()
        if not sp.is_file():
            return SourceStatus(
                source_id=self.source_id,
                display_name=self.display_name,
                status="stale",
            )
        import json

        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            return SourceStatus.model_validate(raw)
        except (OSError, ValueError):
            return SourceStatus(
                source_id=self.source_id,
                display_name=self.display_name,
                status="error",
            )

    def is_cache_fresh(self) -> bool:
        """缓存是否在刷新间隔内。"""
        s = self.status()
        if s.last_fetched is None:
            return False
        age = datetime.now(timezone.utc) - s.last_fetched
        return age < timedelta(hours=self.config.refresh_interval_hours)

    def is_available(self) -> bool:
        """数据源是否可用（有缓存或可拉取）。"""
        return self.config.enabled and (
            self.is_cache_fresh() or self._cache_path.is_file()
        )