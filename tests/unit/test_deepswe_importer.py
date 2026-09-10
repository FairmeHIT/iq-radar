from __future__ import annotations

import json
from pathlib import Path

from iqradar.deepswe.importer import (
    base_url_hash,
    import_pier_run,
    read_pier_trials,
)


def _trial_dir(root: Path, name: str, *, reward: int = 1, exception: bool = False) -> Path:
    trial = root / name
    verifier = trial / "verifier"
    verifier.mkdir(parents=True)
    trial_result = {
        "task_id": {"path": f"tasks/{name}"},
        "task_name": f"owner/{name}",
        "trial_name": name,
        "started_at": "2026-08-12T21:32:36.317260Z",
        "finished_at": "2026-08-12T21:42:36.317260Z",
        "n_agent_steps": 12,
        "agent_result": {
            "n_input_tokens": 1000,
            "n_cache_tokens": 200,
            "n_output_tokens": 500,
            "cost_usd": 0.5,
        },
    }
    if exception:
        trial_result["exception_info"] = {
            "exception_type": "RuntimeError",
            "exception_message": "docker compose command failed",
        }
        trial_result["agent_result"] = None
    (trial / "result.json").write_text(json.dumps(trial_result), encoding="utf-8")
    (verifier / "reward.json").write_text(
        json.dumps(
            {
                "reward": reward,
                "f2p_total": 2,
                "f2p_passed": reward,
                "p2p_total": 1,
                "p2p_passed": 1,
                "partial": 0.66,
            }
        ),
        encoding="utf-8",
    )
    (trial / "trial.log").write_text("log\n", encoding="utf-8")
    return trial


def _task_toml(tasks_path: Path, name: str, *, repo: str = "owner/repo", language: str = "python") -> None:
    task_path = tasks_path / name
    task_path.mkdir(parents=True, exist_ok=True)
    (task_path / "task.toml").write_text(
        f'repo = "{repo}"\nlanguage = "{language}"\n',
        encoding="utf-8",
    )


def test_import_pier_run_parses_trials_into_run_records(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    tasks_path = tmp_path / "tasks"
    _trial_dir(jobs_root / "run-1", "task-a", reward=1)
    _trial_dir(jobs_root / "run-1", "task-b", reward=0)
    _task_toml(tasks_path, "task-a")
    _task_toml(tasks_path, "task-b")

    records = import_pier_run(
        tasks_path=tasks_path,
        jobs_root=jobs_root,
        run_id="run-1",
        model_id="model-a",
        base_url_hash="sha256:test",
    )

    assert [record.benchmark.task_id for record in records] == ["task-a", "task-b"]
    assert records[0].result.status == "passed"
    assert records[0].result.verifier_passed is True
    assert records[0].benchmark.repo == "owner/repo"
    assert records[0].benchmark.language == "python"
    assert records[0].usage.input_tokens == 1000
    assert records[0].usage.output_tokens == 500
    assert records[0].usage.agent_steps == 12
    assert records[0].cost.total_cost == 0.5
    assert records[0].usage.wall_time_sec == 600.0
    assert records[0].model.name == "model-a"
    assert records[1].result.status == "failed"
    assert records[1].result.error_type == "verifier_failed"


def test_import_pier_run_marks_exception_trials_as_failed(tmp_path: Path) -> None:
    jobs_root = tmp_path / "jobs"
    tasks_path = tmp_path / "tasks"
    _trial_dir(jobs_root / "run-1", "task-a", exception=True)
    _task_toml(tasks_path, "task-a")

    records = import_pier_run(
        tasks_path=tasks_path,
        jobs_root=jobs_root,
        run_id="run-1",
        model_id="model-a",
        base_url_hash="sha256:test",
    )

    assert records[0].result.status == "failed"
    assert records[0].result.verifier_passed is False
    assert records[0].result.error_type == "RuntimeError"
    assert "docker compose" in (records[0].result.error_message_redacted or "")
    assert records[0].usage.input_tokens == 0
    assert records[0].cost.total_cost == 0.0


def test_read_pier_trials_ignores_partial_directories(tmp_path: Path) -> None:
    job_dir = tmp_path / "run-1"
    (job_dir / "partial").mkdir(parents=True)
    _trial_dir(job_dir, "complete")

    trials = read_pier_trials(job_dir)

    assert [path.name for path in trials] == ["complete"]


def test_base_url_hash_is_deterministic() -> None:
    assert base_url_hash("https://models.example/v1") == base_url_hash("https://models.example/v1")
    assert base_url_hash("https://models.example/v1").startswith("sha256:")
