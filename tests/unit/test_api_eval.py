"""Unit tests for the api-eval benchmark adapter.

Covers the config schema, registry wiring, dataset loading/sampling, answer
normalization/scoring, the stdlib gateway client, and the backend's
run/import/lifecycle behaviour — all without a network or Docker.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from iqradar.benchmarks import api_eval
from iqradar.benchmarks.api_eval import (
    ApiEvalBackend,
    ApiEvalItem,
    _gateway_model_name,
    extract_answer,
    extract_json_answer,
    gateway_complete,
    is_gateway_failure,
    load_dataset,
    normalize_answer,
    normalize_numeric,
    parse_judge_verdict,
    sample_items,
    score_item,
)
from iqradar.benchmarks.registry import build_backends, label
from iqradar.config.schema import (
    BenchmarkConfig,
    BenchmarkConfigSet,
    ModelPrice,
    PriceConfig,
    QuotaConfig,
)


def _config(tmp_path: Path, dataset_path: Path) -> BenchmarkConfig:
    return BenchmarkConfig(
        type="api-eval",
        repo_url="https://example.com/dataset",
        local_path=tmp_path / "local",
        tasks_path=dataset_path,
        default_timeout_sec=3600,
        default_concurrency=1,
        artifact_root=tmp_path / "jobs",
    )


def _backend(tmp_path: Path, dataset_path: Path, name: str = "gpqa-diamond") -> ApiEvalBackend:
    return ApiEvalBackend(_config(tmp_path, dataset_path), name=name)


class _FakeResponse:
    def __init__(self, body: str, content_type: str = "application/json") -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __iter__(self):
        for line in self._body.encode("utf-8").splitlines(keepends=True):
            yield line


# ---------------------------------------------------------------------------
# Config schema + registry
# ---------------------------------------------------------------------------

class TestApiEvalConfig:
    def test_api_eval_config_constructs(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path, tmp_path / "q.jsonl")
        assert cfg.type == "api-eval"
        assert cfg.resolved_env_file() == tmp_path / "local" / ".env"

    def test_build_backends_registers_api_eval(self, tmp_path: Path) -> None:
        config_set = BenchmarkConfigSet(
            benchmarks={
                "gpqa-diamond": _config(tmp_path, tmp_path / "q.jsonl"),
            }
        )
        backends = build_backends(config_set, tmp_path)
        assert "gpqa-diamond" in backends
        assert isinstance(backends["gpqa-diamond"], ApiEvalBackend)

    def test_api_eval_label(self) -> None:
        assert label("gpqa-diamond", "api-eval") == "GPQA Diamond"
        assert label("aime-2024", "api-eval") == "AIME 2024"
        assert label("mmlu-pro", "api-eval") == "MMLU-Pro"
        assert label("arc-agi-2", "api-eval") == "ARC-AGI-2"


# ---------------------------------------------------------------------------
# Dataset loading / sampling
# ---------------------------------------------------------------------------

class TestDataset:
    def test_load_dataset_reads_jsonl(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "a", "prompt": "p1", "reference": "1"})
            + "\n"
            + json.dumps({"task_id": "b", "prompt": "p2", "reference": "2", "match": "set"})
            + "\n"
            + json.dumps(
                {"task_id": "c", "prompt": "p3", "reference": "3", "subject": "math", "extra": 1}
            )
            + "\n"
            + "not json\n"
            + json.dumps({"task_id": "d"})
            + "\n",
            encoding="utf-8",
        )
        items = load_dataset(dataset)
        assert len(items) == 3
        assert items[0].task_id == "a"
        assert items[1].match == "set"
        assert items[2].task_id == "c"  # extra fields (subject/extra) ignored

    def test_load_dataset_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_dataset(tmp_path / "missing.jsonl") == []

    def test_sample_items_deterministic(self) -> None:
        items = [
            ApiEvalItem(task_id=f"t{i}", prompt="p", reference="r") for i in range(20)
        ]
        s1 = sample_items(items, 5, 42)
        s2 = sample_items(items, 5, 42)
        assert [i.task_id for i in s1] == [i.task_id for i in s2]
        assert len(s1) == 5
        assert len(sample_items(items, 100, 0)) == 20


# ---------------------------------------------------------------------------
# Normalization + scoring
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_normalize_answer_basic(self) -> None:
        assert normalize_answer("  HELLO  ") == "hello"
        assert normalize_answer("42.") == "42"
        assert normalize_answer(' "hello" ') == "hello"

    def test_normalize_answer_latex(self) -> None:
        assert normalize_answer("\\boxed{42}") == "42"
        assert normalize_answer("$42$") == "42"
        assert normalize_answer("\\(x\\)") == "x"

    def test_normalize_numeric(self) -> None:
        assert normalize_numeric("1,000") == "1000"
        assert normalize_numeric("3.0") == "3"
        assert normalize_numeric(" 3.00 ") == "3"
        assert normalize_numeric("3.14") == "3.14"


class TestScore:
    def test_score_exact(self) -> None:
        item = ApiEvalItem(task_id="t", prompt="p", reference="42")
        assert score_item(item, "42") == (True, "exact_match")
        assert score_item(item, "43") == (False, "exact_match")
        assert score_item(item, "") == (False, "empty_response")

    def test_score_set(self) -> None:
        item = ApiEvalItem(task_id="t", prompt="p", reference="2,3", match="set")
        assert score_item(item, "3, 2") == (True, "set_match")
        assert score_item(item, "2,4") == (False, "set_match")

    def test_score_numeric(self) -> None:
        item = ApiEvalItem(
            task_id="t", prompt="p", reference="3", match="numeric", tolerance=0.5
        )
        assert score_item(item, "3.2") == (True, "numeric_match")
        assert score_item(item, "4.0") == (False, "numeric_match")


# ---------------------------------------------------------------------------
# Gateway client
# ---------------------------------------------------------------------------

class TestGatewayComplete:
    def test_gateway_complete_parses(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"choices":[{"message":{"content":"hello"}}],'
                '"usage":{"prompt_tokens":10,"completion_tokens":5,'
                '"prompt_tokens_details":{"cached_tokens":3}}}'
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, usage, error = gateway_complete("http://gw/v1", "k", "m", "hi")
        assert content == "hello"
        assert usage == {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 3}
        assert error is None

    def test_gateway_complete_falls_back_to_reasoning(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"choices":[{"message":{"content":"","reasoning":'
                '"the answer is 385"}}]}'
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, usage, error = gateway_complete("http://gw/v1", "k", "m", "hi")
        assert content == "the answer is 385"
        assert error is None

    def test_gateway_complete_prefers_content_over_reasoning(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"choices":[{"message":{"content":"42","reasoning":"ignore"}}]}'
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, _, error = gateway_complete("http://gw/v1", "k", "m", "hi")
        assert content == "42"
        assert error is None

    def test_gateway_complete_http_error(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b'{"error":"bad"}')
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, usage, error = gateway_complete("http://gw/v1", "sk-secret", "m", "hi")
        assert content is None
        assert usage == {}
        assert error is not None and error.startswith("HTTPError:500:")

    def test_gateway_complete_missing_usage(self, monkeypatch) -> None:
        def fake_urlopen(request, timeout=None):
            return _FakeResponse('{"choices":[{"message":{"content":"42"}}]}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, usage, error = gateway_complete("http://gw/v1", "", "m", "hi")
        assert content == "42"
        assert usage == {}
        assert error is None

    def test_gateway_complete_streaming_sse(self, monkeypatch) -> None:
        """Streaming mode: parse SSE chunks into content + usage."""
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"hel"}}],"usage":null}\n',
            'data: {"choices":[{"delta":{"content":"lo"}}],"usage":null}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":10,"completion_tokens":2}}\n',
            'data: [DONE]\n',
        ]
        body = "".join(sse_lines)

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(body, content_type="text/event-stream")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, usage, error = gateway_complete("http://gw/v1", "k", "m", "hi")
        assert content == "hello"
        assert usage == {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 0}
        assert error is None

    def test_gateway_complete_streaming_reasoning_content(self, monkeypatch) -> None:
        """Streaming mode: reasoning_content deltas are concatenated when content is empty."""
        sse_lines = [
            'data: {"choices":[{"delta":{"reasoning_content":"the answer is "}}]}\n',
            'data: {"choices":[{"delta":{"reasoning_content":"42"}}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            'data: [DONE]\n',
        ]
        body = "".join(sse_lines)

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(body, content_type="text/event-stream")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, _, error = gateway_complete("http://gw/v1", "k", "m", "hi")
        assert content == "the answer is 42"
        assert error is None

    def test_gateway_complete_streaming_falls_back_to_non_stream(self, monkeypatch) -> None:
        """Gateway ignores stream:true and returns JSON — should still parse."""
        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                '{"choices":[{"message":{"content":"fallback"}}]}',
                content_type="application/json",
            )

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        content, _, error = gateway_complete("http://gw/v1", "k", "m", "hi")
        assert content == "fallback"
        assert error is None


# ---------------------------------------------------------------------------
# Backend run / import / lifecycle
# ---------------------------------------------------------------------------

class TestApiEvalBackend:
    def test_gateway_model_name_strips_provider(self) -> None:
        assert _gateway_model_name("openai/gateway/deepseek-v4-flash") == (
            "gateway/deepseek-v4-flash"
        )
        assert _gateway_model_name("openai/gpt-4o") == "gpt-4o"
        assert _gateway_model_name("gateway/deepseek-v4-flash") == (
            "gateway/deepseek-v4-flash"
        )

    def test_run_writes_results(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "q1", "prompt": "1+1", "reference": "2"})
            + "\n"
            + json.dumps({"task_id": "q2", "prompt": "2+2", "reference": "4"})
            + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        responses = iter(["2", "5"])

        def fake_gateway(*args, **kwargs):
            return (next(responses), {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, error = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=2,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        assert returncode == 0
        assert error == ""
        assert (backend.jobs_root / "r1" / "result.json").is_file()
        lines = (backend.jobs_root / "r1" / "results.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        assert len(lines) == 2
        statuses = [json.loads(line)["status"] for line in lines]
        assert statuses == ["passed", "failed"]

    def test_run_cancels(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "q1", "prompt": "p", "reference": "r"}) + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        from threading import Event

        cancel_event = Event()
        cancel_event.set()
        returncode, error = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            cancel_event=cancel_event,
        )
        assert returncode == 1
        assert "cancelled" in error

    def test_import_records_builds_run_records(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "q1", "prompt": "p", "reference": "2"}) + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        run_dir = backend.jobs_root / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "q1",
                    "prompt": "p",
                    "reference": "2",
                    "language": "en",
                    "response": "2",
                    "status": "passed",
                    "error_type": None,
                    "error_message": None,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cached_input_tokens": 3,
                    "usage_estimated": False,
                    "wall_time_sec": 1.5,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "task_id": "q2",
                    "prompt": "p",
                    "reference": "4",
                    "language": "en",
                    "response": "5",
                    "status": "failed",
                    "error_type": "exact_match",
                    "error_message": "scored wrong (exact_match)",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "usage_estimated": True,
                    "wall_time_sec": 0.5,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        records = backend.import_records(run_id="r1", model_id="m1", base_url_hash="h1")
        assert len(records) == 2
        r0 = records[0]
        assert r0.benchmark.name == "gpqa-diamond"
        assert r0.result.status == "passed"
        assert r0.result.verifier_passed is True
        assert r0.usage.input_tokens == 10
        assert r0.usage.cached_input_tokens == 3
        assert r0.model.effort_requested == "high"
        r1 = records[1]
        assert r1.result.status == "failed"
        assert r1.usage.usage_estimated is True

    def test_job_finished_checks_result_json(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text("", encoding="utf-8")
        backend = _backend(tmp_path, dataset)
        run_dir = backend.jobs_root / "r1"
        run_dir.mkdir(parents=True)
        assert backend.job_finished("r1") is False
        (run_dir / "result.json").write_text("{}", encoding="utf-8")
        assert backend.job_finished("r1") is False
        (run_dir / "result.json").write_text(
            '{"finished_at": "2026-08-16T00:00:00"}', encoding="utf-8"
        )
        assert backend.job_finished("r1") is True

    def test_runner_alive_tracks_active(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text("", encoding="utf-8")
        backend = _backend(tmp_path, dataset)
        assert backend.runner_alive("r1") is False
        backend._active.add("r1")
        assert backend.runner_alive("r1") is True
        backend._active.discard("r1")
        assert backend.runner_alive("r1") is False


# ---------------------------------------------------------------------------
# Phase 2: answer extraction
# ---------------------------------------------------------------------------

class TestExtract:
    def test_raw(self) -> None:
        assert extract_answer("thinking\n42", "raw") == "thinking\n42"

    def test_last_line(self) -> None:
        assert extract_answer("thinking\n\n42", "last_line") == "42"

    def test_boxed(self) -> None:
        assert extract_answer("so \\boxed{7}", "boxed") == "7"

    def test_boxed_fallback_to_raw(self) -> None:
        assert extract_answer("no boxed here", "boxed") == "no boxed here"

    def test_final(self) -> None:
        assert extract_answer("reasoning\nFinal Answer: 42", "final") == "42"

    def test_final_answer(self) -> None:
        assert extract_answer("Answer: 42", "final") == "42"

    def test_final_chinese(self) -> None:
        assert extract_answer("答案：42", "final") == "42"

    def test_final_falls_back_to_last_line(self) -> None:
        assert extract_answer("reasoning\n42", "final") == "42"

    def test_score_item_uses_extract(self) -> None:
        item = ApiEvalItem(task_id="t", prompt="p", reference="42", extract="final")
        assert score_item(item, "reasoning\nFinal Answer: 42") == (True, "exact_match")

    def test_score_mcq_verbose_answer_letter(self) -> None:
        """多选题：模型输出 '…答案是 (D) 1, 2, 4…'，参考答案是单字母 D，应判对。

        截获自真实 GPQA 运行：标准 final 抽取抓到整句，归一化后
        "**(d) 1, 2, 4**" ≠ "d"，字母兜底应把它拉回来判对。
        """
        item = ApiEvalItem(task_id="t", prompt="p", reference="D", extract="final")
        verbose = (
            "Assumption 3 concerns quark-level physics.\n\n"
            "## Answer\n\nThe correct answer is **(D) 1, 2, 4**."
        )
        assert score_item(item, verbose) == (True, "exact_match")

    def test_score_mcq_wrong_letter(self) -> None:
        item = ApiEvalItem(task_id="t", prompt="p", reference="D", extract="final")
        # 模型明确表示选 B：即使它啰嗦地解释，也应判错
        assert score_item(item, "The correct answer is **(B) 1, 2, 4**.") == (
            False,
            "exact_match",
        )

    def test_score_mcq_bare_letter(self) -> None:
        item = ApiEvalItem(task_id="t", prompt="p", reference="D", extract="final")
        assert score_item(item, "Final Answer: D") == (True, "exact_match")

    def test_score_non_letter_reference_unchanged(self) -> None:
        """非字母参考答案不走字母兜底，避免误判。"""
        item = ApiEvalItem(task_id="t", prompt="p", reference="42", extract="final")
        assert score_item(item, "The answer is **(7)** days.") == (False, "exact_match")

    def test_score_mcq_letter_j(self) -> None:
        """MMLU-Pro 是 10 选项（A–J）：字母抽取必须支持到 J。"""
        item = ApiEvalItem(task_id="t", prompt="p", reference="J", extract="final")
        assert score_item(item, "Final Answer: J") == (True, "exact_match")
        assert score_item(item, "The correct answer is **(J) 3, 5**.") == (True, "exact_match")
        assert score_item(item, "The correct answer is **(H) 3, 5**.") == (False, "exact_match")


# ---------------------------------------------------------------------------
# Phase 3: ARC-style grid answers (json extract + grid match)
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_fenced_json(self) -> None:
        text = "Reasoning...\n```json\n[[0, 1], [2, 3]]\n```\n"
        assert extract_json_answer(text) == [[0, 1], [2, 3]]

    def test_bare_json_with_prose(self) -> None:
        text = "The output grid is:\n[[0,1,2],[3,4,5]]\nDone."
        assert extract_json_answer(text) == [[0, 1, 2], [3, 4, 5]]

    def test_output_key_wrapper(self) -> None:
        text = '{"output": [[7, 8], [9, 0]]}'
        assert extract_json_answer(text) == [[7, 8], [9, 0]]

    def test_none_when_unparseable(self) -> None:
        assert extract_json_answer("I don't know") is None
        assert extract_json_answer("") is None

    def test_extract_answer_json_kind(self) -> None:
        from iqradar.benchmarks.api_eval import extract_answer

        assert extract_answer("...\n[[0,1],[1,0]]", "json") == "[[0, 1], [1, 0]]"


class TestScoreGrid:
    def test_grid_exact(self) -> None:
        item = ApiEvalItem(
            task_id="t", prompt="p", reference="[[0,1],[1,0]]", match="grid", extract="json"
        )
        assert score_item(item, "[[0, 1], [1, 0]]") == (True, "grid_match")
        assert score_item(item, "[[0,1],[1,1]]") == (False, "grid_match")

    def test_grid_output_key(self) -> None:
        item = ApiEvalItem(
            task_id="t", prompt="p", reference="[[5]]", match="grid", extract="json"
        )
        assert score_item(item, '{"output": [[5]]}') == (True, "grid_match")

    def test_grid_prose_fallback(self) -> None:
        """即使 extract 没抽出来，整段回答也应能在判分阶段兜底抽取。"""
        item = ApiEvalItem(
            task_id="t", prompt="p", reference="[[1,2],[3,4]]", match="grid", extract="final"
        )
        assert score_item(item, "Here it is:\n[[1, 2], [3, 4]]\nDone") == (True, "grid_match")

    def test_grid_invalid(self) -> None:
        item = ApiEvalItem(
            task_id="t", prompt="p", reference="[[1]]", match="grid", extract="json"
        )
        assert score_item(item, "nope") == (False, "grid_match")
        # 空回答走统一的 empty_response 分支
        assert score_item(item, "") == (False, "empty_response")


# ---------------------------------------------------------------------------
# Phase 2: LLM judge
# ---------------------------------------------------------------------------

class TestJudgeVerdict:
    def test_parse_verdict(self) -> None:
        assert parse_judge_verdict("CORRECT") is True
        assert parse_judge_verdict("INCORRECT") is False
        assert parse_judge_verdict("not correct") is False
        assert parse_judge_verdict("The answer is correct.") is True
        assert parse_judge_verdict("unclear") is None


class TestJudgeScoring:
    def test_judge_item_passes(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps(
                {
                    "task_id": "j1",
                    "prompt": "Why is the sky blue?",
                    "reference": "Rayleigh scattering",
                    "match": "judge",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "Why is the sky blue?":
                return ("Rayleigh scattering", {"input_tokens": 1, "output_tokens": 1}, None)
            return ("CORRECT", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, error = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        assert returncode == 0
        assert error == ""
        assert len(calls) == 2  # model call + judge call
        result = json.loads(
            (backend.jobs_root / "r1" / "results.jsonl").read_text(encoding="utf-8")
        )
        assert result["status"] == "passed"

    def test_judge_item_incorrect(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps(
                {
                    "task_id": "j1",
                    "prompt": "Why is the sky blue?",
                    "reference": "Rayleigh scattering",
                    "match": "judge",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            if prompt == "Why is the sky blue?":
                return ("Mie scattering", {"input_tokens": 1, "output_tokens": 1}, None)
            return ("INCORRECT", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        result = json.loads(
            (backend.jobs_root / "r1" / "results.jsonl").read_text(encoding="utf-8")
        )
        assert result["status"] == "failed"
        assert result["error_type"] == "judge_incorrect"


# ---------------------------------------------------------------------------
# Phase 2: max_tasks and concurrency
# ---------------------------------------------------------------------------

class TestTaskPool:
    def test_task_count_reflects_dataset_size(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        lines = [
            json.dumps({"task_id": f"t{i}", "prompt": "p", "reference": "r"})
            for i in range(200)
        ]
        dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
        backend = _backend(tmp_path, dataset)
        assert backend.task_count() == 200
        assert backend.max_tasks() == 200

    def test_max_tasks_uses_small_dataset_size(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "a", "prompt": "p", "reference": "r"}) + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        assert backend.task_count() == 1
        assert backend.max_tasks() == 1

    def test_max_tasks_empty_dataset_falls_back_to_117(self, tmp_path: Path) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text("", encoding="utf-8")
        backend = _backend(tmp_path, dataset)
        assert backend.task_count() == 0
        assert backend.max_tasks() == 117


class TestParallel:
    def test_parallel_run_scores_all_items(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        lines = [
            json.dumps({"task_id": f"t{i}", "prompt": f"p{i}", "reference": "r"})
            for i in range(6)
        ]
        dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
        config = _config(tmp_path, dataset).model_copy(update={"default_concurrency": 3})
        backend = ApiEvalBackend(config, name="demo")

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=6,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        assert returncode == 0
        results = [
            json.loads(line)
            for line in (backend.jobs_root / "r1" / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(results) == 6
        assert {r["task_id"] for r in results} == {f"t{i}" for i in range(6)}
        assert all(r["status"] == "passed" for r in results)
        # deterministic order matches the sampled order
        expected_order = [
            i.task_id for i in sample_items(load_dataset(dataset), 6, 0)
        ]
        assert [r["task_id"] for r in results] == expected_order

    def test_parallel_resume_skips_completed_prefix(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        lines = [
            json.dumps({"task_id": f"t{i}", "prompt": f"p{i}", "reference": "r"})
            for i in range(4)
        ]
        dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
        config = _config(tmp_path, dataset).model_copy(update={"default_concurrency": 2})
        backend = ApiEvalBackend(config, name="demo")
        sampled = sample_items(load_dataset(dataset), 4, 0)
        previous = backend.jobs_root / "old-run" / "results.jsonl"
        previous.parent.mkdir(parents=True)
        previous.write_text(
            json.dumps({"task_id": sampled[0].task_id, "status": "passed", "kept": True}) + "\n",
            encoding="utf-8",
        )
        prompts: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            prompts.append(prompt)
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="resumed",
            model_name="openai/m",
            n_tasks=4,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            resume_run_id="old-run",
            n_concurrent=2,
        )
        assert returncode == 0
        assert sampled[0].prompt not in prompts
        results = [
            json.loads(line)
            for line in (backend.jobs_root / "resumed" / "results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert results[0]["kept"] is True
        assert [r["task_id"] for r in results] == [item.task_id for item in sampled]

    def test_run_n_concurrent_overrides_config(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        lines = [
            json.dumps({"task_id": f"t{i}", "prompt": f"p{i}", "reference": "r"})
            for i in range(4)
        ]
        dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
        backend = _backend(tmp_path, dataset)
        assert backend.n_concurrent == 1

        captured: list[int] = []
        real = api_eval.concurrent.futures.ThreadPoolExecutor

        class _Capturing(real):
            def __init__(self, *args, **kwargs):
                captured.append(kwargs.get("max_workers"))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(
            api_eval.concurrent.futures, "ThreadPoolExecutor", _Capturing
        )

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=4,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            n_concurrent=3,
        )
        assert returncode == 0
        assert captured == [3]


# ---------------------------------------------------------------------------
# Phase 2: reasoning_effort and per-record cost
# ---------------------------------------------------------------------------

class TestReasoningEffort:
    def test_gateway_complete_sends_reasoning_effort(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse('{"choices":[{"message":{"content":"ok"}}]}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gateway_complete("http://gw/v1", "k", "m", "hi", reasoning_effort="max")
        assert captured["body"]["reasoning_effort"] == "max"

    def test_gateway_complete_omits_reasoning_effort_by_default(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse('{"choices":[{"message":{"content":"ok"}}]}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gateway_complete("http://gw/v1", "k", "m", "hi")
        assert "reasoning_effort" not in captured["body"]

    def test_run_passes_reasoning_effort(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "a", "prompt": "p", "reference": "r"}) + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        captured: dict[str, object] = {}

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            captured["reasoning_effort"] = kwargs.get("reasoning_effort")
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            effort="max",
        )
        assert captured["reasoning_effort"] == "max"

    def test_gateway_complete_sends_max_tokens(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse('{"choices":[{"message":{"content":"ok"}}]}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gateway_complete("http://gw/v1", "k", "m", "hi", max_tokens=32000)
        assert captured["body"]["max_tokens"] == 32000

    def test_gateway_complete_omits_max_tokens_by_default(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse('{"choices":[{"message":{"content":"ok"}}]}')

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        gateway_complete("http://gw/v1", "k", "m", "hi")
        assert "max_tokens" not in captured["body"]

    def test_run_passes_configured_max_tokens(self, tmp_path: Path, monkeypatch) -> None:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "a", "prompt": "p", "reference": "r"}) + "\n",
            encoding="utf-8",
        )
        config = _config(tmp_path, dataset).model_copy(update={"max_tokens": 65536})
        backend = ApiEvalBackend(config, name="demo")
        captured: dict[str, object] = {}

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return ("r", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=1,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        assert captured["max_tokens"] == 65536


class TestRecordCost:
    def _prices(self) -> PriceConfig:
        return PriceConfig(
            prices={
                "m1": ModelPrice(
                    input_usd_per_1m=1.0,
                    cached_input_usd_per_1m=1.0,
                    output_usd_per_1m=1.0,
                )
            },
            quota=QuotaConfig(weekly_budget_usd=20.0),
        )

    def _write_result(self, tmp_path: Path) -> ApiEvalBackend:
        dataset = tmp_path / "q.jsonl"
        dataset.write_text(
            json.dumps({"task_id": "a", "prompt": "p", "reference": "r"}) + "\n",
            encoding="utf-8",
        )
        backend = _backend(tmp_path, dataset)
        run_dir = backend.jobs_root / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "a",
                    "prompt": "p",
                    "reference": "r",
                    "language": "en",
                    "response": "r",
                    "status": "passed",
                    "error_type": None,
                    "error_message": None,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cached_input_tokens": 3,
                    "usage_estimated": False,
                    "wall_time_sec": 1.0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return backend

    def test_cost_computed_from_prices(self, tmp_path: Path) -> None:
        backend = self._write_result(tmp_path)
        records = backend.import_records(
            run_id="r1", model_id="m1", base_url_hash="h", prices=self._prices()
        )
        # (10 + 5 + 3) / 1e6 * 1.0 = 18e-6
        assert records[0].cost.total_cost == pytest.approx(18e-6)
        assert records[0].cost.input_cost == pytest.approx(10e-6)

    def test_cost_zero_without_prices(self, tmp_path: Path) -> None:
        backend = self._write_result(tmp_path)
        records = backend.import_records(run_id="r1", model_id="m1", base_url_hash="h")
        assert records[0].cost.total_cost == 0.0

    def test_record_reflects_requested_effort(self, tmp_path: Path) -> None:
        backend = self._write_result(tmp_path)
        records = backend.import_records(
            run_id="r1", model_id="m1", base_url_hash="h", effort="max"
        )
        assert records[0].model.effort_requested == "max"
        assert records[0].model.effort_effective is True


# ---------------------------------------------------------------------------
# Gateway-failure classification (网关失败重测)
# ---------------------------------------------------------------------------

class TestGatewayFailureClassification:
    def test_transient_http_errors_are_gateway_failures(self) -> None:
        for code in (408, 425, 429, 500, 502, 503, 504):
            assert is_gateway_failure(
                {
                    "status": "runner_error",
                    "error_type": "HTTPError",
                    "error_message": f"HTTPError:{code}:boom",
                }
            ) is True, code

    def test_permanent_http_errors_are_not_retried(self) -> None:
        for code in (400, 401, 403, 404, 422):
            assert is_gateway_failure(
                {
                    "status": "runner_error",
                    "error_type": "HTTPError",
                    "error_message": f"HTTPError:{code}:nope",
                }
            ) is False, code

    def test_transport_errors_are_gateway_failures(self) -> None:
        for error_type, message in (
            ("URLError", "URLError:timed out"),
            ("TimeoutError", "TimeoutError:The read operation timed out"),
            ("StreamError", "StreamError:ConnectionResetError:reset by peer"),
            ("JSONDecodeError", "JSONDecodeError:bad body"),
            ("runner_error", "item execution failed"),
        ):
            assert is_gateway_failure(
                {"status": "runner_error", "error_type": error_type, "error_message": message}
            ) is True, error_type

    def test_unparseable_http_code_defaults_to_retryable(self) -> None:
        assert is_gateway_failure(
            {"status": "runner_error", "error_type": "HTTPError", "error_message": "HTTPError"}
        ) is True

    def test_empty_gateway_response_is_retried(self) -> None:
        assert is_gateway_failure(
            {
                "status": "verifier_error",
                "error_type": "empty_response",
                "error_message": "empty model response",
            }
        ) is True

    def test_run_timeout_placeholder_is_retried(self) -> None:
        # 整个 run 到达时限时被填充的占位结果：题目根本没被执行过
        assert is_gateway_failure(
            {"status": "timeout", "error_type": "timeout", "error_message": "run timeout reached"}
        ) is True

    def test_model_wrong_answers_are_not_retried(self) -> None:
        for error_type in (
            "exact_match",
            "set_match",
            "numeric_match",
            "grid_match",
            "empty_response",  # 模型有输出但抽不出答案：模型的问题，不是网关的
            "judge_incorrect",
            "judge_unclear",
        ):
            assert is_gateway_failure(
                {
                    "status": "failed",
                    "error_type": error_type,
                    "error_message": f"scored wrong ({error_type})",
                }
            ) is False, error_type

    def test_judge_gateway_error_is_retried(self) -> None:
        # 模型已作答，但 LLM judge 的网关调用失败：该题从未被真正评分
        assert is_gateway_failure(
            {"status": "failed", "error_type": "judge_error", "error_message": "scored wrong (judge_error)"}
        ) is True

    def test_passed_is_never_retried(self) -> None:
        assert is_gateway_failure({"status": "passed", "error_type": None, "error_message": None}) is False

    def test_result_line_roundtrip_preserves_order(self, tmp_path: Path) -> None:
        # 行号即题目下标：损坏/空行必须原样保留，不能被跳过或改写
        path = tmp_path / "results.jsonl"
        path.write_text('{"a":1}\nnot json\n\n{"b":2}\n', encoding="utf-8")
        lines = api_eval._read_result_lines(path)
        assert lines is not None
        assert [raw for raw, _ in lines] == ['{"a":1}', "not json", "", '{"b":2}']
        assert lines[0][1] == {"a": 1}
        assert lines[1][1] is None
        assert lines[2][1] is None
        assert lines[3][1] == {"b": 2}
        api_eval._write_result_records(path, lines, {0: {"a": 9}, 3: {"b": 9}})
        raw_lines = path.read_text(encoding="utf-8").splitlines()
        assert json.loads(raw_lines[0]) == {"a": 9}
        assert raw_lines[1] == "not json"
        assert raw_lines[2] == ""
        assert json.loads(raw_lines[3]) == {"b": 9}


class TestGatewayRetry:
    """网关失败重测：全部题目跑完后，只重测网关超时/临时错误失败的题目。"""

    @staticmethod
    def _dataset(tmp_path: Path, count: int = 4) -> Path:
        dataset = tmp_path / "q.jsonl"
        lines = [
            json.dumps({"task_id": f"t{i}", "prompt": f"p{i}", "reference": "ans"})
            for i in range(count)
        ]
        dataset.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return dataset

    @staticmethod
    def _results(backend: ApiEvalBackend, run_id: str) -> list[dict]:
        text = (backend.jobs_root / run_id / "results.jsonl").read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def test_retry_recovers_gateway_failures_not_wrong_answers(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dataset = self._dataset(tmp_path)
        backend = _backend(tmp_path, dataset)
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "p1":
                # 第一轮网关超时，重测时恢复
                if calls.count("p1") == 1:
                    return (None, {}, "TimeoutError:The read operation timed out")
                return ("ans", {"input_tokens": 2, "output_tokens": 2}, None)
            if prompt == "p2":
                return ("wrong", {"input_tokens": 1, "output_tokens": 1}, None)
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, error = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=3,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            retry_gateway_failures=True,
            gateway_retry_rounds=2,
        )
        assert returncode == 0
        assert error == ""
        results = {r["task_id"]: r for r in self._results(backend, "r1")}
        assert results["t0"]["status"] == "passed"
        assert results["t1"]["status"] == "passed"  # 重测后恢复
        assert results["t2"]["status"] == "failed"  # 模型答错：不重测
        assert results["t2"]["error_type"] == "exact_match"
        # t1 恰好被调用两次（主轮 + 一轮重测）；t0/t2 各一次
        assert calls.count("p1") == 2
        assert calls.count("p0") == 1
        assert calls.count("p2") == 1
        # 运行日志记录重测过程
        log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
        assert "重测" in log_text
        assert "无需重测" not in log_text
        # 重测结束后进度回到满值
        assert backend.progress("r1") == {"total": 3, "completed": 3}

    def test_retry_disabled_by_default(self, tmp_path: Path, monkeypatch) -> None:
        dataset = self._dataset(tmp_path)
        backend = _backend(tmp_path, dataset)
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "p0":
                return (None, {}, "URLError:connection refused")
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=2,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        assert returncode == 0
        results = {r["task_id"]: r for r in self._results(backend, "r1")}
        assert results["t0"]["status"] == "runner_error"
        assert calls.count("p0") == 1  # 没有重测

    def test_retry_skipped_when_no_gateway_failures(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dataset = self._dataset(tmp_path)
        backend = _backend(tmp_path, dataset)

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            if prompt == "p0":
                return ("wrong", {"input_tokens": 1, "output_tokens": 1}, None)
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=2,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            retry_gateway_failures=True,
        )
        assert returncode == 0
        log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
        assert "无需重测" in log_text

    def test_retry_rounds_are_capped(self, tmp_path: Path, monkeypatch) -> None:
        dataset = self._dataset(tmp_path)
        backend = _backend(tmp_path, dataset)
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "p0":
                return (None, {}, "URLError:connection refused")
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=2,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            retry_gateway_failures=True,
            gateway_retry_rounds=2,
        )
        assert returncode == 0
        results = {r["task_id"]: r for r in self._results(backend, "r1")}
        # 主轮 + 2 轮重测后仍失败：保留最后一次的失败结果，不死循环
        assert results["t0"]["status"] == "runner_error"
        assert calls.count("p0") == 3
        log_text = (tmp_path / "run.log").read_text(encoding="utf-8")
        assert "已达最大重测轮数" in log_text
        assert "仍有 1 题因网关错误失败" in log_text

    def test_permanent_http_error_not_retried(self, tmp_path: Path, monkeypatch) -> None:
        dataset = self._dataset(tmp_path)
        backend = _backend(tmp_path, dataset)
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "p0":
                return (None, {}, "HTTPError:401:unauthorized")
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=2,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            retry_gateway_failures=True,
        )
        assert returncode == 0
        results = {r["task_id"]: r for r in self._results(backend, "r1")}
        assert results["t0"]["status"] == "runner_error"
        assert calls.count("p0") == 1  # 401 是配置问题：不重测

    def test_cancel_during_retry_keeps_completed_retries(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from threading import Event

        dataset = self._dataset(tmp_path)
        backend = _backend(tmp_path, dataset)
        cancel_event = Event()
        calls: list[str] = []
        retried: set[str] = set()

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt in ("p0", "p3"):
                if calls.count(prompt) == 1:
                    return (None, {}, "URLError:timed out")
                # 第一次重测触发用户取消；题目本身重测成功
                if not retried:
                    retried.add(prompt)
                    cancel_event.set()
                return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, error = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=4,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            cancel_event=cancel_event,
            retry_gateway_failures=True,
            gateway_retry_rounds=2,
        )
        assert returncode == 1
        assert "cancelled" in error
        results = self._results(backend, "r1")
        # 已完成的重测写回：网关失败的两题一题恢复、一题保留失败，
        # 其余照常
        assert len(results) == 4
        assert sum(1 for r in results if r["status"] == "runner_error") == 1
        assert sum(1 for r in results if r["status"] == "passed") == 3

    def test_parallel_retry_recovers_gateway_failures(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        dataset = self._dataset(tmp_path)
        config = _config(tmp_path, dataset).model_copy(update={"default_concurrency": 3})
        backend = ApiEvalBackend(config, name="demo")
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "p0":
                if calls.count("p0") == 1:
                    return (None, {}, "StreamError:TimeoutError:timed out")
                return ("ans", {"input_tokens": 2, "output_tokens": 2}, None)
            if prompt == "p2":
                return (None, {}, "HTTPError:503:service unavailable")
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        returncode, _ = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=4,
            sample_seed=0,
            log_path=tmp_path / "run.log",
            retry_gateway_failures=True,
            gateway_retry_rounds=2,
        )
        assert returncode == 0
        results = {r["task_id"]: r for r in self._results(backend, "r1")}
        assert len(results) == 4
        # p0 重测恢复；p2 网关持续 503，两轮重测后仍失败
        assert results["t0"]["status"] == "passed"
        assert results["t2"]["status"] == "runner_error"
        assert calls.count("p0") == 2
        assert calls.count("p2") == 3
        assert backend.progress("r1") == {"total": 4, "completed": 4}

    def test_retry_reruns_timeout_placeholder_questions(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # run 整体超时时未执行的题目（timeout 占位行）也在重测范围内
        dataset = self._dataset(tmp_path, count=3)
        backend = _backend(tmp_path, dataset)

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        run_dir = backend.jobs_root / "r1"
        # 直接预置主轮产物：t0 通过、t1/t2 为 timeout 占位（模拟 run 到达时限）
        run_dir.mkdir(parents=True)
        items = sample_items(load_dataset(dataset), 3, 0)
        with (run_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
            for item in items:
                status = "passed" if item.task_id == "t0" else "timeout"
                handle.write(
                    json.dumps({"task_id": item.task_id, "status": status}) + "\n"
                )
        cancelled = backend._retry_gateway_failures(
            run_dir=run_dir,
            items=items,
            model="m",
            base_url="http://gw/v1",
            api_key="k",
            log_path=tmp_path / "run.log",
            cancel_event=None,
            effort="high",
            concurrency=1,
            max_rounds=1,
        )
        assert cancelled is False
        results = {r["task_id"]: r for r in self._results(backend, "r1")}
        assert all(r["status"] == "passed" for r in results.values())

    def test_retry_gateway_failures_on_finished_run(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # 评测记录「重测」按钮：对一个已完成的 api-eval run 单独重测网关失败题。
        # 复用公共 retry_gateway_failures 入口：删/重建 result.json 完成标记。
        dataset = self._dataset(tmp_path, count=3)
        backend = _backend(tmp_path, dataset)
        calls: list[str] = []

        def fake_gateway(base_url, api_key, model, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "p1":
                # 主轮网关超时；重测时恢复
                if calls.count("p1") == 1:
                    return (None, {}, "TimeoutError:read timed out")
                return ("ans", {"input_tokens": 2, "output_tokens": 2}, None)
            if prompt == "p2":
                return ("wrong", {"input_tokens": 1, "output_tokens": 1}, None)
            return ("ans", {"input_tokens": 1, "output_tokens": 1}, None)

        monkeypatch.setattr(api_eval, "gateway_complete", fake_gateway)
        # 先跑一轮主评测（含网关失败 + 模型答错）
        returncode, error = backend.run(
            run_id="r1",
            model_name="openai/m",
            n_tasks=3,
            sample_seed=0,
            log_path=tmp_path / "run.log",
        )
        assert returncode == 0
        before = {r["task_id"]: r["status"] for r in self._results(backend, "r1")}
        assert before == {"t0": "passed", "t1": "runner_error", "t2": "failed"}
        # result.json 完成标记存在
        assert (backend.jobs_root / "r1" / "result.json").is_file()

        # 手动重测一轮：仅 t1（网关失败）重测，t2（答错）不动
        returncode, error = backend.retry_gateway_failures(
            run_id="r1",
            n_tasks=3,
            sample_seed=0,
            model_name="openai/m",
            max_rounds=1,
            log_path=tmp_path / "run.log",
        )
        assert returncode == 0
        assert error == ""
        after = {r["task_id"]: r["status"] for r in self._results(backend, "r1")}
        assert after == {"t0": "passed", "t1": "passed", "t2": "failed"}
        # t1 恰好被调用两次（主轮 + 重测）；t0/t2 各一次
        assert calls.count("p1") == 2
        assert calls.count("p0") == 1
        assert calls.count("p2") == 1
        # result.json 完成标记重建（run 仍标记为完成）
        assert (backend.jobs_root / "r1" / "result.json").is_file()
        assert backend.progress("r1") == {"total": 3, "completed": 3}
