from __future__ import annotations

from math import sqrt

from iqradar.schemas.summary import ConfidenceInterval


def wilson_interval(passed: int, total: int, z: float = 1.96) -> ConfidenceInterval:
    if passed < 0:
        raise ValueError("passed must be non-negative")
    if total < 0:
        raise ValueError("total must be non-negative")
    if passed > total:
        raise ValueError("passed cannot exceed total")
    if total == 0:
        return ConfidenceInterval(lower=0.0, upper=0.0)

    p = passed / total
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    margin = z * sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator
    return ConfidenceInterval(
        lower=max(0.0, center - margin),
        upper=min(1.0, center + margin),
    )
