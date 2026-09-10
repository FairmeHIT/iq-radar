"""mini-swe-agent trajectory 解析：TB/TB2/SWE-bench-pro usage 采集的数据源。"""

from __future__ import annotations

import json
from pathlib import Path

from iqradar.benchmarks.mini_trajectory import read_mini_trajectory_usage


def _trajectory(api_calls: int, calls: list[dict]) -> dict:
    return {
        "info": {"model_stats": {"instance_cost": 0.12, "api_calls": api_calls}},
        "messages": [
            {
                "role": "assistant",
                "content": "doing things",
                "extra": {"response": {"usage": call}},
            }
            for call in calls
        ],
        "trajectory_format": "mini-swe-agent-1.1",
    }


def test_parses_steps_and_tokens_including_cached(tmp_path: Path) -> None:
    path = tmp_path / "mini-swe-agent.trajectory.json"
    path.write_text(
        json.dumps(
            _trajectory(
                3,
                [
                    {
                        "prompt_tokens": 1200,
                        "completion_tokens": 80,
                        "prompt_tokens_details": {"cached_tokens": 900},
                    },
                    {
                        "prompt_tokens": 1500,
                        "completion_tokens": 40,
                        "prompt_tokens_details": {"cached_tokens": 1200},
                    },
                    {
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "prompt_tokens_details": None,
                    },
                ],
            )
        ),
        encoding="utf-8",
    )
    usage = read_mini_trajectory_usage(path)
    assert usage == {
        "agent_steps": 3,
        "input_tokens": 2800,
        "output_tokens": 130,
        "cached_input_tokens": 2100,
    }


def test_returns_none_for_missing_or_empty_trajectory(tmp_path: Path) -> None:
    assert read_mini_trajectory_usage(tmp_path / "absent.json") is None
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    assert read_mini_trajectory_usage(empty) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_mini_trajectory_usage(broken) is None


def test_accepts_responses_api_usage_shape(tmp_path: Path) -> None:
    """Responses-API 模型把 usage 放在消息顶层（与 harbor 的容错查找一致）。"""
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps(
            {
                "info": {"model_stats": {"api_calls": 1}},
                "messages": [
                    {
                        "role": "assistant",
                        "object": "response",
                        "usage": {
                            "input_tokens": 500,
                            "output_tokens": 20,
                            "input_tokens_details": {"cached_tokens": 100},
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    usage = read_mini_trajectory_usage(path)
    assert usage == {
        "agent_steps": 1,
        "input_tokens": 500,
        "output_tokens": 20,
        "cached_input_tokens": 100,
    }
