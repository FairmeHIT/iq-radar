from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from iqradar.api.app import create_app
from iqradar.schemas.run_record import RunRecord


def run_record(task_id: str, status: str = "passed") -> RunRecord:
    return RunRecord.model_validate(
        {
            "run_id": f"{task_id}__model-a__high",
            "benchmark": {
                "name": "deep-swe",
                "version": "local",
                "task_id": task_id,
                "repo": "owner/repo",
                "language": "python",
                "task_path": f"tasks/{task_id}",
            },
            "model": {
                "provider": "openai-compatible",
                "base_url_hash": "sha256:test",
                "name": "model-a",
                "effort_requested": "high",
                "effort_effective": False,
            },
            "result": {
                "status": status,
                "verifier_passed": status == "passed",
                "exit_code": 0,
            },
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cached_input_tokens": 0,
                "agent_steps": 3,
                "wall_time_sec": 60,
            },
            "cost": {
                "currency": "USD",
                "input_cost": 0,
                "cached_input_cost": 0,
                "output_cost": 0,
                "total_cost": 0,
            },
            "artifacts": {
                "patch_path": None,
                "log_path": "data/artifacts/run.log",
                "verifier_path": None,
            },
            "created_at": datetime(2026, 8, 3, tzinfo=UTC),
        }
    )


def write_summary(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_at": "2026-08-03T13:00:00Z",
                "summaries": [
                    {
                        "benchmark": {"name": "deep-swe", "version": "local"},
                        "model": "model-a",
                        "effort": "high",
                        "tasks_total": 2,
                        "tasks_passed": 1,
                        "tasks_failed": 1,
                        "pass_rate": 0.5,
                        "pass_rate_percent": 50.0,
                        "iq": 75.0,
                        "avg_cost_usd": 0.01,
                        "total_cost_usd": 0.02,
                        "avg_wall_time_sec": 60.0,
                        "tasks_per_hour": 60.0,
                        "avg_input_tokens": 1000.0,
                        "avg_output_tokens": 500.0,
                        "output_tokens_per_min": 500.0,
                        "avg_agent_steps": 3.0,
                        "agent_steps_per_hour": 180.0,
                        "cost_per_pass_usd": 0.02,
                        "cost_per_iq_point_usd": 0.000266,
                        "quota_percent_per_task": 0.05,
                        "estimated_tasks_per_week": 2000,
                        "estimated_passes_per_week": 1000,
                        "output_quota_percent_per_task": None,
                        "confidence": {
                            "method": "wilson",
                            "level": 0.95,
                            "lower": 0.0945,
                            "upper": 0.9055,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def write_runs(path: Path) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        run_record("task-a", "passed").model_dump_json() + "\n"
        + run_record("task-b", "failed").model_dump_json() + "\n",
        encoding="utf-8",
    )


def write_prices(path: Path) -> None:
    path.write_text(
        """prices:
  model-a:
    currency: USD
    input_usd_per_1m: 0.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 0.0
  deepseek-v4-flash:
    currency: USD
    input_usd_per_1m: 0.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 0.0
quota:
  weekly_budget_usd: 20.0
  weekly_output_token_budget: null
""",
        encoding="utf-8",
    )


def write_models(path: Path) -> None:
    path.write_text(
        """models:
  - id: deepseek-v4-flash
    display_name: DeepSeek V4 Flash
    provider: openai-compatible
    model_name: deepseek/deepseek-v4-flash
    env:
      base_url: GATEWAY_BASE_URL
    effort:
      supported: true
      parameter_path: reasoning_effort
      values:
        high: high
        max: max
""",
        encoding="utf-8",
    )


def write_benchmark(tmp_path: Path) -> Path:
    path = tmp_path / "configs" / "benchmark.yaml"
    path.parent.mkdir(parents=True)
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)  # preflight: real dataset
    path.write_text(
        f"""benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: {tmp_path / "deep-swe"}
    tasks_path: {tmp_path / "tasks"}
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: {tmp_path / "jobs"}
""",
        encoding="utf-8",
    )
    return path


def write_deepswe_env(tmp_path: Path) -> None:
    deep_swe_dir = tmp_path / "deep-swe"
    deep_swe_dir.mkdir(parents=True, exist_ok=True)
    (deep_swe_dir / ".env").write_text(
        "OPENAI_BASE_URL=https://gateway.example.com/v1\nOPENAI_API_KEY=sk-secret\n",
        encoding="utf-8",
    )


def app_with(tmp_path: Path, **overrides) -> object:
    write_prices(tmp_path / "prices.yaml")
    write_models(tmp_path / "models.yaml")
    benchmark = write_benchmark(tmp_path)
    write_deepswe_env(tmp_path)
    options = {
        "data_path": tmp_path / "aggregate" / "radar.json",
        "models_path": tmp_path / "models.yaml",
        "benchmark_path": benchmark,
        "prices_path": tmp_path / "prices.yaml",
        "deepswe_runs_path": tmp_path / "deepswe-runs",
        "reporting_path": tmp_path / "reporting",
        # 隔离测试页网关配置存储，避免读到/写入真实 data/settings/gateway.json
        "gateway_settings_path": tmp_path / "settings" / "gateway.json",
    }
    options.update(overrides)
    return create_app(**options)


def test_health_reports_missing_data_path(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.get("/health/readiness")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["service"] == "iq_radar"


def test_openapi_and_docs_are_available(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    docs_response = client.get("/docs")
    openapi_response = client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    assert openapi_response.get_json()["info"]["title"] == "IQRadar API"


def test_index_serves_dashboard_build_when_present(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"IQRadar" in response.data


def test_summary_returns_enveloped_aggregate(tmp_path: Path) -> None:
    summary_path = tmp_path / "aggregate" / "radar.json"
    write_summary(summary_path)
    client = app_with(tmp_path, data_path=summary_path).test_client()

    response = client.get("/api/summary")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["summaries"][0]["iq"] == 75.0


def test_radar_endpoints_return_normalized_axes(tmp_path: Path) -> None:
    summary_path = tmp_path / "aggregate" / "radar.json"
    write_summary(summary_path)
    client = app_with(tmp_path, data_path=summary_path).test_client()

    iq_payload = client.get("/api/radar/iq").get_json()
    quota_payload = client.get("/api/radar/quota").get_json()

    assert iq_payload["success"] is True
    assert iq_payload["data"][0]["axes"]["iq"] == 50.0
    assert iq_payload["data"][0]["axes"]["pass_rate"] == 50.0
    assert quota_payload["data"][0]["axes"]["quota_remaining_friendliness"] == 99.95


def test_runs_endpoint_filters_and_redacts_records(tmp_path: Path) -> None:
    summary_path = tmp_path / "aggregate" / "radar.json"
    write_summary(summary_path)
    raw_path = tmp_path / "raw" / "runs" / "runs.jsonl"
    write_runs(raw_path)
    client = app_with(tmp_path, data_path=summary_path, raw_runs_path=raw_path.parent).test_client()

    response = client.get("/api/runs?status=passed&limit=1")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert len(payload["data"]) == 1
    assert payload["data"][0]["result"]["status"] == "passed"
    assert "base_url_hash" in payload["data"][0]["model"]


def test_dashboard_bundle_pins_summary_radar_and_runs_to_one_snapshot(tmp_path: Path) -> None:
    summary_path = tmp_path / "aggregate" / "radar.json"
    raw_path = tmp_path / "raw" / "runs" / "runs.jsonl"
    write_summary(summary_path)
    write_runs(raw_path)
    client = app_with(tmp_path, data_path=summary_path, raw_runs_path=raw_path.parent).test_client()

    response = client.get("/api/dashboard")
    data = response.get_json()["data"]

    assert response.status_code == 200
    assert data["snapshot_id"] == "legacy"
    assert data["summary"]["summaries"][0]["iq"] == 75.0
    assert data["iq_radar"][0]["model"] == "model-a"
    assert {run["benchmark"]["task_id"] for run in data["runs"]} == {"task-a", "task-b"}


def test_default_dashboard_ignores_legacy_aggregate_file(tmp_path: Path) -> None:
    """生产默认大盘只看发布快照；旧 aggregate 文件不再制造残留数据。"""
    write_summary(tmp_path / "aggregate" / "radar.json")
    write_runs(tmp_path / "raw" / "runs" / "runs.jsonl")
    write_prices(tmp_path / "prices.yaml")
    write_models(tmp_path / "models.yaml")
    benchmark = write_benchmark(tmp_path)
    write_deepswe_env(tmp_path)
    client = create_app(
        models_path=tmp_path / "models.yaml",
        benchmark_path=benchmark,
        prices_path=tmp_path / "prices.yaml",
        deepswe_runs_path=tmp_path / "deepswe-runs",
        reporting_path=tmp_path / "reporting",
        gateway_settings_path=tmp_path / "settings" / "gateway.json",
    ).test_client()

    response = client.get("/api/dashboard")

    assert response.status_code == 404
    assert response.get_json()["error"] == "aggregate data not found"


def test_model_catalog_is_loaded_from_config(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["source"] == "yaml"
    assert payload["error"] is None
    assert [model["id"] for model in payload["models"]] == ["deepseek-v4-flash"]
    assert payload["models"][0]["display_name"] == "DeepSeek V4 Flash"


def test_model_catalog_can_come_from_env_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "IQRADAR_MODEL_NAMES", "gateway/deepseek-v4-flash, z.ai/glm-5.2"
    )
    client = app_with(tmp_path).test_client()

    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["source"] == "env-names"
    assert payload["error"] is None
    assert [model["id"] for model in payload["models"]] == [
        "gateway/deepseek-v4-flash",
        "z.ai/glm-5.2",
    ]
    assert payload["models"][0]["label"] == "gateway/deepseek-v4-flash"
    assert payload["models"][0]["provider"] == "gateway"


def test_model_catalog_discovered_from_gateway_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-test")

    captured: dict[str, object] = {}

    class FakeResponse:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self._body

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["authorization"] = dict(request.headers).get("Authorization")
        body = json.dumps(
            {"data": [{"id": "gateway/deepseek-v4-flash"}, {"id": "gateway/glm-5.2"}]}
        ).encode("utf-8")
        return FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = app_with(tmp_path).test_client()
    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["source"] == "gateway"
    assert payload["error"] is None
    assert [model["id"] for model in payload["models"]] == [
        "gateway/deepseek-v4-flash",
        "gateway/glm-5.2",
    ]
    assert captured["url"] == "http://gateway.example/v1/models"
    assert captured["authorization"] == "Bearer sk-test"


def test_model_catalog_falls_back_to_yaml_when_gateway_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.example/v1")

    def failing_urlopen(request, timeout=None) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)

    client = app_with(tmp_path).test_client()
    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    # 网关不可达：可见回退到本地目录，error 说明原因供前端提示
    assert payload["source"] == "yaml"
    assert "connection refused" in payload["error"]
    assert [model["id"] for model in payload["models"]] == ["deepseek-v4-flash"]


def test_deepswe_run_submission_accepts_env_model_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IQRADAR_MODEL_NAMES", "gateway/deepseek-v4-flash")
    client = app_with(tmp_path).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "gateway/deepseek-v4-flash", "n_tasks": 1},
    )

    # The run is submitted (202) and records the env-provided model name.
    assert response.status_code == 202
    run = response.get_json()["data"]
    assert run["model_id"] == "gateway/deepseek-v4-flash"


def test_deepswe_run_submission_validates_request(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    assert client.post("/api/deepswe-runs", json={"model_id": "nope", "n_tasks": 5}).status_code == 400
    assert client.post("/api/deepswe-runs", json={"model_id": "deepseek-v4-flash", "n_tasks": 0}).status_code == 400
    assert client.post("/api/deepswe-runs", json={"model_id": "deepseek-v4-flash", "n_tasks": 500}).status_code == 400


def test_deepswe_run_submission_starts_a_run_and_reports_status(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 5, "sample_seed": 0},
    )

    assert response.status_code == 202
    run = response.get_json()["data"]
    assert run["status"] in {"queued", "running", "failed"}
    assert run["model_id"] == "deepseek-v4-flash"
    assert run["n_tasks"] == 5
    assert run["sample_seed"] == 0
    assert client.get(f"/api/deepswe-runs/{run['run_id']}").status_code == 200
    assert client.get("/api/deepswe-runs/latest").get_json()["data"]["run_id"] == run["run_id"]


def test_run_status_and_logs_include_question_progress(tmp_path: Path) -> None:
    """题目进度随 run 状态与日志响应下发（总共 N 题 / 已完成 M 题）。"""
    client = app_with(tmp_path).test_client()
    run_id = "runprogress0000000000000000000000ff"
    runs_root = tmp_path / "deepswe-runs" / run_id
    runs_root.mkdir(parents=True)
    (runs_root / "state.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "running",
                "model_id": "deepseek-v4-flash",
                "n_tasks": 5,
                "sample_seed": 0,
                "base_url": "",
                "created_at": datetime.now(UTC).isoformat(),
                "completed_at": None,
                "error": None,
                "jobs_dir": None,
                "benchmark": "deep-swe",
                "effort": "high",
                "n_concurrent": None,
            }
        ),
        encoding="utf-8",
    )

    # 无任何产物（如 Docker 环境构建阶段）：活跃 run 回退 0/N
    status = client.get(f"/api/deepswe-runs/{run_id}").get_json()["data"]
    assert status["progress"] == {"total": 5, "completed": 0}
    latest = client.get("/api/deepswe-runs/latest").get_json()["data"]
    assert latest["progress"] == {"total": 5, "completed": 0}

    # pier 的 job 级 result.json 出现后：进度来自其统计（含运行中/待处理）
    job_result = tmp_path / "jobs" / run_id / "result.json"
    job_result.parent.mkdir(parents=True, exist_ok=True)
    job_result.write_text(
        json.dumps(
            {
                "finished_at": None,
                "n_total_trials": 5,
                "stats": {
                    "n_completed_trials": 2,
                    "n_errored_trials": 1,
                    "n_running_trials": 1,
                    "n_pending_trials": 2,
                },
            }
        ),
        encoding="utf-8",
    )

    status = client.get(f"/api/deepswe-runs/{run_id}").get_json()["data"]
    assert status["progress"] == {
        "total": 5,
        "completed": 2,
        "running": 1,
        "pending": 2,
    }
    logs = client.get(f"/api/deepswe-runs/{run_id}/logs").get_json()["data"]
    assert logs["progress"] == {
        "total": 5,
        "completed": 2,
        "running": 1,
        "pending": 2,
    }


def test_deepswe_run_submission_records_n_concurrent(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "n_concurrent": 4},
    )

    assert response.status_code == 202
    run = response.get_json()["data"]
    assert run["n_concurrent"] == 4


def test_deepswe_run_submission_rejects_bad_n_concurrent(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    assert client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "n_concurrent": 0},
    ).status_code == 400
    assert client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "n_concurrent": 65},
    ).status_code == 400


def test_run_submission_rejects_unresumable_run_id(tmp_path: Path) -> None:
    """resume_run_id 指向无可续结果的 run（或基准不支持续跑）时 409。"""
    client = app_with(tmp_path).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={
            "model_id": "deepseek-v4-flash",
            "n_tasks": 1,
            "resume_run_id": "does-not-exist",
        },
    )

    assert response.status_code == 409
    assert "no resumable partial results" in response.get_json()["error"]


def test_run_submission_forwards_resume_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """terminal-bench-2 提交携带 resume_run_id 时透传给后端（harbor job resume）。"""
    from iqradar.benchmarks.terminal_bench_2 import TerminalBench2Backend

    tasks = tmp_path / "tasks-tb2"
    (tasks / "music-harmony").mkdir(parents=True)
    (tasks / "music-harmony" / "task.toml").write_text(
        'version = "1.0"\n', encoding="utf-8"
    )
    jobs = tmp_path / "jobs-tb2"
    trial = jobs / "old-run" / "music-harmony__abc1234"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text("{}", encoding="utf-8")
    (jobs / "old-run" / "config.json").write_text(
        json.dumps(
            {"agents": [{"name": "mini-swe-agent", "model_name": "openai/m"}]}
        ),
        encoding="utf-8",
    )
    # 独立目录：app_with 会用 mkdir（无 exist_ok）新建 configs/，撞不得
    benchmark = tmp_path / "configs-tb2" / "benchmark.yaml"
    benchmark.parent.mkdir(parents=True)
    benchmark.write_text(
        f"""benchmarks:
  terminal-bench-2:
    type: terminal-bench-2
    repo_url: https://github.com/harbor-framework/terminal-bench.git
    local_path: {tmp_path / "tb2"}
    tasks_path: {tasks}
    default_timeout_sec: 7200
    default_concurrency: 4
    artifact_root: {jobs}
""",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _fake_run(
        self,
        *,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        log_path,
        cancel_event=None,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> tuple[int, str]:
        captured["resume_run_id"] = resume_run_id
        captured["run_id"] = run_id
        return 0, ""

    monkeypatch.setattr(TerminalBench2Backend, "run", _fake_run)
    monkeypatch.setattr(TerminalBench2Backend, "preflight", lambda self: None)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={
            "model_id": "deepseek-v4-flash",
            "n_tasks": 1,
            "benchmark": "terminal-bench-2",
            "resume_run_id": "old-run",
        },
    )

    assert response.status_code == 202
    # the worker thread executes asynchronously; wait for the stub to fire
    for _ in range(200):
        if "resume_run_id" in captured:
            break
        time.sleep(0.01)
    assert captured.get("resume_run_id") == "old-run"


def test_successful_resume_removes_interrupted_source_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接续成功后评测记录只保留新的完整 run，删除旧 interrupted 记录。"""
    from iqradar.benchmarks.api_eval import ApiEvalBackend

    benchmark = _write_api_eval_benchmark(tmp_path)

    def _fake_run(
        self,
        *,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        log_path,
        cancel_event=None,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> tuple[int, str]:
        return 0, ""

    monkeypatch.setattr(ApiEvalBackend, "run", _fake_run)
    monkeypatch.setattr(ApiEvalBackend, "preflight", lambda self: None)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()
    store = client.application.extensions["iqradar_deepswe_runs"]
    old = store.submit(
        model_id="deepseek-v4-flash",
        n_tasks=1,
        sample_seed=0,
        benchmark="gpqa-diamond",
    )
    store.save(
        old.updated(
            status="failed",
            completed_at=datetime.now(UTC),
            error="interrupted: server restarted mid-run",
        )
    )
    old_job = tmp_path / "jobs-apieval" / old.run_id
    old_job.mkdir(parents=True)
    (old_job / "results.jsonl").write_text(
        json.dumps({"task_id": "q1", "status": "passed"}) + "\n",
        encoding="utf-8",
    )

    response = client.post(
        "/api/deepswe-runs",
        json={
            "model_id": "deepseek-v4-flash",
            "n_tasks": 1,
            "benchmark": "gpqa-diamond",
            "resume_run_id": old.run_id,
        },
    )
    assert response.status_code == 202
    new_id = response.get_json()["data"]["run_id"]

    for _ in range(200):
        new_run = client.get(f"/api/deepswe-runs/{new_id}").get_json()["data"]
        old_status = client.get(f"/api/deepswe-runs/{old.run_id}").status_code
        if new_run["status"] == "completed" and old_status == 404:
            break
        time.sleep(0.01)

    assert client.get(f"/api/deepswe-runs/{new_id}").get_json()["data"]["status"] == "completed"
    assert client.get(f"/api/deepswe-runs/{old.run_id}").status_code == 404


def _write_api_eval_benchmark(tmp_path: Path) -> Path:
    """An api-eval benchmark config (own configs dir; app_with mkdirs its own)."""
    dataset = tmp_path / "questions.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        json.dumps({"task_id": "q1", "prompt": "p", "reference": "r"}) + "\n",
        encoding="utf-8",
    )
    benchmark = tmp_path / "configs-apieval" / "benchmark.yaml"
    benchmark.parent.mkdir(parents=True)
    benchmark.write_text(
        f"""benchmarks:
  gpqa-diamond:
    type: api-eval
    repo_url: https://example.com/gpqa
    local_path: {tmp_path / "gpqa"}
    tasks_path: {dataset}
    default_timeout_sec: 3600
    default_concurrency: 1
    artifact_root: {tmp_path / "jobs-apieval"}
""",
        encoding="utf-8",
    )
    return benchmark


def test_run_submission_validates_retry_options(tmp_path: Path) -> None:
    """网关失败重测参数：布尔开关 + 1..10 的轮数，非法值 400。"""
    client = app_with(tmp_path).test_client()

    bad_bool = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "retry_gateway_failures": "yes"},
    )
    assert bad_bool.status_code == 400
    assert "retry_gateway_failures" in bad_bool.get_json()["error"]

    for bad_rounds in (0, 11, 2.5, True):
        bad_rounds_response = client.post(
            "/api/deepswe-runs",
            json={
                "model_id": "deepseek-v4-flash",
                "n_tasks": 1,
                "retry_gateway_failures": True,
                "gateway_retry_rounds": bad_rounds,
            },
        )
        assert bad_rounds_response.status_code == 400, bad_rounds

    # 批量端点同样校验（多基准端点的校验在 api-eval 转发测试里覆盖）
    assert client.post(
        "/api/deepswe-batches",
        json={"model_ids": ["deepseek-v4-flash"], "n_tasks": 1, "gateway_retry_rounds": 0},
    ).status_code == 400


def test_run_submission_forwards_retry_options_to_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """api-eval 提交携带重测参数时透传给后端，并记录在 run 状态里。"""
    from iqradar.benchmarks.api_eval import ApiEvalBackend

    benchmark = _write_api_eval_benchmark(tmp_path)
    captured: dict[str, object] = {}

    def _fake_run(
        self,
        *,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        log_path,
        cancel_event=None,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> tuple[int, str]:
        captured["retry_gateway_failures"] = retry_gateway_failures
        captured["gateway_retry_rounds"] = gateway_retry_rounds
        return 0, ""

    monkeypatch.setattr(ApiEvalBackend, "run", _fake_run)
    monkeypatch.setattr(ApiEvalBackend, "preflight", lambda self: None)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={
            "model_id": "deepseek-v4-flash",
            "n_tasks": 1,
            "benchmark": "gpqa-diamond",
            "retry_gateway_failures": True,
            "gateway_retry_rounds": 3,
        },
    )

    assert response.status_code == 202
    run = response.get_json()["data"]
    assert run["retry_gateway_failures"] is True
    assert run["gateway_retry_rounds"] == 3
    for _ in range(200):
        if "retry_gateway_failures" in captured:
            break
        time.sleep(0.01)
    assert captured.get("retry_gateway_failures") is True
    assert captured.get("gateway_retry_rounds") == 3


def test_multi_bench_submission_forwards_retry_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多基准并发提交携带重测参数：写入批次状态并透传给每个 run。"""
    from iqradar.benchmarks.api_eval import ApiEvalBackend

    benchmark = _write_api_eval_benchmark(tmp_path)
    captured: dict[str, object] = {}

    def _fake_run(
        self,
        *,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        log_path,
        cancel_event=None,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> tuple[int, str]:
        captured["retry_gateway_failures"] = retry_gateway_failures
        captured["gateway_retry_rounds"] = gateway_retry_rounds
        return 0, ""

    monkeypatch.setattr(ApiEvalBackend, "run", _fake_run)
    monkeypatch.setattr(ApiEvalBackend, "preflight", lambda self: None)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    # 非布尔开关在多基准端点同样被拒绝（400）
    assert client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [{"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash"}],
            "max_concurrent": 1,
            "n_tasks": 1,
            "retry_gateway_failures": 1,
        },
    ).status_code == 400

    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [{"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash"}],
            "max_concurrent": 1,
            "n_tasks": 1,
            "retry_gateway_failures": True,
            "gateway_retry_rounds": 2,
        },
    )

    assert response.status_code == 202
    state = response.get_json()["data"]
    assert state["retry_gateway_failures"] is True
    assert state["gateway_retry_rounds"] == 2
    for _ in range(400):
        if "retry_gateway_failures" in captured:
            break
        time.sleep(0.01)
    assert captured.get("retry_gateway_failures") is True
    assert captured.get("gateway_retry_rounds") == 2


def test_benchmarks_endpoint_reports_default_concurrency(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    payload = client.get("/api/benchmarks").get_json()["data"]

    assert payload
    assert all("default_concurrency" in b for b in payload)
    assert any(b["default_concurrency"] == 1 for b in payload)


def test_batch_submission_starts_and_reports_status(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.post(
        "/api/deepswe-batches",
        json={
            "model_ids": ["deepseek-v4-flash"],
            "n_tasks": 3,
            "sample_seed": 0,
        },
    )

    assert response.status_code == 202
    batch = response.get_json()["data"]
    assert batch["status"] == "running"
    assert batch["n_tasks"] == 3
    assert [entry["model_id"] for entry in batch["models"]] == ["deepseek-v4-flash"]
    assert batch["models"][0]["status"] in {"pending", "running", "completed", "failed"}
    assert client.get(f"/api/deepswe-batches/{batch['batch_id']}").status_code == 200
    assert client.get("/api/deepswe-batches/nope").status_code == 404


def test_batch_submission_validates_input(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    assert (
        client.post("/api/deepswe-batches", json={"model_ids": [], "n_tasks": 3}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/deepswe-batches",
            json={"model_ids": ["not-a-model"], "n_tasks": 3},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/deepswe-batches",
            json={"model_ids": ["deepseek-v4-flash"], "n_tasks": 0},
        ).status_code
        == 400
    )
    assert (
        client.post("/api/deepswe-batches", json={"model_ids": "deepseek-v4-flash"}).status_code
        == 400
    )


def test_deepswe_run_rejects_parallel_active_runs(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    first = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 5, "sample_seed": 0},
    ).get_json()["data"]

    blocked = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 5, "sample_seed": 1},
    )

    assert first["status"] in {"queued", "running"}
    assert blocked.status_code == 409


def test_publication_requires_completed_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    run = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 5, "sample_seed": 0},
    ).get_json()["data"]

    response = client.post(f"/api/deepswe-runs/{run['run_id']}/publish")

    assert response.status_code in {404, 409}
    if response.status_code == 409:
        assert response.get_json()["error"] == "only completed deep-swe runs can be published"


def test_deepswe_run_cancel_returns_404_for_unknown_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.post("/api/deepswe-runs/does-not-exist/cancel")

    assert response.status_code == 404
    assert response.get_json()["error"] == "deep-swe run not found"


def test_deepswe_run_cancel_returns_409_for_finished_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    store = client.application.extensions["iqradar_deepswe_runs"]
    run = store.submit(model_id="deepseek-v4-flash", n_tasks=5, sample_seed=0)
    store.save(run.updated(status="failed"))

    response = client.post(f"/api/deepswe-runs/{run.run_id}/cancel")

    assert response.status_code == 409
    assert response.get_json()["error"] == "deep-swe run is not active"


def _seed_deepswe_run(client, *, model_id="deepseek-v4-flash", status="failed"):
    store = client.application.extensions["iqradar_deepswe_runs"]
    run = store.submit(model_id=model_id, n_tasks=5, sample_seed=0)
    updated = run.updated(status=status)
    store.save(updated)
    return updated


def test_deepswe_runs_listed_newest_first(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    first = _seed_deepswe_run(client, model_id="model-a", status="completed")
    second = _seed_deepswe_run(client, model_id="model-b", status="failed")

    response = client.get("/api/deepswe-runs")

    assert response.status_code == 200
    runs = response.get_json()["data"]
    assert [run["run_id"] for run in runs] == [second.run_id, first.run_id]
    assert runs[0]["status"] == "failed"


def test_deepswe_runs_expose_resumable_api_eval_partial_results(tmp_path: Path) -> None:
    """评测记录列表/详情标出 api-eval 中途中断 run，前端据此显示「接续」。"""
    benchmark = _write_api_eval_benchmark(tmp_path)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()
    store = client.application.extensions["iqradar_deepswe_runs"]
    run = store.submit(
        model_id="deepseek-v4-flash",
        n_tasks=5,
        sample_seed=0,
        benchmark="gpqa-diamond",
    )
    failed = run.updated(
        status="failed",
        completed_at=datetime.now(UTC),
        error="interrupted: server restarted mid-run",
    )
    store.save(failed)
    job_dir = tmp_path / "jobs-apieval" / run.run_id
    job_dir.mkdir(parents=True)
    (job_dir / "results.jsonl").write_text(
        json.dumps({"task_id": "q1", "status": "passed"}) + "\n",
        encoding="utf-8",
    )

    listed = client.get("/api/deepswe-runs").get_json()["data"]
    entry = next(item for item in listed if item["run_id"] == run.run_id)
    assert entry["resumable_partial_results"] is True

    detail = client.get(f"/api/deepswe-runs/{run.run_id}").get_json()["data"]
    assert detail["resumable_partial_results"] is True


def test_deepswe_runs_list_includes_publication_status(tmp_path: Path) -> None:
    # 评测记录列表每条附带 snapshot_id：未发布为 null，发布后为快照 id
    client = app_with(tmp_path).test_client()
    run = _seed_deepswe_run(client, status="completed")

    listed = client.get("/api/deepswe-runs").get_json()["data"]
    entry = next(r for r in listed if r["run_id"] == run.run_id)
    assert entry.get("snapshot_id") is None

    # publish_records 需要结果文件；这里直接用空的 records 列表发布会失败，
    # 改为通过 publication 端点发布（run 已 completed）。
    publish = client.post(f"/api/deepswe-runs/{run.run_id}/publish")
    # 无 records 时后端返回 503：该 run 没有可发布结果，列表仍应为 null
    if publish.status_code == 503:
        listed = client.get("/api/deepswe-runs").get_json()["data"]
        entry = next(r for r in listed if r["run_id"] == run.run_id)
        assert entry.get("snapshot_id") is None
    else:
        listed = client.get("/api/deepswe-runs").get_json()["data"]
        entry = next(r for r in listed if r["run_id"] == run.run_id)
        assert entry.get("snapshot_id") is not None


def test_deepswe_runs_list_returns_snapshot_id_for_published_run(
    tmp_path: Path, monkeypatch
) -> None:
    # 回归：list_runs 不得对已发布 run（publication_for_job 返回 Publication 对象）抛 500
    client = app_with(tmp_path).test_client()
    run = _seed_deepswe_run(client, status="completed")
    publisher = client.application.extensions["iqradar_publisher"]

    from iqradar.publication.service import Publication

    monkeypatch.setattr(
        publisher,
        "publication_for_job",
        lambda run_id: Publication(snapshot_id="snap-pub", source_job_id=run_id),
    )

    response = client.get("/api/deepswe-runs")

    assert response.status_code == 200
    entry = next(
        r for r in response.get_json()["data"] if r["run_id"] == run.run_id
    )
    assert entry["snapshot_id"] == "snap-pub"


def test_unpublish_endpoint_returns_404_when_not_published(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    run = _seed_deepswe_run(client, status="completed")

    response = client.delete(f"/api/deepswe-runs/{run.run_id}/publish")

    assert response.status_code == 404
    assert response.get_json()["error"] == "run is not published"


def test_retry_gateway_failures_rejects_non_api_eval_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    # 默认 benchmark 为 deep-swe（docker），重测应拒绝
    run = _seed_deepswe_run(client, status="completed")

    response = client.post(f"/api/deepswe-runs/{run.run_id}/retry-gateway-failures")

    assert response.status_code == 409
    assert response.get_json()["error"] == "retry only supported for api-eval runs"


def test_retry_gateway_failures_rejects_active_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    store = client.application.extensions["iqradar_deepswe_runs"]
    run = store.submit(model_id="deepseek-v4-flash", n_tasks=5, sample_seed=0)
    # run 默认 queued/running：非 api-eval 基准先被拒绝
    response = client.post(f"/api/deepswe-runs/{run.run_id}/retry-gateway-failures")

    assert response.status_code == 409
    assert response.get_json()["error"] == "retry only supported for api-eval runs"


def test_deepswe_run_delete_removes_finished_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    run = _seed_deepswe_run(client, status="failed")

    response = client.delete(f"/api/deepswe-runs/{run.run_id}")

    assert response.status_code == 200
    assert response.get_json()["data"]["deleted"] is True
    assert client.get(f"/api/deepswe-runs/{run.run_id}").status_code == 404


def test_deepswe_run_delete_returns_404_for_unknown_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.delete("/api/deepswe-runs/does-not-exist")

    assert response.status_code == 404
    assert response.get_json()["error"] == "deep-swe run not found"


def test_deepswe_run_delete_returns_400_for_invalid_run_id(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.delete("/api/deepswe-runs/bad%20id")

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid run id"


def test_deepswe_run_delete_returns_409_for_active_run(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    store = client.application.extensions["iqradar_deepswe_runs"]
    run = store.submit(model_id="deepseek-v4-flash", n_tasks=5, sample_seed=0)

    response = client.delete(f"/api/deepswe-runs/{run.run_id}")

    assert response.status_code == 409
    assert response.get_json()["error"] == "active run cannot be deleted; cancel it first"


def test_deepswe_runs_delete_batch_reports_deleted_and_skipped(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    failed = _seed_deepswe_run(client, status="failed")
    completed = _seed_deepswe_run(client, status="completed")
    store = client.application.extensions["iqradar_deepswe_runs"]
    active = store.submit(model_id="deepseek-v4-flash", n_tasks=5, sample_seed=0)

    response = client.post(
        "/api/deepswe-runs/delete-batch",
        json={"run_ids": [failed.run_id, completed.run_id, active.run_id, "nope"]},
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert set(data["deleted"]) == {failed.run_id, completed.run_id}
    skipped_by_id = {item["run_id"]: item["reason"] for item in data["skipped"]}
    assert "active" in skipped_by_id[active.run_id]
    assert "not found" in skipped_by_id["nope"]


def test_deepswe_runs_delete_batch_validates_payload(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    assert client.post("/api/deepswe-runs/delete-batch", json="nope").status_code == 400
    assert client.post("/api/deepswe-runs/delete-batch", json={"run_ids": []}).status_code == 400
    bad = client.post("/api/deepswe-runs/delete-batch", json={"run_ids": ["bad id!"]})
    assert bad.status_code == 400
    assert bad.get_json()["error"] == "invalid run id in run_ids"


def test_deepswe_run_cancel_stops_active_run(tmp_path: Path, monkeypatch) -> None:
    import threading

    monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-test")
    client = app_with(tmp_path).test_client()
    started = threading.Event()
    released = threading.Event()

    def fake_run_pier_sync(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        started.set()
        if cancel_event is not None:
            cancel_event.wait(timeout=10)
        released.set()
        return 1, "cancelled by user"

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake_run_pier_sync)
    run = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 5, "sample_seed": 0},
    ).get_json()["data"]

    assert started.wait(5)
    response = client.post(f"/api/deepswe-runs/{run['run_id']}/cancel")

    assert response.status_code == 200
    assert released.wait(5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get(f"/api/deepswe-runs/{run['run_id']}").get_json()["data"]["status"] == "failed":
            break
        time.sleep(0.05)
    assert (
        client.get(f"/api/deepswe-runs/{run['run_id']}").get_json()["data"]["status"]
        == "failed"
    )


def test_batch_cancel_is_idempotent_at_api(tmp_path: Path, monkeypatch) -> None:
    """Repeated 中断 clicks must never surface a 409 "not active" error."""
    import threading

    monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-test")
    app = app_with(tmp_path)
    started = threading.Event()

    def fake_run_pier_sync(config, *, model, n_tasks, sample_seed, job_name, log_path, cancel_event=None, gateway_overrides=None):
        started.set()
        if cancel_event is not None:
            cancel_event.wait(timeout=10)
        return 1, "cancelled by user"

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake_run_pier_sync)
    service = app.extensions["iqradar_deepswe"]
    batch = service.submit_batch(
        model_ids=["deepseek-v4-flash"],
        model_names=["openai/deepseek-v4-flash"],
        n_tasks=5,
        sample_seed=0,
    )
    batch_id = batch["batch_id"]
    assert started.wait(5)
    client = app.test_client()

    first = client.post(f"/api/deepswe-batches/{batch_id}/cancel")
    second = client.post(f"/api/deepswe-batches/{batch_id}/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["data"]["status"] == "cancelled"


def test_api_rejects_non_loopback_clients(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    response = client.get("/api/health", environ_base={"REMOTE_ADDR": "203.0.113.10"})

    assert response.status_code == 403
    assert response.get_json()["error"] == "IQRadar API is available only from loopback"


# ---------------------------------------------------------------------------
# api-eval benchmark adapter
# ---------------------------------------------------------------------------

def write_api_eval_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "api-eval" / "gpqa.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        json.dumps({"task_id": "q1", "prompt": "1+1", "reference": "2"}) + "\n"
        + json.dumps({"task_id": "q2", "prompt": "2+2", "reference": "4"}) + "\n",
        encoding="utf-8",
    )
    return dataset


def write_benchmark_with_api_eval(tmp_path: Path, dataset: Path) -> Path:
    path = tmp_path / "benchmark-api-eval.yaml"
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)  # preflight: real dataset
    path.write_text(
        f"""benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: {tmp_path / "deep-swe"}
    tasks_path: {tmp_path / "tasks"}
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: {tmp_path / "jobs"}
  gpqa-diamond:
    type: api-eval
    repo_url: https://example.com/gpqa
    local_path: {tmp_path / "api-eval"}
    tasks_path: {dataset}
    default_timeout_sec: 3600
    default_concurrency: 1
    artifact_root: {tmp_path / "api-eval-jobs"}
""",
        encoding="utf-8",
    )
    return path


def write_benchmark_multi_api_eval(tmp_path: Path, dataset: Path) -> Path:
    """Two api-eval benchmarks (gpqa + aime) sharing one dataset."""
    path = tmp_path / "benchmark-multi-api-eval.yaml"
    path.write_text(
        f"""benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: {tmp_path / "deep-swe"}
    tasks_path: {tmp_path / "tasks"}
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: {tmp_path / "jobs"}
  gpqa-diamond:
    type: api-eval
    repo_url: https://example.com/gpqa
    local_path: {tmp_path / "api-eval"}
    tasks_path: {dataset}
    default_timeout_sec: 3600
    default_concurrency: 1
    artifact_root: {tmp_path / "api-eval-jobs"}
  aime-2024:
    type: api-eval
    repo_url: https://example.com/aime
    local_path: {tmp_path / "api-eval"}
    tasks_path: {dataset}
    default_timeout_sec: 3600
    default_concurrency: 1
    artifact_root: {tmp_path / "api-eval-jobs"}
""",
        encoding="utf-8",
    )
    return path


def _wait_for_run_status(client, run_id: str, terminal: set[str], timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        status = client.get(f"/api/deepswe-runs/{run_id}").get_json()["data"]["status"]
        if status in terminal:
            return status
        time.sleep(0.05)
    return status


def test_multi_bench_submission_rejects_docker_benchmark(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [{"benchmark": "deep-swe", "model_id": "deepseek-v4-flash"}],
            "max_concurrent": 1,
            "n_tasks": 1,
        },
    )
    assert response.status_code == 400


def test_multi_bench_per_item_n_tasks_validation(tmp_path: Path) -> None:
    """Per-item n_tasks is validated against each benchmark's max_tasks."""
    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_multi_api_eval(tmp_path, dataset)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()
    # 超过单基准 max_tasks 应被拒（gpqa-diamond 数据集只有 2 题）
    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [
                {"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash", "n_tasks": 99},
                {"benchmark": "aime-2024", "model_id": "deepseek-v4-flash"},
            ],
            "max_concurrent": 1,
            "n_tasks": 2,
        },
    )
    assert response.status_code == 400
    # 非法类型应被拒
    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [
                {"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash", "n_tasks": "many"},
            ],
            "max_concurrent": 1,
            "n_tasks": 2,
        },
    )
    assert response.status_code == 400
    # 缺省 item 回退批量级 n_tasks
    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [
                {"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash"},
            ],
            "max_concurrent": 1,
            "n_tasks": 2,
        },
    )
    assert response.status_code == 202
    batch = response.get_json()["data"]
    assert batch["items"][0]["n_tasks"] == 2


def test_multi_bench_accepts_three_models_across_ten_benchmarks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 个模型 × 10 个 api-eval 基准应可一次提交。"""
    from iqradar.benchmarks.api_eval import ApiEvalBackend

    dataset = write_api_eval_dataset(tmp_path)
    (tmp_path / "tasks").mkdir(parents=True, exist_ok=True)
    benchmark_path = tmp_path / "benchmark-ten-api-eval.yaml"
    api_benchmarks = "\n".join(
        f"""  api-eval-{index}:
    type: api-eval
    repo_url: https://example.com/api-eval-{index}
    local_path: {tmp_path / 'api-eval'}
    tasks_path: {dataset}
    default_timeout_sec: 3600
    default_concurrency: 1
    artifact_root: {tmp_path / 'api-eval-jobs'}"""
        for index in range(10)
    )
    benchmark_path.write_text(
        f"""benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: {tmp_path / 'deep-swe'}
    tasks_path: {tmp_path / 'tasks'}
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: {tmp_path / 'jobs'}
{api_benchmarks}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("IQRADAR_MODEL_NAMES", "gateway/model-a,gateway/model-b,gateway/model-c")
    monkeypatch.setattr(ApiEvalBackend, "preflight", lambda self: None)
    monkeypatch.setattr(ApiEvalBackend, "run", lambda *args, **kwargs: (0, ""))
    client = app_with(tmp_path, benchmark_path=benchmark_path).test_client()

    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [
                {"benchmark": f"api-eval-{bench_index}", "model_id": model_id, "n_tasks": 1}
                for bench_index in range(10)
                for model_id in ["gateway/model-a", "gateway/model-b", "gateway/model-c"]
            ],
            "max_concurrent": 16,
            "sample_seed": 0,
        },
    )

    assert response.status_code == 202
    batch = response.get_json()["data"]
    assert len(batch["items"]) == 30


def test_multi_bench_per_item_n_tasks_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不同 item 用各自的 n_tasks：持久化在 state 且调度时生效。"""
    import iqradar.benchmarks.api_eval as api_eval_mod

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_multi_api_eval(tmp_path, dataset)

    def fake_gateway(base_url, api_key, model, prompt, **kwargs):
        return ("2", {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}, None)

    monkeypatch.setattr(api_eval_mod, "gateway_complete", fake_gateway)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [
                # gpqa 抽 1 题，aime 抽 2 题 —— 各自独立
                {"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash", "n_tasks": 1},
                {"benchmark": "aime-2024", "model_id": "deepseek-v4-flash", "n_tasks": 2},
            ],
            "max_concurrent": 2,
            "n_tasks": 2,
            "sample_seed": 0,
        },
    )
    assert response.status_code == 202
    batch = response.get_json()["data"]
    # state 里持久化 per-item n_tasks
    by_bench = {item["benchmark"]: item for item in batch["items"]}
    assert by_bench["gpqa-diamond"]["n_tasks"] == 1
    assert by_bench["aime-2024"]["n_tasks"] == 2

    # 等待完成，验证每个 run 记了对应题数
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        batch = client.get(f"/api/deepswe-multi-batches/{batch['batch_id']}").get_json()["data"]
        if batch["status"] != "running":
            break
        time.sleep(0.1)
    assert batch["status"] == "completed"
    counts = {}
    for item in batch["items"]:
        run = client.get(f"/api/deepswe-runs/{item['run_id']}").get_json()["data"]
        counts[item["benchmark"]] = run["n_tasks"]
    assert counts["gpqa-diamond"] == 1
    assert counts["aime-2024"] == 2


def test_benchmarks_endpoint_reports_category(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    payload = client.get("/api/benchmarks").get_json()["data"]
    assert payload
    assert all("category" in b for b in payload)
    assert any(b["category"] == "docker" for b in payload)


def test_multi_bench_submission_runs_api_eval_benchmarks_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iqradar.benchmarks.api_eval as api_eval_mod

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_multi_api_eval(tmp_path, dataset)

    def fake_gateway(base_url, api_key, model, prompt, **kwargs):
        answer = "2" if prompt == "1+1" else "5"
        return (answer, {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}, None)

    monkeypatch.setattr(api_eval_mod, "gateway_complete", fake_gateway)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-multi-batches",
        json={
            "items": [
                {"benchmark": "gpqa-diamond", "model_id": "deepseek-v4-flash"},
                {"benchmark": "aime-2024", "model_id": "deepseek-v4-flash"},
            ],
            "max_concurrent": 2,
            "n_tasks": 2,
            "sample_seed": 0,
        },
    )
    assert response.status_code == 202
    batch = response.get_json()["data"]
    assert batch["status"] == "running"
    assert batch["max_concurrent"] == 2
    assert len(batch["items"]) == 2

    # 等待并发完成（两个 run 同时跑，mock 网关秒回）
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        batch = client.get(f"/api/deepswe-multi-batches/{batch['batch_id']}").get_json()["data"]
        if batch["status"] != "running":
            break
        time.sleep(0.1)
    assert batch["status"] == "completed"
    assert all(i["status"] == "completed" for i in batch["items"])
    # No auto-publish on completion: snapshot_id stays null until records are
    # published explicitly from the test page's 评测记录 list.
    assert batch["snapshot_id"] is None
    # The two completed runs are unpublished individually.
    runs = client.get("/api/deepswe-runs").get_json()["data"]
    completed = [r for r in runs if r["status"] == "completed"]
    assert {r["benchmark"] for r in completed} == {"gpqa-diamond", "aime-2024"}
    assert all(r["snapshot_id"] is None for r in completed)

    # 发布选中：把两条完成的 run 一次性合并发布成一个快照，每条 run 标记为已发布。
    publishable = [r["run_id"] for r in completed]
    publish = client.post("/api/deepswe-runs/publish-batch", json={"run_ids": publishable})
    assert publish.status_code == 201
    result = publish.get_json()["data"]
    assert result["snapshot_id"]
    assert result["published_run_ids"] == publishable
    # Each published run now carries the shared snapshot_id.
    runs_after = client.get("/api/deepswe-runs").get_json()["data"]
    by_id = {r["run_id"]: r for r in runs_after}
    assert all(by_id[rid]["snapshot_id"] == result["snapshot_id"] for rid in publishable)
    # The dashboard reflects the merged snapshot.
    dash = client.get("/api/dashboard").get_json()["data"]
    assert dash["snapshot_id"] == result["snapshot_id"]


def test_api_eval_run_dispatches_to_backend_and_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iqradar.benchmarks.api_eval as api_eval_mod

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_with_api_eval(tmp_path, dataset)

    def fake_gateway(base_url, api_key, model, prompt, **kwargs):
        answer = "2" if prompt == "1+1" else "5"
        return (answer, {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}, None)

    monkeypatch.setattr(api_eval_mod, "gateway_complete", fake_gateway)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 2, "sample_seed": 0, "benchmark": "gpqa-diamond"},
    )
    assert response.status_code == 202
    run_id = response.get_json()["data"]["run_id"]

    assert _wait_for_run_status(client, run_id, {"completed", "failed"}) == "completed"

    records = client.get(f"/api/deepswe-runs/{run_id}/records").get_json()["data"]
    assert len(records) == 2
    assert {record["benchmark"]["name"] for record in records} == {"gpqa-diamond"}
    by_task = {record["benchmark"]["task_id"]: record["result"]["status"] for record in records}
    assert by_task == {"q1": "passed", "q2": "failed"}


def test_api_eval_base_url_hash_uses_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iqradar.benchmarks.api_eval as api_eval_mod
    from iqradar.deepswe.importer import base_url_hash

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_with_api_eval(tmp_path, dataset)
    monkeypatch.setenv("GATEWAY_BASE_URL", "http://gateway.example/v1")

    monkeypatch.setattr(
        api_eval_mod,
        "gateway_complete",
        lambda *a, **kw: ("2", {"input_tokens": 1, "output_tokens": 1}, None),
    )
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "benchmark": "gpqa-diamond"},
    )
    run_id = response.get_json()["data"]["run_id"]
    assert _wait_for_run_status(client, run_id, {"completed", "failed"}) == "completed"

    records = client.get(f"/api/deepswe-runs/{run_id}/records").get_json()["data"]
    assert records[0]["model"]["base_url_hash"] == base_url_hash("http://gateway.example/v1")


def test_api_eval_long_run_not_misclassified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long-running api-eval run must stay 'running' after RECONCILE_AGE_SEC
    because its worker thread is alive (no subprocess to track)."""
    import threading

    import iqradar.benchmarks.api_eval as api_eval_mod
    from iqradar.deepswe.service import DeepSweService

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_with_api_eval(tmp_path, dataset)
    monkeypatch.setattr(DeepSweService, "RECONCILE_AGE_SEC", 0)

    started = threading.Event()
    release = threading.Event()

    def blocking_gateway(base_url, api_key, model, prompt, **kwargs):
        started.set()
        release.wait(timeout=10)
        return ("2", {"input_tokens": 1, "output_tokens": 1}, None)

    monkeypatch.setattr(api_eval_mod, "gateway_complete", blocking_gateway)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "benchmark": "gpqa-diamond"},
    )
    run_id = response.get_json()["data"]["run_id"]

    assert started.wait(5)
    time.sleep(0.2)  # exceed RECONCILE_AGE_SEC=0 so reconcile runs
    status = client.get(f"/api/deepswe-runs/{run_id}").get_json()["data"]["status"]
    assert status in {"queued", "running"}

    release.set()
    assert _wait_for_run_status(client, run_id, {"completed", "failed"}) == "completed"


def test_api_eval_submit_records_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iqradar.benchmarks.api_eval as api_eval_mod

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_with_api_eval(tmp_path, dataset)
    monkeypatch.setattr(
        api_eval_mod,
        "gateway_complete",
        lambda *a, **kw: ("2", {"input_tokens": 1, "output_tokens": 1}, None),
    )
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    # Invalid effort is rejected before submission.
    bad = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "benchmark": "gpqa-diamond", "effort": "bogus"},
    )
    assert bad.status_code == 400

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "benchmark": "gpqa-diamond", "effort": "max"},
    )
    assert response.status_code == 202
    run = response.get_json()["data"]
    assert run["effort"] == "max"
    run_id = run["run_id"]
    assert _wait_for_run_status(client, run_id, {"completed", "failed"}) == "completed"

    records = client.get(f"/api/deepswe-runs/{run_id}/records").get_json()["data"]
    assert records[0]["model"]["effort_requested"] == "max"


def test_api_eval_accepts_n_tasks_beyond_117(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """api-eval's max_tasks tracks the dataset size, so >117 questions is legal."""
    import iqradar.benchmarks.api_eval as api_eval_mod

    dataset = tmp_path / "api-eval" / "gpqa.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        "\n".join(
            json.dumps({"task_id": f"t{i}", "prompt": "1+1", "reference": "2"})
            for i in range(150)
        )
        + "\n",
        encoding="utf-8",
    )
    benchmark = write_benchmark_with_api_eval(tmp_path, dataset)
    monkeypatch.setattr(
        api_eval_mod,
        "gateway_complete",
        lambda *a, **kw: ("2", {"input_tokens": 1, "output_tokens": 1}, None),
    )
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 120, "sample_seed": 0, "benchmark": "gpqa-diamond"},
    )
    assert response.status_code == 202
    run_id = response.get_json()["data"]["run_id"]
    assert _wait_for_run_status(client, run_id, {"completed", "failed"}) == "completed"


# ---------------------------------------------------------------------------
# 测试页网关配置（runtime gateway settings）
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_environ():
    """保存网关配置会把覆盖写进 os.environ（进程级副作用），测试后恢复。"""
    import os

    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


def save_gateway_settings(client, **overrides) -> dict:
    payload = {
        "models_base_url": "",
        "models_api_key": "",
        "inference_base_url": "",
        "inference_api_key": "",
    }
    payload.update(overrides)
    response = client.post("/api/gateway-settings", json=payload)
    assert response.status_code == 200
    return response.get_json()["data"]


class _FakeGatewayResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeGatewayResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_gateway_settings_roundtrip_applies_immediately(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()

    saved = save_gateway_settings(
        client,
        models_base_url="http://models.example/v1",
        models_api_key="sk-models",
        inference_base_url="http://chat.example/v1",
        inference_api_key="sk-chat",
    )

    assert saved["models_base_url"] == "http://models.example/v1"
    assert saved["inference_api_key"] == "sk-chat"
    assert saved["updated_at"]
    fetched = client.get("/api/gateway-settings").get_json()["data"]
    assert fetched == saved
    # 已保存的覆盖立刻对运行时后端可见（无需重启）
    settings = client.application.extensions["iqradar_gateway_settings"]
    assert settings.inference_base_url() == "http://chat.example/v1"
    assert settings.inference_api_key() == "sk-chat"
    assert settings.models_base_url() == "http://models.example/v1"


def test_gateway_settings_validates_payload(tmp_path: Path) -> None:
    client = app_with(tmp_path).test_client()
    blank = {
        "models_base_url": "",
        "models_api_key": "",
        "inference_base_url": "",
        "inference_api_key": "",
    }

    assert client.post("/api/gateway-settings", json=None).status_code == 400
    assert client.post("/api/gateway-settings").status_code == 400
    assert client.post("/api/gateway-settings", json={"models_base_url": ""}).status_code == 400
    assert (
        client.post(
            "/api/gateway-settings", json={**blank, "models_base_url": "ftp://x/v1"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/gateway-settings", json={**blank, "gateway_base_url": "http://x/v1"}
        ).status_code
        == 400
    )

    # 合法 payload：base URL 可为空（=回退 .env 基线）
    assert client.post("/api/gateway-settings", json=blank).status_code == 200


def test_gateway_settings_test_models_probe_reports_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["authorization"] = dict(request.headers).get("Authorization")
        captured["timeout"] = timeout
        return _FakeGatewayResponse(
            json.dumps(
                {"data": [{"id": "gateway/deepseek-v4-flash"}, {"id": "z.ai/glm-5.2"}]}
            ).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = app_with(tmp_path).test_client()

    # 显式传入待验证的 baseurl/key（保存前即可探测）
    response = client.post(
        "/api/gateway-settings/test-models",
        json={"base_url": "http://probe.example/v1", "api_key": "sk-probe"},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["ok"] is True
    assert payload["model_count"] == 2
    assert payload["model_ids"] == ["gateway/deepseek-v4-flash", "z.ai/glm-5.2"]
    assert captured["url"] == "http://probe.example/v1/models"
    assert captured["authorization"] == "Bearer sk-probe"


def test_gateway_settings_test_models_probe_uses_saved_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["authorization"] = dict(request.headers).get("Authorization")
        return _FakeGatewayResponse(json.dumps({"data": [{"id": "saved/model"}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = app_with(tmp_path).test_client()
    save_gateway_settings(
        client,
        models_base_url="http://saved.example/v1",
        models_api_key="sk-saved",
    )

    response = client.post("/api/gateway-settings/test-models", json={})

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["ok"] is True
    assert payload["model_ids"] == ["saved/model"]
    assert captured["url"] == "http://saved.example/v1/models"
    assert captured["authorization"] == "Bearer sk-saved"


def test_gateway_settings_test_models_probe_reports_errors_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    def failing_urlopen(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    client = app_with(tmp_path).test_client()

    response = client.post(
        "/api/gateway-settings/test-models",
        json={"base_url": "http://unreachable.example/v1", "api_key": "sk-secret-value"},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["ok"] is False
    assert "connection refused" in payload["error"]
    assert payload["model_ids"] == []
    # key 不出现在错误信息里
    assert "sk-secret-value" not in (payload["error"] or "")


def test_gateway_settings_test_chat_probe_returns_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["authorization"] = dict(request.headers).get("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeGatewayResponse(
            json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]}
            ).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = app_with(tmp_path).test_client()
    save_gateway_settings(
        client,
        inference_base_url="http://chat.example/v1",
        inference_api_key="sk-chat",
    )

    response = client.post(
        "/api/gateway-settings/test-chat",
        json={"model": "gateway/deepseek-v4-flash"},
    )

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["ok"] is True
    assert payload["content"] == "Hello!"
    assert payload["model"] == "gateway/deepseek-v4-flash"
    assert payload["latency_ms"] >= 0
    assert captured["url"] == "http://chat.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer sk-chat"
    assert captured["body"]["messages"] == [{"role": "user", "content": "Hello!"}]


def test_gateway_settings_test_chat_requires_model_and_base_url(
    tmp_path: Path,
) -> None:
    client = app_with(tmp_path).test_client()

    assert (
        client.post("/api/gateway-settings/test-chat", json={}).status_code == 400
    )
    assert (
        client.post(
            "/api/gateway-settings/test-chat", json={"model": "some-model"}
        ).status_code
        == 400
    )


def test_models_discovery_uses_saved_models_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    monkeypatch.setenv("GATEWAY_BASE_URL", "http://legacy.example/v1")
    monkeypatch.setenv("GATEWAY_API_KEY", "sk-legacy")
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["authorization"] = dict(request.headers).get("Authorization")
        return _FakeGatewayResponse(
            json.dumps({"data": [{"id": "new/model-a"}, {"id": "new/model-b"}]}).encode("utf-8")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = app_with(tmp_path).test_client()
    save_gateway_settings(
        client,
        models_base_url="http://models.example/v1",
        models_api_key="sk-models",
    )

    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["source"] == "gateway"
    assert [model["id"] for model in payload["models"]] == ["new/model-a", "new/model-b"]
    assert captured["url"] == "http://models.example/v1/models"
    assert captured["authorization"] == "Bearer sk-models"


def test_api_eval_run_uses_saved_inference_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import iqradar.benchmarks.api_eval as api_eval_mod
    from iqradar.deepswe.importer import base_url_hash

    dataset = write_api_eval_dataset(tmp_path)
    benchmark = write_benchmark_with_api_eval(tmp_path, dataset)
    captured: dict[str, str] = {}

    def fake_gateway(base_url, api_key, model, prompt, **kwargs):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return ("2", {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}, None)

    monkeypatch.setattr(api_eval_mod, "gateway_complete", fake_gateway)
    client = app_with(tmp_path, benchmark_path=benchmark).test_client()
    save_gateway_settings(
        client,
        inference_base_url="http://saved-chat.example/v1",
        inference_api_key="sk-saved-chat",
    )

    response = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1, "benchmark": "gpqa-diamond"},
    )
    run_id = response.get_json()["data"]["run_id"]
    assert _wait_for_run_status(client, run_id, {"completed", "failed"}) == "completed"

    assert captured["base_url"] == "http://saved-chat.example/v1"
    assert captured["api_key"] == "sk-saved-chat"
    records = client.get(f"/api/deepswe-runs/{run_id}/records").get_json()["data"]
    assert records[0]["model"]["base_url_hash"] == base_url_hash("http://saved-chat.example/v1")


def test_deepswe_run_passes_saved_inference_endpoint_to_pier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # deep-swe 后端在无固定任务子集时会枚举 tasks 目录；预置一个任务，
    # 让运行真正到达 run_pier_sync（pier 调用本身被 fake 掉）。
    task_dir = tmp_path / "tasks" / "task-a"
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        '[environment]\ndocker_image = "example/image:latest"\n', encoding="utf-8"
    )
    client = app_with(tmp_path).test_client()
    save_gateway_settings(
        client,
        inference_base_url="http://saved-chat.example/v1",
        inference_api_key="sk-saved-chat",
    )
    captured: dict[str, object] = {}

    def fake_run_pier_sync(
        config,
        *,
        model,
        n_tasks,
        sample_seed,
        job_name,
        log_path,
        cancel_event=None,
        gateway_overrides=None,
    ):
        captured["overrides"] = gateway_overrides
        return 1, "stop"

    monkeypatch.setattr("iqradar.benchmarks.deep_swe.run_pier_sync", fake_run_pier_sync)

    run = client.post(
        "/api/deepswe-runs",
        json={"model_id": "deepseek-v4-flash", "n_tasks": 1},
    ).get_json()["data"]
    assert _wait_for_run_status(client, run["run_id"], {"failed"}) == "failed"

    assert captured["overrides"] == {
        "OPENAI_BASE_URL": "http://saved-chat.example/v1",
        "OPENAI_API_KEY": "sk-saved-chat",
        "MSWEA_API_KEY": "sk-saved-chat",
    }
