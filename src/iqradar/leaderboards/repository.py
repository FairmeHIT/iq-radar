from __future__ import annotations

import json
from pathlib import Path

from iqradar.leaderboards.schemas import LeaderboardEntry, ScenarioDef, ScenarioView

SCENARIO_VIEW_SCHEMA_VERSION = "1.0"

# 本地缓存文件: data/leaderboards/scenarios/<scenario_id>.json
# 该缓存保存聚合后的场景视图快照，与原始数据缓存分离，
# 以便 dashboard 无需重新聚合即可读取。


class FileLeaderboardRepository:
    """场景视图本地缓存。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._scenarios_root = root / "scenarios"
        self._scenarios_root.mkdir(parents=True, exist_ok=True)

    def save_scenario_view(self, view: ScenarioView) -> None:
        """将场景视图写入缓存。"""
        path = self._scenario_path(view.scenario_id)
        payload = {
            "schema_version": SCENARIO_VIEW_SCHEMA_VERSION,
            "scenario_id": view.scenario_id,
            "scenario_label": view.scenario_label,
            "scenario_description": view.scenario_description,
            "rankings": [r.model_dump(mode="json") for r in view.rankings],
            "posters": [p.model_dump(mode="json") for p in view.posters],
        }
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def load_scenario_view(self, scenario_id: str) -> ScenarioView | None:
        """读取场景视图缓存。"""
        path = self._scenario_path(scenario_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("schema_version") != SCENARIO_VIEW_SCHEMA_VERSION:
            return None
        try:
            return ScenarioView(
                scenario_id=data["scenario_id"],
                scenario_label=data["scenario_label"],
                scenario_description=data.get("scenario_description", ""),
                rankings=[
                    _ranking_from_dict(r) for r in data.get("rankings", [])
                ],
                posters=[_poster_from_dict(p) for p in data.get("posters", [])],
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _scenario_path(self, scenario_id: str) -> Path:
        return self._scenarios_root / f"{scenario_id}.json"


def _ranking_from_dict(data: dict) -> "Ranking":
    from iqradar.leaderboards.schemas import ScenarioRanking

    return ScenarioRanking(
        rank=data["rank"],
        model_name=data["model_name"],
        model_display=data["model_display"],
        provider=data.get("provider"),
        composite_score=data["composite_score"],
        scores=data.get("scores", {}),
        sources=data.get("sources", []),
    )


def _poster_from_dict(data: dict) -> "Poster":
    from iqradar.leaderboards.schemas import PosterDef

    return PosterDef(
        id=data["id"],
        name=data["name"],
        url=data["url"],
        description=data.get("description", ""),
        scenarios=data.get("scenarios", []),
        image_url=data.get("image_url"),
        update_frequency=data.get("update_frequency", ""),
        source_type=data.get("source_type", "poster"),
    )