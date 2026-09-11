"""Question-level evaluation reports for benchmark runs.

The benchmark adapters keep their native artifacts, while this module provides a
small, adapter-independent view that is suitable for a human evaluation report:
all requested questions are listed, including questions that were never recorded,
and every recorded result has a stable outcome/failure category.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from iqradar.schemas.run_record import RunRecord


STATUS_NAMES = (
    "passed",
    "failed",
    "timeout",
    "runner_error",
    "verifier_error",
    "budget_stopped",
    "skipped",
    "not_executed",
)

FAILURE_CATEGORIES = (
    "model_wrong_answer",
    "model_empty_or_unparseable",
    "gateway_error",
    "judge_error",
    "judge_unclear",
    "harness_error",
    "verifier_error",
    "timeout",
    "budget_stopped",
    "skipped",
    "not_executed",
    "unknown_failure",
)

# These statuses are deliberately the same two statuses used by aggregate.py.
# Keeping the report's denominator explicit makes the current IQ convention
# visible without silently changing existing dashboard scores.
INFRASTRUCTURE_STATUSES = frozenset({"runner_error", "verifier_error"})


def classify_outcome(status: str, error_type: str | None = None) -> tuple[str, str | None]:
    """Return ``(outcome, failure_category)`` for one result.

    ``outcome`` is one of ``success``, ``model_failure``, ``infrastructure_error``,
    ``not_executed`` or ``skipped``. ``failure_category`` is stable across
    adapters; the original adapter-specific ``error_type`` is retained beside it.
    """
    status = str(status or "")
    error_type = str(error_type or "")
    if status == "passed":
        return "success", None
    if status == "not_executed":
        return "not_executed", "not_executed"
    if status == "skipped":
        return "skipped", "skipped"
    if status in INFRASTRUCTURE_STATUSES:
        if status == "verifier_error":
            return "infrastructure_error", "verifier_error"
        return "infrastructure_error", "gateway_error"
    if status == "timeout":
        return "model_failure", "timeout"
    if status == "budget_stopped":
        return "model_failure", "budget_stopped"
    if error_type in {"judge_error"}:
        return "infrastructure_error", "judge_error"
    if error_type in {"judge_unclear"}:
        return "model_failure", "judge_unclear"
    if error_type == "empty_response":
        return "model_failure", "model_empty_or_unparseable"
    if error_type in {"exact_match", "set_match", "numeric_match", "grid_match", "judge_incorrect"}:
        return "model_failure", "model_wrong_answer"
    if status == "runner_error":
        return "infrastructure_error", "gateway_error"
    if status == "failed":
        # DeepSWE and Harbor use verifier_failed for a valid run whose patch did
        # not pass. Other exception names are harness failures rather than model
        # answers, unless the adapter already supplied a scoring reason.
        if error_type in {"verifier_failed", "wrong_answer", "incorrect"}:
            return "model_failure", "model_wrong_answer"
        if error_type and error_type.endswith("_match"):
            return "model_failure", "model_wrong_answer"
        if error_type and error_type not in {"failed"}:
            return "infrastructure_error", "harness_error"
        return "model_failure", "unknown_failure"
    return "infrastructure_error", "unknown_failure"


def _raw_question_row(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    """Normalize one adapter-native result row without dropping useful detail."""
    status = str(row.get("status") or "failed")
    error_type = row.get("error_type")
    error_type_value = str(error_type) if error_type is not None else None
    outcome, category = classify_outcome(status, error_type_value)
    result: dict[str, Any] = {
        "index": int(row.get("index", index)),
        "task_id": str(row.get("task_id") or f"item-{index}"),
        "recorded": True,
        "status": status,
        "outcome": outcome,
        "failure_category": category,
        "error_type": error_type_value,
        "error_message": row.get("error_message"),
        "verifier_passed": status == "passed",
    }
    # Keep report-friendly answer evidence and resource data when an adapter has
    # it. Values are local artifacts and are intentionally not copied into the
    # public RunRecord schema.
    for key in (
        "prompt",
        "reference",
        "response",
        "language",
        "match",
        "extract",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "usage_estimated",
        "wall_time_sec",
        # 首 token 时延（TTFT）：仅流式调用可得，非流式正文/旧记录为 None。
        "first_token_sec",
        "first_content_sec",
    ):
        if key in row:
            result[key] = row[key]
    return result


def _record_row(record: RunRecord, index: int) -> dict[str, Any]:
    result = record.result
    outcome, category = classify_outcome(result.status, result.error_type)
    return {
        "index": index,
        "task_id": record.benchmark.task_id,
        "recorded": True,
        "status": result.status,
        "outcome": outcome,
        "failure_category": category,
        "error_type": result.error_type,
        "error_message": result.error_message_redacted,
        "verifier_passed": result.verifier_passed,
        "language": record.benchmark.language,
        "input_tokens": record.usage.input_tokens,
        "output_tokens": record.usage.output_tokens,
        "cached_input_tokens": record.usage.cached_input_tokens,
        "usage_estimated": record.usage.usage_estimated,
        "wall_time_sec": record.usage.wall_time_sec,
    }


def _not_executed_row(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    row = {
        "index": index,
        "task_id": str(item.get("task_id") or f"item-{index}"),
        "recorded": False,
        "status": "not_executed",
        "outcome": "not_executed",
        "failure_category": "not_executed",
        "error_type": "not_executed",
        "error_message": "question was not recorded by the runner",
        "verifier_passed": False,
    }
    for key in ("prompt", "reference", "language", "match", "extract"):
        if key in item:
            row[key] = item[key]
    return row


def build_evaluation_report(
    *,
    run: Mapping[str, Any],
    records: Sequence[RunRecord],
    raw_questions: Sequence[Mapping[str, Any]] = (),
    expected_questions: Sequence[Mapping[str, Any]] = (),
    expected_count: int | None = None,
) -> dict[str, Any]:
    """Build a report containing one row for every expected question.

    API-eval supplies both ``expected_questions`` and rich ``raw_questions``.
    Docker adapters generally supply only RunRecord rows, so their report still
    records every imported trial and exposes the requested-vs-recorded gap while
    making unknown question IDs explicit through the coverage fields.
    """
    raw_by_index = {
        int(row.get("index", index)): _raw_question_row(row, index)
        for index, row in enumerate(raw_questions)
        if isinstance(row, Mapping)
    }
    record_by_task = {record.benchmark.task_id: _record_row(record, index) for index, record in enumerate(records)}

    if expected_questions:
        questions: list[dict[str, Any]] = []
        for index, item in enumerate(expected_questions):
            row = raw_by_index.get(index)
            if row is None:
                task_id = str(item.get("task_id") or f"item-{index}")
                row = record_by_task.get(task_id)
            questions.append(row if row is not None else _not_executed_row(item, index))
    elif raw_by_index:
        questions = [raw_by_index[index] for index in sorted(raw_by_index)]
    else:
        questions = [_record_row(record, index) for index, record in enumerate(records)]

    expected = len(expected_questions) if expected_questions else expected_count
    if not expected_questions and expected is not None and expected > len(questions):
        # Docker adapters may know the requested count but cannot reconstruct the
        # sampled task IDs after a partial harness failure. Keep one explicit row
        # per missing slot so coverage is never mistaken for a full execution.
        questions.extend(
            _not_executed_row({}, index)
            for index in range(len(questions), int(expected))
        )

    if expected is None:
        expected = len(questions)
    expected = max(0, int(expected))
    # A catalog can be unavailable for Docker runs. In that case all imported
    # rows are still reportable, but the requested-vs-recorded count is the best
    # defensible coverage estimate.
    recorded = sum(1 for row in questions if row.get("recorded", True))
    if expected < recorded:
        expected = recorded
    status_counts = Counter(str(row.get("status") or "unknown") for row in questions)
    category_counts = Counter(
        str(row["failure_category"])
        for row in questions
        if row.get("failure_category")
    )
    error_type_counts = Counter(
        str(row["error_type"])
        for row in questions
        if row.get("error_type") and row.get("status") != "passed"
    )
    scored = sum(
        1
        for row in questions
        if row.get("status") not in INFRASTRUCTURE_STATUSES
        and row.get("status") not in {"skipped", "not_executed"}
    )
    passed = status_counts.get("passed", 0)
    coverage_rate = recorded / expected if expected else 0.0
    pass_rate = passed / scored if scored else 0.0
    return {
        "report_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "run": dict(run),
        "coverage": {
            "requested_tasks": expected,
            "recorded_tasks": recorded,
            "missing_tasks": max(0, expected - recorded),
            "coverage_rate": round(coverage_rate, 6),
            "status_counts": {name: status_counts[name] for name in STATUS_NAMES if status_counts[name]},
            "success_count": passed,
            "model_failure_count": sum(1 for row in questions if row.get("outcome") == "model_failure"),
            "infrastructure_error_count": sum(
                1 for row in questions if row.get("outcome") == "infrastructure_error"
            ),
            "not_executed_count": sum(1 for row in questions if row.get("outcome") == "not_executed"),
            "scored_count_current_metric": scored,
            "pass_rate_current_metric": round(pass_rate, 6),
            "failure_category_counts": dict(sorted(category_counts.items())),
            "error_type_counts": dict(sorted(error_type_counts.items())),
        },
        "questions": questions,
        "taxonomy": {
            "success": "verifier passed",
            "model_failure": "the question was executed but the answer/verifier score did not pass",
            "infrastructure_error": "gateway, harness, judge, or verifier prevented a trustworthy score",
            "not_executed": "the requested question has no recorded result",
            "scoring_denominator_note": "Current IQ excludes runner_error/verifier_error, counts timeout as scored, and excludes skipped.",
        },
    }
