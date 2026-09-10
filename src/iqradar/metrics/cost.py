from __future__ import annotations

from pydantic import Field

from iqradar.config.schema import ModelPrice, StrictConfigModel


class CostBreakdown(StrictConfigModel):
    currency: str = Field(default="USD", min_length=1)
    input_cost: float = Field(ge=0)
    cached_input_cost: float = Field(default=0.0, ge=0)
    output_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    price: ModelPrice,
) -> CostBreakdown:
    if input_tokens < 0:
        raise ValueError("input_tokens must be non-negative")
    if output_tokens < 0:
        raise ValueError("output_tokens must be non-negative")
    if cached_input_tokens < 0:
        raise ValueError("cached_input_tokens must be non-negative")

    input_cost = input_tokens / 1_000_000 * price.input_usd_per_1m
    cached_input_cost = cached_input_tokens / 1_000_000 * price.cached_input_usd_per_1m
    output_cost = output_tokens / 1_000_000 * price.output_usd_per_1m
    return CostBreakdown(
        currency=price.currency,
        input_cost=input_cost,
        cached_input_cost=cached_input_cost,
        output_cost=output_cost,
        total_cost=input_cost + cached_input_cost + output_cost,
    )
