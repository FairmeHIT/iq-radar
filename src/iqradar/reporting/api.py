from __future__ import annotations

import json

from flask import Blueprint, request
from pydantic import ValidationError

from iqradar.reporting.service import DashboardService
from iqradar.shared.api_responses import error, ok


def create_reporting_blueprint(service: DashboardService) -> Blueprint:
    blueprint = Blueprint("reporting", __name__)

    @blueprint.get("/api/dashboard")
    def dashboard():
        snapshot_id = request.args.get("snapshot")
        if snapshot_id == "":
            snapshot_id = None
        try:
            data = service.dashboard(snapshot_id=snapshot_id)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @blueprint.get("/api/snapshots")
    def snapshots():
        try:
            data = service.list_snapshots()
        except (OSError, json.JSONDecodeError):
            return error("snapshot list is unavailable", 503)
        return ok(data)

    @blueprint.delete("/api/snapshots/<snapshot_id>")
    def delete_snapshot(snapshot_id: str):
        try:
            result = service.delete_snapshot(snapshot_id)
        except ValueError:
            return error("invalid dashboard snapshot ID", 400)
        except OSError:
            return error("dashboard snapshot could not be deleted", 503)
        if result is None:
            return error("dashboard snapshot not found", 404)
        return ok(result)

    @blueprint.get("/api/summary")
    def summary():
        try:
            data = service.summary()
        except (OSError, json.JSONDecodeError, ValidationError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @blueprint.get("/api/radar/iq")
    def iq_radar():
        try:
            data = service.iq_radar()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @blueprint.get("/api/radar/quota")
    def quota_radar():
        try:
            data = service.quota_radar()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @blueprint.get("/api/runs")
    def runs():
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            return error("limit must be an integer", 400)
        if not 1 <= limit <= 1000:
            return error("limit must be between 1 and 1000", 400)
        try:
            records = service.runs(
                model=request.args.get("model"),
                effort=request.args.get("effort"),
                status=request.args.get("status"),
                limit=limit,
            )
        except (OSError, json.JSONDecodeError, ValidationError):
            return error("published dashboard runs are unavailable", 503)
        return ok([record.model_dump(mode="json") for record in records])

    return blueprint
