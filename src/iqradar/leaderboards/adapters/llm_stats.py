from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from iqradar.leaderboards.base import LeaderboardSource
from iqradar.leaderboards.schemas import LeaderboardEntry, SourceConfig

DEFAULT_BASE_URL = "https://api.llm-stats.com/stats/v1"
FETCH_TIMEOUT_SEC = 20

# 感兴趣的 benchmark 列表
BENCHMARK_MAP: dict[str, str] = {
    "swe_bench_verified": "SWE-bench Verified",
    "gpqa": "GPQA Diamond",
    "aime_2025": "AIME 2025",
    "mmlu_pro": "MMLU-Pro",
    "live_code_bench": "LiveCodeBench",
    "hle": "Humanity's Last Exam",
    "mmlu": "MMLU",
}


class LLMStatsSource(LeaderboardSource):
    """LLM-Stats 数据源（需 API key，从环境变量读取）。"""

    def __init__(self, config: SourceConfig, cache_root: Path) -> None:
        super().__init__(config, cache_root)
        self._base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = self._resolve_api_key(config)

    @staticmethod
    def _resolve_api_key(config: SourceConfig) -> str | None:
        env_name = config.api_key_env or "LLM_STATS_API_KEY"
        return os.environ.get(env_name) or None

    @property
    def display_name(self) -> str:
        return "LLM-Stats"

    async def fetch(self) -> list[LeaderboardEntry]:
        if not self._api_key:
            import logging

            logging.warning("LLM-Stats API key not configured; skipping fetch")
            return []

        entries: list[LeaderboardEntry] = []
        now = datetime.now(timezone.utc)

        # 先获取所有模型列表，用于名称映射
        models = self._fetch_models()
        model_map = {m["id"]: m for m in models} if models else {}

        for bench_id, bench_label in BENCHMARK_MAP.items():
            try:
                raw = self._fetch_scores(bench_id)
                entries.extend(
                    self._parse_scores(bench_id, bench_label, raw, model_map, now)
                )
            except Exception as exc:
                import logging

                logging.warning(
                    "LLM-Stats benchmark %s fetch failed: %s", bench_id, exc
                )

        return entries

    def _fetch_models(self) -> list[dict]:
        """获取模型列表。"""
        try:
            data = self._api_get("/models")
            return data if isinstance(data, list) else data.get("data", [])
        except Exception:
            return []

    def _fetch_scores(self, benchmark: str) -> list[dict]:
        """获取指定 benchmark 的得分。"""
        data = self._api_get(f"/scores?benchmark={benchmark}")
        if isinstance(data, list):
            return data
        return data.get("data", [])

    def _api_get(self, path: str) -> dict | list:
        """调用 LLM-Stats API。"""
        url = f"{self._base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _parse_scores(
        self,
        bench_id: str,
        bench_label: str,
        raw: list[dict],
        model_map: dict[str, dict],
        now: datetime,
    ) -> list[LeaderboardEntry]:
        entries: list[LeaderboardEntry] = []
        for item in raw:
            model_id = item.get("model_id") or item.get("model") or ""
            if not model_id:
                continue
            display = model_map.get(model_id, {}).get("name") or model_id
            provider = model_map.get(model_id, {}).get("organization_name")
            score = item.get("score") or item.get("normalized_score") or 0
            rank = item.get("rank")

            entries.append(
                LeaderboardEntry(
                    source_id=self.source_id,
                    model_name=model_id,
                    model_display=display,
                    provider=provider,
                    benchmark_name=bench_id,
                    benchmark_label=bench_label,
                    score=float(score) if score is not None else 0.0,
                    score_label="accuracy",
                    rank=rank,
                    ci=None,
                    metadata={
                        "analysis_method": item.get("analysis_method"),
                        "verified": item.get("verified"),
                        "self_reported": item.get("self_reported"),
                    },
                    fetched_at=now,
                )
            )
        return entries