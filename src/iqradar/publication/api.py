from __future__ import annotations

import re

from flask import Blueprint, request

from iqradar.deepswe.service import DeepSweService
from iqradar.publication.service import DashboardPublisher
from iqradar.shared.api_responses import error, ok

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")


def _valid_run_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    return candidate if _RUN_ID_RE.fullmatch(candidate) else None


def create_publication_blueprint(
    service: DeepSweService,
    publisher: DashboardPublisher,
) -> Blueprint:
    blueprint = Blueprint("publication", __name__)

    @blueprint.post("/api/deepswe-runs/publish-batch")
    def publish_deepswe_runs():
        """Publish several completed runs as one accumulated snapshot.

        Selected from the test page's 评测记录 list (发布选中). Each run must
        be completed; the records are merged into the current snapshot and one
        snapshot is activated carrying a publication marker per run.
        """
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON publish request is required", 400)
        run_ids = payload.get("run_ids")
        if not isinstance(run_ids, list) or not run_ids:
            return error("run_ids list is required", 400)
        if len(run_ids) > 100:
            return error("at most 100 runs per publish", 400)
        clean: list[str] = []
        for value in run_ids:
            rid = _valid_run_id(value)
            if rid is None:
                return error("invalid run id in run_ids", 400)
            clean.append(rid)
        merge_raw = (request.args.get("merge") or "true").strip().lower()
        if merge_raw not in {"true", "1", "false", "0"}:
            return error("merge must be a boolean", 400)
        merge = merge_raw in {"true", "1"}
        try:
            publication = service.publish_runs(
                clean, publisher=publisher, merge_with_current=merge
            )
        except ValueError as caught:
            return error(str(caught), 409)
        except (KeyError, OSError):
            return error("run results could not be published", 503)
        return ok(
            {
                "snapshot_id": publication.snapshot_id,
                "source_job_id": publication.source_job_id,
                "published_run_ids": clean,
            }
        ), 201

    @blueprint.post("/api/deepswe-runs/<run_id>/publish")
    def publish_deepswe_run(run_id: str):
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        if run.status != "completed":
            return error("only completed deep-swe runs can be published", 409)
        merge_raw = (request.args.get("merge") or "true").strip().lower()
        if merge_raw not in {"true", "1", "false", "0"}:
            return error("merge must be a boolean", 400)
        merge = merge_raw in {"true", "1"}
        try:
            publication = service.publish(
                run_id, publisher=publisher, merge_with_current=merge
            )
        except (KeyError, OSError, ValueError):
            return error("run results could not be published", 503)
        return ok(
            {
                "snapshot_id": publication.snapshot_id,
                "source_job_id": publication.source_job_id,
            }
        ), 201

    @blueprint.get("/api/deepswe-runs/<run_id>/publication")
    def deepswe_run_publication(run_id: str):
        try:
            publication = publisher.publication_for_job(run_id)
        except (KeyError, OSError, ValueError):
            return error("run publication is unavailable", 503)
        if publication is None:
            return ok(None)
        return ok(
            {
                "snapshot_id": publication.snapshot_id,
                "source_job_id": publication.source_job_id,
            }
        )

    @blueprint.delete("/api/deepswe-runs/<run_id>/publish")
    def unpublish_deepswe_run(run_id: str):
        """回撤一个 run 的发布：删除所有包含该来源的快照并修复 current。"""
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        try:
            result = service.unpublish_run(run_id, publisher=publisher)
        except ValueError as caught:
            return error(str(caught), 404)
        if result is None:
            return error("run is not published", 404)
        return ok(result)

    return blueprint
