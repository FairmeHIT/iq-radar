from __future__ import annotations

import json
import hashlib
import re
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iqradar.schemas.run_record import RunRecord


def pier_jobs_dir(config_tasks_path: Path, jobs_root: Path, run_id: str) -> Path:
    """Directory where pier writes a job's trials: <jobs_root>/<run_id>/<trial>/."""
    return jobs_root / run_id


def read_pier_trials(job_dir: Path) -> list[Path]:
    """Sorted trial directories (each containing result.json)."""
    if not job_dir.is_dir():
        return []
    return sorted(
        (path for path in job_dir.iterdir() if path.is_dir() and (path / "result.json").is_file()),
        key=lambda path: path.name,
    )


def import_pier_run(
    *,
    tasks_path: Path,
    jobs_root: Path,
    run_id: str,
    model_id: str,
    base_url_hash: str,
    effort: str = "high",
) -> list[RunRecord]:
    job_dir = pier_jobs_dir(tasks_path, jobs_root, run_id)
    trials = read_pier_trials(job_dir)
    return [
        _trial_record(
            trial_dir,
            tasks_path=tasks_path,
            model_id=model_id,
            base_url_hash=base_url_hash,
            effort=effort,
        )
        for trial_dir in trials
    ]


def _trial_record(
    trial_dir: Path,
    *,
    tasks_path: Path,
    model_id: str,
    base_url_hash: str,
    effort: str,
) -> RunRecord:
    trial = _read_json(trial_dir / "result.json") or {}
    reward = _read_json(trial_dir / "verifier" / "reward.json") or {}
    task_path = _task_path(trial)
    task_id = task_path.name
    repo, language = _task_metadata(tasks_path, task_id)

    status, verifier_passed, error_type, error_message = _outcome(trial, reward)
    usage = _usage(trial)
    cost = _cost(trial)
    wall_time_sec = _wall_time(trial)
    return RunRecord.model_validate(
        {
            "run_id": f"deep-swe__{task_id}__{model_id}__{effort}",
            "benchmark": {
                "name": "deep-swe",
                "version": "local",
                "task_id": task_id,
                "repo": repo,
                "language": language,
                "task_path": str(task_path),
            },
            "model": {
                "provider": "openai-compatible",
                "base_url_hash": base_url_hash,
                "name": model_id,
                "effort_requested": effort,
                "effort_effective": False,
            },
            "result": {
                "status": status,
                "verifier_passed": verifier_passed,
                "exit_code": 0,
                "error_type": error_type,
                "error_message_redacted": error_message,
            },
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "cached_input_tokens": usage["cached_input_tokens"],
                "agent_steps": usage["agent_steps"],
                "wall_time_sec": wall_time_sec,
                "usage_estimated": False,
            },
            "cost": cost,
            "artifacts": {
                "patch_path": None,
                "log_path": str(trial_dir / "trial.log") if (trial_dir / "trial.log").is_file() else None,
                "verifier_path": str(trial_dir / "verifier" / "reward.json"),
            },
            "created_at": datetime.now(UTC),
        }
    )


def _outcome(trial: dict[str, Any], reward: dict[str, Any]) -> tuple[str, bool, str | None, str | None]:
    exception = trial.get("exception_info")
    if isinstance(exception, dict) and exception.get("exception_type"):
        return (
            "failed",
            False,
            str(exception.get("exception_type") or "pier_trial_error"),
            str(exception.get("exception_message") or "").strip() or None,
        )
    reward_value = reward.get("reward")
    if isinstance(reward_value, bool):
        passed = reward_value
    elif isinstance(reward_value, (int, float)):
        passed = reward_value >= 1
    else:
        passed = False
    if passed:
        return "passed", True, None, None
    return "failed", False, "verifier_failed", _verifier_message(reward)


def _verifier_message(reward: dict[str, Any]) -> str:
    fields = ("reward", "f2p_passed", "f2p_total", "p2p_passed", "p2p_total", "partial")
    values = [f"{name}={reward[name]}" for name in fields if name in reward]
    return f"Verifier did not pass: {', '.join(values)}" if values else "Verifier did not pass"


def _usage(trial: dict[str, Any]) -> dict[str, int]:
    agent_result = trial.get("agent_result")
    nested = agent_result if isinstance(agent_result, dict) else trial
    n_agent_steps = trial.get("n_agent_steps")
    return {
        "input_tokens": _int(nested.get("n_input_tokens")),
        "output_tokens": _int(nested.get("n_output_tokens")),
        "cached_input_tokens": _int(nested.get("n_cache_tokens")),
        "agent_steps": _int(n_agent_steps if n_agent_steps is not None else nested.get("n_agent_steps")),
    }


def _cost(trial: dict[str, Any]) -> dict[str, float | str]:
    agent_result = trial.get("agent_result")
    cost_usd = agent_result.get("cost_usd") if isinstance(agent_result, dict) else None
    return {
        "currency": "USD",
        "input_cost": 0.0,
        "cached_input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": float(cost_usd) if isinstance(cost_usd, (int, float)) else 0.0,
    }


def _wall_time(trial: dict[str, Any]) -> float:
    started = _parse_time(trial.get("started_at"))
    finished = _parse_time(trial.get("finished_at"))
    if started and finished:
        return max(0.0, (finished - started).total_seconds())
    return 0.0


def _task_path(trial: dict[str, Any]) -> Path:
    task_id = trial.get("task_id")
    if isinstance(task_id, dict):
        raw = task_id.get("path")
        if isinstance(raw, str) and raw:
            return Path(raw)
    task_name = trial.get("task_name")
    if isinstance(task_name, str) and "/" in task_name:
        return Path("tasks") / task_name.split("/", 1)[1]
    return Path("tasks") / (trial.get("trial_name") or "unknown")


def _task_metadata(tasks_path: Path, task_id: str) -> tuple[str, str]:
    task_toml = tasks_path / task_id / "task.toml"
    if not task_toml.is_file():
        return "unknown", "unknown"
    try:
        with task_toml.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return "unknown", "unknown"
    repo = _first(data, ("repo", "repository", "repository_url"))
    language = _first(data, ("language",))
    return repo or "unknown", language or "unknown"


def _first(data: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for key, value in data.items():
        if key in names and isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = _first(value, names)
            if nested:
                return nested
    return None


def base_url_hash(base_url: str) -> str:
    return "sha256:" + hashlib.sha256(base_url.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    return int(value)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
