from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class LeaderboardEntry(BaseModel):
    """归一化榜单条目——各 adapter 输出的统一格式。"""

    source_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_display: str = Field(min_length=1)
    provider: str | None = None
    benchmark_name: str = Field(min_length=1)
    benchmark_label: str = Field(min_length=1)
    score: float
    score_label: str = "score"
    rank: int | None = None
    ci: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime


class SourceConfig(BaseModel):
    """数据源配置（来自 YAML）。"""

    id: str = Field(min_length=1)
    enabled: bool = True
    api_key_env: str | None = None
    base_url: str | None = None
    refresh_interval_hours: int = 24
    extra: dict[str, Any] = Field(default_factory=dict)


class ScenarioWeight(BaseModel):
    """场景权重——一个 source:benchmark 对及其权重。"""

    key: str = Field(min_length=1)  # "arena-ai:code" 或 "llm-stats:swe_bench_verified"
    source: str = Field(min_length=1)
    benchmark: str = Field(min_length=1)
    weight: float = Field(ge=0, le=1)


class ScenarioDef(BaseModel):
    """场景定义。"""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    icon: str = "FileText"
    weights: list[ScenarioWeight] = Field(default_factory=list)


class PosterDef(BaseModel):
    """海报式榜单——无法获取数据时的展示信息。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    description: str = ""
    scenarios: list[str] = Field(default_factory=list)
    image_url: str | None = None
    update_frequency: str = ""
    source_type: Literal["api", "dataset", "html", "poster"] = "poster"


class LeaderboardConfig(BaseModel):
    """完整配置。"""

    sources: list[SourceConfig] = Field(default_factory=list)
    scenarios: list[ScenarioDef] = Field(default_factory=list)
    posters: list[PosterDef] = Field(default_factory=list)
    model_name_aliases: dict[str, list[str]] = Field(default_factory=dict)


class ScenarioRanking(BaseModel):
    """某个场景下某模型的综合排名条目。"""

    rank: int
    model_name: str
    model_display: str
    provider: str | None
    composite_score: float
    scores: dict[str, float]  # source:benchmark → score
    sources: list[str]  # 贡献来源列表


class ScenarioView(BaseModel):
    """场景视图——返回给前端。"""

    scenario_id: str
    scenario_label: str
    scenario_description: str
    rankings: list[ScenarioRanking]
    posters: list[PosterDef]


class SourceStatus(BaseModel):
    """数据源状态。"""

    source_id: str
    display_name: str
    status: Literal["ok", "stale", "error", "disabled"]
    last_fetched: datetime | None = None
    next_refresh: datetime | None = None
    entries_count: int = 0
    error_message: str | None = None