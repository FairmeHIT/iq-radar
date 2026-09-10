from __future__ import annotations

from pydantic import ValidationError
import pytest

from iqradar.schemas.run_record import RunRecord


def valid_run_record_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "run_id": "2026-08-03T120000Z_deepswe_my-model_high_0001",
        "benchmark": {
            "name": "deep-swe",
            "version": "local-git-sha-or-release",
            "task_id": "happy-dom__abort-pending-body-reads",
            "repo": "capricorn86/happy-dom",
            "language": "typescript",
            "task_path": "vendor/deep-swe/tasks/happy-dom__abort-pending-body-reads",
        },
        "model": {
            "provider": "openai-compatible",
            "base_url_hash": "sha256:base-url-only-hash",
            "name": "my-model",
            "effort_requested": "high",
            "effort_effective": True,
        },
        "result": {
            "status": "passed",
            "verifier_passed": True,
            "exit_code": 0,
            "error_type": None,
            "error_message_redacted": None,
        },
        "usage": {
            "input_tokens": 120000,
            "output_tokens": 45000,
            "cached_input_tokens": 0,
            "agent_steps": 82,
            "wall_time_sec": 3660,
            "usage_estimated": False,
        },
        "cost": {
            "currency": "USD",
            "input_cost": 0.24,
            "cached_input_cost": 0.0,
            "output_cost": 0.9,
            "total_cost": 1.14,
        },
        "artifacts": {
            "patch_path": "data/artifacts/2026-08-03T120000Z/task/patch.diff",
            "log_path": "data/artifacts/2026-08-03T120000Z/task/run.log",
            "verifier_path": "data/artifacts/2026-08-03T120000Z/task/reward.json",
        },
        "created_at": "2026-08-03T12:00:00Z",
    }


def test_valid_run_record_accepts_plan_schema() -> None:
    record = RunRecord.model_validate(valid_run_record_data())

    assert record.schema_version == "1.0"
    assert record.benchmark.task_id == "happy-dom__abort-pending-body-reads"
    assert record.model.effort_requested == "high"
    assert record.result.status == "passed"
    assert record.usage.output_tokens == 45000
    assert record.cost.total_cost == 1.14


@pytest.mark.parametrize(
    "status",
    [
        "failed",
        "timeout",
        "runner_error",
        "verifier_error",
        "budget_stopped",
        "skipped",
    ],
)
def test_run_record_accepts_all_planned_result_statuses(status: str) -> None:
    data = valid_run_record_data()
    data["result"] = {**data["result"], "status": status}

    record = RunRecord.model_validate(data)

    assert record.result.status == status


def test_run_record_rejects_unknown_result_status() -> None:
    data = valid_run_record_data()
    data["result"] = {**data["result"], "status": "partially_passed"}

    with pytest.raises(ValidationError):
        RunRecord.model_validate(data)


def test_run_record_rejects_unknown_effort() -> None:
    data = valid_run_record_data()
    data["model"] = {**data["model"], "effort_requested": "extreme"}

    with pytest.raises(ValidationError):
        RunRecord.model_validate(data)


def test_run_record_rejects_negative_usage_and_cost_values() -> None:
    data = valid_run_record_data()
    data["usage"] = {**data["usage"], "input_tokens": -1}
    data["cost"] = {**data["cost"], "total_cost": -0.01}

    with pytest.raises(ValidationError):
        RunRecord.model_validate(data)


def test_run_record_forbids_unplanned_fields() -> None:
    data = valid_run_record_data()
    data["unexpected"] = "not part of the JSONL contract"

    with pytest.raises(ValidationError):
        RunRecord.model_validate(data)
