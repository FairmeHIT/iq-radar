from __future__ import annotations

from flask import Blueprint, jsonify

from iqradar.leaderboards.service import LeaderboardService
from iqradar.shared.api_responses import error, ok


def create_leaderboard_blueprint(service: LeaderboardService) -> Blueprint:
    blueprint = Blueprint("leaderboards", __name__)

    @blueprint.get("/api/leaderboards/scenarios")
    def list_scenarios():
        return ok(service.list_scenarios())

    @blueprint.get("/api/leaderboards/scenarios/<scenario_id>")
    def scenario_rankings(scenario_id: str):
        view = service.scenario_view(scenario_id)
        if view is None:
            return error(f"scenario not found: {scenario_id}", 404)
        return ok(
            {
                "scenario_id": view.scenario_id,
                "scenario_label": view.scenario_label,
                "scenario_description": view.scenario_description,
                "rankings": [r.model_dump(mode="json") for r in view.rankings],
                "posters": [p.model_dump(mode="json") for p in view.posters],
            }
        )

    @blueprint.post("/api/leaderboards/refresh")
    def refresh_leaderboards():
        try:
            service.refresh_all()
            return ok({"message": "leaderboards refreshed"})
        except Exception as exc:
            return error(f"refresh failed: {exc}", 503)

    @blueprint.get("/api/leaderboards/sources")
    def source_statuses():
        return ok([s.model_dump(mode="json") for s in service.source_statuses()])

    return blueprint