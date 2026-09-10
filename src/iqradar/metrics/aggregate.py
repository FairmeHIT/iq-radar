from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

from iqradar.config.schema import EffortName, ModelPrice, PriceConfig
from iqradar.metrics.confidence import wilson_interval
from iqradar.metrics.cost import calculate_cost
from iqradar.metrics.quota import calculate_quota
from iqradar.schemas.run_record import RunRecord
from iqradar.schemas.summary import AggregateSummary, BenchmarkSummary, ModelEffortSummary


def calculate_iq(pass_rate_percent: float) -> float:
    return min(150.0, max(0.0, pass_rate_percent * 1.5))


#: Records with these statuses never reached a scoring verdict: the harness
#: could not reach the gateway, crashed, or the verifier itself failed. They
#: are excluded from the pass-rate denominator (and thus IQ) so gateway
#: outages are not indistinguishable from the model failing every task.
#: They stay in ``tasks_total`` and are reported as ``infra_error_count``.
INFRA_ERROR_STATUSES = frozenset({"runner_error", "verifier_error"})


def _zero_price() -> ModelPrice:
    """Fallback price for models not yet added to the price config."""
    return ModelPrice(input_usd_per_1m=0.0, output_usd_per_1m=0.0)


def aggregate_runs(records: Sequence[RunRecord], prices: PriceConfig) -> AggregateSummary:
    grouped: dict[tuple[str, str, str, EffortName], list[RunRecord]] = defaultdict(list)
    for record in records:
        if record.result.status == "skipped":
            continue
        key = (
            record.benchmark.name,
            record.benchmark.version,
            record.model.name,
            record.model.effort_requested,
        )
        grouped[key].append(record)

    summaries = [
        _summarize_group(
            benchmark_name=benchmark_name,
            benchmark_version=benchmark_version,
            model=model,
            effort=effort,
            records=group_records,
            prices=prices,
        )
        for (benchmark_name, benchmark_version, model, effort), group_records in sorted(grouped.items())
    ]
    return AggregateSummary(generated_at=datetime.now(UTC), summaries=summaries)


def _summarize_group(
    *,
    benchmark_name: str,
    benchmark_version: str,
    model: str,
    effort: EffortName,
    records: Sequence[RunRecord],
    prices: PriceConfig,
) -> ModelEffortSummary:
    tasks_total = len(records)
    tasks_scored = sum(
        1 for record in records if record.result.status not in INFRA_ERROR_STATUSES
    )
    infra_error_count = tasks_total - tasks_scored
    tasks_passed = sum(1 for record in records if record.result.status == "passed")
    tasks_failed = tasks_total - tasks_passed
    pass_rate = tasks_passed / tasks_scored if tasks_scored else 0.0
    pass_rate_percent = pass_rate * 100
    iq = calculate_iq(pass_rate_percent)

    model_price = prices.prices.get(model) or _zero_price()
    costs = [
        calculate_cost(
            input_tokens=record.usage.input_tokens,
            output_tokens=record.usage.output_tokens,
            cached_input_tokens=record.usage.cached_input_tokens,
            price=model_price,
        )
        for record in records
    ]

    total_cost_usd = sum(cost.total_cost for cost in costs)
    total_wall_time_sec = sum(record.usage.wall_time_sec for record in records)
    total_input_tokens = sum(record.usage.input_tokens for record in records)
    total_output_tokens = sum(record.usage.output_tokens for record in records)
    total_agent_steps = sum(record.usage.agent_steps for record in records)

    avg_cost_usd = total_cost_usd / tasks_total if tasks_total else 0.0
    avg_output_tokens = total_output_tokens / tasks_total if tasks_total else 0.0
    quota = calculate_quota(
        avg_cost_usd=avg_cost_usd,
        pass_rate=pass_rate,
        avg_output_tokens=avg_output_tokens,
        quota=prices.quota,
    )
    hours = total_wall_time_sec / 3600
    minutes = total_wall_time_sec / 60

    return ModelEffortSummary(
        benchmark=BenchmarkSummary(name=benchmark_name, version=benchmark_version),
        model=model,
        effort=effort,
        tasks_total=tasks_total,
        tasks_scored=tasks_scored,
        infra_error_count=infra_error_count,
        tasks_passed=tasks_passed,
        tasks_failed=tasks_failed,
        pass_rate=pass_rate,
        pass_rate_percent=pass_rate_percent,
        iq=iq,
        avg_cost_usd=avg_cost_usd,
        total_cost_usd=total_cost_usd,
        avg_wall_time_sec=total_wall_time_sec / tasks_total if tasks_total else 0.0,
        tasks_per_hour=tasks_total / hours if hours else 0.0,
        avg_input_tokens=total_input_tokens / tasks_total if tasks_total else 0.0,
        avg_output_tokens=avg_output_tokens,
        output_tokens_per_min=total_output_tokens / minutes if minutes else 0.0,
        avg_agent_steps=total_agent_steps / tasks_total if tasks_total else 0.0,
        agent_steps_per_hour=total_agent_steps / hours if hours else 0.0,
        cost_per_pass_usd=total_cost_usd / tasks_passed if tasks_passed else None,
        cost_per_iq_point_usd=total_cost_usd / iq if iq else None,
        quota_percent_per_task=quota.quota_percent_per_task,
        estimated_tasks_per_week=quota.estimated_tasks_per_week,
        estimated_passes_per_week=quota.estimated_passes_per_week,
        output_quota_percent_per_task=quota.output_quota_percent_per_task,
        confidence=wilson_interval(passed=tasks_passed, total=tasks_scored),
    )
