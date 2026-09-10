from __future__ import annotations

from pathlib import Path

from iqradar.deepswe.runner import DeepSweConfig, build_pier_command, deep_swe_base_url


def _config(tmp_path: Path) -> DeepSweConfig:
    return DeepSweConfig(
        local_path=tmp_path / "deep-swe",
        tasks_path=tmp_path / "tasks",
        env_file=tmp_path / "deep-swe" / ".env",
        jobs_root=tmp_path / "jobs",
        default_timeout_sec=7200,
        n_concurrent=1,
    )


def test_build_pier_command_quotes_paths_and_passes_deepswe_flags(tmp_path: Path) -> None:
    config = _config(tmp_path)
    command = build_pier_command(
        config,
        model="openai/deepseek/deepseek-v4-flash",
        n_tasks=10,
        sample_seed=0,
        job_name="run-1",
    )

    assert command[0].endswith("pier")
    assert "run" in command
    assert "-p" in command
    assert str(config.tasks_path) in command
    assert "--agent-import-path" in command
    assert "cn_agent:CnMiniSweAgent" in command
    assert "--model" in command
    assert "openai/deepseek/deepseek-v4-flash" in command
    assert "--env-file" in command
    assert str(config.env_file) in command
    assert "--jobs-dir" in command
    assert str(config.jobs_root) in command
    assert "--job-name" in command
    assert "run-1" in command
    assert "--n-tasks" in command
    assert "10" in command
    assert "--sample-seed" in command
    assert "0" in command
    assert "--yes" in command


def test_deep_swe_base_url_reads_openai_base_url_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "deep-swe" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "# comment\nOPENAI_BASE_URL=https://gateway.example.com/v1\nOPENAI_API_KEY=sk-test\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)

    assert deep_swe_base_url(config) == "https://gateway.example.com/v1"


def test_deep_swe_base_url_returns_empty_when_missing(tmp_path: Path) -> None:
    config = _config(tmp_path)

    assert deep_swe_base_url(config) == ""
