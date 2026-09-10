from __future__ import annotations

import csv
import io
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from iqradar.leaderboards.base import LeaderboardSource
from iqradar.leaderboards.schemas import LeaderboardEntry, SourceConfig

CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRR3Wl7wsCgHpwUw1_eUXW_fptAPLL3FkhnW"
    "_rua0O1Ji_GIVrpTjY5LaKAhwO-WeARjnY_KNw0SYNJ"
    "/pub?output=csv"
)
FETCH_TIMEOUT_SEC = 20


class AgentBenchSource(LeaderboardSource):
    """AgentBench 数据源（Google Sheets CSV 导出，免费无需 key）。

    覆盖 6 个 Agent 任务：alfworld, dbbench, knowledgegraph,
    os_interaction, webshop, 综合得分。
    """

    def __init__(self, config: SourceConfig, cache_root: Path) -> None:
        super().__init__(config, cache_root)
        self._csv_url = config.base_url or CSV_URL

    @property
    def display_name(self) -> str:
        return "AgentBench"

    async def fetch(self) -> list[LeaderboardEntry]:
        try:
            content = self._download_csv()
            return self._parse_csv(content)
        except Exception as exc:
            logging.warning("AgentBench fetch failed: %s", exc)
            return []

    def _download_csv(self) -> str:
        req = urllib.request.Request(
            self._csv_url,
            method="GET",
            headers={
                "User-Agent": "IQRadar/1.0",
                "Accept": "text/csv,application/csv",
            },
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
            return resp.read().decode("utf-8-sig", errors="replace")

    def _parse_csv(self, content: str) -> list[LeaderboardEntry]:
        entries: list[LeaderboardEntry] = []
        now = datetime.now(timezone.utc)

        reader = csv.DictReader(io.StringIO(content))
        # 期望列名: Model, Agent, 及任务名
        # 常见列: Model, Agent, AlfWorld, DbBench, KnowledgeGraph,
        #         OSInteraction, WebShop, Overall/综合
        if not reader.fieldnames:
            return entries

        for row in reader:
            model = (row.get("Model") or row.get("model") or "").strip()
            agent = (row.get("Agent") or row.get("agent") or "").strip()
            if not model:
                continue

            model_display = f"{model} / {agent}" if agent else model

            # 提取各任务得分
            for task_col in (
                "AlfWorld",
                "alfworld",
                "DbBench",
                "dbbench",
                "KnowledgeGraph",
                "knowledgegraph",
                "OSInteraction",
                "os_interaction",
                "WebShop",
                "webshop",
                "Overall",
                "overall",
                "综合",
                "AgentBench",
                "agentbench",
            ):
                raw = row.get(task_col)
                if raw is None:
                    continue
                try:
                    score = float(raw.strip())
                except (ValueError, AttributeError):
                    continue

                bench_slug = _task_slug(task_col)
                bench_label = _task_label(task_col)

                entries.append(
                    LeaderboardEntry(
                        source_id=self.source_id,
                        model_name=model,
                        model_display=model_display,
                        provider=None,
                        benchmark_name=bench_slug,
                        benchmark_label=bench_label,
                        score=score,
                        score_label=bench_label,
                        rank=None,
                        ci=None,
                        metadata={
                            "agent": agent,
                            "_task_col": task_col,
                        },
                        fetched_at=now,
                    )
                )
        return entries


def _task_slug(col: str) -> str:
    _map = {
        "alfworld": "alfworld",
        "dbbench": "dbbench",
        "knowledgegraph": "knowledgegraph",
        "os_interaction": "os_interaction",
        "webshop": "webshop",
    }
    lower = col.lower()
    for k, v in _map.items():
        if k in lower:
            return v
    return "overall"


def _task_label(col: str) -> str:
    _map = {
        "alfworld": "AlfWorld",
        "dbbench": "DbBench",
        "knowledgegraph": "KnowledgeGraph",
        "os_interaction": "OSInteraction",
        "webshop": "WebShop",
    }
    lower = col.lower()
    for k, v in _map.items():
        if k in lower:
            return v
    return "Overall"