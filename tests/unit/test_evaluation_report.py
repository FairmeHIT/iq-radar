"""Unit tests for the question-level evaluation report.

Covers the stable outcome/failure taxonomy, the report builder's coverage
accounting (including questions that were requested but never recorded), the
per-backend question catalogs, and the service/persistence/API surface.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from flask import Flask

from iqradar.benchmarks.api_eval import ApiEvalBackend, ApiEvalItem
from iqradar.benchmarks.base import BenchmarkBackend
from iqradar.config.schema import BenchmarkConfig
from iqradar.deepswe.api import create_deepswe_blueprint
from iqradar.deepswe.runs import FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService
from iqradar.metrics.evaluation import (
    build_evaluation_report,
    classify_outcome,
)
from iqradar.shared.atomic_files import atomic_write_text
from iqradar.schemas.run_record import RunRecord


# ---------------------------------------------------------------------------
# classify_outcome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("status", "error_type", "outcome", "category"),
    [
        ("passed", None, "success", None),
        ("passed", "exact_match", "success", None),
        ("failed", "exact_match", "model_failure", "model_wrong_answer"),
        ("failed", "judge_incorrect", "model_failure", "model_wrong_answer"),
        ("failed", "empty_response", "model_failure", "model_empty_or_unparseable"),
        ("failed", "judge_unclear", "model_failure", "judge_unclear"),
        ("failed", "judge_error", "infrastructure_error", "judge_error"),
        ("failed", "verifier_failed", "model_failure", "model_wrong_answer"),
        ("failed", "RuntimeError", "infrastructure_error", "harness_error"),
        ("runner_error", "URLError", "infrastructure_error", "gateway_error"),
        ("verifier_error", "empty_response", "infrastructure_error", "verifier_error"),
        ("timeout", "timeout", "model_failure", "timeout"),
        ("budget_stopped", "budget", "model_failure", "budget_stopped"),
        ("skipped", "skipped", "skipped", "skipped"),
        ("not_executed", "not_executed", "not_executed", "not_executed"),
    ],
)
def test_classify_outcome_maps_statuses_and_error_types(
    status: str, error_type: str | None, outcome: str, category: str | None
) -> None:
    assert classify_outcome(status, error_type) == (outcome, category)


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def make_record(task_id: str, status: str, error_type: str | None = None) -> RunRecord:
    return RunRecord.model_validate(
        {
            "run_id": f"deep-swe__{task_id}__model-a__high",
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
                "effort_effective": True,
            },
            "result": {
                "status": status,
                "verifier_passed": status == "passed",
                "exit_code": 0,
                "error_type": error_type,
            },
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_input_tokens": 0,
                "agent_steps": 1,
                "wall_time_sec": 1.0,
            },
            "cost": {
                "input_cost": 0.0,
                "cached_input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            },
            "artifacts": {},
            "created_at": datetime(2026, 9, 1, tzinfo=UTC),
        }
    )


def test_report_lists_unexecuted_questions_with_coverage(tmp_path: Path) -> None:
    run = {"run_id": "run-1", "model_id": "model-a", "benchmark": "deep-swe", "status": "completed"}
    expected = [
        {"task_id": "q1", "prompt": "1+1", "reference": "2"},
        {"task_id": "q2", "prompt": "2+2", "reference": "4"},
        {"task_id": "q3", "prompt": "3+3", "reference": "6"},
    ]
    raw_questions = [
        {"index": 0, "task_id": "q1", "status": "passed", "response": "2", "wall_time_sec": 1.2},
        {"index": 1, "task_id": "q2", "status": "failed", "error_type": "exact_match", "error_message": "scored wrong"},
    ]

    report = build_evaluation_report(
        run=run,
        records=[make_record("q1", "passed")],
        raw_questions=raw_questions,
        expected_questions=expected,
    )

    coverage = report["coverage"]
    assert coverage["requested_tasks"] == 3
    assert coverage["recorded_tasks"] == 2
    assert coverage["missing_tasks"] == 1
    assert coverage["coverage_rate"] == pytest.approx(2 / 3)
    assert coverage["status_counts"] == {"passed": 1, "failed": 1, "not_executed": 1}
    assert coverage["success_count"] == 1
    assert coverage["model_failure_count"] == 1
    assert coverage["infrastructure_error_count"] == 0
    assert coverage["not_executed_count"] == 1
    assert coverage["failure_category_counts"] == {
        "model_wrong_answer": 1,
        "not_executed": 1,
    }

    questions = report["questions"]
    assert [q["task_id"] for q in questions] == ["q1", "q2", "q3"]
    assert questions[0]["outcome"] == "success"
    assert questions[1]["failure_category"] == "model_wrong_answer"
    assert questions[2]["recorded"] is False
    assert questions[2]["status"] == "not_executed"
    assert questions[2]["prompt"] == "3+3"


def test_report_pads_unknown_slots_when_catalog_is_unavailable() -> None:
    records = [make_record("task-a", "passed")]
    report = build_evaluation_report(
        run={"run_id": "run-2", "model_id": "model-a"},
        records=records,
        expected_count=3,
    )

    coverage = report["coverage"]
    assert coverage["requested_tasks"] == 3
    assert coverage["recorded_tasks"] == 1
    assert coverage["missing_tasks"] == 2
    assert coverage["not_executed_count"] == 2
    assert len(report["questions"]) == 3
    assert [q["status"] for q in report["questions"]] == ["passed", "not_executed", "not_executed"]


def test_report_separates_infrastructure_errors_from_pass_rate() -> None:
    records = [
        make_record("a", "passed"),
        make_record("b", "failed", "exact_match"),
        make_record("c", "runner_error", "URLError"),
        make_record("d", "timeout", "timeout"),
    ]
    report = build_evaluation_report(run={"run_id": "run-3", "model_id": "m"}, records=records)

    coverage = report["coverage"]
    assert coverage["infrastructure_error_count"] == 1
    assert coverage["success_count"] == 1
    assert coverage["model_failure_count"] == 2
    # 当前 IQ 口径：timeout 计入分母、runner_error 不计入。
    assert coverage["scored_count_current_metric"] == 3
    assert coverage["pass_rate_current_metric"] == pytest.approx(1 / 3)
    assert coverage["failure_category_counts"] == {
        "gateway_error": 1,
        "model_wrong_answer": 1,
        "timeout": 1,
    }
    assert report["taxonomy"]["scoring_denominator_note"]


# ---------------------------------------------------------------------------
# Backend question catalogs
# ---------------------------------------------------------------------------

def test_api_eval_question_catalog_returns_deterministic_sample(tmp_path: Path) -> None:
    dataset = tmp_path / "q.jsonl"
    dataset.write_text(
        "".join(
            json.dumps({"task_id": f"t{i}", "prompt": "p", "reference": "r"}) + "\n"
            for i in range(5)
        ),
        encoding="utf-8",
    )
    backend = ApiEvalBackend(
        BenchmarkConfig(
            type="api-eval",
            repo_url="https://example.com",
            local_path=tmp_path / "local",
            tasks_path=dataset,
            default_timeout_sec=60,
            default_concurrency=1,
            artifact_root=tmp_path / "jobs",
        ),
        name="gpqa-diamond",
    )

    catalog = backend.question_catalog(n_tasks=3, sample_seed=42)
    ids = [entry["task_id"] for entry in catalog]

    assert len(catalog) == 3
    # 与 run 使用同一确定性采样
    from iqradar.benchmarks.api_eval import sample_items

    expected = [item.task_id for item in sample_items(_load(dataset), 3, 42)]
    assert ids == expected


def _load(dataset: Path) -> list[ApiEvalItem]:
    from iqradar.benchmarks.api_eval import load_dataset

    return load_dataset(dataset)


class _FakeDockerBackend(BenchmarkBackend):
    name = "deep-swe"
    benchmark_type = "deep-swe"

    def __init__(self, records: list[RunRecord], catalog: list[dict[str, object]]) -> None:
        self.records = records
        self.catalog = catalog
        from pathlib import Path as _Path

        self.jobs_root = _Path("/tmp/nonexistent-jobs")
        self.env_file = _Path("/tmp/nonexistent.env")
        self.default_timeout_sec = 60
        self.n_concurrent = 1

    def base_url(self) -> str:
        return "https://models.example/v1"

    def import_records(self, *, run_id, model_id, base_url_hash, effort="high", prices=None):
        return self.records

    def question_catalog(self, *, n_tasks: int, sample_seed: int):
        return self.catalog


# ---------------------------------------------------------------------------
# Service + persistence + API surface
# ---------------------------------------------------------------------------

def _service_with(tmp_path: Path, backend: BenchmarkBackend) -> DeepSweService:
    runs = FileDeepSweRunStore(tmp_path / "runs")
    return DeepSweService(backends={"deep-swe": backend}, runs=runs)


def test_service_evaluation_report_persists_json(tmp_path: Path) -> None:
    records = [make_record("task-a", "passed"), make_record("task-b", "failed", "verifier_failed")]
    backend = _FakeDockerBackend(
        records,
        catalog=[{"task_id": "task-a"}, {"task_id": "task-b"}, {"task_id": "task-c"}],
    )
    service = _service_with(tmp_path, backend)
    run = service._runs.submit(
        model_id="model-a", n_tasks=3, sample_seed=0, benchmark="deep-swe"
    )
    service._runs.save(run.updated(status="completed"))

    report = service.evaluation_report(run.run_id)

    assert report is not None
    assert report["coverage"]["requested_tasks"] == 3
    assert report["coverage"]["recorded_tasks"] == 2
    assert report["coverage"]["missing_tasks"] == 1
    categories = {q["task_id"]: q["failure_category"] for q in report["questions"]}
    assert categories == {
        "task-a": None,
        "task-b": "model_wrong_answer",
        "task-c": "not_executed",
    }
    persisted = json.loads(
        (tmp_path / "runs" / run.run_id / "evaluation-report.json").read_text(encoding="utf-8")
    )
    assert persisted["coverage"] == report["coverage"]


def test_evaluation_report_endpoint_returns_enveloped_report(tmp_path: Path) -> None:
    records = [make_record("task-a", "passed")]
    backend = _FakeDockerBackend(records, catalog=[{"task_id": "task-a"}, {"task_id": "task-b"}])
    service = _service_with(tmp_path, backend)
    run = service._runs.submit(model_id="model-a", n_tasks=2, sample_seed=0, benchmark="deep-swe")
    service._runs.save(run.updated(status="completed"))

    app = Flask(__name__)
    app.register_blueprint(create_deepswe_blueprint(service, models_path=None))
    client = app.test_client()

    response = client.get(f"/api/deepswe-runs/{run.run_id}/evaluation-report")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["data"]["coverage"]["requested_tasks"] == 2
    assert payload["data"]["coverage"]["recorded_tasks"] == 1

    missing = client.get("/api/deepswe-runs/does-not-exist/evaluation-report")
    assert missing.status_code == 404


def test_report_writer_uses_atomic_helper(tmp_path: Path) -> None:
    # atomic_write_text 是报告持久化的唯一写入路径：间接防回归。
    target = tmp_path / "report.json"
    atomic_write_text(target, "{}\n")
    assert target.read_text(encoding="utf-8") == "{}\n"
