from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

from iqradar.leaderboards.base import LeaderboardSource
from iqradar.leaderboards.repository import FileLeaderboardRepository
from iqradar.leaderboards.scenarios import POSTERS, normalize_model_name
from iqradar.leaderboards.schemas import (
    LeaderboardEntry,
    PosterDef,
    ScenarioRanking,
    ScenarioView,
    ScenarioWeight,
    SourceConfig,
    SourceStatus,
)


class LeaderboardService:
    """榜单汇聚服务——聚合数据、缓存、场景映射。"""

    def __init__(
        self,
        sources: dict[str, LeaderboardSource],
        repository: FileLeaderboardRepository,
        scenarios: list,
        posters: list[PosterDef],
        model_name_aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self._sources = sources
        self._repository = repository
        self._scenarios = scenarios
        self._posters = posters
        self._model_name_aliases = model_name_aliases or {}

    def scenario_view(self, scenario_id: str) -> ScenarioView | None:
        """获取某个场景的视图（排名优先从缓存读取，海报始终返回全部）。"""
        # 找到场景定义
        scenario = self._find_scenario(scenario_id)
        if scenario is None:
            return None

        # 海报始终返回全部（不区分场景）
        posters = list(POSTERS)

        # 排名走缓存
        cached = self._repository.load_scenario_view(scenario_id)
        if cached is not None:
            cached.posters = posters
            return cached

        # 从所有数据源加载数据并聚合
        all_entries = self._load_all_entries()
        rankings = self._compute_rankings(scenario_id, scenario.weights, all_entries)

        view = ScenarioView(
            scenario_id=scenario.id,
            scenario_label=scenario.label,
            scenario_description=scenario.description,
            rankings=rankings,
            posters=posters,
        )

        # 写缓存
        self._repository.save_scenario_view(view)
        return view

    def refresh_all(self) -> None:
        """强制刷新所有数据源并重新计算所有场景。"""
        # 后台异步拉取所有数据源
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._fetch_all())
        finally:
            loop.close()

        # 重新计算所有场景
        all_entries = self._load_all_entries()
        for scenario in self._scenarios:
            rankings = self._compute_rankings(
                scenario.id, scenario.weights, all_entries
            )
            view = ScenarioView(
                scenario_id=scenario.id,
                scenario_label=scenario.label,
                scenario_description=scenario.description,
                rankings=rankings,
                posters=list(POSTERS),
            )
            self._repository.save_scenario_view(view)

    def source_statuses(self) -> list[SourceStatus]:
        """返回所有数据源状态。"""
        return [source.status() for source in self._sources.values()]

    def list_scenarios(self) -> list[dict]:
        """返回场景列表。"""
        return [
            {
                "id": s.id,
                "label": s.label,
                "description": s.description,
                "icon": s.icon,
            }
            for s in self._scenarios
        ]

    # --- 内部方法 ---------------------------------------------------

    def _find_scenario(self, scenario_id: str):
        for s in self._scenarios:
            if s.id == scenario_id:
                return s
        return None

    def _load_all_entries(self) -> list[LeaderboardEntry]:
        """从所有数据源加载数据（优先缓存，否则拉取）。"""
        entries: list[LeaderboardEntry] = []
        for source in self._sources.values():
            if not source.is_available():
                continue
            cached = source.load_cache()
            if cached is not None:
                entries.extend(cached)
            elif source.is_cache_fresh():
                entries.extend(cached or [])
        return entries

    def _compute_rankings(
        self,
        scenario_id: str,
        weights: list[ScenarioWeight],
        all_entries: list[LeaderboardEntry],
    ) -> list[ScenarioRanking]:
        """根据权重计算场景排名（各 benchmark 分数先 min-max 归一化）。"""
        # 按 model_name 分组
        model_scores: dict[str, dict[str, float]] = defaultdict(dict)
        model_sources: dict[str, set[str]] = defaultdict(set)
        model_meta: dict[str, tuple[str, str | None]] = {}  # name → (display, provider)

        for entry in all_entries:
            normalized = normalize_model_name(
                entry.model_name, self._model_name_aliases
            )
            key = f"{entry.source_id}:{entry.benchmark_name}"
            model_scores[normalized][key] = entry.score
            model_sources[normalized].add(entry.source_id)
            if normalized not in model_meta:
                model_meta[normalized] = (entry.model_display, entry.provider)
            # 更新 display 为更友好版本
            if entry.model_display != entry.model_name:
                model_meta[normalized] = (entry.model_display, entry.provider)

        # 对每个 source:benchmark 做 min-max 归一化（0-100），
        # 使不同量纲（ELO 1000-1700 / IQ 0-150 / 评分 0-10 / 准确率 0-1）可比。
        normalized_scores: dict[str, dict[str, float]] = defaultdict(dict)
        for model_name, scores in model_scores.items():
            for key, value in scores.items():
                normalized_scores[model_name][key] = value  # 保留原始值
        normalizer = _benchmark_normalizer(model_scores)
        for model_name, scores in model_scores.items():
            for key, value in scores.items():
                normalized_scores[model_name][key] = normalizer[key](value)

        # 计算综合得分（用归一化值）
        rankings: list[ScenarioRanking] = []
        for model_name, scores in normalized_scores.items():
            composite = 0.0
            total_weight = 0.0
            used_scores: dict[str, float] = {}
            used_sources: set[str] = set()

            for w in weights:
                norm = scores.get(w.key)
                if norm is not None:
                    composite += norm * w.weight
                    total_weight += w.weight
                    # 记录原始值与归一化值，便于前端展示
                    raw = model_scores[model_name].get(w.key)
                    used_scores[w.key] = raw if raw is not None else norm
                    used_sources.add(w.source)

            if total_weight > 0 and used_scores:
                composite /= total_weight
                display, provider = model_meta.get(model_name, (model_name, None))
                rankings.append(
                    ScenarioRanking(
                        rank=0,  # 排序后赋 rank
                        model_name=model_name,
                        model_display=display,
                        provider=provider,
                        composite_score=round(composite, 2),
                        scores=used_scores,
                        sources=sorted(used_sources),
                    )
                )

        # 按综合得分降序排列
        rankings.sort(key=lambda r: r.composite_score, reverse=True)
        for i, r in enumerate(rankings, 1):
            r.rank = i

        return rankings

    async def _fetch_all(self) -> None:
        """并行拉取所有数据源。"""
        tasks = []
        for source in self._sources.values():
            if not source.config.enabled:
                continue
            tasks.append(self._fetch_one(source))
        await asyncio.gather(*tasks)

    async def _fetch_one(self, source: LeaderboardSource) -> None:
        """拉取单个数据源并缓存。"""
        try:
            entries = await source.fetch()
            if entries:
                source.save_cache(entries)
        except Exception as exc:
            import logging

            logging.warning(
                "Leaderboard fetch %s failed: %s", source.source_id, exc
            )


def _benchmark_normalizer(
    model_scores: dict[str, dict[str, float]],
) -> dict[str, callable]:
    """为每个 source:benchmark 生成 min-max 归一化函数（输出 0-100）。

    对只有一个数据点的 benchmark 使用 sigmoid 软映射。
    """
    # 收集每个 key 的分数
    key_values: dict[str, list[float]] = defaultdict(list)
    for scores in model_scores.values():
        for key, value in scores.items():
            key_values[key].append(value)

    normalizers: dict[str, callable] = {}
    for key, values in key_values.items():
        if len(values) == 0:
            normalizers[key] = lambda v: 0.0
        elif len(values) == 1:
            # 单个数据点：用 sigmoid 软映射，mid=50
            v = values[0]
            normalizers[key] = lambda x, mid=v, scale=_auto_scale(v): _soft_norm(
                x, mid, scale
            )
        else:
            mn = min(values)
            mx = max(values)
            if mx == mn:
                normalizers[key] = lambda _: 50.0
            else:
                normalizers[key] = lambda v, lo=mn, hi=mx: _minmax_norm(v, lo, hi)

    return normalizers


def _minmax_norm(value: float, lo: float, hi: float) -> float:
    """Min-max 归一化到 0-100。"""
    return round(min(100.0, max(0.0, (value - lo) / (hi - lo) * 100)), 1)


def _auto_scale(mid: float) -> float:
    """自动选择 sigmoid 缩放因子。"""
    if mid > 500:
        return mid / 4  # ELO 级别
    if mid > 50:
        return mid / 3
    return 10.0


def _soft_norm(value: float, mid: float, scale: float) -> float:
    """Sigmoid 软映射到 0-100，以 mid 为 50 分。"""
    import math

    return round(100.0 / (1.0 + math.exp(-(value - mid) / scale)), 1)