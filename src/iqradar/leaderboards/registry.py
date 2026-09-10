from __future__ import annotations

from pathlib import Path

from iqradar.leaderboards.adapters.agentbench import AgentBenchSource
from iqradar.leaderboards.adapters.arena_ai import ArenaAISource
from iqradar.leaderboards.adapters.codex_radar import CodexRadarSource
from iqradar.leaderboards.adapters.llm_stats import LLMStatsSource
from iqradar.leaderboards.adapters.superclue import SuperCLUESource
from iqradar.leaderboards.base import LeaderboardSource
from iqradar.leaderboards.schemas import SourceConfig


def build_sources(
    configs: list[SourceConfig], cache_root: Path
) -> dict[str, LeaderboardSource]:
    """根据配置构建所有启用的数据源。"""
    sources: dict[str, LeaderboardSource] = {}
    for cfg in configs:
        if not cfg.enabled:
            continue
        source = _build_source(cfg, cache_root)
        if source is not None:
            sources[cfg.id] = source
    return sources


def _build_source(
    config: SourceConfig, cache_root: Path
) -> LeaderboardSource | None:
    if config.id == "arena-ai":
        return ArenaAISource(config, cache_root)
    if config.id == "llm-stats":
        return LLMStatsSource(config, cache_root)
    if config.id == "codex-radar":
        return CodexRadarSource(config, cache_root)
    if config.id == "superclue":
        return SuperCLUESource(config, cache_root)
    if config.id == "agentbench":
        return AgentBenchSource(config, cache_root)
    # 后续可扩展更多 adapter
    return None