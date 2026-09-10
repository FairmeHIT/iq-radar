from __future__ import annotations

from datetime import UTC, datetime

import pytest

from iqradar.config.schema import ModelPrice, PriceConfig, QuotaConfig
from iqradar.metrics.aggregate import aggregate_runs, calculate_iq
from iqradar.metrics.confidence import wilson_interval
from iqradar.metrics.cost import calculate_cost
from iqradar.schemas.run_record import RunRecord


def make_record(
    *,
    task_id: str,
    status: str,
    model: str = "model-a",
    effort: str = "high",
    benchmark: str = "deep-swe",
    version: str = "v1",
    input_tokens: int = 1_000_000,
    output_tokens: int = 500_000,
    cached_input_tokens: int = 0,
    wall_time_sec: float = 600.0,
    agent_steps: int = 10,
) -> RunRecord:
    return RunRecord.model_validate(
        {
            "run_id": f"{benchmark}_{model}_{effort}_{task_id}",
            "benchmark": {
                "name": benchmark,
                "version": version,
                "task_id": task_id,
                "repo": "owner/repo",
                "language": "python",
                "task_path": f"tasks/{task_id}.json",
            },
            "model": {
                "provider": "openai-compatible",
                "base_url_hash": "sha256:test",
                "name": model,
                "effort_requested": effort,
                "effort_effective": True,
            },
            "result": {
                "status": status,
                "verifier_passed": status == "passed",
                "exit_code": 0,
            },
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "agent_steps": agent_steps,
                "wall_time_sec": wall_time_sec,
            },
            "cost": {
                "input_cost": 0.0,
                "cached_input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            },
            "artifacts": {},
            "created_at": datetime(2026, 8, 3, tzinfo=UTC),
        }
    )


def make_prices() -> PriceConfig:
    return PriceConfig(
        prices={
            "model-a": ModelPrice(input_usd_per_1m=2.0, output_usd_per_1m=8.0),
            "model-b": ModelPrice(input_usd_per_1m=1.0, output_usd_per_1m=4.0),
        },
        quota=QuotaConfig(weekly_budget_usd=20.0, weekly_output_token_budget=5_000_000),
    )


@pytest.mark.parametrize(
    ("pass_rate_percent", "expected_iq"),
    [(40.0, 60.0), (60.0, 90.0), (100.0, 150.0), (-1.0, 0.0), (200.0, 150.0)],
)
def test_calculate_iq_caps_to_0_to_150(pass_rate_percent: float, expected_iq: float) -> None:
    assert calculate_iq(pass_rate_percent) == expected_iq


def test_wilson_interval_returns_95_percent_bounds() -> None:
    interval = wilson_interval(passed=3, total=5)

    assert interval.method == "wilson"
    assert interval.level == 0.95
    assert interval.lower == pytest.approx(0.2307, abs=0.0001)
    assert interval.upper == pytest.approx(0.8824, abs=0.0001)


def test_wilson_interval_handles_empty_samples() -> None:
    interval = wilson_interval(passed=0, total=0)

    assert interval.lower == 0.0
    assert interval.upper == 0.0


def test_calculate_cost_uses_model_price_table() -> None:
    cost = calculate_cost(
        input_tokens=1_000_000,
        output_tokens=500_000,
        cached_input_tokens=250_000,
        price=ModelPrice(
            input_usd_per_1m=2.0,
            cached_input_usd_per_1m=0.5,
            output_usd_per_1m=8.0,
        ),
    )

    assert cost.input_cost == pytest.approx(2.0)
    assert cost.cached_input_cost == pytest.approx(0.125)
    assert cost.output_cost == pytest.approx(4.0)
    assert cost.total_cost == pytest.approx(6.125)


def test_aggregate_runs_groups_by_benchmark_model_and_effort_and_ignores_skipped() -> None:
    records = [
        make_record(task_id="a", status="passed", output_tokens=500_000),
        make_record(task_id="b", status="failed", output_tokens=250_000),
        make_record(task_id="c", status="skipped", output_tokens=10_000),
        make_record(task_id="d", status="passed", model="model-b", effort="low", benchmark="mockbench"),
    ]

    aggregate = aggregate_runs(records, make_prices())

    summaries = {
        (summary.benchmark.name, summary.model, summary.effort): summary
        for summary in aggregate.summaries
    }
    high = summaries[("deep-swe", "model-a", "high")]
    low = summaries[("mockbench", "model-b", "low")]

    assert high.tasks_total == 2
    assert high.tasks_passed == 1
    assert high.tasks_failed == 1
    assert high.pass_rate == pytest.approx(0.5)
    assert high.pass_rate_percent == pytest.approx(50.0)
    assert high.iq == pytest.approx(75.0)
    assert high.total_cost_usd == pytest.approx(10.0)
    assert high.avg_cost_usd == pytest.approx(5.0)
    assert high.cost_per_pass_usd == pytest.approx(10.0)
    assert high.cost_per_iq_point_usd == pytest.approx(10.0 / 75.0)
    assert high.avg_wall_time_sec == pytest.approx(600.0)
    assert high.tasks_per_hour == pytest.approx(6.0)
    assert high.avg_input_tokens == pytest.approx(1_000_000.0)
    assert high.avg_output_tokens == pytest.approx(375_000.0)
    assert high.output_tokens_per_min == pytest.approx(37_500.0)
    assert high.avg_agent_steps == pytest.approx(10.0)
    assert high.agent_steps_per_hour == pytest.approx(60.0)
    assert high.quota_percent_per_task == pytest.approx(25.0)
    assert high.estimated_tasks_per_week == 4
    assert high.estimated_passes_per_week == 2
    assert high.output_quota_percent_per_task == pytest.approx(7.5)
    assert high.confidence.lower == pytest.approx(0.0945, abs=0.0001)
    assert high.confidence.upper == pytest.approx(0.9055, abs=0.0001)

    assert low.benchmark.name == "mockbench"
    assert low.model == "model-b"
    assert low.effort == "low"
    assert low.tasks_total == 1


def test_aggregate_runs_uses_null_ratios_for_zero_passes_and_zero_iq() -> None:
    records = [
        make_record(task_id="a", status="failed", output_tokens=500_000),
        make_record(task_id="b", status="timeout", output_tokens=500_000),
    ]

    summary = aggregate_runs(records, make_prices()).summaries[0]

    assert summary.tasks_total == 2
    assert summary.tasks_passed == 0
    assert summary.pass_rate == 0.0
    assert summary.iq == 0.0
    assert summary.cost_per_pass_usd is None
    assert summary.cost_per_iq_point_usd is None
    assert summary.estimated_passes_per_week == 0


def test_aggregate_runs_excludes_infra_errors_from_iq_denominator() -> None:
    """Gateway/verifier failures must not drag IQ down like model failures.

    Regression context: a published gpqa summary scored IQ 44.7 while 124 of
    198 records were URLError/TimeoutError runner errors; the model's real
    pass rate over scored tasks was ~80%.
    """
    records = [
        make_record(task_id="a", status="passed"),
        make_record(task_id="b", status="passed"),
        make_record(task_id="c", status="failed"),
        make_record(task_id="d", status="runner_error"),
        make_record(task_id="e", status="verifier_error"),
        make_record(task_id="f", status="timeout"),
    ]

    summary = aggregate_runs(records, make_prices()).summaries[0]

    assert summary.tasks_total == 6
    assert summary.tasks_scored == 4
    assert summary.infra_error_count == 2
    assert summary.tasks_passed == 2
    # scored denominator: 2 passed / (passed+failed+timeout) — the two infra
    # errors are excluded from the rate but still visible in tasks_total.
    assert summary.pass_rate == pytest.approx(0.5)
    assert summary.iq == pytest.approx(75.0)
    # Wilson interval over the scored denominator (2 of 4), not tasks_total.
    assert summary.confidence.lower == pytest.approx(0.1500, abs=0.0001)
    assert summary.confidence.upper == pytest.approx(0.8500, abs=0.0001)


def test_aggregate_runs_all_infra_errors_scores_nothing() -> None:
    records = [make_record(task_id="a", status="runner_error")]

    summary = aggregate_runs(records, make_prices()).summaries[0]

    assert summary.tasks_total == 1
    assert summary.tasks_scored == 0
    assert summary.infra_error_count == 1
    assert summary.pass_rate == 0.0
    assert summary.iq == 0.0
