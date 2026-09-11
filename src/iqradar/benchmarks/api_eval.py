from __future__ import annotations

import concurrent.futures
import json
import random
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iqradar.benchmarks.base import BenchmarkBackend
from iqradar.config.schema import BenchmarkConfig, PriceConfig
from iqradar.ingest.redact import redact_secret_text
from iqradar.metrics.cost import calculate_cost
from iqradar.schemas.run_record import RunRecord
from iqradar.settings.inference import (
    InferenceEndpoint,
    resolve_inference_endpoint,
)
from iqradar.shared.atomic_files import atomic_write_text
# 单次 chat/completions 调用超时（秒）。复杂推理题（GPQA + effort=high）
# 单次生成可超过一分钟，60s 会误截断整题；基准配置可用
# ``request_timeout_sec`` 覆盖（见 BenchmarkConfig）。
DEFAULT_REQUEST_TIMEOUT_SEC = 120

# 网关失败重测：全部题目跑完后的默认重测轮数上限（请求未指定时的缺省）。
DEFAULT_GATEWAY_RETRY_ROUNDS = 2

# 日志时间戳使用北京时间（Asia/Shanghai），方便快速定位问题发生时间。
_LOG_TZ = ZoneInfo("Asia/Shanghai")

MatchKind = Literal["exact", "set", "numeric", "judge", "grid"]
ExtractKind = Literal["raw", "last_line", "boxed", "final", "json"]

_FINAL_ANSWER_PATTERNS = (
    r"(?im)final\s+answer\s*(?:is|are)?\s*[:：=]\s*(.+)$",
    r"(?im)(?:the\s+)?answer\s*(?:is|are)?\s*[:：=]\s*(.+)$",
    r"(?im)(?:答案是|答案|答)\s*[:：=]\s*(.+)$",
)

JUDGE_PROMPT_TEMPLATE = (
    "You are a strict answer grader. Given a question, a reference answer, and "
    "a candidate answer, decide whether the candidate answer is correct.\n\n"
    "Question: {question}\n\n"
    "Reference answer: {reference}\n\n"
    "Candidate answer: {candidate}\n\n"
    "Reply with exactly one word: CORRECT or INCORRECT."
)


class ApiEvalItem(BaseModel):
    """One question in an api-eval dataset JSONL file.

    ``extra="ignore"`` keeps real-world datasets (which carry extra metadata
    such as subject/difficulty) loadable without a schema migration; only
    ``task_id``/``prompt``/``reference`` are required.

    ``extract`` selects how the final answer is pulled out of the model
    response before scoring (useful for reasoning models that interleave
    chain-of-thought with the answer). ``match="judge"`` scores with an LLM
    judge instead of string matching.
    """

    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    language: str = "en"
    match: MatchKind = "exact"
    extract: ExtractKind = "raw"
    tolerance: float = 1e-6


# ---------------------------------------------------------------------------
# Dataset loading and sampling
# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> list[ApiEvalItem]:
    """Read an api-eval question set (one JSON object per line).

    Malformed lines and lines failing validation are skipped, matching the
    lenient behaviour of the other backends' dataset loaders.
    """
    if not path.is_file():
        return []
    items: list[ApiEvalItem] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            try:
                items.append(ApiEvalItem.model_validate(data))
            except ValidationError:
                continue
    return items


def sample_items(
    items: list[ApiEvalItem], n_tasks: int, sample_seed: int
) -> list[ApiEvalItem]:
    """Deterministic sampling: shuffle with the seed, take the first n."""
    ordered = list(items)
    random.Random(sample_seed).shuffle(ordered)
    return ordered[: max(0, n_tasks)]


# ---------------------------------------------------------------------------
# Answer normalization and scoring (pure functions)
# ---------------------------------------------------------------------------

def normalize_answer(text: str) -> str:
    """Normalize a single answer for exact comparison.

    Strips surrounding punctuation, lowercases, applies NFKC, unwraps LaTeX
    math delimiters (``\\boxed{}``, ``$``, ``\\(...\\)``), and collapses
    whitespace. Separator characters are left intact so set matching can split
    before this step.
    """
    value = str(text).strip()
    value = unicodedata.normalize("NFKC", value)
    value = value.lower()
    value = re.sub(r"\\boxed\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\left\.|\\right\.", "", value)
    value = value.replace("$", "")
    value = re.sub(r"\\[()\[\]]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.strip(" \t\n\r'\".:,;")


def normalize_numeric(text: str) -> str:
    """Normalize a numeric answer: strip thousands separators and trailing .0."""
    value = normalize_answer(text)
    value = value.replace(",", "")
    value = re.sub(r"\.0+$", "", value)
    return value


def normalize_set(text: str) -> set[str]:
    """Split a set-style answer on separators and normalize each element."""
    tokens = re.split(r"[,;/|]|\s+", str(text))
    return {normalize_answer(token) for token in tokens if normalize_answer(token)}


def extract_answer(text: str, kind: str) -> str:
    """Pull the final answer out of a model response.

    - ``raw``: the whole response;
    - ``last_line``: the last non-empty line;
    - ``boxed``: the last ``\\boxed{...}`` group (falls back to raw);
    - ``final``: text after the last "final answer/answer/答案/答:" marker
      (falls back to the last non-empty line).
    """
    value = (text or "").strip()
    if not value:
        return ""
    if kind == "last_line":
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        return lines[-1] if lines else value
    if kind == "boxed":
        matches = re.findall(r"\\boxed\s*\{([^{}]*)\}", value)
        return matches[-1] if matches else value
    if kind == "json":
        grid = extract_json_answer(value)
        return json.dumps(grid, ensure_ascii=False) if grid is not None else value
    if kind == "final":
        for pattern in _FINAL_ANSWER_PATTERNS:
            matches = re.findall(pattern, value)
            if matches:
                return matches[-1].strip()
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        return lines[-1] if lines else value
    return value


def parse_judge_verdict(text: str) -> bool | None:
    """Parse an LLM judge verdict. Returns None when the verdict is unclear."""
    lowered = re.sub(r"\s+", " ", (text or "").lower())
    if re.search(r"\bincorrect\b", lowered):
        return False
    if re.search(r"\bnot\s+correct\b", lowered):
        return False
    if re.search(r"\bcorrect\b", lowered):
        return True
    return None


def judge_prompt(question: str, reference: str, candidate: str) -> str:
    return JUDGE_PROMPT_TEMPLATE.format(
        question=question, reference=reference, candidate=candidate
    )


def score_item(item: ApiEvalItem, model_answer: str) -> tuple[bool, str]:
    """Score an extracted model answer against ``item.reference``.

    ``match="judge"`` is *not* handled here (it needs a gateway call); the
    backend routes judge items before this function. Returns ``(correct,
    reason)`` where ``reason`` is a short, stable tag.

    When ``match="exact"`` and the reference is a single choice letter (A–E),
    the function also tries to extract the letter from the model's answer
    directly. This handles reasoning models that produce verbose output
    like "The correct answer is **(D) 1, 2, 4**." — the standard extraction
    captures the whole clause, which normalises differently from the bare
    letter ``D``.
    """
    answer = extract_answer(model_answer, item.extract).strip()
    reference = normalize_answer(item.reference)
    if not answer:
        # 多选题：即便 final/last_line 抽取为空，也尝试从回答中识别选项字母
        if re.fullmatch(r"[A-J]", reference, re.I):
            letter = _extract_choice_letter(model_answer)
            if letter:
                answer = letter
            else:
                return False, "empty_response"
        else:
            return False, "empty_response"
    if item.match == "set":
        correct = normalize_set(answer) == normalize_set(item.reference)
        return correct, "set_match"
    if item.match == "numeric":
        return _numeric_match(answer, item.reference, item.tolerance), "numeric_match"
    if item.match == "grid":
        return _grid_match(model_answer, item.reference, extracted_answer=answer), "grid_match"
    # exact: 归一化比较；若参考答案是单个选项字母，额外用字母抽取兜底
    if normalize_answer(answer) == reference:
        return True, "exact_match"
    if re.fullmatch(r"[A-J]", reference, re.I):
        letter = _extract_choice_letter(model_answer)
        if letter and letter.lower() == reference:
            return True, "exact_match"
    return False, "exact_match"


def _extract_choice_letter(text: str) -> str:
    """Best-effort extraction of a single MCQ choice letter from model output.

    Returns the uppercase letter, or ``""`` when none is found.
    """
    tail = (text or "").strip()[-600:]
    # 1. "Answer is (J)" or "Answer: J" or "answer: (J)" — A..J covers MMLU-Pro's 10 options
    for pat in (
        r"(?im)(?:the\s+)?(?:final\s+)?answer\s*(?:is|are|:|=)\s*[\(\[]?\s*([A-J])\s*[\)\]]?",
        # 2. "option/choice (J)"
        r"(?im)\b(?:option|choice)\s*\(\s*([A-J])\s*\)",
        # 3. parenthesised letter at end of line
        r"(?im)\(([A-J])\)\s*$",
    ):
        m = re.search(pat, tail)
        if m:
            return m.group(1).upper()
    # 4. last parenthesised letter in the tail (heuristic)
    letters = re.findall(r"\(([A-J])\)", tail)
    if letters:
        return letters[-1].upper()
    return ""


def _numeric_match(model_answer: str, reference: str, tolerance: float) -> bool:
    try:
        model_value = float(normalize_numeric(model_answer))
        reference_value = float(normalize_numeric(reference))
    except ValueError:
        return False
    return abs(model_value - reference_value) <= tolerance


# ---------------------------------------------------------------------------
# Grid answers (ARC-style 2-D integer grids, e.g. ARC-AGI-2)
# ---------------------------------------------------------------------------

def extract_json_answer(text: str) -> list | None:
    """Extract the last valid JSON array (a 2-D grid) from a model response.

    Handles markdown fences (````` ```json ... ``` ````), a JSON object
    wrapper such as ``{"output": [[...]]}``, and bare arrays. Returns ``None``
    when no valid array can be parsed.
    """
    value = (text or "").strip()
    if not value:
        return None
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", value)
    candidates = list(fenced)
    output_match = re.search(r'"output"\s*:\s*(\[[\s\S]*\])', value)
    if output_match:
        candidates.append(output_match.group(1))
    candidates.append(value)
    for candidate in candidates:
        parsed = _parse_json_array(candidate)
        if parsed is not None:
            return parsed
    return None


def _parse_json_array(text: str) -> list | None:
    """Parse ``text`` as a JSON array, or find the first bracket-balanced
    substring that does (used when the response carries extra prose around the
    grid). Returns ``None`` when nothing parses.
    """
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    for match in re.finditer(r"\[", text):
        start = match.start()
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, list):
                        return parsed
                    break
    return None


def _grid_match(
    model_answer: str,
    reference: str,
    extracted_answer: str | None = None,
) -> bool:
    """Structural equality of two ARC-style grids (nested lists of ints).

    Tries the extracted answer first, then falls back to the full model
    response (which ``extract_json_answer`` can pull the grid out of). Either
    side may be wrapped in an object with an ``output`` key (e.g.
    ``{"output": [[...]]}``); the reference is a plain JSON array as stored by
    the dataset converter. Any parse failure or shape mismatch scores False.
    """
    def _find(text: str | None) -> list | None:
        if not text or not text.strip():
            return None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = extract_json_answer(text)
        if isinstance(parsed, dict):
            parsed = parsed.get("output")
        return parsed if isinstance(parsed, list) else None

    answer = _find(extracted_answer)
    if answer is None:
        answer = _find(model_answer)
    if answer is None:
        return False
    try:
        reference_grid = json.loads(reference)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(reference_grid, list):
        return False
    return answer == reference_grid


# ---------------------------------------------------------------------------
# Gateway-failure classification (网关失败重测)
# ---------------------------------------------------------------------------

# 视为「临时」的网关 HTTP 状态码：请求超时(408)、过早(425)、限流(429)、
# 全部 5xx（网关/上游临时故障）。401/403/404 等配置类错误重测无意义，不含。
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429} | set(range(500, 600)))


def is_gateway_failure(record: dict[str, Any]) -> bool:
    """该题结果是否属于「上游网关超时/临时错误导致的测试失败」（值得重测）。

    - ``runner_error``：模型调用本身失败。HTTP 错误仅当状态码属于临时类
      （408/425/429/5xx）才重测，401/403/404 等配置问题重测无意义；其余
      传输层错误（URLError/Timeout/StreamError/响应解析失败）按临时处理。
    - ``verifier_error`` + ``empty_response``：网关调用成功但没返回任何内容。
    - ``timeout``：整个 run 到达时限，该题根本没有被执行过。
    - ``failed`` + ``judge_error``：模型已作答，但 LLM judge 的网关调用失败，
      该题从未被真正评分。

    模型答错（``failed`` + ``*_match`` / ``judge_incorrect``，以及模型有输出
    但抽不出答案的 ``empty_response``）不属于网关失败，不重测。
    """
    status = str(record.get("status") or "")
    error_type = str(record.get("error_type") or "")
    if status == "runner_error":
        return _is_transient_gateway_error(
            error_type, str(record.get("error_message") or "")
        )
    if status == "verifier_error":
        return error_type == "empty_response"
    if status == "timeout":
        return True
    if status == "failed":
        return error_type == "judge_error"
    return False


def _is_transient_gateway_error(error_type: str, error_message: str) -> bool:
    if error_type == "HTTPError":
        match = re.match(r"HTTPError:(\d{3}):", error_message)
        if match:
            return int(match.group(1)) in TRANSIENT_HTTP_STATUSES
        return True  # 状态码解析不出：宁可多重测一次
    return True  # 传输层错误（超时/连接/流中断/坏响应）按临时处理


def _read_result_lines(
    path: Path,
) -> list[tuple[str, dict[str, Any] | None]] | None:
    """``results.jsonl`` → ``[(原始行, 解析结果或 None), ...]``，保持行序。

    行号即题目下标（顺序/并行执行都按 items 顺序逐行落盘），所以重测时必须
    原样保留无法解析的行，不能跳过。返回 None 表示文件不可读。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    records: list[tuple[str, dict[str, Any] | None]] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            records.append((raw, None))
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        records.append((raw, data if isinstance(data, dict) else None))
    return records


def _write_result_records(
    path: Path,
    lines: list[tuple[str, dict[str, Any] | None]],
    updates: dict[int, dict[str, Any]],
) -> None:
    """把重测产生的新结果原位替换进 ``results.jsonl``（其余行原样保留）。"""
    payload = [
        json.dumps(updates[index], ensure_ascii=True)
        if index in updates
        else raw
        for index, (raw, _) in enumerate(lines)
    ]
    atomic_write_text(path, "\n".join(payload) + "\n")


# ---------------------------------------------------------------------------
# Gateway client (stdlib urllib, no extra dependency)
# ---------------------------------------------------------------------------

def gateway_complete(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    timeout_sec: int = DEFAULT_REQUEST_TIMEOUT_SEC,
    temperature: float = 0.0,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
    cancel_event: Event | None = None,
    timing: dict[str, float] | None = None,
) -> tuple[str | None, dict[str, int], str | None]:
    """POST one chat completion to an OpenAI-compatible gateway (streaming).

    Uses ``stream: true`` so the gateway sends the first byte as soon as
    generation starts.  This avoids connect-stage timeouts (e.g. the
    MobileWork signing proxy's 60 s non-streaming connect timeout) because
    the proxy sees a response header immediately and switches to the
    generous stream-idle timeout.  Falls back to non-streaming when the
    gateway rejects ``stream: true`` (400) or returns a non-SSE body.

    Returns ``(content, usage, error)``:
    - success: ``content`` is the model text (possibly ``""`` when the gateway
      returned no text) and ``error`` is ``None``;
    - failure: ``content`` is ``None`` and ``error`` describes the problem.

    When *cancel_event* is set, the SSE read loop stops between chunks and
    returns whatever content accumulated so far (possibly ``""``). This lets a
    user-requested interrupt unwind a long streaming generation within seconds
    instead of waiting for the full per-call timeout.

    When *timing* is a dict it is filled in place with the stream timings that
    do not fit the return value (see :func:`_parse_sse_stream`):
    ``first_token_sec`` (first SSE delta, reasoning included) and
    ``first_content_sec`` (first visible ``content`` delta). Both are measured
    from just before the HTTP request is issued, so they include connect time.
    Callers that do not need them pass nothing and nothing changes.
    """
    endpoint = InferenceEndpoint(base_url, api_key)
    url = endpoint.chat_completions_url()
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "stream": True,
    }
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    headers = endpoint.auth_headers()
    headers["Accept"] = "text/event-stream"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    # 首 token 时延的计时起点：包含建连与请求发送，与客户端常见的
    # 「从发起请求到第一个 token」定义一致。
    started_at = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                return _parse_sse_stream(
                    response,
                    timeout_sec,
                    cancel_event,
                    started_at=started_at,
                    timing=timing,
                )
            # Gateway ignored stream:true and returned a single JSON body.
            raw = response.read().decode("utf-8", errors="replace")
            return _parse_non_stream_json(raw)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        return None, {}, f"HTTPError:{error.code}:{redact_secret_text(detail, (api_key,))}"
    except (OSError, urllib.error.URLError) as error:
        return None, {}, f"{error.__class__.__name__}:{error}"


def _parse_sse_stream(
    response: object,
    timeout_sec: int,
    cancel_event: Event | None = None,
    *,
    started_at: float | None = None,
    timing: dict[str, float] | None = None,
) -> tuple[str | None, dict[str, int], str | None]:
    """Read an SSE chat-completion stream and accumulate content + usage.

    OpenAI-compatible SSE chunks carry ``choices[0].delta`` with incremental
    ``content`` / ``reasoning_content`` text and an optional ``usage`` in the
    final chunk.  We concatenate all deltas; the final text is scored exactly
    like a non-streaming response (``_message_text`` rules apply).

    When *timing* and *started_at* are given, the first-delta timings are
    recorded into *timing* (seconds since *started_at*):

    - ``first_token_sec`` — first delta carrying any text, ``reasoning_content``
      included; this is the 首 token 时延（TTFT）as seen by a client;
    - ``first_content_sec`` — first delta carrying visible ``content``, which is
      later than ``first_token_sec`` when the model streams reasoning first.

    Role-only or empty deltas do not count as a token. Timings are absent when
    the gateway answered with a non-streaming body, or when no text arrived.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, int] = {}
    has_content = False
    has_reasoning = False

    def mark(kind: str) -> None:
        if timing is None or started_at is None:
            return
        elapsed = round(time.monotonic() - started_at, 3)
        timing.setdefault("first_token_sec", elapsed)
        if kind == "content":
            timing.setdefault("first_content_sec", elapsed)

    try:
        for raw_line in response:
            if cancel_event is not None and cancel_event.is_set():
                break
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if not isinstance(chunk, dict):
                continue
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage = _extract_usage(chunk_usage)
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if not isinstance(delta, dict):
                continue
            c = delta.get("content")
            if isinstance(c, str) and c:
                content_parts.append(c)
                has_content = True
                mark("content")
            rc = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(rc, str) and rc:
                reasoning_parts.append(rc)
                has_reasoning = True
                mark("reasoning")
    except (OSError, TimeoutError) as error:
        # If we already have partial content, return it as-is (the stream was
        # truncated mid-way, but the answer might still be parseable).
        if content_parts or reasoning_parts:
            pass
        else:
            return None, usage, f"StreamError:{error.__class__.__name__}:{error}"

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    if content.strip():
        return content, usage, None
    if reasoning.strip():
        return reasoning, usage, None
    return "", usage, None


def _parse_non_stream_json(
    raw: str,
) -> tuple[str | None, dict[str, int], str | None]:
    """Parse a classic non-streaming JSON response body."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        return None, {}, f"JSONDecodeError:{error}"
    if not isinstance(data, dict):
        return None, {}, "invalid response shape"
    content: str = ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = _message_text(message)
    return content, _extract_usage(data.get("usage")), None


def _message_text(message: dict[str, Any]) -> str:
    """Text used for scoring from a chat-completions ``message``.

    Prefer visible ``content``. Bifrost / DeepSeek-style gateways often return
    an empty ``content`` and put the chain-of-thought (including the final
    answer) in ``reasoning``, ``reasoning_content``, or ``reasoning_details``.
    """
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    details = message.get("reasoning_details")
    if isinstance(details, list):
        parts: list[str] = []
        for item in details:
            if not isinstance(item, dict):
                continue
            text = item.get("text") or item.get("content")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        if parts:
            return "\n".join(parts)
    return content if isinstance(content, str) else ""


def _extract_usage(usage: object) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    result: dict[str, int] = {
        "input_tokens": _int(usage.get("prompt_tokens")),
        "output_tokens": _int(usage.get("completion_tokens")),
        "cached_input_tokens": 0,
    }
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        result["cached_input_tokens"] = _int(details.get("cached_tokens"))
    return result


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class ApiEvalBackend(BenchmarkBackend):
    """Lightweight API-only benchmark: no Docker, no subprocess, no checkout.

    ``run()`` executes synchronously inside the worker thread: it samples the
    configured question set, calls the gateway once per question, scores with
    exact/normalized match, and writes ``results.jsonl`` + ``result.json``
    under ``jobs_root/<run_id>/``. ``import_records()`` turns those raw results
    into ``RunRecord`` rows for the existing aggregation pipeline.
    """

    benchmark_type = "api-eval"
    log_source_names = ("run", "results")

    def __init__(
        self,
        config: BenchmarkConfig,
        *,
        name: str,
        run_log_path: Callable[[str], Path] | None = None,
        gateway_settings: object | None = None,
    ) -> None:
        self.name = name
        self._config = config
        self.jobs_root = config.artifact_root
        self.env_file = config.resolved_env_file()
        self.default_timeout_sec = config.default_timeout_sec
        self.n_concurrent = config.default_concurrency
        self._gateway = gateway_settings
        # 单次网关调用超时：基准配置可选覆盖，缺省用模块默认值
        self.request_timeout_sec = config.request_timeout_sec or DEFAULT_REQUEST_TIMEOUT_SEC
        # 单次调用输出 token 预算：缺省不传（用网关默认上限）
        self.max_tokens = config.max_tokens
        self._run_log_path = run_log_path or (
            lambda run_id: self.jobs_root / run_id / "run.log"
        )
        self._dataset_path = config.tasks_path
        self._repo = config.repo_url
        self._active: set[str] = set()
        # 题目实时进度（run_id -> {total, completed}）：执行线程每完成一题
        # 更新一次（顺序/并行两条路径都会走），结束后保留最后值；服务重启
        # 丢掉内存态时 progress() 回退数 results.jsonl 行数。
        self._progress: dict[str, dict[str, int]] = {}
        self._progress_lock = Lock()

    # --- runner lifecycle ---------------------------------------------------

    def base_url(self) -> str:
        return resolve_inference_endpoint(self._gateway).base_url

    def preflight(self) -> str | None:
        """Runnable check for the service layer: dataset file readable."""
        if not self._dataset_path.is_file():
            return f"tasks file does not exist: {self._dataset_path}"
        try:
            items = load_dataset(self._dataset_path)
        except (OSError, ValueError) as exc:
            return f"tasks file unreadable: {self._dataset_path} ({exc})"
        if not items:
            return f"no items found in {self._dataset_path}"
        return None

    def task_count(self) -> int:
        """Number of questions in the configured dataset (the runnable pool)."""
        return len(load_dataset(self._dataset_path))

    def question_catalog(
        self, *, n_tasks: int, sample_seed: int
    ) -> list[dict[str, object]]:
        """Return the exact deterministic question sample used by a run."""
        return [
            {
                "task_id": item.task_id,
                "prompt": item.prompt,
                "reference": item.reference,
                "language": item.language,
                "match": item.match,
                "extract": item.extract,
            }
            for item in sample_items(load_dataset(self._dataset_path), n_tasks, sample_seed)
        ]

    def max_tasks(self) -> int:
        """Upper bound on ``n_tasks``: the dataset size, with a 117 fallback
        only when the dataset is empty (so validation never reports the
        confusing ``between 1 and 0``)."""
        return self.task_count() or 117

    def runner_marker(self, run_id: str) -> str:
        # No host subprocess to track; liveness comes from ``runner_alive``.
        return ""

    def runner_alive(self, run_id: str) -> bool:
        return run_id in self._active

    def mark_active(self, run_id: str) -> None:
        # 由 service 在派发 worker 线程前调用，消除「线程已起、_active
        # 尚未 add」的窗口（reconcile 据此避免抢先标完成）。
        self._active.add(run_id)

    def release_active(self, run_id: str) -> None:
        self._active.discard(run_id)

    def job_finished(self, run_id: str) -> bool:
        result = self.jobs_root / run_id / "result.json"
        if not result.is_file():
            return False
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(isinstance(data, dict) and data.get("finished_at"))

    def has_partial_results(self, run_id: str) -> bool:
        """True when *run_id* has a partially written results file and no
        finished marker (i.e. the run was interrupted mid-execution)."""
        results = self.jobs_root / run_id / "results.jsonl"
        return results.is_file() and not self.job_finished(run_id)

    # --- question progress --------------------------------------------------

    def _set_progress(self, run_id: str, *, total: int, completed: int) -> None:
        with self._progress_lock:
            self._progress[run_id] = {"total": total, "completed": completed}

    def progress(self, run_id: str) -> dict[str, int] | None:
        """题目进度：优先用执行线程实时维护的计数。

        回退路径（服务重启后 / 历史已完成 run）：``results.jsonl`` 每行一题，
        且只在 run 正常结束（result.json 落盘）后才完整，此时
        completed == total == 行数；未结束又无内存态时返回 None。
        """
        with self._progress_lock:
            live = self._progress.get(run_id)
            if live is not None:
                return dict(live)
        if not self.job_finished(run_id):
            return None
        results = self.jobs_root / run_id / "results.jsonl"
        if not results.is_file():
            return None
        try:
            completed = results.read_text(encoding="utf-8").count("\n")
        except OSError:
            return None
        if completed <= 0:
            return None
        return {"total": completed, "completed": completed}

    def _resume_offset(self, resume_run_id: str | None, run_dir: Path) -> int:
        """Number of items already completed in *resume_run_id*'s run.

        Sequential execution writes results in item order, so the line count
        of the previous run's ``results.jsonl`` is exactly the number of items
        to skip. Those lines are copied into ``run_dir`` so the resumed run
        publishes the full record set.
        """
        if not resume_run_id:
            return 0
        prev_path = self.jobs_root / resume_run_id / "results.jsonl"
        if not prev_path.is_file():
            return 0
        try:
            previous = prev_path.read_text(encoding="utf-8")
        except OSError:
            return 0
        count = previous.count("\n")
        if count > 0:
            results_path = run_dir / "results.jsonl"
            results_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                results_path.write_text(previous, encoding="utf-8")
            except OSError:
                return 0
        return count

    # --- execution ----------------------------------------------------------

    def run(
        self,
        *,
        run_id: str,
        model_name: str,
        n_tasks: int,
        sample_seed: int,
        log_path: Path,
        cancel_event: Event | None = None,
        effort: str = "high",
        resume_run_id: str | None = None,
        n_concurrent: int | None = None,
        retry_gateway_failures: bool = False,
        gateway_retry_rounds: int | None = None,
    ) -> tuple[int, str]:
        """Execute the benchmark for ``run_id``; returns (returncode, error).

        *retry_gateway_failures*（网关失败重测）：全部题目跑完后检查
        ``results.jsonl``，仅对因网关超时/临时错误失败的题目重新调用网关
        （模型答错的题目不重测），最多 *gateway_retry_rounds* 轮（缺省 2）。
        """
        items = sample_items(load_dataset(self._dataset_path), n_tasks, sample_seed)
        if not items:
            return 1, f"no items found in {self._dataset_path}"
        run_dir = self.jobs_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        start_index = self._resume_offset(resume_run_id, run_dir)
        self._set_progress(run_id, total=len(items), completed=0)
        _append_log(
            log_path,
            _log_line(
                f"开始评测 {self.name}: model={_gateway_model_name(model_name)} "
                f"tasks={len(items)} concurrency={n_concurrent or self.n_concurrent}"
            ),
        )
        endpoint = resolve_inference_endpoint(self._gateway)
        base_url = endpoint.base_url
        api_key = endpoint.api_key
        model = _gateway_model_name(model_name)
        self._active.add(run_id)
        try:
            concurrency = (
                n_concurrent if n_concurrent and n_concurrent > 0 else self.n_concurrent
            )
            cancelled = self._execute_items(
                items=items,
                run_dir=run_dir,
                model=model,
                base_url=base_url,
                api_key=api_key,
                log_path=log_path,
                cancel_event=cancel_event,
                effort=effort,
                start_index=start_index,
                concurrency=concurrency,
            )
            if cancelled:
                return 1, "cancelled by user"
            if retry_gateway_failures:
                rounds = (
                    gateway_retry_rounds
                    if gateway_retry_rounds and gateway_retry_rounds > 0
                    else DEFAULT_GATEWAY_RETRY_ROUNDS
                )
                cancelled = self._retry_gateway_failures(
                    run_dir=run_dir,
                    items=items,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    log_path=log_path,
                    cancel_event=cancel_event,
                    effort=effort,
                    concurrency=concurrency,
                    max_rounds=rounds,
                )
                if cancelled:
                    return 1, "cancelled by user"
            self._write_result_json(run_dir)
            _append_log(log_path, _log_line(f"评测完成 {self.name}: tasks={len(items)}"))
            return 0, ""
        finally:
            self._active.discard(run_id)

    def retry_gateway_failures(
        self,
        *,
        run_id: str,
        n_tasks: int,
        sample_seed: int,
        model_name: str,
        n_concurrent: int | None = None,
        max_rounds: int = 1,
        effort: str = "high",
        cancel_event: Event | None = None,
        log_path: Path,
    ) -> tuple[int, str]:
        """重测一个已完成 api-eval run 中因网关超时/临时错误失败的题目。

        复用 :meth:`_retry_gateway_failures`：按 ``n_tasks``/``sample_seed``
        重建题目集，读 ``results.jsonl`` 挑出网关失败行，逐题重新调用网关并
        原位替换。模型答错的题不动。重测期间先移除 ``result.json`` 的完成
        标记（让进度回退、防止 reconcile 误判），结束后重建。返回
        ``(returncode, error)``，``returncode == 1`` 表示被用户取消。
        """
        items = sample_items(load_dataset(self._dataset_path), n_tasks, sample_seed)
        if not items:
            return 1, f"no items found in {self._dataset_path}"
        run_dir = self.jobs_root / run_id
        results_path = run_dir / "results.jsonl"
        if not results_path.is_file():
            return 1, "no results to retry"
        endpoint = resolve_inference_endpoint(self._gateway)
        concurrency = (
            n_concurrent if n_concurrent and n_concurrent > 0 else self.n_concurrent
        )
        model = _gateway_model_name(model_name)
        result_marker = run_dir / "result.json"
        had_marker = result_marker.is_file()
        if had_marker:
            try:
                result_marker.unlink()
            except OSError:
                pass
        self._active.add(run_id)
        try:
            cancelled = self._retry_gateway_failures(
                run_dir=run_dir,
                items=items,
                model=model,
                base_url=endpoint.base_url,
                api_key=endpoint.api_key,
                log_path=log_path,
                cancel_event=cancel_event,
                effort=effort,
                concurrency=concurrency,
                max_rounds=max(1, max_rounds),
            )
            self._write_result_json(run_dir)
            return (1, "cancelled by user") if cancelled else (0, "")
        finally:
            self._active.discard(run_id)

    def run_questions(
        self, *, run_id: str, n_tasks: int, sample_seed: int
    ) -> list[dict[str, Any]]:
        """读取一个 run 的逐题结果（``results.jsonl``），附题目下标。

        供评测记录的题目级重测 UI 使用：前端按 ``task_id`` 展示每题状态，
        并允许对任意题目（无论成败）发起重测。文件不可读时返回空列表。
        """
        results_path = self.jobs_root / run_id / "results.jsonl"
        lines = _read_result_lines(results_path)
        if lines is None:
            return []
        items = sample_items(load_dataset(self._dataset_path), n_tasks, sample_seed)
        questions: list[dict[str, Any]] = []
        for index, (_, record) in enumerate(lines):
            if record is None:
                continue
            entry = dict(record)
            entry["index"] = index
            if not entry.get("task_id"):
                entry["task_id"] = (
                    items[index].task_id if index < len(items) else f"item-{index}"
                )
            questions.append(entry)
        return questions

    def retry_questions(
        self,
        *,
        run_id: str,
        n_tasks: int,
        sample_seed: int,
        model_name: str,
        task_ids: list[str],
        effort: str = "high",
        cancel_event: Event | None = None,
        log_path: Path,
    ) -> tuple[int, str]:
        """重测指定题目（评测记录的「单题重测 / 全部重测」入口）。

        与 :meth:`retry_gateway_failures` 不同，这里不筛失败类型——前端传
        哪些 ``task_id`` 就重测哪些（全部重测即传全部 ``task_id``）。结果
        原位替换进 ``results.jsonl``；重测期间先移除 ``result.json`` 完成标
        记并回退进度，结束后重建。返回 ``(returncode, error)``。
        """
        items = sample_items(load_dataset(self._dataset_path), n_tasks, sample_seed)
        if not items:
            return 1, f"no items found in {self._dataset_path}"
        run_dir = self.jobs_root / run_id
        results_path = run_dir / "results.jsonl"
        if not results_path.is_file():
            return 1, "no results to retry"
        wanted = {str(task_id) for task_id in task_ids}
        indices = [i for i, item in enumerate(items) if item.task_id in wanted]
        if not indices:
            return 1, "no matching questions to retry"
        endpoint = resolve_inference_endpoint(self._gateway)
        model = _gateway_model_name(model_name)
        result_marker = run_dir / "result.json"
        had_marker = result_marker.is_file()
        if had_marker:
            try:
                result_marker.unlink()
            except OSError:
                pass
        self._active.add(run_id)
        try:
            lines = _read_result_lines(results_path)
            if lines is None:
                return 1, "results.jsonl is unreadable"
            total = len(lines)
            settled = max(0, total - len(indices))
            self._set_progress(run_id, total=total, completed=settled)
            updates: dict[int, dict[str, Any]] = {}
            cancelled = False
            for done, index in enumerate(indices):
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                result = self._run_item(
                    items[index],
                    model,
                    endpoint.base_url,
                    endpoint.api_key,
                    effort,
                    cancel_event,
                )
                updates[index] = result
                settled += 1
                self._set_progress(run_id, total=total, completed=min(settled, total))
                _append_log(
                    log_path,
                    _log_line(
                        f"[重测 {done + 1}/{len(indices)}] "
                        f"{items[index].task_id} {result['status']}"
                    ),
                )
            if updates:
                _write_result_records(results_path, lines, updates)
            self._write_result_json(run_dir)
            return (1, "cancelled by user") if cancelled else (0, "")
        finally:
            self._active.discard(run_id)

    def _execute_items(
        self,
        *,
        items: list[ApiEvalItem],
        run_dir: Path,
        model: str,
        base_url: str,
        api_key: str,
        log_path: Path,
        cancel_event: Event | None,
        effort: str,
        start_index: int = 0,
        concurrency: int | None = None,
    ) -> bool:
        """Run items against the gateway and write ``results.jsonl``.

        Returns True when cancelled (remaining items are left unrecorded).
        Dispatches to parallel execution when *concurrency* > 1.
        When *start_index* > 0, items 0..*start_index*-1 are skipped (they
        were already completed in a previous run that is being resumed).
        """
        workers = concurrency if concurrency and concurrency > 0 else self.n_concurrent
        if workers > 1:
            return self._execute_parallel(
                items=items,
                run_dir=run_dir,
                model=model,
                base_url=base_url,
                api_key=api_key,
                log_path=log_path,
                cancel_event=cancel_event,
                effort=effort,
                concurrency=workers,
                start_index=start_index,
            )
        return self._execute_sequential(
            items=items,
            run_dir=run_dir,
            model=model,
            base_url=base_url,
            api_key=api_key,
            log_path=log_path,
            cancel_event=cancel_event,
            effort=effort,
            start_index=start_index,
        )

    def _execute_sequential(
        self,
        *,
        items: list[ApiEvalItem],
        run_dir: Path,
        model: str,
        base_url: str,
        api_key: str,
        log_path: Path,
        cancel_event: Event | None,
        effort: str,
        start_index: int = 0,
    ) -> bool:
        deadline = time.monotonic() + self.default_timeout_sec
        results_path = run_dir / "results.jsonl"
        total = len(items)
        run_id = run_dir.name
        # Append mode: on a resumed run the file already contains the items
        # copied from the interrupted run; fresh runs start from an empty file.
        with results_path.open("a", encoding="utf-8") as handle:
            for index, item in enumerate(items):
                if index < start_index:
                    continue
                if cancel_event is not None and cancel_event.is_set():
                    return True
                if time.monotonic() >= deadline:
                    for remaining in items[index:]:
                        handle.write(
                            json.dumps(
                                self._timeout_result(remaining), ensure_ascii=True
                            )
                            + "\n"
                        )
                    break
                result = self._run_item(item, model, base_url, api_key, effort, cancel_event)
                handle.write(json.dumps(result, ensure_ascii=True) + "\n")
                handle.flush()
                # 顺序执行：index+1 即已完成题数（含断点续跑跳过的前缀）。
                done = index + 1
                self._set_progress(run_id, total=total, completed=done)
                _append_log(
                    log_path,
                    f"[{datetime.now(_LOG_TZ).isoformat(timespec='seconds')}] "
                    f"[{done}/{total}] {item.task_id} {result['status']}",
                )
        return False

    def _execute_parallel(
        self,
        *,
        items: list[ApiEvalItem],
        run_dir: Path,
        model: str,
        base_url: str,
        api_key: str,
        log_path: Path,
        cancel_event: Event | None,
        effort: str,
        concurrency: int,
        start_index: int = 0,
    ) -> bool:
        results: list[dict[str, Any] | None] = [None] * len(items)
        total = len(items)
        run_id = run_dir.name
        workers = max(1, concurrency)
        results_path = run_dir / "results.jsonl"
        prefix_lines: list[str] = []
        if start_index > 0 and results_path.is_file():
            prefix_lines = results_path.read_text(encoding="utf-8").splitlines()[:start_index]
        self._set_progress(run_id, total=total, completed=min(start_index, total))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_index: dict[concurrent.futures.Future[dict[str, Any]], int] = {}
            for index, item in enumerate(items):
                if index < start_index:
                    continue
                if cancel_event is not None and cancel_event.is_set():
                    return True
                future = pool.submit(self._run_item, item, model, base_url, api_key, effort, cancel_event)
                future_to_index[future] = index
            cancelled = False
            # 完成即记：题目一结束就写运行日志并推进实时进度，而不是等
            # 全部 future 结束后一次性落盘（否则长时间并行运行期间面板
            # 一直“暂无日志输出”，看起来像卡死）。
            done = min(start_index, total)
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception:  # pragma: no cover - defensive
                    results[index] = self._error_result(
                        items[index], "runner_error", "item execution failed"
                    )
                resolved = results[index]
                if resolved is not None:
                    done += 1
                    self._set_progress(run_id, total=total, completed=done)
                    _append_log(
                        log_path,
                        f"[{datetime.now(_LOG_TZ).isoformat(timespec='seconds')}] "
                        f"[{done}/{total}] {items[index].task_id} {resolved['status']}",
                    )
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    # Stop waiting for the rest. Running futures abort fast
                    # (gateway_complete checks the event between SSE chunks);
                    # .cancel() only prevents not-yet-started ones from running.
                    for pending in future_to_index:
                        pending.cancel()
                    break
        with results_path.open("w", encoding="utf-8") as handle:
            for index, result in enumerate(results):
                if index < start_index:
                    if index < len(prefix_lines):
                        handle.write(prefix_lines[index] + "\n")
                    else:  # defensive fallback for a truncated copied prefix
                        handle.write(json.dumps(self._timeout_result(items[index]), ensure_ascii=True) + "\n")
                    continue
                resolved = result if result is not None else self._timeout_result(items[index])
                handle.write(json.dumps(resolved, ensure_ascii=True) + "\n")
        return cancelled

    def _retry_gateway_failures(
        self,
        *,
        run_dir: Path,
        items: list[ApiEvalItem],
        model: str,
        base_url: str,
        api_key: str,
        log_path: Path,
        cancel_event: Event | None,
        effort: str,
        concurrency: int,
        max_rounds: int,
    ) -> bool:
        """全部题目跑完后，重测「网关超时/临时错误」失败的题目。

        每轮读取 ``results.jsonl``，挑出 :func:`is_gateway_failure` 命中的
        行（模型答错的行不动），重新调用网关并用新结果原位替换旧行；仍有
        网关失败的题目进入下一轮，直到全部恢复或到达 *max_rounds*。题目
        进度在重测期间先回退为「已稳定题数」，随重测完成逐步回到满值。
        返回 True 表示被用户取消（已完成的重测仍会写回）。
        """
        results_path = run_dir / "results.jsonl"
        workers = max(1, concurrency or 1)
        for round_no in range(1, max_rounds + 1):
            lines = _read_result_lines(results_path)
            if lines is None:
                return False
            retry_indices = [
                index
                for index, (_, record) in enumerate(lines)
                if record is not None
                and index < len(items)
                and is_gateway_failure(record)
            ]
            if not retry_indices:
                message = (
                    f"全部 {len(lines)} 题完成：无网关超时/临时错误失败的题目，无需重测"
                    if round_no == 1
                    else f"第 {round_no - 1} 轮重测后已无网关失败题目"
                )
                _append_log(log_path, _log_line(message))
                return False
            _append_log(
                log_path,
                _log_line(
                    f"检测到 {len(retry_indices)}/{len(lines)} 题因网关超时/临时错误失败，"
                    f"开始重测（第 {round_no}/{max_rounds} 轮；仅重测网关失败题，模型答错不重测）"
                ),
            )
            total = len(lines)
            settled = total - len(retry_indices)
            self._set_progress(run_dir.name, total=total, completed=settled)
            if workers > 1:
                updates, cancelled = self._retry_round_parallel(
                    run_dir=run_dir,
                    items=items,
                    retry_indices=retry_indices,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    log_path=log_path,
                    cancel_event=cancel_event,
                    effort=effort,
                    workers=workers,
                    total=total,
                    settled_start=settled,
                )
            else:
                updates = {}
                cancelled = False
                for done, index in enumerate(retry_indices):
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        break
                    result = self._run_item(
                        items[index], model, base_url, api_key, effort, cancel_event
                    )
                    updates[index] = result
                    settled += 1
                    self._set_progress(
                        run_dir.name, total=total, completed=min(settled, total)
                    )
                    _append_log(
                        log_path,
                        _log_line(
                            f"[重测 {done + 1}/{len(retry_indices)}] "
                            f"{items[index].task_id} {result['status']}"
                        ),
                    )
            if updates:
                _write_result_records(results_path, lines, updates)
            if cancelled:
                return True
        lines = _read_result_lines(results_path)
        remaining = 0
        if lines is not None:
            remaining = sum(
                1
                for _, record in lines
                if record is not None and is_gateway_failure(record)
            )
        _append_log(
            log_path,
            _log_line(
                f"已达最大重测轮数（{max_rounds} 轮），仍有 {remaining} 题因网关错误失败"
            ),
        )
        return False

    def _retry_round_parallel(
        self,
        *,
        run_dir: Path,
        items: list[ApiEvalItem],
        retry_indices: list[int],
        model: str,
        base_url: str,
        api_key: str,
        log_path: Path,
        cancel_event: Event | None,
        effort: str,
        workers: int,
        total: int,
        settled_start: int,
    ) -> tuple[dict[int, dict[str, Any]], bool]:
        """并行重测一轮；返回 (更新表, 是否被取消)。

        取消时已完成的重测也包含在更新表里（调用方负责写回，不丢成果）。
        """
        updates: dict[int, dict[str, Any]] = {}
        cancelled = False
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_index = {
                pool.submit(
                    self._run_item, items[index], model, base_url, api_key, effort, cancel_event
                ): index
                for index in retry_indices
            }
            for future in concurrent.futures.as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    updates[index] = future.result()
                except Exception:  # pragma: no cover - defensive
                    updates[index] = self._error_result(
                        items[index], "runner_error", "item execution failed"
                    )
                done += 1
                self._set_progress(
                    run_dir.name,
                    total=total,
                    completed=min(settled_start + done, total),
                )
                _append_log(
                    log_path,
                    _log_line(
                        f"[重测 {done}/{len(retry_indices)}] "
                        f"{items[index].task_id} {updates[index]['status']}"
                    ),
                )
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    for pending in future_to_index:
                        pending.cancel()
                    break
        return updates, cancelled

    def _run_item(
        self,
        item: ApiEvalItem,
        model: str,
        base_url: str,
        api_key: str,
        effort: str,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        # 首 token 时延（TTFT）由 gateway_complete 就地写回：仅流式响应才有值。
        timing: dict[str, float] = {}
        content, usage, error = gateway_complete(
            base_url,
            api_key,
            model,
            item.prompt,
            timeout_sec=self._request_timeout(),
            reasoning_effort=effort,
            max_tokens=self.max_tokens,
            cancel_event=cancel_event,
            timing=timing,
        )
        wall_time_sec = time.monotonic() - started
        if error is not None:
            return self._result(
                item,
                response=None,
                status="runner_error",
                error_type=error.split(":", 1)[0] or "gateway_error",
                error_message=error,
                usage=usage,
                wall_time_sec=wall_time_sec,
                timing=timing,
                effort=effort,
            )
        if content is None or content == "":
            return self._result(
                item,
                response=None,
                status="verifier_error",
                error_type="empty_response",
                error_message="empty model response",
                usage=usage,
                wall_time_sec=wall_time_sec,
                timing=timing,
                effort=effort,
            )
        if item.match == "judge":
            correct, reason = self._judge(item, content, base_url, api_key, model, cancel_event)
        else:
            correct, reason = score_item(item, content)
        return self._result(
            item,
            response=content,
            status="passed" if correct else "failed",
            error_type=None if correct else reason,
            error_message=None if correct else f"scored wrong ({reason})",
            usage=usage,
            wall_time_sec=wall_time_sec,
            timing=timing,
            effort=effort,
        )

    def _judge(
        self,
        item: ApiEvalItem,
        candidate: str,
        base_url: str,
        api_key: str,
        model: str,
        cancel_event: Event | None = None,
    ) -> tuple[bool, str]:
        verdict, _, judge_error = gateway_complete(
            base_url,
            api_key,
            model,
            judge_prompt(item.prompt, item.reference, candidate),
            timeout_sec=self._request_timeout(),
            max_tokens=self.max_tokens,
            cancel_event=cancel_event,
        )
        if judge_error is not None:
            return False, "judge_error"
        parsed = parse_judge_verdict(verdict or "")
        if parsed is None:
            return False, "judge_unclear"
        return parsed, "judge_correct" if parsed else "judge_incorrect"

    def _request_timeout(self) -> int:
        return min(self.request_timeout_sec, max(1, self.default_timeout_sec))

    def _result(
        self,
        item: ApiEvalItem,
        *,
        response: str | None,
        status: str,
        error_type: str | None,
        error_message: str | None,
        usage: dict[str, int],
        wall_time_sec: float,
        timing: dict[str, float] | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        timing = timing or {}
        input_tokens = _int(usage.get("input_tokens"))
        output_tokens = _int(usage.get("output_tokens"))
        cached_input_tokens = _int(usage.get("cached_input_tokens"))
        wall = round(wall_time_sec, 3)
        ttft = timing.get("first_token_sec")
        first_content = timing.get("first_content_sec")
        generation_time = round(max(0.0, wall - ttft), 3) if ttft is not None else None
        output_tps = (
            round(output_tokens / generation_time, 3)
            if generation_time is not None and generation_time > 0 and output_tokens > 0
            else None
        )
        total_tokens = input_tokens + output_tokens
        return {
            # Legacy flat fields stay for backward compatibility with existing
            # reports/retry logic. New consumers should prefer the structured
            # request/timing/usage/quality/reliability blocks below.
            "task_id": item.task_id,
            "prompt": item.prompt,
            "reference": item.reference,
            "language": item.language,
            "response": response,
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens,
            "usage_estimated": not bool(usage),
            "wall_time_sec": wall,
            "first_token_sec": ttft,
            "first_content_sec": first_content,
            "output_tokens_per_sec": output_tps,
            "request": {
                "temperature": 0.0,
                "reasoning_effort": None if effort == "" else effort,
                "max_tokens": self.max_tokens,
                "stream": True,
                "prompt_chars": len(item.prompt),
            },
            "timing": {
                "wall_time_sec": wall,
                "ttft_sec": ttft,
                "first_content_sec": first_content,
                "generation_time_sec": generation_time,
                "output_tokens_per_sec": output_tps,
            },
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "usage_estimated": not bool(usage),
            },
            "quality": {
                "status": status,
                "score": 1 if status == "passed" else 0,
                "reference": item.reference,
                "response": response,
                "extracted_answer": extract_answer(response or "", item.extract) if response else None,
                "match": item.match,
                "extract": item.extract,
            },
            "reliability": {
                "http_status": None,
                "error_type": error_type,
                "error_message_redacted": error_message,
                "retry_count": 0,
                "cancelled": False,
                "timeout": status == "timeout",
            },
        }

    def _error_result(
        self,
        item: ApiEvalItem,
        status: str,
        error_message: str,
    ) -> dict[str, Any]:
        return self._result(
            item,
            response=None,
            status=status,
            error_type=status,
            error_message=error_message,
            usage={},
            wall_time_sec=0.0,
        )

    def _timeout_result(self, item: ApiEvalItem) -> dict[str, Any]:
        return self._result(
            item,
            response=None,
            status="timeout",
            error_type="timeout",
            error_message="run timeout reached",
            usage={"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0},
            wall_time_sec=0.0,
        )

    def _write_result_json(self, run_dir: Path) -> None:
        (run_dir / "result.json").write_text(
            json.dumps(
                {"finished_at": datetime.now(UTC).isoformat()}, ensure_ascii=True
            )
            + "\n",
            encoding="utf-8",
        )

    # --- import -------------------------------------------------------------

    def import_records(
        self,
        *,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str = "high",
        prices: PriceConfig | None = None,
    ) -> list[RunRecord]:
        results_path = self.jobs_root / run_id / "results.jsonl"
        if not results_path.is_file():
            return []
        records: list[RunRecord] = []
        for line in results_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            records.append(
                self._record(
                    data,
                    run_id=run_id,
                    model_id=model_id,
                    base_url_hash=base_url_hash,
                    effort=effort,
                    prices=prices,
                )
            )
        return records

    def _record(
        self,
        data: dict[str, Any],
        *,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str,
        prices: PriceConfig | None,
    ) -> RunRecord:
        task_id = str(data.get("task_id") or "unknown")
        status = str(data.get("status") or "failed")
        run_log = self._run_log_path(run_id)
        cost = _record_cost(
            model_id=model_id,
            input_tokens=_int(data.get("input_tokens")),
            output_tokens=_int(data.get("output_tokens")),
            cached_input_tokens=_int(data.get("cached_input_tokens")),
            prices=prices,
        )
        return RunRecord.model_validate(
            {
                "run_id": f"{self.name}__{task_id}__{model_id}__{effort}",
                "benchmark": {
                    "name": self.name,
                    "version": "local",
                    "task_id": task_id,
                    "repo": self._repo or "local",
                    "language": str(data.get("language") or "en"),
                    "task_path": str(self._dataset_path),
                },
                "model": {
                    "provider": "openai-compatible",
                    "base_url_hash": base_url_hash,
                    "name": model_id,
                    "effort_requested": effort,
                    "effort_effective": True,
                },
                "result": {
                    "status": status,
                    "verifier_passed": status == "passed",
                    "exit_code": 0,
                    "error_type": data.get("error_type"),
                    "error_message_redacted": data.get("error_message"),
                },
                "usage": {
                    "input_tokens": _int(data.get("input_tokens")),
                    "output_tokens": _int(data.get("output_tokens")),
                    "cached_input_tokens": _int(data.get("cached_input_tokens")),
                    "total_tokens": _optional_int(_nested(data, "usage", "total_tokens"))
                    or _int(data.get("input_tokens")) + _int(data.get("output_tokens")),
                    "agent_steps": 1,
                    "wall_time_sec": float(data.get("wall_time_sec") or 0.0),
                    "first_token_sec": _optional_float(data.get("first_token_sec")),
                    "first_content_sec": _optional_float(data.get("first_content_sec")),
                    "generation_time_sec": _optional_float(_nested(data, "timing", "generation_time_sec")),
                    "output_tokens_per_sec": _optional_float(
                        data.get("output_tokens_per_sec")
                        if data.get("output_tokens_per_sec") is not None
                        else _nested(data, "timing", "output_tokens_per_sec")
                    ),
                    "usage_estimated": bool(data.get("usage_estimated")),
                },
                "cost": cost,
                "artifacts": {
                    "patch_path": None,
                    "log_path": str(run_log) if run_log.is_file() else None,
                    "verifier_path": str(self.jobs_root / run_id / "results.jsonl"),
                },
                "created_at": datetime.now(UTC),
            }
        )

    # --- logs ---------------------------------------------------------------

    def _log_path(self, run_id: str, source: str) -> Path | None:
        if source == "run":
            path = self._run_log_path(run_id)
            return path if path.is_file() else None
        if source == "results":
            path = self.jobs_root / run_id / "results.jsonl"
            return path if path.is_file() else None
        return None


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _record_cost(
    *,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    prices: PriceConfig | None,
) -> dict[str, Any]:
    """Per-record cost from the price table, or zeros when unknown."""
    if prices is None or model_id not in prices.prices:
        return {
            "currency": "USD",
            "input_cost": 0.0,
            "cached_input_cost": 0.0,
            "output_cost": 0.0,
            "total_cost": 0.0,
        }
    breakdown = calculate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        price=prices.prices[model_id],
    )
    return {
        "currency": breakdown.currency,
        "input_cost": breakdown.input_cost,
        "cached_input_cost": breakdown.cached_input_cost,
        "output_cost": breakdown.output_cost,
        "total_cost": breakdown.total_cost,
    }


def _gateway_model_name(model_name: str) -> str:
    """Strip the pier-style ``openai/`` provider prefix from a gateway model id."""
    if model_name.startswith("openai/"):
        return model_name[len("openai/"):]
    return model_name


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(data: dict[str, Any], section: str, key: str) -> object:
    value = data.get(section)
    if isinstance(value, dict):
        return value.get(key)
    return None


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def _log_line(message: str) -> str:
    """运行日志行：北京时间前缀 + 正文（与主执行路径同格式）。"""
    return (
        f"[{datetime.now(_LOG_TZ).isoformat(timespec='seconds')}] {message}"
    )
