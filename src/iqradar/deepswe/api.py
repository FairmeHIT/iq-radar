from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from flask import Blueprint, request

from iqradar.settings.inference import gateway_setting, resolve_inference_endpoint
from iqradar.benchmarks.registry import label as benchmark_label
from iqradar.config.loader import load_model_config
from iqradar.config.schema import ALLOWED_EFFORTS, ModelConfigSet
from iqradar.deepswe.service import DeepSweService, DEFAULT_BENCHMARK
from iqradar.ingest.redact import redact_secret_text
from iqradar.settings.gateway import (
    MODELS_API_KEY_ENV,
    MODELS_BASE_URL_ENV,
)
from iqradar.shared.api_responses import error, ok

DEFAULT_GATEWAY_MODEL_NAME = "gateway/deepseek-v4-flash"

# When set (e.g. from .env), a comma/space-separated model-name list that
# overrides the YAML catalog as the source of the test-page model list.
MODEL_NAMES_ENV = "IQRADAR_MODEL_NAMES"

GATEWAY_MODELS_TIMEOUT_SEC = 15.0

# 网关失败重测轮数上限（API 校验边界；后端缺省值见 api_eval）。
MAX_GATEWAY_RETRY_ROUNDS = 10


def _parse_retry_options(payload: dict) -> tuple[bool, int]:
    """解析「网关失败重测」请求参数：``(retry_gateway_failures, rounds)``。

    - ``retry_gateway_failures``：布尔，缺省 False（不重测）。
    - ``gateway_retry_rounds``：1..10 的整数，缺省 2。

    仅 api-eval 基准实际生效；其余基准接受并忽略（前端也不会展示开关）。
    """
    retry = payload.get("retry_gateway_failures", False)
    if not isinstance(retry, bool):
        raise ValueError("retry_gateway_failures must be a boolean")
    rounds = payload.get("gateway_retry_rounds", 2)
    if (
        isinstance(rounds, bool)
        or not isinstance(rounds, int)
        or not 1 <= rounds <= MAX_GATEWAY_RETRY_ROUNDS
    ):
        raise ValueError(
            f"gateway_retry_rounds must be an integer between 1 and {MAX_GATEWAY_RETRY_ROUNDS}"
        )
    return retry, rounds


def _run_publication(publisher: object | None, run_id: str):
    """该 run 的发布对象（Publication，含 snapshot_id），未发布或不可读返回 None。"""
    if publisher is None:
        return None
    try:
        return publisher.publication_for_job(run_id)
    except (KeyError, OSError, ValueError):
        return None


def _resolve_model_catalog(
    models_path, gateway_settings: object | None = None
) -> tuple[ModelConfigSet | None, str, str | None]:
    """Resolve the test-page model catalog with provenance.

    The 待测模型 picker is driven by the gateway config's 模型列表获取 pair
    (``GET /v1/models``). Priority: 1) the configured gateway (test-page
    settings first, then ``GATEWAY_BASE_URL``); 2) ``IQRADAR_MODEL_NAMES``
    when explicitly set in the environment; 3) the local YAML catalog as a
    visible fallback. Returns ``(config, source, error)`` where source is one
    of ``gateway`` / ``env-names`` / ``yaml`` / ``default`` and error explains
    why the gateway could not be used (None when it succeeded or was not
    configured).
    """
    raw = os.environ.get(MODEL_NAMES_ENV, "").strip()
    if raw:
        names = [part for part in re.split(r"[,\s]+", raw) if part.strip()]
        try:
            return ModelConfigSet.from_env_names(names), "env-names", None
        except ValueError:
            pass  # 空列表：继续按网关发现处理
    base_url, api_key = _models_endpoint(gateway_settings)
    discovery_error: str | None = None
    if base_url:
        discovered, discovery_error = _discover_gateway_models(base_url, api_key)
        if discovered is not None:
            return discovered, "gateway", None
    try:
        return load_model_config(models_path), "yaml", discovery_error
    except (OSError, ValueError):
        return None, "default", discovery_error


def _model_config(models_path, gateway_settings: object | None = None) -> ModelConfigSet | None:
    """Config-only view of :func:`_resolve_model_catalog` (run validation)."""
    return _resolve_model_catalog(models_path, gateway_settings)[0]


def _models_endpoint(gateway_settings: object | None) -> tuple[str, str]:
    """Resolve the (base_url, api_key) pair used to list models.

    Test-page gateway config wins; the dedicated ``IQRADAR_MODELS_*`` env
    vars come next; both fall back to the inference pair (``GATEWAY_*``).
    """
    base_url = (
        gateway_setting(gateway_settings, "models_base_url")
        or os.environ.get(MODELS_BASE_URL_ENV, "").strip()
        or os.environ.get("GATEWAY_BASE_URL", "").strip()
    )
    api_key = (
        gateway_setting(gateway_settings, "models_api_key")
        or os.environ.get(MODELS_API_KEY_ENV, "").strip()
        or os.environ.get("GATEWAY_API_KEY", "").strip()
    )
    return base_url.rstrip("/"), api_key


def _discover_gateway_models(base_url: str, api_key: str) -> tuple[ModelConfigSet | None, str | None]:
    """Fetch the model list from the gateway's ``/v1/models`` endpoint.

    Returns ``(config, error)``; config is None and error explains the
    failure when the endpoint is unreachable or yields no usable model ids.
    Errors are redacted so the bearer key never leaks into the UI.
    """
    if not base_url:
        return None, None
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(f"{base_url}/models", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=GATEWAY_MODELS_TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as caught:
        detail = caught.read().decode("utf-8", errors="replace")[:200]
        return None, redact_secret_text(f"HTTP {caught.code}: {detail}", (api_key,))
    except (OSError, ValueError, json.JSONDecodeError) as caught:
        return None, redact_secret_text(f"{caught.__class__.__name__}: {caught}", (api_key,))
    if not isinstance(payload, dict):
        return None, "unexpected response shape (missing data[])"
    data = payload.get("data")
    if not isinstance(data, list):
        return None, "unexpected response shape (missing data[])"
    ids = [
        str(item["id"]).strip()
        for item in data
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
    ]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return None, "gateway returned no usable model ids"
    try:
        return ModelConfigSet.from_env_names(ids), None
    except ValueError as caught:
        return None, str(caught)


def create_deepswe_blueprint(
    service: DeepSweService,
    *,
    models_path,
    publisher: object = None,
    gateway_settings: object | None = None,
) -> Blueprint:
    blueprint = Blueprint("deepswe", __name__)

    @blueprint.get("/api/models")
    def models():
        try:
            config, source, catalog_error = _resolve_model_catalog(
                models_path, gateway_settings
            )
        except (OSError, ValueError):
            config, source, catalog_error = None, "default", "model catalog failed to load"
        return ok(
            {
                # 待测模型来源：gateway=网关配置的 GET /v1/models（优先）、
                # env-names=IQRADAR_MODEL_NAMES、yaml=本地目录（网关不可达时
                # 的可见回退，error 说明原因）、default=内置默认。
                "source": source,
                "error": catalog_error,
                "models": [_model_choice(model) for model in _model_list(config)],
            }
        )

    @blueprint.get("/api/benchmarks")
    def benchmarks():
        return ok([
            {
                "id": name,
                "type": getattr(backend, "benchmark_type", None),
                "label": benchmark_label(name, getattr(backend, "benchmark_type", None)),
                # 可取样任务池大小（test_tasks 固定子集时为其大小）；前端用它
                # 限制 n_tasks 上限。api-eval 等无池概念的返回 null。
                "task_count": backend.task_count(),
                "default_concurrency": backend.n_concurrent,
                "category": backend.category,
            }
            for name, backend in service.backends.items()
        ])

    @blueprint.post("/api/deepswe-runs")
    def submit_run():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON run request is required", 400)
        model_id = payload.get("model_id")
        n_tasks = payload.get("n_tasks")
        sample_seed = payload.get("sample_seed", 0)
        benchmark = str(payload.get("benchmark", DEFAULT_BENCHMARK))
        effort = str(payload.get("effort", "high"))
        n_concurrent = payload.get("n_concurrent")
        resume_run_id = payload.get("resume_run_id")
        if resume_run_id is not None and (
            not isinstance(resume_run_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", resume_run_id)
        ):
            return error("resume_run_id must be a run id string", 400)
        if n_concurrent is not None and (
            isinstance(n_concurrent, bool)
            or not isinstance(n_concurrent, int)
            or not 1 <= n_concurrent <= 64
        ):
            return error("n_concurrent must be an integer between 1 and 64", 400)
        if _model_id(model_id) is None:
            return error("valid model_id is required", 400)
        if effort not in ALLOWED_EFFORTS:
            return error("effort must be one of low, medium, high, max", 400)
        if benchmark not in service.backends:
            return error(f"unknown benchmark: {benchmark}", 400)
        max_tasks = service.backends[benchmark].max_tasks()
        if n_tasks is None:
            n_tasks = max_tasks
        if isinstance(n_tasks, bool) or not isinstance(n_tasks, int) or not 1 <= n_tasks <= max_tasks:
            return error(f"n_tasks must be an integer between 1 and {max_tasks}", 400)
        if isinstance(sample_seed, bool) or not isinstance(sample_seed, int) or not 0 <= sample_seed <= 2**31:
            return error("sample_seed must be a non-negative integer", 400)
        try:
            retry_gateway_failures, gateway_retry_rounds = _parse_retry_options(payload)
        except ValueError as caught:
            return error(str(caught), 400)
        if resume_run_id:
            # Fast feedback for the resume path: the backend re-validates the
            # job dir and model when the run executes; here we only refuse
            # ids that could never resume on the selected benchmark.
            if not service.backends[benchmark].has_partial_results(resume_run_id):
                return error(
                    f"run '{resume_run_id}' has no resumable partial results "
                    f"on benchmark '{benchmark}'",
                    409,
                )
        try:
            config = _model_config(models_path, gateway_settings)
        except (OSError, ValueError):
            config = None
        model = None
        if config is not None:
            try:
                model = config.get(model_id)
            except KeyError:
                model = None
        if model is None:
            return error("model is not in the configured catalog", 400)
        # Resolve the endpoint from the *selected* backend (not the default
        # DeepSWE backend), so api-eval records hash the gateway URL they
        # actually used rather than the deep-swe container bridge address.
        # The test page's configured 模型调用 baseurl wins when set — pier
        # runs then use the same endpoint (see DeepSweBackend overrides).
        base_url = (
            resolve_inference_endpoint(gateway_settings).base_url
            or service.backends[benchmark].base_url()
        )
        try:
            run = service.submit(
                model_id=model.id,
                model_name=_pier_model_name(model.effective_model_name()),
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                base_url=base_url,
                benchmark=benchmark,
                effort=effort,
                n_concurrent=n_concurrent,
                resume_run_id=resume_run_id,
                retry_gateway_failures=retry_gateway_failures,
                gateway_retry_rounds=gateway_retry_rounds,
            )
        except ValueError as caught:
            return error(str(caught), 409)
        return ok(run.as_dict()), 202

    @blueprint.post("/api/deepswe-batches")
    def submit_batch():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON batch request is required", 400)
        model_ids = payload.get("model_ids")
        n_tasks = payload.get("n_tasks")
        sample_seed = payload.get("sample_seed", 0)
        benchmark = str(payload.get("benchmark", DEFAULT_BENCHMARK))
        effort = str(payload.get("effort", "high"))
        n_concurrent = payload.get("n_concurrent")
        max_concurrent = payload.get("max_concurrent", 1)
        if n_concurrent is not None and (
            isinstance(n_concurrent, bool)
            or not isinstance(n_concurrent, int)
            or not 1 <= n_concurrent <= 64
        ):
            return error("n_concurrent must be an integer between 1 and 64", 400)
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or not 1 <= max_concurrent <= 16:
            return error("max_concurrent must be an integer between 1 and 16", 400)
        if not isinstance(model_ids, list) or not model_ids:
            return error("model_ids list is required", 400)
        if len(model_ids) > 20:
            return error("at most 20 models per batch", 400)
        if effort not in ALLOWED_EFFORTS:
            return error("effort must be one of low, medium, high, max", 400)
        if benchmark not in service.backends:
            return error(f"unknown benchmark: {benchmark}", 400)
        max_tasks = service.backends[benchmark].max_tasks()
        if n_tasks is None:
            n_tasks = max_tasks
        if isinstance(n_tasks, bool) or not isinstance(n_tasks, int) or not 1 <= n_tasks <= max_tasks:
            return error(f"n_tasks must be an integer between 1 and {max_tasks}", 400)
        if isinstance(sample_seed, bool) or not isinstance(sample_seed, int) or not 0 <= sample_seed <= 2**31:
            return error("sample_seed must be a non-negative integer", 400)
        try:
            retry_gateway_failures, gateway_retry_rounds = _parse_retry_options(payload)
        except ValueError as caught:
            return error(str(caught), 400)
        try:
            config = _model_config(models_path, gateway_settings)
        except (OSError, ValueError):
            config = None
        model_names: list[str] = []
        for model_id in model_ids:
            if _model_id(model_id) is None:
                return error("valid model_id is required", 400)
            model = None
            if config is not None:
                try:
                    model = config.get(model_id)
                except KeyError:
                    model = None
            if model is None:
                return error(f"model is not in the configured catalog: {model_id}", 400)
            model_names.append(_pier_model_name(model.effective_model_name()))
        try:
            batch = service.submit_batch(
                model_ids=model_ids,
                model_names=model_names,
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                benchmark=benchmark,
                effort=effort,
                n_concurrent=n_concurrent,
                max_concurrent=max_concurrent,
                retry_gateway_failures=retry_gateway_failures,
                gateway_retry_rounds=gateway_retry_rounds,
            )
        except ValueError as caught:
            return error(str(caught), 409)
        return ok(batch), 202

    @blueprint.post("/api/deepswe-batches/<batch_id>/cancel")
    def cancel_batch(batch_id: str):
        if service.get_batch(batch_id) is None:
            return error("deep-swe batch not found", 404)
        if not service.cancel_batch(batch_id):
            return error("deep-swe batch is not active", 409)
        # Return the post-cancel state so the client can sync immediately
        # instead of relying on the next poll.
        return ok(service.get_batch(batch_id))

    @blueprint.post("/api/deepswe-batches/<batch_id>/resume")
    def resume_batch(batch_id: str):
        if service.get_batch(batch_id) is None:
            return error("deep-swe batch not found", 404)
        try:
            batch = service.resume_batch(batch_id)
        except ValueError as caught:
            return error(str(caught), 409)
        if batch is None:
            return error("deep-swe batch is not resumable", 409)
        return ok(batch), 202

    @blueprint.get("/api/deepswe-batches/latest")
    def latest_batch():
        batch = service.latest_batch()
        return ok(batch)

    @blueprint.get("/api/deepswe-batches/<batch_id>")
    def batch_status(batch_id: str):
        batch = service.get_batch(batch_id)
        return ok(batch) if batch else error("deep-swe batch not found", 404)

    @blueprint.get("/api/deepswe-runs/<run_id>")
    def run_status(run_id: str):
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        # 附带题目进度（total/completed），驱动测试页「评测进程」与日志面板
        # 的进度条；未知进度时为 null，前端回退展示任务总数。
        return ok({
            **run.as_dict(),
            "progress": service.progress(run_id),
            "resumable_partial_results": service.resumable_partial_results(run),
        })

    @blueprint.get("/api/deepswe-runs/latest")
    def latest_run():
        run = service.latest()
        if run is None:
            return ok(None)
        return ok({
            **run.as_dict(),
            "progress": service.progress(run.run_id),
            "resumable_partial_results": service.resumable_partial_results(run),
        })

    @blueprint.get("/api/deepswe-runs")
    def list_runs():
        # 测试页「评测记录」列表：返回全部持久化 run（新→旧），供筛选与
        # 清理无效结果。active run 先做一次 reconcile，让重启后遗留的
        # queued/running 反映真实终态。每条附带发布状态（snapshot_id），
        # 评测记录行据此显示「发布 / 回撤」按钮。
        runs = service.list_runs()
        payload = []
        for run in runs:
            entry = run.as_dict()
            publication = _run_publication(publisher, run.run_id)
            entry["snapshot_id"] = (
                publication.snapshot_id if publication is not None else None
            )
            entry["resumable_partial_results"] = service.resumable_partial_results(run)
            retryable_count = service.retryable_infrastructure_failure_count(run)
            if retryable_count is not None:
                entry["retryable_infrastructure_failure_count"] = retryable_count
            api_metrics = service.run_api_metrics(run)
            if api_metrics is not None:
                entry["api_metrics"] = api_metrics
            payload.append(entry)
        return ok(payload)

    @blueprint.get("/api/deepswe-runs/<run_id>/records")
    def run_records(run_id: str):
        if service.get(run_id) is None:
            return error("deep-swe run not found", 404)
        return ok([record.model_dump(mode="json") for record in service.records(run_id)])

    @blueprint.get("/api/deepswe-runs/<run_id>/evaluation-report")
    def evaluation_report(run_id: str):
        """Return the report-ready question outcomes for one run."""
        if _run_id(run_id) is None:
            return error("invalid run id", 400)
        if service.get(run_id) is None:
            return error("deep-swe run not found", 404)
        try:
            report = service.evaluation_report(run_id)
        except (OSError, ValueError, json.JSONDecodeError, TypeError, KeyError):
            return error("evaluation report is unavailable", 503)
        return ok(report) if report is not None else error("deep-swe run not found", 404)

    @blueprint.delete("/api/deepswe-runs/<run_id>")
    def delete_run(run_id: str):
        if _run_id(run_id) is None:
            return error("invalid run id", 400)
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        if run.status in {"queued", "running"}:
            return error("active run cannot be deleted; cancel it first", 409)
        service.delete_run(run_id, publisher=publisher)
        return ok({"run_id": run_id, "deleted": True})

    @blueprint.post("/api/deepswe-runs/delete-batch")
    def delete_runs_batch():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON delete request is required", 400)
        run_ids = payload.get("run_ids")
        if not isinstance(run_ids, list) or not run_ids:
            return error("run_ids list is required", 400)
        if len(run_ids) > 500:
            return error("at most 500 runs per delete-batch", 400)
        clean: list[str] = []
        for value in run_ids:
            run_id = _run_id(value)
            if run_id is None:
                return error("invalid run id in run_ids", 400)
            clean.append(run_id)
        return ok(service.delete_runs(clean, publisher=publisher))

    @blueprint.post("/api/deepswe-runs/<run_id>/cancel")
    def cancel_run(run_id: str):
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        if run.status not in {"queued", "running"}:
            return error("deep-swe run is not active", 409)
        if not service.cancel(run_id):
            return error("deep-swe run could not be cancelled", 409)
        return ok(run.as_dict())

    @blueprint.post("/api/deepswe-runs/<run_id>/retry-gateway-failures")
    def retry_run_gateway_failures(run_id: str):
        """重测该 run 中因网关超时/临时错误失败的题目（模型答错不重测）。

        仅 api-eval run 支持。按目录解析模型名（model_id→model_name）后
        交给 service 起线程重测一轮；run 置 running，结束后自动重发布
        （若此前已发布）。返回最新的 run 状态（202）。
        """
        if _run_id(run_id) is None:
            return error("invalid run id", 400)
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        backend = service.backends.get(run.benchmark or DEFAULT_BENCHMARK)
        if getattr(backend, "benchmark_type", None) != "api-eval":
            return error("retry only supported for api-eval runs", 409)
        if run.status not in {"completed", "failed"}:
            return error("only finished runs can be retried", 409)
        config = _model_config(models_path, gateway_settings)
        model = _resolve_run_model(config, run.model_id)
        if model is None:
            return error(
                f"model '{run.model_id}' is no longer in the catalog; cannot retry",
                409,
            )
        try:
            updated = service.retry_run_gateway_failures(
                run_id,
                model_name=_pier_model_name(model.effective_model_name()),
                publisher=publisher,
            )
        except ValueError as caught:
            return error(str(caught), 409)
        if updated is None:
            return error("deep-swe run not found", 404)
        return ok(updated.as_dict()), 202

    @blueprint.get("/api/deepswe-runs/<run_id>/questions")
    def run_questions(run_id: str):
        """逐题结果列表（仅 api-eval run），供评测记录的题目级重测。"""
        if _run_id(run_id) is None:
            return error("invalid run id", 400)
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        backend = service.backends.get(run.benchmark or DEFAULT_BENCHMARK)
        if getattr(backend, "benchmark_type", None) != "api-eval":
            return error("questions only available for api-eval runs", 409)
        try:
            questions = service.run_questions(run_id)
        except ValueError as caught:
            return error(str(caught), 409)
        return ok({"run_id": run_id, "questions": questions})

    @blueprint.post("/api/deepswe-runs/<run_id>/retry-questions")
    def retry_run_questions(run_id: str):
        """重测指定题目（单题或全部；不筛失败类型，传哪题重跑哪题）。

        仅 api-eval、已完成/失败且当前无活跃 run 时可用。按目录解析模型名
        后交给 service 起线程重测；run 置 running，结束后若此前已发布会自
        动重发布。返回最新的 run 状态（202）。
        """
        if _run_id(run_id) is None:
            return error("invalid run id", 400)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON payload is required", 400)
        task_ids = payload.get("task_ids")
        if (
            not isinstance(task_ids, list)
            or not task_ids
            or len(task_ids) > 500
            or any(not isinstance(t, str) or not t.strip() for t in task_ids)
        ):
            return error("task_ids must be a non-empty list of task ids", 400)
        clean_ids = [t.strip() for t in dict.fromkeys(task_ids)]
        run = service.get(run_id)
        if run is None:
            return error("deep-swe run not found", 404)
        backend = service.backends.get(run.benchmark or DEFAULT_BENCHMARK)
        if getattr(backend, "benchmark_type", None) != "api-eval":
            return error("retry only supported for api-eval runs", 409)
        if run.status not in {"completed", "failed"}:
            return error("only finished runs can be retried", 409)
        config = _model_config(models_path, gateway_settings)
        model = _resolve_run_model(config, run.model_id)
        if model is None:
            return error(
                f"model '{run.model_id}' is no longer in the catalog; cannot retry",
                409,
            )
        try:
            updated = service.retry_run_questions(
                run_id,
                model_name=_pier_model_name(model.effective_model_name()),
                task_ids=clean_ids,
                publisher=publisher,
            )
        except ValueError as caught:
            return error(str(caught), 409)
        if updated is None:
            return error("deep-swe run not found", 404)
        return ok(updated.as_dict()), 202

    @blueprint.get("/api/deepswe-runs/<run_id>/logs")
    def run_logs(run_id: str):
        if service.get(run_id) is None:
            return error("deep-swe run not found", 404)
        sources = service.log_sources(run_id)
        # 日志轮询（3s）比 run 状态轮询（1s）活得久：run 结束后状态轮询停了，
        # 日志响应里带上进度让面板保持最终进度展示。
        progress = service.progress(run_id)
        source = request.args.get("source")
        if source is None:
            return ok(
                {
                    "sources": sources,
                    "selected": None,
                    "content": "",
                    "progress": progress,
                }
            )
        if source is not None and not any(s["name"] == source for s in sources):
            return error("invalid log source", 400)
        try:
            tail = int(request.args.get("tail", "2000"))
        except ValueError:
            tail = 2000
        return ok(
            {
                "sources": sources,
                "selected": source,
                "progress": progress,
                **service.log_content(run_id, source, tail=tail),
            }
        )

    # ── 多基准并发：多个 api-eval bench 同时跑，上限 N 个并发 ──────────
    @blueprint.post("/api/deepswe-multi-batches")
    def submit_multi_bench():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("JSON multi-bench request is required", 400)
        items = payload.get("items")
        max_concurrent = payload.get("max_concurrent")
        n_tasks = payload.get("n_tasks")
        sample_seed = payload.get("sample_seed", 0)
        effort = str(payload.get("effort", "high"))
        n_concurrent = payload.get("n_concurrent")
        if not isinstance(items, list) or not items:
            return error("items list is required", 400)
        if len(items) > 200:
            return error("at most 200 items per multi-bench", 400)
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or not 1 <= max_concurrent <= 16:
            return error("max_concurrent must be an integer between 1 and 16", 400)
        if effort not in ALLOWED_EFFORTS:
            return error("effort must be one of low, medium, high, max", 400)
        if n_concurrent is not None and (
            isinstance(n_concurrent, bool)
            or not isinstance(n_concurrent, int)
            or not 1 <= n_concurrent <= 64
        ):
            return error("n_concurrent must be an integer between 1 and 64", 400)
        try:
            config = _model_config(models_path, gateway_settings)
        except (OSError, ValueError):
            config = None
        resolved_items: list[dict[str, str]] = []
        max_tasks_ceiling = None
        for entry in items:
            if not isinstance(entry, dict):
                return error("each item must be an object", 400)
            benchmark = str(entry.get("benchmark", ""))
            model_id = entry.get("model_id")
            if benchmark not in service.backends:
                return error(f"unknown benchmark: {benchmark}", 400)
            if service.backends[benchmark].category != "api-eval":
                return error(
                    f"benchmark '{benchmark}' is not api-eval; multi-bench is api-eval only",
                    400,
                )
            if _model_id(model_id) is None:
                return error("valid model_id is required", 400)
            model = None
            if config is not None:
                try:
                    model = config.get(str(model_id))
                except KeyError:
                    model = None
            if model is None:
                return error(f"model is not in the configured catalog: {model_id}", 400)
            # 每项可选独立 n_tasks；缺省评测该基准当前可用题池全量。
            bench_max = service.backends[benchmark].max_tasks()
            item_n_tasks = entry.get("n_tasks")
            if item_n_tasks is None:
                item_n_tasks = n_tasks if n_tasks is not None else bench_max
            elif isinstance(item_n_tasks, bool) or not isinstance(item_n_tasks, int):
                return error("each item's n_tasks must be an integer", 400)
            if not 1 <= item_n_tasks <= bench_max:
                return error(
                    f"benchmark '{benchmark}' n_tasks must be an integer "
                    f"between 1 and {bench_max}",
                    400,
                )
            resolved_items.append(
                {
                    "benchmark": benchmark,
                    "model_id": model.id,
                    "model_name": _pier_model_name(model.effective_model_name()),
                    "n_tasks": item_n_tasks,
                }
            )
            max_tasks_ceiling = bench_max if max_tasks_ceiling is None else min(max_tasks_ceiling, bench_max)
        if n_tasks is None:
            n_tasks = max_tasks_ceiling
        if isinstance(n_tasks, bool) or not isinstance(n_tasks, int) or not 1 <= n_tasks <= max_tasks_ceiling:
            return error(f"n_tasks must be an integer between 1 and {max_tasks_ceiling}", 400)
        if isinstance(sample_seed, bool) or not isinstance(sample_seed, int) or not 0 <= sample_seed <= 2**31:
            return error("sample_seed must be a non-negative integer", 400)
        try:
            retry_gateway_failures, gateway_retry_rounds = _parse_retry_options(payload)
        except ValueError as caught:
            return error(str(caught), 400)
        try:
            batch = service.submit_multi_bench(
                items=resolved_items,
                max_concurrent=max_concurrent,
                n_tasks=n_tasks,
                sample_seed=sample_seed,
                effort=effort,
                n_concurrent=n_concurrent,
                retry_gateway_failures=retry_gateway_failures,
                gateway_retry_rounds=gateway_retry_rounds,
            )
        except ValueError as caught:
            return error(str(caught), 409)
        return ok(batch), 202

    @blueprint.post("/api/deepswe-multi-batches/<batch_id>/cancel")
    def cancel_multi_batch(batch_id: str):
        if service.get_multi_batch(batch_id) is None:
            return error("multi-bench batch not found", 404)
        if not service.cancel_multi_batch(batch_id):
            return error("multi-bench batch is not active", 409)
        return ok(service.get_multi_batch(batch_id))

    @blueprint.post("/api/deepswe-multi-batches/<batch_id>/resume")
    def resume_multi_batch(batch_id: str):
        if service.get_multi_batch(batch_id) is None:
            return error("multi-bench batch not found", 404)
        try:
            batch = service.resume_multi_batch(batch_id)
        except ValueError as caught:
            return error(str(caught), 409)
        if batch is None:
            return error("multi-bench batch is not resumable", 409)
        return ok(batch), 202

    @blueprint.get("/api/deepswe-multi-batches/latest")
    def latest_multi_batch():
        return ok(service.latest_multi_batch())

    @blueprint.get("/api/deepswe-multi-batches/<batch_id>")
    def multi_batch_status(batch_id: str):
        batch = service.get_multi_batch(batch_id)
        return ok(batch) if batch else error("multi-bench batch not found", 404)

    return blueprint


def _model_list(config: object) -> list[object]:
    if config is None:
        return []
    models = getattr(config, "models", [])
    if not models:
        models = [_default_model()]
    return list(models)


def _resolve_run_model(config, model_id: str):
    """从目录按 id 解析单个模型（重测时用），不在目录返回 None。"""
    if config is None:
        return None
    try:
        return config.get(model_id)
    except KeyError:
        return None


def _model_choice(model: object) -> dict[str, object]:
    display_name = getattr(model, "display_name", None) or getattr(model, "id")
    efforts = list(getattr(getattr(model, "effort", None), "values", {}) or {})
    resolved_name = _resolved_model_name(model)
    provider = (
        resolved_name.split("/", 1)[0]
        if resolved_name and "/" in resolved_name
        else "gateway"
    )
    return {
        "id": getattr(model, "id"),
        "display_name": display_name,
        "provider": provider,
        "label": display_name,
        "efforts": efforts or ["low", "medium", "high", "max"],
    }


def _resolved_model_name(model: object) -> str | None:
    fn = getattr(model, "effective_model_name", None)
    if not callable(fn):
        return None
    try:
        value = fn({})
    except (KeyError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _default_model():
    from iqradar.config.schema import EffortConfig, ModelConfig, ModelEnvConfig

    return ModelConfig(
        id="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        provider="openai-compatible",
        model_name=DEFAULT_GATEWAY_MODEL_NAME,
        env=ModelEnvConfig(base_url="GATEWAY_BASE_URL"),
        effort=EffortConfig(supported=False, values={}),
    )


def _pier_model_name(model_name: str) -> str:
    if model_name.startswith("openai/"):
        return model_name
    return f"openai/{model_name}"


def _model_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", candidate):
        return candidate
    return None


def _run_id(value: object) -> str | None:
    """Validate a deep-swe run id (uuid4().hex shape; no path traversal)."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    return candidate if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", candidate) else None
