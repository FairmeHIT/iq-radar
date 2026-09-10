#!/usr/bin/env python3
"""Download GPQA Diamond / AIME 2024 from Hugging Face and convert to api-eval JSONL.

Produces question-set files consumable by the ``api-eval`` benchmark backend
(``data/datasets/api-eval/*.jsonl``): one JSON object per line with ``task_id``,
``prompt``, ``reference``, and optional ``language`` / ``match`` / ``extract`` /
``tolerance`` fields.

Presets
-------
``--preset gpqa``
    ``talzoomanzoo/gpqa_diamond`` — an ungated re-export of ``Idavidrein/gpqa``
    ``gpqa_diamond`` (198 graduate-level science questions). Multiple-choice:
    the script re-labels the four answer options A–D and sets ``reference`` to
    the correct letter.

``--preset aime``
    ``HuggingFaceH4/aime_2024`` (30 problems). Numeric answers (integer
    0–999); ``match="numeric"`` with ``extract="boxed"``.

``--preset mmlupro``
    ``TIGER-Lab/MMLU-Pro`` (12,032 test questions, 10 options each).
    Multiple-choice with options A–J; ``match="exact"`` with
    ``extract="final"``. The official org is ``TIGER-Lab`` (not
    ``TIGER-AI-Lab``, which 404s on the mirror).

``--preset arcagi2``
    ``arc-agi-community/arc-agi-2`` (``test`` split) — the official 120-task
    ARC-AGI-2 public evaluation set (173 test cases, outputs included),
    mirrored by the community. The official ``arcprize/arc-agi-2`` and the
    script-based ``arcprize/arc_agi_v2_public_eval`` are not reachable through
    hf-mirror, so this community parquet copy is used. Each test input becomes
    one item; the expected output grid is stored as a compact JSON array and
    scored with ``match="grid"`` + ``extract="json"`` (no vision needed: grids
    are serialized as JSON text).

``--preset chinese_simpleqa``
    ``OpenStellarTeam/Chinese-SimpleQA`` (3,000 Chinese factual QA questions,
    short single-fact answers). ``language="zh"`` + ``match="judge"`` +
    ``extract="raw"``: 官方协议为 LLM-as-judge（不要求完全匹配）；参考答案是
    单一标准表述、无别名列表，字符串全等会把正确的等价回答全部判错。

``--preset hmmt_feb_2026``
    ``MathArena/hmmt_feb_2026`` (33 problems from HMMT February 2026, with
    LaTeX answers such as ``-\\frac{1}{21}``). ``match="judge"`` because LaTeX
    answer normalization is impractical with string matching.

``--preset hle``
    ``macabdul9/hle_text_only`` (``test`` split) — an ungated text-only
    re-export of ``cais/hle`` (Humanity's Last Exam; the official repo is
    login-gated on the mirror). Multiple-choice items (reference is a letter)
    are scored ``match="exact"``; free-form items ``match="judge"``.

``--preset livecodebench``
    ``ali-elganzory/livecodebench-code_generation_lite`` (``test`` split,
    1,055 problems) — a parquet mirror of ``livecodebench/…lite`` (whose
    dataset script the current ``datasets`` version refuses to run). Pure
    text generation, so it is scored with ``match="judge"``: the reference
    carries the public sample input/output pairs a correct solution must
    satisfy. NOTE: no sandbox executes the code; correctness is judged by an
    LLM.

``--preset codeforces``
    ``touristgpt/Codeforces-COTs`` (9,413 problems with a ``rating`` column
    800–3500). Filtered to ``--rating-min``/``--rating-max`` (defaults 1400
    and 2400). Like LiveCodeBench: ``match="judge"`` + samples in the
    reference (no code execution; LLM judge).

``--preset imo_answer_bench``
    ``OpenEvals/IMO-AnswerBench`` (IMO / IMO Shortlist problems with short
    LaTeX answers). ``match="judge"`` for the same LaTeX-normalization reason
    as HMMT.

Usage
-----
::

    uv run --with "datasets" python scripts/prepare_api_eval.py --preset gpqa --limit 20
    uv run --with "datasets" python scripts/prepare_api_eval.py --preset aime
    uv run --with "datasets" python scripts/prepare_api_eval.py --preset hle   # needs Pillow (image column)

    # bypass hf-mirror.com (needs direct access to huggingface.co)
    uv run --with "datasets" python scripts/prepare_api_eval.py --preset gpqa --direct

Pillow is required by the ``hle`` preset (the dataset carries an ``image``
column that ``datasets`` decodes lazily); add ``--with pillow`` for that one.

``datasets`` (and its ``pyarrow`` dependency) is only needed for this one-time
data preparation; the api-eval benchmark itself runs on the stdlib. On the
restricted network the mirror ``hf-mirror.com`` is used by default, and the HF
cache is kept project-local under
``.hf-cache/`` (the default ``~/.cache/huggingface`` is read-only on WSL).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PRESETS = {
    "gpqa": {
        # ``Idavidrein/gpqa`` is gated (requires an HF token); this mirror is an
        # ungated re-export with columns Question / Choices / Correct Choice.
        "dataset": "talzoomanzoo/gpqa_diamond",
        "config": None,
        "split": "train",
        "converter": "gpqa",
        "dest": "gpqa-diamond.jsonl",
        "prefix": "gpqa-diamond",
    },
    "aime": {
        "dataset": "HuggingFaceH4/aime_2024",
        "config": None,
        "split": "train",
        "converter": "aime",
        "dest": "aime-2024.jsonl",
        "prefix": "aime-2024",
    },
    "mmlupro": {
        "dataset": "TIGER-Lab/MMLU-Pro",
        "config": None,
        "split": "test",
        "converter": "mmlupro",
        "dest": "mmlu-pro.jsonl",
        "prefix": "mmlu-pro",
    },
    "arcagi2": {
        # 官方 arcprize/arc-agi-2 与脚本型 arcprize/arc_agi_v2_public_eval 在
        # hf-mirror 上不可达；用社区 parquet 副本的 test split（即官方公开评测
        # 120 题 / 173 用例，均含答案）。
        "dataset": "arc-agi-community/arc-agi-2",
        "config": None,
        "split": "test",
        "converter": "arcagi2",
        "dest": "arc-agi-2.jsonl",
        "prefix": "arc-agi-2",
    },
    "chinese_simpleqa": {
        "dataset": "OpenStellarTeam/Chinese-SimpleQA",
        "config": None,
        "split": "train",
        "converter": "chinese_simpleqa",
        "dest": "chinese-simpleqa.jsonl",
        "prefix": "chinese-simpleqa",
    },
    "hmmt_feb_2026": {
        "dataset": "MathArena/hmmt_feb_2026",
        "config": None,
        "split": "train",
        "converter": "hmmt_feb_2026",
        "dest": "hmmt-feb-2026.jsonl",
        "prefix": "hmmt-feb-2026",
    },
    "hle": {
        # 官方 cais/hle 在镜像上是 gated（需登录）；macabdul9/hle_text_only 是
        # 免认证的纯文本版（2370 题，含 image 列但全为 None，加载需 Pillow）。
        "dataset": "macabdul9/hle_text_only",
        "config": None,
        "split": "test",
        "converter": "hle",
        "dest": "hle.jsonl",
        "prefix": "hle",
    },
    "livecodebench": {
        # 官方 livecodebench/code_generation_lite 是脚本型数据集，当前
        # datasets 版本拒绝加载；用社区 parquet 转换版（test 1055 题）。
        "dataset": "ali-elganzory/livecodebench-code_generation_lite",
        "config": None,
        "split": "test",
        "converter": "livecodebench",
        "dest": "livecodebench.jsonl",
        "prefix": "livecodebench",
    },
    "codeforces": {
        # touristgpt/Codeforces-COTs 带 rating 列(800–3500)；按 --rating-min/max
        # 过滤（默认 1400–2400 的“冲分”区间，避免过易/过难）。
        "dataset": "touristgpt/Codeforces-COTs",
        "config": None,
        "split": "train",
        "converter": "codeforces",
        "dest": "codeforces.jsonl",
        "prefix": "codeforces",
        "rating_min": 1400,
        "rating_max": 2400,
    },
    "imo_answer_bench": {
        "dataset": "OpenEvals/IMO-AnswerBench",
        "config": None,
        "split": "train",
        "converter": "imo_answer_bench",
        "dest": "imo-answerbench.jsonl",
        "prefix": "imo-answerbench",
    },
}

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        help="Built-in dataset preset (gpqa | aime)",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=Path("data/datasets/api-eval"),
        help="Output directory (default: data/datasets/api-eval)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only convert the first N rows (useful for a smoke test)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Pull from huggingface.co instead of hf-mirror.com",
    )
    parser.add_argument("--dataset-name", help="Custom HF dataset id (overrides --preset)")
    parser.add_argument("--config", help="Custom HF dataset config")
    parser.add_argument(
        "--split",
        default=None,
        help="Custom HF dataset split (default: preset's split, falls back to train)",
    )
    parser.add_argument(
        "--out-name",
        help="Custom output filename (default derives from preset)",
    )
    parser.add_argument(
        "--rating-min",
        type=float,
        default=None,
        help="Codeforces: keep problems with rating >= this value "
        "(default from preset, 1400). Ignored for other presets.",
    )
    parser.add_argument(
        "--rating-max",
        type=float,
        default=None,
        help="Codeforces: keep problems with rating <= this value "
        "(default from preset, 2400). Ignored for other presets.",
    )
    return parser.parse_args()


def _set_env(direct: bool) -> None:
    if not direct and "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    # WSL 根文件系统只读：默认 ~/.cache/huggingface 不可写，把 HF 缓存重定向到
    # 项目内可写的 .hf-cache（与 start.sh 把 uv 缓存重定向到 .uv-cache 同理）。
    if "HF_HOME" not in os.environ:
        os.environ["HF_HOME"] = str(Path(__file__).resolve().parents[1] / ".hf-cache")


def _first(row: dict, *names: str) -> str:
    """Return the first present, non-empty value among candidate column names."""
    for name in names:
        if name in row and row[name] is not None and str(row[name]).strip():
            return str(row[name]).strip()
    return ""


def _is_letter(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Da-d]", value.strip()))


def _contains_choices(question: str) -> bool:
    return bool(re.search(r"\([A-Da-d]\)", question))


def _choices_dict(row: dict) -> dict | None:
    """Return the ``Choices`` column as a ``{letter: text}`` dict, if present."""
    raw = row.get("Choices") or row.get("choices")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _gpqa_item(row: dict, index: int, prefix: str) -> dict:
    question = _first(row, "Question", "question")
    correct = _first(row, "Correct Choice", "Correct Answer", "correct_answer")
    record_id = _first(row, "id", "record_id", "uuid") or f"{index:04d}"
    choices = _choices_dict(row)

    if _contains_choices(question):
        # The question already carries the lettered options; use it verbatim.
        prompt = question
        reference = correct.upper() if _is_letter(correct) else correct
    elif choices:
        # ``{letter: text}`` dict plus a separate letter answer: build the list.
        labeled = [
            (str(letter), str(text))
            for letter, text in sorted(choices.items())
            if str(letter) and str(text)
        ]
        prompt = (
            f"{question}\n\n"
            + "\n".join(f"({letter}) {text}" for letter, text in labeled)
            + "\n\nAnswer with the single letter (A, B, C, or D)."
        )
        reference = correct.upper() if correct else ""
    else:
        prompt = question
        reference = correct

    return {
        "task_id": f"{prefix}-{record_id}",
        "prompt": prompt,
        "reference": reference,
        "language": "en",
        "match": "exact",
        "extract": "final",
    }


def _aime_item(row: dict, index: int, prefix: str) -> dict:
    problem = _first(row, "problem", "question")
    answer = _first(row, "answer", "solution")
    problem_number = _first(row, "problem_number", "problem_id")
    task_id = f"{prefix}-{problem_number}" if problem_number else f"{prefix}-{index + 1:02d}"
    return {
        "task_id": task_id,
        "prompt": problem,
        "reference": answer,
        "language": "en",
        "match": "numeric",
        "extract": "boxed",
        "tolerance": 0.0,
    }


def _mmlupro_item(row: dict, index: int, prefix: str) -> dict:
    """Convert a MMLU-Pro row (10 options A–J) into one api-eval item."""
    question = _first(row, "question", "Question")
    raw_options = row.get("options") or row.get("Choices") or []
    if isinstance(raw_options, str):
        try:
            raw_options = json.loads(raw_options)
        except (json.JSONDecodeError, TypeError):
            raw_options = []
    options = [
        str(option).strip()
        for option in raw_options
        if option is not None and str(option).strip()
    ]
    answer_index = row.get("answer_index")
    answer_letter = str(row.get("answer") or "").strip().upper()
    if isinstance(answer_index, int) and 0 <= answer_index < len(options):
        answer_letter = chr(ord("A") + answer_index)
    labeled = [
        f"({chr(ord('A') + i)}) {option}" for i, option in enumerate(options)
    ]
    prompt = question
    if labeled:
        prompt = (
            f"{question}\n\n"
            + "\n".join(labeled)
            + "\n\nAnswer with the single letter (A-J)."
        )
    record_id = row.get("question_id")
    task_id = f"{prefix}-{record_id}" if record_id is not None else f"{prefix}-{index:05d}"
    return {
        "task_id": task_id,
        "prompt": prompt,
        "reference": answer_letter,
        "language": "en",
        "match": "exact",
        "extract": "final",
        "subject": str(row.get("category") or row.get("subject") or ""),
        "src": str(row.get("src") or ""),
    }


def _grid_json(grid: object) -> str:
    """Compact, unambiguous JSON serialization of an ARC grid."""
    return json.dumps(grid, ensure_ascii=False)


def _arc_agi_prompt(train_examples: list, test_input: object) -> str:
    lines = [
        "You are solving an ARC (Abstraction and Reasoning Corpus) task. "
        "Each grid is a 2D array of integers (colors 0-9). "
        "The examples below show input/output pairs that follow one transformation rule.",
        "",
    ]
    for i, example in enumerate(train_examples, 1):
        lines.append(f"Example {i} input:")
        lines.append(_grid_json(example.get("input")))
        lines.append(f"Example {i} output:")
        lines.append(_grid_json(example.get("output")))
        lines.append("")
    lines.append("Now apply the same rule to the following input grid.")
    lines.append("Respond with ONLY the output grid as a JSON 2D array, e.g. [[0,1],[2,3]].")
    lines.append("")
    lines.append("Input grid:")
    lines.append(_grid_json(test_input))
    return "\n".join(lines)


def _arcagi2_item(row: dict, index: int, prefix: str) -> list[dict]:
    """Convert one ARC-AGI-2 task into one api-eval item per test case.

    One task has several few-shot train pairs plus 1–2 test inputs; each test
    input becomes its own item (so pass rate counts test cases, matching the
    official evaluation). Tasks whose test outputs are absent (private-set
    rows) are skipped.
    """
    task_id = _first(row, "id", "task_id") or f"{index:04d}"
    train = row.get("fewshots") or row.get("train") or []
    test = row.get("question") or row.get("test") or []
    items: list[dict] = []
    for test_index, test_case in enumerate(test, 1):
        test_input = test_case.get("input") if isinstance(test_case, dict) else None
        test_output = test_case.get("output") if isinstance(test_case, dict) else None
        if test_input is None or test_output is None:
            continue
        items.append(
            {
                "task_id": f"{prefix}-{task_id}-{test_index}",
                "prompt": _arc_agi_prompt(train, test_input),
                "reference": _grid_json(test_output),
                "language": "en",
                "match": "grid",
                "extract": "json",
                "source": "arc-agi-2",
            }
        )
    return items


def _ref_from_list(reference: object) -> str:
    """Serialize an answer that may be a list of acceptable answers."""
    if reference is None:
        return ""
    if isinstance(reference, (list, tuple)):
        return "\n".join(str(a).strip() for a in reference if str(a).strip())
    return str(reference).strip()


def _chinese_simpleqa_item(row: dict, index: int, prefix: str) -> dict:
    """Convert a Chinese-SimpleQA row (short factual QA, zh) into one item.

    判分遵循官方协议（LLM-as-judge，0-shot，不要求完全匹配）：参考答案是单一
    标准表述且无别名列表，字符串全等会把语义正确的回答（含 Markdown、中英对照、
    别名、补充说明）全部判错，因此必须走裁判模型。
    """
    question = _first(row, "question", "Question")
    answer = _ref_from_list(_first(row, "answer", "Answer"))
    record_id = _first(row, "id") or f"{index:05d}"
    return {
        "task_id": f"{prefix}-{record_id}",
        "prompt": question,
        "reference": answer,
        "language": "zh",
        "match": "judge",
        "extract": "raw",
        "source": "Chinese-SimpleQA",
    }


def _hmmt_feb_2026_item(row: dict, index: int, prefix: str) -> dict:
    """Convert an HMMT Feb 2026 problem (LaTeX answer) into one item."""
    problem = _first(row, "problem")
    answer = _ref_from_list(row.get("answer"))
    pid = _first(row, "problem_idx") or f"{index + 1:02d}"
    ptype = row.get("problem_type") or []
    subject = ", ".join(str(t) for t in ptype) if isinstance(ptype, (list, tuple)) else str(ptype or "")
    return {
        "task_id": f"{prefix}-{pid}",
        "prompt": (
            "Solve the following HMMT (Harvard-MIT Math Tournament) problem. "
            "Present your reasoning and state the final answer clearly.\n\n"
            f"{problem}"
        ),
        "reference": answer,
        "language": "en",
        "match": "judge",
        "extract": "raw",
        "subject": subject,
        "source": "HMMT Feb 2026",
    }


def _hle_item(row: dict, index: int, prefix: str) -> dict:
    """Convert an HLE text-only question into one item.

    Multiple-choice items have a single-letter answer; those are scored with
    ``match="exact"`` (+``extract="final"``). Free-form items go through the
    LLM judge with the raw response.
    """
    question = _first(row, "question", "Question")
    answer = _ref_from_list(row.get("answer"))
    record_id = _first(row, "id", "question_id") or f"{index:05d}"
    is_choice = bool(re.fullmatch(r"[A-J]", answer.strip()))
    return {
        "task_id": f"{prefix}-{record_id}",
        "prompt": question,
        "reference": answer,
        "language": "en",
        "match": "exact" if is_choice else "judge",
        "extract": "final" if is_choice else "raw",
        "source": "HLE (text-only)",
        "category": str(row.get("category") or row.get("raw_subject") or ""),
    }


def _format_sample_tests(examples: list | None, limit: int = 4) -> str:
    """Render input/output sample tests into a compact text block."""
    if not examples:
        return ""
    lines: list[str] = []
    for example in examples[:limit]:
        if not isinstance(example, dict):
            continue
        sample_input = str(example.get("input") or "")
        sample_output = str(example.get("output") or "")
        lines.append("Sample input:\n%s\nSample output:\n%s" % (sample_input, sample_output))
    return "\n\n".join(lines)


def _lcb_prompt(problem: str, samples: str) -> str:
    base = "Write a correct solution for the following competitive programming problem. "
    base += "Output only the final code (with a code block).\n\n" + problem
    if samples:
        base += "\n\nSample tests:\n" + samples
    return base


def _livecodebench_item(row: dict, index: int, prefix: str) -> dict:
    """Convert a LiveCodeBench problem into one judge-scored code item."""
    problem = _first(row, "question_content", "question")
    qid = _first(row, "question_id") or f"{index:05d}"
    public = row.get("public_test_cases") or []
    samples = _format_sample_tests(public)
    reference = (
        "A correct solution of the problem above. The public sample tests "
        "(shown in the prompt) must all pass.\nSample expected outputs:\n"
        + "\n".join(
            "- %s => %s" % (str(t.get("input", ""))[:120], str(t.get("output", ""))[:160])
            for t in public[:4]
            if isinstance(t, dict)
        )
    )
    return {
        "task_id": f"{prefix}-{qid}",
        "prompt": _lcb_prompt(problem, samples),
        "reference": reference,
        "language": "en",
        "match": "judge",
        "extract": "raw",
        "difficulty": str(row.get("difficulty") or ""),
        "source": "LiveCodeBench",
    }


def _codeforces_item(row: dict, index: int, prefix: str) -> dict:
    """Convert a Codeforces problem (with rating) into one judge-scored item."""
    title = _first(row, "title", "problem_name")
    description = _first(row, "description")
    inp = _first(row, "input_format")
    outp = _first(row, "output_format")
    examples = row.get("examples") or []
    samples = _format_sample_tests(examples)
    rating = row.get("rating")
    rating_s = ""
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        rating_s = f"{rating:.0f}"
    elif rating is not None:
        rating_s = str(rating)
    parts = [p for p in (title, description) if p]
    if inp:
        parts.append("Input format:\n" + inp)
    if outp:
        parts.append("Output format:\n" + outp)
    problem = "\n\n".join(parts)
    prompt = _lcb_prompt(problem, samples)
    reference = (
        "A correct solution of the Codeforces problem above. The public sample "
        "tests (shown in the prompt) must all pass.\n"
        + ("Problem rating: %s.\n" % rating_s if rating_s else "")
        + "\n".join(
            "- %s => %s" % (str(t.get("input", ""))[:120], str(t.get("output", ""))[:160])
            for t in examples[:4]
            if isinstance(t, dict)
        )
    )
    record_id = (
        _first(row, "id", "problem_id")
        or f"{row.get('contest_id', '')}-{_first(row, 'index')}"
        or f"{index:05d}"
    )
    return {
        "task_id": f"{prefix}-{record_id}",
        "prompt": prompt,
        "reference": reference,
        "language": "en",
        "match": "judge",
        "extract": "raw",
        "difficulty": rating_s,
        "source": "Codeforces",
    }


def _imo_answer_bench_item(row: dict, index: int, prefix: str) -> dict:
    """Convert an IMO Shortlist problem with short answer into one item."""
    problem = _first(row, "Problem", "problem")
    answer = _ref_from_list(row.get("Short Answer") or row.get("short_answer") or row.get("answer"))
    pid = _first(row, "Problem ID", "problem_id") or f"{index + 1:03d}"
    category = _first(row, "Category", "category")
    return {
        "task_id": f"{prefix}-{pid}",
        "prompt": (
            "Solve the following olympiad mathematics problem. Present your "
            "reasoning and state the final answer clearly at the end.\n\n"
            f"{problem}"
        ),
        "reference": answer,
        "language": "en",
        "match": "judge",
        "extract": "raw",
        "subject": category,
        "source": "IMO-AnswerBench",
    }


def main() -> None:
    args = _parse_args()
    if not args.preset and not args.dataset_name:
        print("Provide --preset or --dataset-name.", file=sys.stderr)
        raise SystemExit(2)

    preset = PRESETS.get(args.preset, {})
    dataset_name = args.dataset_name or preset.get("dataset")
    config = args.config if args.config is not None else preset.get("config")
    split = args.split or preset.get("split", "train")
    converter = preset.get("converter", "gpqa" if "gpqa" in (dataset_name or "") else "aime")
    prefix = preset.get("prefix", args.preset or "dataset")
    out_name = args.out_name or preset.get("dest", f"{prefix}.jsonl")

    _set_env(args.direct)

    try:
        from datasets import load_dataset  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - depends on env
        raise SystemExit(
            "The 'datasets' package is required. Install it first, e.g.\n"
            "  uv run --with 'datasets' python scripts/prepare_api_eval.py --preset gpqa\n"
            "or  pip install datasets"
        ) from exc

    print(f"Loading {dataset_name} (config={config}, split={split}) …", file=sys.stderr)
    kwargs = {"split": split}
    if config:
        kwargs["name"] = config
    dataset = load_dataset(dataset_name, **kwargs)
    print(f"Loaded {len(dataset)} rows", file=sys.stderr)

    dest_dir = args.dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / out_name
    converters = {
        "gpqa": _gpqa_item,
        "aime": _aime_item,
        "mmlupro": _mmlupro_item,
        "arcagi2": _arcagi2_item,
        "chinese_simpleqa": _chinese_simpleqa_item,
        "hmmt_feb_2026": _hmmt_feb_2026_item,
        "hle": _hle_item,
        "livecodebench": _livecodebench_item,
        "codeforces": _codeforces_item,
        "imo_answer_bench": _imo_answer_bench_item,
    }
    convert = converters.get(converter, _gpqa_item)

    # Codeforces rating filter（默认区间来自 preset；--rating-min/max 可覆盖）。
    if converter == "codeforces":
        rating_min = args.rating_min if args.rating_min is not None else preset.get("rating_min")
        rating_max = args.rating_max if args.rating_max is not None else preset.get("rating_max")
    else:
        rating_min = rating_max = None

    count = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(dataset):
            if converter == "codeforces":
                rating = row.get("rating")
                if isinstance(rating, (int, float)) and not isinstance(rating, bool):
                    if rating_min is not None and rating < rating_min:
                        continue
                    if rating_max is not None and rating > rating_max:
                        continue
                elif rating_min is not None or rating_max is not None:
                    # 无 rating（NaN）的题在给定区间时跳过
                    continue
            converted = convert(row, index, prefix)
            items = converted if isinstance(converted, list) else [converted]
            for item in items:
                if not item["prompt"] or not item["reference"]:
                    print(f"WARN skipped row {index}: missing prompt/reference", file=sys.stderr)
                    continue
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1
            # --limit 按“输出的条目数”计（对 codeforces 这类带过滤的 preset 更直观）
            if args.limit and count >= args.limit:
                break

    print(f"Wrote {count} items to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
