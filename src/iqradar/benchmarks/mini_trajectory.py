"""mini-swe-agent trajectory 解析：为 TB/TB2/SWE-bench-pro 补齐 usage 采集。

三个 Docker 基准的 agent 都是 mini-swe-agent（TB1 经 ``tb_agents.MiniSweAgentCompat``、
TB2 经 harbor 内置 agent、SWE-bench-pro 经 AGENT_BOOTSTRAP 直跑）。mini 2.x 支持
``-o <path>`` 把 trajectory JSON 落盘：

- ``info.model_stats.api_calls``：LLM 调用次数 → 我们的 ``agent_steps``；
- 每条 assistant 消息带完整 litellm response（``extra.response``），其
  ``usage.prompt_tokens`` / ``completion_tokens`` / ``prompt_tokens_details.cached_tokens``
  按调用累加即 input/output/cached tokens。

上游 harness 自己不提供这些数字（TB1 的 installed-agent 结果硬编码 0，
SWE-bench-pro 的 result.json 默认 0），因此统一从 trajectory 采集；文件缺失
或无 usage 时返回 None，调用方回退到 0（大盘对 0 步显示 N/A）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: mini-swe-agent trajectory 文件名（TB1/TB2 挂载目录内的落盘名）。
MINI_TRAJECTORY_FILENAME = "mini-swe-agent.trajectory.json"


def read_mini_trajectory_usage(path: Path) -> dict[str, int] | None:
    """Parse one mini trajectory into usage totals, or None when unavailable.

    Keys: ``agent_steps`` (LLM calls), ``input_tokens``, ``output_tokens``,
    ``cached_input_tokens``. None means the trajectory is missing, unreadable
    or carries no usage at all — callers treat that as "not collected".
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    stats = (data.get("info") or {}).get("model_stats") or {}
    steps = _int(stats.get("api_calls") if isinstance(stats, dict) else 0)
    input_tokens = 0
    output_tokens = 0
    cached_tokens = 0
    messages = data.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            usage = _message_usage(message)
            input_tokens += _int(usage.get("prompt_tokens"))
            output_tokens += _int(usage.get("completion_tokens"))
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                cached_tokens += _int(details.get("cached_tokens"))
    if steps == 0 and input_tokens == 0 and output_tokens == 0:
        return None
    return {
        "agent_steps": steps,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_tokens,
    }


def _message_usage(message: dict[str, Any]) -> dict[str, Any]:
    """Normalized usage of one trajectory message.

    Chat-completions (LitellmModel, our gateways): full response under
    ``extra.response`` with ``prompt_tokens``/``completion_tokens``.
    Responses-API models put usage at the message top level with
    ``input_tokens``/``output_tokens`` — mirror harbor's tolerant lookup and
    normalization so both shapes accumulate.
    """
    response = (message.get("extra") or {}).get("response")
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        usage = message.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
        "completion_tokens": (
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        ),
        "prompt_tokens_details": (
            usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
        ),
    }


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
