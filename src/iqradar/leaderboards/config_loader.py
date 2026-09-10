from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from iqradar.leaderboards.schemas import LeaderboardConfig, SourceConfig


def load_leaderboard_config(path: Path) -> LeaderboardConfig:
    """从 YAML 加载榜单配置，合并代码中定义的场景与海报。"""
    from iqradar.leaderboards.scenarios import MODEL_NAME_ALIASES, POSTERS, SCENARIOS

    raw = _load_yaml(path)
    sources = [
        SourceConfig.model_validate(item) for item in raw.get("sources", [])
    ]
    aliases = raw.get("model_name_aliases", {})

    return LeaderboardConfig(
        sources=sources,
        scenarios=SCENARIOS,
        posters=POSTERS,
        model_name_aliases=aliases or MODEL_NAME_ALIASES,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Leaderboard config must contain a YAML mapping: {path}")
    return data