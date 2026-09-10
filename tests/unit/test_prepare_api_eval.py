"""Tests for the api-eval dataset preparation script's converters.

The GPQA/AIME converters are pure functions, so they are exercised here
without any network or ``datasets`` dependency.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_api_eval", Path("scripts/prepare_api_eval.py")
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


prepare = _load_module()


class TestGpqaItem:
    def test_choices_dict_builds_lettered_prompt(self) -> None:
        row = {
            "Question": "Which one is correct?",
            "Choices": {"A": "alpha", "B": "beta", "C": "gamma", "D": "delta"},
            "Correct Choice": "D",
            "id": 7,
        }
        item = prepare._gpqa_item(row, 0, "gpqa-diamond")
        assert item["reference"] == "D"
        assert item["match"] == "exact"
        assert "(A) alpha" in item["prompt"]
        assert "(D) delta" in item["prompt"]
        assert item["prompt"].startswith("Which one is correct?")
        assert item["task_id"] == "gpqa-diamond-7"

    def test_choices_json_string(self) -> None:
        import json as _json

        row = {
            "Question": "Q?",
            "Choices": _json.dumps({"A": "a", "B": "b", "C": "c", "D": "d"}),
            "Correct Choice": "C",
            "id": 1,
        }
        item = prepare._gpqa_item(row, 0, "gpqa-diamond")
        assert item["reference"] == "C"
        assert "(C) c" in item["prompt"]

    def test_letter_in_question_fallback(self) -> None:
        row = {
            "question": "Q? (A) a (B) b (C) c (D) d",
            "Correct Answer": "C",
        }
        item = prepare._gpqa_item(row, 0, "gpqa-diamond")
        assert item["reference"] == "C"
        assert item["prompt"] == "Q? (A) a (B) b (C) c (D) d"


class TestAimeItem:
    def test_aime_item(self) -> None:
        row = {"problem": "P", "answer": "042", "problem_number": "7"}
        item = prepare._aime_item(row, 0, "aime-2024")
        assert item["reference"] == "042"
        assert item["match"] == "numeric"
        assert item["extract"] == "boxed"
        assert item["task_id"] == "aime-2024-7"

    def test_aime_item_falls_back_to_index(self) -> None:
        row = {"problem": "P", "answer": "13"}
        item = prepare._aime_item(row, 2, "aime-2024")
        assert item["task_id"] == "aime-2024-03"


class TestMmluProItem:
    def test_mmlupro_item_uses_answer_index(self) -> None:
        row = {
            "question": "What is 2+2?",
            "options": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"],
            "answer_index": 3,
            "answer": "D",
            "question_id": 123,
            "category": "math",
            "src": "aops",
        }
        item = prepare._mmlupro_item(row, 0, "mmlu-pro")
        assert item["reference"] == "D"
        assert item["match"] == "exact"
        assert item["extract"] == "final"
        assert item["task_id"] == "mmlu-pro-123"
        assert item["subject"] == "math"
        assert "(A) 1" in item["prompt"]
        assert "(J) 10" in item["prompt"]
        assert "Answer with the single letter (A-J)." in item["prompt"]

    def test_mmlupro_item_answer_letter_preferred(self) -> None:
        # 若 answer 列是字母且 answer_index 缺失，直接采用字母
        row = {"question": "Q", "options": ["a", "b"], "answer": "B", "answer_index": None}
        item = prepare._mmlupro_item(row, 7, "mmlu-pro")
        assert item["reference"] == "B"
        assert item["task_id"] == "mmlu-pro-00007"


class TestArcAgi2Item:
    def test_arcagi2_item_per_test_case(self) -> None:
        row = {
            "id": "abc123",
            "fewshots": [
                {"input": [[0, 0], [0, 0]], "output": [[1, 1], [1, 1]]},
                {"input": [[2, 2], [2, 2]], "output": [[3, 3], [3, 3]]},
            ],
            "question": [
                {"input": [[4, 4], [4, 4]], "output": [[5, 5], [5, 5]]},
                {"input": [[6, 6], [6, 6]], "output": [[7, 7], [7, 7]]},
            ],
        }
        items = prepare._arcagi2_item(row, 0, "arc-agi-2")
        assert len(items) == 2
        first, second = items
        assert first["task_id"] == "arc-agi-2-abc123-1"
        assert second["task_id"] == "arc-agi-2-abc123-2"
        assert first["match"] == "grid"
        assert first["extract"] == "json"
        assert first["reference"] == "[[5, 5], [5, 5]]"
        assert "Example 1 input:" in first["prompt"]
        assert "[[0, 0], [0, 0]]" in first["prompt"]
        assert "Input grid:" in first["prompt"]
        assert "[[4, 4], [4, 4]]" in first["prompt"]

    def test_arcagi2_item_skips_hidden_outputs(self) -> None:
        row = {
            "id": "x",
            "fewshots": [{"input": [[0]], "output": [[1]]}],
            "question": [{"input": [[2]], "output": [[3]]}, {"input": [[9]]}],
        }
        items = prepare._arcagi2_item(row, 0, "arc-agi-2")
        assert len(items) == 1
        assert items[0]["task_id"] == "arc-agi-2-x-1"


class TestChineseSimpleQaItem:
    def test_chinese_simpleqa_item(self) -> None:
        row = {"question": "伏兔穴所属的经脉是什么?", "answer": "足阳明胃经", "id": "abc"}
        item = prepare._chinese_simpleqa_item(row, 1, "chinese-simpleqa")
        # 官方协议为 LLM-as-judge（不要求完全匹配）；参考答案无别名列表，
        # 字符串全等会把语义正确的等价回答全部判错。
        assert item["match"] == "judge"
        assert item["language"] == "zh"
        assert item["extract"] == "raw"
        assert item["reference"] == "足阳明胃经"
        assert item["task_id"] == "chinese-simpleqa-abc"

    def test_chinese_simpleqa_default_task_id(self) -> None:
        row = {"question": "Q?", "answer": "A"}
        item = prepare._chinese_simpleqa_item(row, 3, "chinese-simpleqa")
        assert item["task_id"] == "chinese-simpleqa-00003"


class TestHmmtItem:
    def test_hmmt_item_judge(self) -> None:
        row = {
            "problem": "P",
            "answer": "-\\frac{1}{21}",
            "problem_idx": 3,
            "problem_type": ["Algebra", "Number Theory"],
        }
        item = prepare._hmmt_feb_2026_item(row, 0, "hmmt-feb-2026")
        assert item["match"] == "judge"
        assert item["reference"] == "-\\frac{1}{21}"
        assert item["subject"] == "Algebra, Number Theory"
        assert item["task_id"] == "hmmt-feb-2026-3"
        assert "HMMT" in item["prompt"]


class TestHleItem:
    def test_hle_choice_is_exact(self) -> None:
        row = {"question": "Which condition...?", "answer": "D", "id": "q1"}
        item = prepare._hle_item(row, 0, "hle")
        assert item["match"] == "exact"
        assert item["extract"] == "final"
        assert item["reference"] == "D"

    def test_hle_freeform_is_judge(self) -> None:
        row = {"question": "What is X?", "answer": ["42", "forty-two"], "id": "q2"}
        item = prepare._hle_item(row, 1, "hle")
        assert item["match"] == "judge"
        assert item["extract"] == "raw"
        assert "42\nforty-two" in item["reference"]


class TestLivecodebenchItem:
    def test_lcb_item(self) -> None:
        row = {
            "question_content": "Solve the problem",
            "question_id": "1873_A",
            "difficulty": "easy",
            "public_test_cases": [{"input": "2", "output": "4"}],
        }
        item = prepare._livecodebench_item(row, 0, "livecodebench")
        assert item["match"] == "judge"
        assert item["difficulty"] == "easy"
        assert item["task_id"] == "livecodebench-1873_A"
        assert "Sample input:" in item["prompt"]
        assert "Sample expected outputs" in item["reference"]


class TestCodeforcesItem:
    def test_codeforces_item_with_rating(self) -> None:
        row = {
            "title": "Maximize",
            "description": "desc",
            "input_format": "in",
            "output_format": "out",
            "examples": [{"input": "1 2", "output": "3"}],
            "rating": 1600.0,
            "contest_id": 1000,
            "index": "A",
        }
        item = prepare._codeforces_item(row, 0, "codeforces")
        assert item["match"] == "judge"
        assert item["difficulty"] == "1600"
        assert item["task_id"] == "codeforces-1000-A"
        assert "Sample input:" in item["prompt"]
        assert "Problem rating: 1600." in item["reference"]

    def test_codeforces_rating_rendered(self) -> None:
        row = {"title": "T", "description": "D", "rating": 900}
        item = prepare._codeforces_item(row, 5, "codeforces")
        assert item["difficulty"] == "900"
        assert item["task_id"].startswith("codeforces-")


class TestImoAnswerBenchItem:
    def test_imo_item(self) -> None:
        row = {
            "Problem": "Find all functions...",
            "Short Answer": "$g(x)=2x^3+c$",
            "Problem ID": "imo-bench-algebra-001",
            "Category": "Algebra",
        }
        item = prepare._imo_answer_bench_item(row, 0, "imo-answerbench")
        assert item["match"] == "judge"
        assert item["subject"] == "Algebra"
        assert item["reference"] == "$g(x)=2x^3+c$"
        assert item["task_id"] == "imo-answerbench-imo-bench-algebra-001"
        assert "olympiad" in item["prompt"]


class TestRefFromList:
    def test_list_joined(self) -> None:
        assert prepare._ref_from_list(["a", "b"]) == "a\nb"
        assert prepare._ref_from_list("single") == "single"
        assert prepare._ref_from_list(None) == ""
