from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from iqradar.leaderboards.base import LeaderboardSource
from iqradar.leaderboards.schemas import LeaderboardEntry, SourceConfig

DEFAULT_BASE_URL = "https://codexradar.com"
FETCH_TIMEOUT_SEC = 15


class CodexRadarSource(LeaderboardSource):
    """CodexRadar 数据源（免费，无需认证，5 分钟刷新）。

    提供两个端点：
    - /api/model-ratings?view=public — 用户评分（平均分+投票数）
    - /api/intelligence-efficiency-metrics — IQ 分（passed/total）+ 成本/token/agent steps
    """

    def __init__(self, config: SourceConfig, cache_root: Path) -> None:
        super().__init__(config, cache_root)
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")

    @property
    def display_name(self) -> str:
        return "CodexRadar"

    async def fetch(self) -> list[LeaderboardEntry]:
        entries: list[LeaderboardEntry] = []
        now = datetime.now(timezone.utc)

        # 获取 IQ 效率指标（核心数据）
        try:
            iq_data = self._api_get("/api/intelligence-efficiency-metrics")
            entries.extend(self._parse_iq_metrics(iq_data, now))
        except Exception as exc:
            import logging

            logging.warning("CodexRadar IQ metrics fetch failed: %s", exc)

        # 获取用户评分
        try:
            ratings = self._api_get("/api/model-ratings?view=public")
            entries.extend(self._parse_ratings(ratings, now))
        except Exception as exc:
            import logging

            logging.warning("CodexRadar ratings fetch failed: %s", exc)

        return entries

    def _api_get(self, path: str) -> dict:
        """调用 CodexRadar API。"""
        url = f"{self._base_url}{path}"
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "IQRadar/1.0"}
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_iq_metrics(self, raw: dict, now: datetime) -> list[LeaderboardEntry]:
        """解析 intelligence-efficiency-metrics 端点。"""
        entries: list[LeaderboardEntry] = []
        points = raw.get("points", [])
        for pt in points:
            model = pt.get("model", "")
            effort = pt.get("effort", "high")
            if not model:
                continue
            passed = pt.get("passed", 0) or 0
            total = pt.get("total", 0) or 0
            iq = pt.get("iq", 0.0) or 0.0
            pass_rate = (passed / total * 100) if total else 0.0

            # 用 IQ 分作为主分数（与 iq-radar 口径一致）
            entries.append(
                LeaderboardEntry(
                    source_id=self.source_id,
                    model_name=model,
                    model_display=f"{model} ({effort})",
                    provider=None,
                    benchmark_name="iq_efficiency",
                    benchmark_label="IQ Efficiency",
                    score=float(iq),
                    score_label="IQ",
                    rank=None,
                    ci=None,
                    metadata={
                        "passed": passed,
                        "total": total,
                        "pass_rate": round(pass_rate, 2),
                        "effort": effort,
                        "average_price_usd": pt.get("average_price_usd"),
                        "average_minutes": pt.get("average_minutes"),
                        "average_agent_steps": pt.get("average_agent_steps"),
                        "average_total_tokens": pt.get("average_total_tokens"),
                        "cache_hit_rate": pt.get("cache_hit_rate"),
                        "runs_total": pt.get("runs_total"),
                        "runs_24h": pt.get("runs_24h"),
                    },
                    fetched_at=now,
                )
            )
        return entries

    def _parse_ratings(self, raw: dict, now: datetime) -> list[LeaderboardEntry]:
        """解析 model-ratings 端点。"""
        entries: list[LeaderboardEntry] = []
        models = raw.get("models", [])
        for item in models:
            model_id = item.get("id", "")
            if not model_id:
                continue
            avg = item.get("average", 0) or 0
            entries.append(
                LeaderboardEntry(
                    source_id=self.source_id,
                    model_name=model_id,
                    model_display=item.get("label", model_id),
                    provider=None,
                    benchmark_name="user_rating",
                    benchmark_label="User Rating",
                    score=float(avg),
                    score_label="avg rating",
                    rank=None,
                    ci=None,
                    metadata={
                        "group": item.get("group"),
                        "count": item.get("count"),
                    },
                    fetched_at=now,
                )
            )
        return entries