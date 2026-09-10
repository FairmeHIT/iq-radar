from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from iqradar.leaderboards.base import LeaderboardSource
from iqradar.leaderboards.schemas import LeaderboardEntry, SourceConfig

# Arena AI Leaderboards 数据源
DEFAULT_BASE_URL = "https://api.wulong.dev/arena-ai-leaderboards/v1"
# GitHub raw 快照（稳定备用通道）
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data"
FETCH_TIMEOUT_SEC = 15
PROBE_TIMEOUT_SEC = 8

# 要拉取的子榜单
ARENA_LEADERBOARDS = {
    "code": "Code Generation",
    "text": "Text & Chat",
    "vision": "Image Understanding",
    "agent": "Agent Coding",
    "document": "Document Understanding",
}


class ArenaAISource(LeaderboardSource):
    """Arena AI Leaderboards 数据源（免费，无需认证）。

    双通道：优先 GitHub raw 快照，回退到 API 镜像。
    """

    def __init__(self, config: SourceConfig, cache_root: Path) -> None:
        super().__init__(config, cache_root)
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._date_str: str | None = None

    @property
    def display_name(self) -> str:
        return "Arena AI"

    async def fetch(self) -> list[LeaderboardEntry]:
        entries: list[LeaderboardEntry] = []
        now = datetime.now(timezone.utc)

        # 优先 GitHub raw（先探测一次 latest.json，短超时）
        if self._probe_github_raw():
            entries = self._fetch_all_github(now)

        if entries:
            return entries

        # 回退到 API 镜像
        for lb_name, lb_label in ARENA_LEADERBOARDS.items():
            try:
                raw = self._fetch_api(lb_name)
                entries.extend(self._parse_leaderboard(lb_name, lb_label, raw, now))
            except Exception as exc:
                import logging

                logging.warning(
                    "Arena AI leaderboard %s (API) fetch failed: %s", lb_name, exc
                )

        return entries

    # ── GitHub raw 通道 ──────────────────────────────────────────

    def _probe_github_raw(self) -> bool:
        """探测 GitHub raw 是否可达，并缓存日期。"""
        try:
            url = f"{GITHUB_RAW_BASE}/latest.json"
            req = urllib.request.Request(
                url, method="GET", headers={"User-Agent": "IQRadar/1.0"}
            )
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SEC) as resp:
                latest = json.loads(resp.read().decode("utf-8"))
            self._date_str = latest.get("date", "")
            return bool(self._date_str)
        except Exception:
            return False

    def _fetch_all_github(self, now: datetime) -> list[LeaderboardEntry]:
        """从 GitHub raw 快照拉取所有子榜单。"""
        entries: list[LeaderboardEntry] = []
        date_str = self._date_str or "2026-08-18"
        for lb_name, lb_label in ARENA_LEADERBOARDS.items():
            try:
                url = f"{GITHUB_RAW_BASE}/{date_str}/{lb_name}.json"
                req = urllib.request.Request(
                    url, method="GET", headers={"User-Agent": "IQRadar/1.0"}
                )
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                entries.extend(
                    self._parse_leaderboard(lb_name, lb_label, raw, now)
                )
            except Exception:
                pass
        return entries

    # ── API 镜像通道 ─────────────────────────────────────────────

    def _fetch_api(self, name: str) -> dict:
        """调用 REST API 镜像。"""
        url = f"{self._base_url}/leaderboard?name={name}"
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "IQRadar/1.0"}
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ── 通用解析 ─────────────────────────────────────────────────

    def _parse_leaderboard(
        self,
        lb_name: str,
        lb_label: str,
        raw: dict,
        now: datetime,
    ) -> list[LeaderboardEntry]:
        """解析 API 返回的榜单数据。"""
        entries: list[LeaderboardEntry] = []
        models = raw.get("models", [])
        for item in models:
            provider = item.get("vendor") or None
            rank = item.get("rank")
            raw_name = item.get("model", "")
            if not raw_name:
                continue

            # agent 榜使用 scores 数组，其他使用 score/ci
            score_value = item.get("score")
            ci_value = item.get("ci")
            scores_list = item.get("scores")

            if score_value is None and scores_list:
                for s in scores_list:
                    score_value = s.get("score")
                    ci_value = s.get("ci")
                    break

            entries.append(
                LeaderboardEntry(
                    source_id=self.source_id,
                    model_name=raw_name,
                    model_display=raw_name,
                    provider=provider,
                    benchmark_name=lb_name,
                    benchmark_label=lb_label,
                    score=float(score_value) if score_value is not None else 0.0,
                    score_label="ELO",
                    rank=rank,
                    ci=float(ci_value) if ci_value is not None else None,
                    metadata={
                        "vendor": provider,
                        "license": item.get("license"),
                        "votes": item.get("votes"),
                        "scores": scores_list,
                        "sessions": item.get("sessions"),
                    },
                    fetched_at=now,
                )
            )
        return entries