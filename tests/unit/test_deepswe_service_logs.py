from __future__ import annotations

import json
from pathlib import Path

from iqradar.deepswe.runner import DeepSweConfig
from iqradar.deepswe.runs import FileDeepSweRunStore
from iqradar.deepswe.service import DeepSweService


def _config(tmp_path: Path) -> DeepSweConfig:
    return DeepSweConfig(
        local_path=tmp_path / "deep-swe",
        tasks_path=tmp_path / "tasks",
        env_file=tmp_path / "deep-swe" / ".env",
        jobs_root=tmp_path / "jobs",
        default_timeout_sec=7200,
        n_concurrent=1,
    )


def _service(tmp_path: Path) -> DeepSweService:
    return DeepSweService(runs=FileDeepSweRunStore(tmp_path / "runs"), config=_config(tmp_path))


def _write_run(tmp_path: Path, run_id: str, *, lines: int = 5) -> None:
    store = FileDeepSweRunStore(tmp_path / "runs")
    run = store.submit(model_id="model-a", n_tasks=2, sample_seed=0, jobs_dir=tmp_path / "jobs" / run_id)
    store.save(run.updated(status="completed"))


def test_log_sources_list_only_existing_files(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-1"
    _write_run(tmp_path, run_id)
    pier_log = tmp_path / "runs" / run_id / "pier.log"
    pier_log.parent.mkdir(parents=True)
    pier_log.write_text("pier output\n", encoding="utf-8")

    sources = service.log_sources(run_id)

    by_name = {source["name"]: source for source in sources}
    assert by_name["pier"]["path"] == str(pier_log)
    assert "job" not in by_name
    assert "trial" not in by_name


def test_log_content_reads_pier_log_and_tails(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-2"
    _write_run(tmp_path, run_id)
    pier_log = tmp_path / "runs" / run_id / "pier.log"
    pier_log.parent.mkdir(parents=True)
    pier_log.write_text("\n".join(f"line {i}" for i in range(50)), encoding="utf-8")

    full = service.log_content(run_id, "pier", tail=0)
    assert full["content"].count("\n") == 49
    assert full["path"] == str(pier_log)

    trimmed = service.log_content(run_id, "pier", tail=5)
    lines = str(trimmed["content"]).splitlines()
    assert lines == ["line 45", "line 46", "line 47", "line 48", "line 49"]


def test_log_content_returns_empty_for_missing_source(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-3"
    _write_run(tmp_path, run_id)

    result = service.log_content(run_id, "agent", tail=0)

    assert result["content"] == ""
    assert result["path"] is None


def test_log_content_prefers_latest_trial(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-4"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    (job_dir / "older").mkdir(parents=True)
    (job_dir / "older" / "result.json").write_text("{}", encoding="utf-8")
    (job_dir / "older" / "trial.log").write_text("older", encoding="utf-8")
    newer = job_dir / "newer"
    newer.mkdir()
    (newer / "result.json").write_text("{}", encoding="utf-8")
    (newer / "trial.log").write_text("newer", encoding="utf-8")

    result = service.log_content(run_id, "trial", tail=0)

    assert str(result["content"]) == "newer"


def _write_trajectory(job_dir: Path, trial_name: str, messages: list[dict]) -> Path:
    trial = job_dir / trial_name
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text("{}", encoding="utf-8")
    path = trial / "agent" / "mini-swe-agent.trajectory.json"
    path.write_text(
        json.dumps({"trajectory_format": "raw", "messages": messages}),
        encoding="utf-8",
    )
    return path


def test_llm_source_is_listed_when_trajectory_exists(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-5"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    path = _write_trajectory(job_dir, "trial-a", [{"role": "assistant", "content": "hi"}])

    sources = {source["name"]: source for source in service.log_sources(run_id)}

    assert sources["llm"]["path"] == str(path)


def test_llm_log_content_renders_reasoning_and_content(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-6"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    _write_trajectory(
        job_dir,
        "trial-a",
        [
            {"role": "user", "content": "task"},
            {"role": "assistant", "reasoning_content": "先看一下", "content": "我来处理"},
            {"role": "tool", "content": "output"},
            {"role": "assistant", "reasoning_content": "再检查一次", "content": ""},
        ],
    )

    result = service.log_content(run_id, "llm", tail=0)

    content = str(result["content"])
    assert "[思考] 先看一下" in content
    assert "[回复] 我来处理" in content
    assert "[思考] 再检查一次" in content
    # 无 content 的响应不出现空 [回复]
    assert "[回复] \n" not in content


def test_llm_log_content_handles_mid_write_json(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-7"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    trial = job_dir / "trial-a"
    (trial / "agent").mkdir(parents=True)
    (trial / "result.json").write_text("{}", encoding="utf-8")
    (trial / "agent" / "mini-swe-agent.trajectory.json").write_text(
        '{"messages": [{"role": "assistant"', encoding="utf-8"
    )

    result = service.log_content(run_id, "llm", tail=0)

    assert result["content"] == ""


def test_llm_log_content_skips_tool_call_only_turns(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-8"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    _write_trajectory(
        job_dir,
        "trial-a",
        [
            {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
            {"role": "assistant", "reasoning_content": "正在思考", "content": "回复文本"},
            {"role": "assistant", "tool_calls": [{"id": "call-2"}]},
        ],
    )

    result = service.log_content(run_id, "llm", tail=0)

    content = str(result["content"])
    assert "[思考] 正在思考" in content
    assert "[回复] 回复文本" in content
    assert "无文本响应" not in content


def test_llm_content_caps_count(tmp_path: Path) -> None:
    service = _service(tmp_path)
    run_id = "run-9"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    _write_trajectory(
        job_dir,
        "trial-a",
        [
            {"role": "assistant", "content": f"response-{i}"}
            for i in range(60)
        ],
    )

    text = str(service.log_content(run_id, "llm", tail=2000)["content"])

    assert text.count("response-") == 50
    assert "response-59" in text
    assert "response-0" not in text


def test_llm_content_prefixes_each_entry_with_timestamp(tmp_path: Path) -> None:
    import re

    service = _service(tmp_path)
    run_id = "run-10"
    _write_run(tmp_path, run_id)
    job_dir = tmp_path / "jobs" / run_id
    _write_trajectory(
        job_dir,
        "trial-a",
        [
            {"role": "assistant", "reasoning_content": "思考一", "content": "回复一"},
            {"role": "assistant", "reasoning_content": "思考二", "content": "回复二"},
        ],
    )

    content = str(service.log_content(run_id, "llm", tail=0)["content"])

    stamps = re.findall(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00\]", content)
    assert len(stamps) == 2
    assert content.lstrip("\n").startswith(stamps[0] + "\n[思考] 思考一")
    # timestamps are recorded once and stay stable on later polls
    again = str(service.log_content(run_id, "llm", tail=0)["content"])
    assert again.lstrip("\n").startswith(stamps[0])
