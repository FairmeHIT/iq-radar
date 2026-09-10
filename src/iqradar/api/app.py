from __future__ import annotations

import json
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory
from pydantic import ValidationError

from iqradar.benchmarks.base import sync_container_gateway_url
from iqradar.benchmarks.registry import build_backends
from iqradar.config.loader import load_benchmark_config, load_price_config
from iqradar.deepswe.api import create_deepswe_blueprint
from iqradar.deepswe.runs import FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService
from iqradar.leaderboards.api import create_leaderboard_blueprint
from iqradar.leaderboards.config_loader import load_leaderboard_config
from iqradar.leaderboards.registry import build_sources
from iqradar.leaderboards.repository import FileLeaderboardRepository
from iqradar.leaderboards.service import LeaderboardService
from iqradar.publication.api import create_publication_blueprint
from iqradar.publication.service import DashboardPublisher
from iqradar.reporting.api import create_reporting_blueprint
from iqradar.reporting.repository import FileDashboardRepository
from iqradar.reporting.service import DashboardService
from iqradar.settings.api import create_gateway_blueprint
from iqradar.settings.gateway import GatewaySettings
from iqradar.shared.api_responses import error, ok
from iqradar import __version__


def _deepswe_source_exists(runs_root: Path, source_job_ids: list[str]) -> bool:
    """Whether every test-page record behind a dashboard snapshot remains.

    Publication ``source_job_id`` values come in three shapes: a single run id
    (``<32-hex>`` → ``data/deepswe/runs/<id>/state.json``), a multi-model batch
    (``batch-<first 8 hex>`` → ``data/deepswe/batches/<full id>.json``), or a
    multi-benchmark batch (``multi-<first 8 hex>`` →
    ``data/deepswe/multi-batches/<full id>.json``). Anything else counts as gone.
    Merged (radar-v3) snapshots carry several sources: every one of them must
    still exist, because each source's data stays in the snapshot even after
    its test-page record is deleted. An empty list counts as existing.
    """
    ids = [str(sid or "") for sid in source_job_ids]
    if not ids:
        return True
    return all(_single_source_exists(runs_root, sid) for sid in ids)


def _single_source_exists(runs_root: Path, sid: str) -> bool:
    if re.fullmatch(r"[0-9a-f]{32}", sid):
        return (runs_root / sid / "state.json").is_file()
    prefixed = re.fullmatch(r"(batch|multi)-([0-9a-f]{8})", sid)
    if prefixed is not None:
        store = runs_root.parent / (
            "batches" if prefixed.group(1) == "batch" else "multi-batches"
        )
        if not store.is_dir():
            return False
        prefix = prefixed.group(2)
        return any(path.stem.startswith(prefix) for path in store.glob("*.json"))
    return False


def create_app(
    data_path: Path | None = None,
    raw_runs_path: Path | None = None,
    models_path: Path | None = None,
    benchmark_path: Path | None = None,
    prices_path: Path | None = None,
    jobs_path: Path | None = None,
    deepswe_runs_path: Path | None = None,
    reporting_path: Path | None = None,
    reporting_repository: FileDashboardRepository | None = None,
    gateway_settings_path: Path | None = None,
) -> Flask:
    project_root = _project_root()
    static_folder = project_root / "web" / "dist"
    legacy_summary_path = _project_path(data_path, project_root) if data_path is not None else None
    legacy_runs_path = _project_path(raw_runs_path, project_root) if raw_runs_path is not None else None
    data_path = legacy_summary_path or _project_path(Path("data/aggregate/radar.json"), project_root)
    raw_runs_path = legacy_runs_path or _project_path(Path("data/raw/runs"), project_root)
    models_path = _project_path(models_path or Path("configs/models.yaml"), project_root)
    benchmark_path = _project_path(benchmark_path or Path("configs/benchmark.yaml"), project_root)
    prices_path = _project_path(prices_path or Path("configs/prices.example.yaml"), project_root)
    deepswe_runs_path = _project_path(
        deepswe_runs_path or Path("data/deepswe/runs"), project_root
    )
    reporting_path = _project_path(reporting_path or Path("data/reporting"), project_root)
    leaderboards_path = _project_path(Path("data/leaderboards"), project_root)
    leaderboards_config_path = _project_path(Path("configs/leaderboards.yaml"), project_root)
    gateway_settings_path = _project_path(
        gateway_settings_path or Path("data/settings/gateway.json"), project_root
    )
    app = Flask(
        __name__,
        static_folder=str(static_folder) if static_folder.exists() else None,
        static_url_path="",
    )
    app.config["IQRADAR_DATA_PATH"] = data_path
    app.config["IQRADAR_RAW_RUNS_PATH"] = raw_runs_path
    app.config["IQRADAR_MODELS_PATH"] = models_path
    app.config["IQRADAR_BENCHMARK_PATH"] = benchmark_path
    app.config["IQRADAR_PRICES_PATH"] = prices_path
    app.config["IQRADAR_DEEPSWE_RUNS_PATH"] = deepswe_runs_path
    app.config["IQRADAR_REPORTING_PATH"] = reporting_path
    app.config["IQRADAR_GATEWAY_SETTINGS_PATH"] = gateway_settings_path
    app.config["IQRADAR_VERSION"] = __version__
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    dashboard_repository = reporting_repository or FileDashboardRepository(
        reporting_path,
        legacy_summary_path=legacy_summary_path,
        legacy_runs_path=legacy_runs_path,
    )
    benchmark_config_set = load_benchmark_config(benchmark_path)
    # 同步容器网关地址：Docker Desktop for WSL 2 下容器访问宿主机要用 WSL
    # eth0 IP（DHCP 动态），把 .env 里不可达的 172.17.0.1/localhost 替换掉。
    for benchmark_config in benchmark_config_set.benchmarks.values():
        sync_container_gateway_url(benchmark_config.resolved_env_file())
    # 测试页「网关配置」存储：加载已保存的 baseurl/key 覆盖并同步到进程
    # 环境（GATEWAY_* / IQRADAR_MODELS_*），让所有后端立即使用新配置。
    gateway_settings = GatewaySettings(gateway_settings_path)
    run_store = FileDeepSweRunStore(deepswe_runs_path)
    backends = build_backends(
        benchmark_config_set,
        project_root,
        run_log_path=run_store.log_path,
        gateway_settings=gateway_settings,
    )
    try:
        price_config = load_price_config(prices_path)
    except (OSError, ValueError):
        price_config = None
    deepswe_service = DeepSweService(
        runs=run_store, backends=backends, prices=price_config
    )
    dashboard_service = DashboardService(
        dashboard_repository,
        source_exists=lambda ids: _deepswe_source_exists(deepswe_runs_path, ids),
    )
    publisher = DashboardPublisher(dashboard_repository, prices_path)
    app.extensions["iqradar_reporting"] = dashboard_service
    app.extensions["iqradar_deepswe"] = deepswe_service
    app.extensions["iqradar_deepswe_runs"] = run_store
    app.extensions["iqradar_publisher"] = publisher
    app.extensions["iqradar_gateway_settings"] = gateway_settings
    app.register_blueprint(create_reporting_blueprint(dashboard_service))
    app.register_blueprint(
        create_deepswe_blueprint(
            deepswe_service,
            models_path=models_path,
            publisher=publisher,
            gateway_settings=gateway_settings,
        )
    )
    app.register_blueprint(create_gateway_blueprint(gateway_settings))
    app.register_blueprint(create_publication_blueprint(deepswe_service, publisher))

    # ── 榜单汇聚模块 ────────────────────────────────────────────────
    try:
        lb_config = load_leaderboard_config(leaderboards_config_path)
        lb_cache_root = leaderboards_path / "cache"
        lb_repository = FileLeaderboardRepository(leaderboards_path)
        lb_sources = build_sources(lb_config.sources, lb_cache_root)
        lb_service = LeaderboardService(
            sources=lb_sources,
            repository=lb_repository,
            scenarios=lb_config.scenarios,
            posters=lb_config.posters,
            model_name_aliases=lb_config.model_name_aliases,
        )
        app.extensions["iqradar_leaderboards"] = lb_service
        app.register_blueprint(create_leaderboard_blueprint(lb_service))
    except Exception as exc:
        # 榜单模块非核心，失败不影响启动
        import logging

        logging.warning("Leaderboard module failed to initialize: %s", exc)

    @app.before_request
    def require_loopback_client():
        if request.path.startswith("/api/") and not _is_loopback_address(request.remote_addr):
            from iqradar.shared.api_responses import error

            return error("IQRadar API is available only from loopback", 403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _is_allowed_origin(
            request.headers.get("Origin")
        ):
            from iqradar.shared.api_responses import error

            return error("Cross-origin API writes are not allowed", 403)
        return None

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        return readiness()

    @app.get("/health/readiness")
    def readiness():
        try:
            snapshot_available = dashboard_repository.load_summary() is not None
            reporting_status = "ready" if snapshot_available else "empty"
        except (OSError, ValueError):
            snapshot_available = False
            reporting_status = "degraded"
        return (
            jsonify(
                {
                    "status": "ok",
                    "service": "iq_radar",
                    "version": app.config["IQRADAR_VERSION"],
                    "exists": snapshot_available,
                    "reporting_status": reporting_status,
                }
            ),
            200,
        )

    @app.get("/openapi.json")
    def openapi_json():
        return jsonify(
            {
                "openapi": "3.0.3",
                "info": {
                    "title": "IQRadar API",
                    "version": app.config["IQRADAR_VERSION"],
                },
                "paths": {
                    "/health/readiness": {"get": {"summary": "Readiness probe"}},
                    "/api/health": {"get": {"summary": "Legacy health probe"}},
                },
            }
        )

    @app.get("/docs")
    def docs():
        return jsonify(
            {
                "service": "iq_radar",
                "version": app.config["IQRADAR_VERSION"],
                "openapi": "/openapi.json",
            }
        )

    @app.get("/v1/summary")
    def v1_summary():
        try:
            data = dashboard_service.summary()
        except (OSError, json.JSONDecodeError, ValidationError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @app.get("/v1/dashboard")
    def v1_dashboard():
        try:
            data = dashboard_service.dashboard()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError, ValueError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @app.get("/v1/radar/iq")
    def v1_iq_radar():
        try:
            data = dashboard_service.iq_radar()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @app.get("/v1/radar/quota")
    def v1_quota_radar():
        try:
            data = dashboard_service.quota_radar()
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValidationError):
            return error("published dashboard snapshot is unavailable", 503)
        return ok(data) if data is not None else error("aggregate data not found", 404)

    @app.get("/v1/runs")
    def v1_runs():
        try:
            limit = int(request.args.get("limit", "100"))
        except ValueError:
            return error("limit must be an integer", 400)
        if not 1 <= limit <= 1000:
            return error("limit must be between 1 and 1000", 400)
        try:
            records = dashboard_service.runs(
                model=request.args.get("model"),
                effort=request.args.get("effort"),
                status=request.args.get("status"),
                limit=limit,
            )
        except (OSError, json.JSONDecodeError, ValidationError):
            return error("published dashboard runs are unavailable", 503)
        return ok([record.model_dump(mode="json") for record in records])

    @app.get("/")
    def index():
        if app.static_folder is None:
            return {"success": False, "data": None, "error": "web build not found"}, 404
        return send_from_directory(app.static_folder, "index.html")

    return app


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _project_path(path: Path, project_root: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def _is_loopback_address(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _is_allowed_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and (
        parsed.hostname == "localhost" or _is_loopback_address(parsed.hostname)
    )
