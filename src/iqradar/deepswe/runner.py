from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from iqradar.benchmarks.base import container_reachable_url

PIER_AGENT_IMPORT_PATH = "cn_agent:CnMiniSweAgent"


@dataclass(frozen=True)
class DeepSweConfig:
    local_path: Path
    tasks_path: Path
    env_file: Path
    jobs_root: Path
    default_timeout_sec: int
    n_concurrent: int
    #: Fixed task-name subset (pier ``--include-task-name`` filters). Empty
    #: means "sample from the whole pool".
    test_tasks: tuple[str, ...] = ()

    @property
    def deep_swe_dir(self) -> Path:
        return self.local_path


def deep_swe_base_url(config: DeepSweConfig) -> str:
    """OPENAI_BASE_URL declared in the deep-swe .env, used to hash run records."""
    try:
        lines = config.env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "OPENAI_BASE_URL":
            return _unquote(value.strip())
    return ""


def _pier_env_file(
    config: DeepSweConfig,
    job_name: str,
    gateway_overrides: dict[str, str] | None = None,
) -> Path:
    """Resolve the ``--env-file`` path for pier.

    Pier loads the env file with ``load_dotenv(..., override=True)``, so an
    OPENAI_BASE_URL that is unreachable from inside Docker Desktop containers
    (e.g. ``172.17.0.1``) cannot be overridden via the process environment.
    When the configured host is unreachable, write a temporary env file under
    the (writable) jobs root with the host rewritten to the live WSL eth0 IP.

    ``gateway_overrides`` (from the test page's runtime gateway settings)
    forces a temporary env file carrying the configured model-invocation
    ``OPENAI_BASE_URL``/``OPENAI_API_KEY``/``MSWEA_API_KEY`` so pier runs use
    the endpoint the user filled in — no code or .env edit needed.

    Host env is not enough by itself: Pier's MiniSweAgent only copies
    ``MSWEA_API_KEY`` into the container when that variable is set.
    ``cn_agent.CnMiniSweAgent.build_process_env`` also forwards
    ``OPENAI_API_KEY`` so mini-swe-agent/litellm can call
    ``POST /v1/chat/completions``.
    """
    env_file = config.env_file
    overrides = gateway_overrides or {}
    override_url = overrides.get("OPENAI_BASE_URL", "").strip()
    if override_url:
        content = ""
        if env_file.is_file():
            try:
                content = env_file.read_text(encoding="utf-8")
            except OSError:
                content = ""
        values: dict[str, str] = {
            "OPENAI_BASE_URL": container_reachable_url(override_url)
        }
        override_key = overrides.get("OPENAI_API_KEY", "").strip()
        if override_key:
            values["OPENAI_API_KEY"] = override_key
            values["MSWEA_API_KEY"] = override_key
        return _write_override_env_file(config, job_name, content, values)
    if not env_file.is_file():
        return env_file
    base_url = deep_swe_base_url(config)
    if not base_url:
        return env_file
    reachable = container_reachable_url(base_url)
    if reachable == base_url:
        return env_file
    tmp_dir = config.jobs_root / ".pier-env"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{job_name}.env"
    content = env_file.read_text(encoding="utf-8")
    content = content.replace(f"OPENAI_BASE_URL={base_url}", f"OPENAI_BASE_URL={reachable}")
    tmp_file.write_text(content, encoding="utf-8")
    return tmp_file


def _write_override_env_file(
    config: DeepSweConfig,
    job_name: str,
    content: str,
    values: dict[str, str],
) -> Path:
    """Write a pier env file with each ``KEY=value`` replaced or appended."""
    tmp_dir = config.jobs_root / ".pier-env"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_file = tmp_dir / f"{job_name}.env"
    for key, value in values.items():
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        replacement = f"{key}={value}"
        if pattern.search(content):
            content = pattern.sub(replacement, content, count=1)
        else:
            content = (content.rstrip("\n") + "\n" if content.strip() else "") + replacement + "\n"
    tmp_file.write_text(content, encoding="utf-8")
    return tmp_file


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def build_pier_command(
    config: DeepSweConfig,
    *,
    model: str,
    n_tasks: int,
    sample_seed: int,
    job_name: str,
    env_file: Path | None = None,
) -> list[str]:
    pier_bin = _pier_binary()
    env_file = env_file or config.env_file
    parts = [f"{shlex.quote(str(pier_bin))} run"]
    # Fixed test subset: pier filters the pool by include patterns before
    # sampling, so only the prewarmed tasks can ever be selected.
    for task_name in config.test_tasks:
        parts.append(f"-i {shlex.quote(task_name)}")
    parts.append(
        f"-p {shlex.quote(str(config.tasks_path))} "
        f"--agent-import-path {PIER_AGENT_IMPORT_PATH} "
        f"--model {shlex.quote(model)} "
        f"--env-file {shlex.quote(str(env_file))} "
        f"--jobs-dir {shlex.quote(str(config.jobs_root))} "
        f"--job-name {shlex.quote(job_name)} "
        f"--n-tasks {n_tasks} "
        f"--sample-seed {sample_seed} "
        f"--n-concurrent {config.n_concurrent} "
        # 保留构建出的任务镜像（默认 --delete 会在完成后 --rmi all 删掉），
        # 让同一任务的后续运行直接命中缓存，测试时不依赖网络。
        "--no-delete "
        "--yes"
    )
    return shlex.split(" ".join(parts))


def _pier_binary() -> Path:
    discovered = shutil.which("pier")
    if discovered:
        return Path(discovered)
    return Path.home() / ".local" / "bin" / "pier"


def run_pier_sync(
    config: DeepSweConfig,
    *,
    model: str,
    n_tasks: int,
    sample_seed: int,
    job_name: str,
    log_path: Path,
    cancel_event: threading.Event | None = None,
    gateway_overrides: dict[str, str] | None = None,
) -> tuple[int, str]:
    command = build_pier_command(
        config,
        model=model,
        n_tasks=n_tasks,
        sample_seed=sample_seed,
        job_name=job_name,
        env_file=_pier_env_file(config, job_name, gateway_overrides),
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(config.deep_swe_dir)
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(config.deep_swe_dir),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            returncode = _wait_with_cancel(
                process,
                cancel_event=cancel_event,
                timeout_sec=config.default_timeout_sec,
            )
        if cancel_event is not None and cancel_event.is_set():
            return 1, "cancelled by user"
        return returncode, ""
    except subprocess.TimeoutExpired as error:
        _terminate_process_tree(process)
        message = "pier run timed out"
        _append_log(log_path, message, error)
        return 1, message
    except FileNotFoundError as error:
        message = f"pier executable not found: {error.filename}"
        log_path.write_text(message + "\n", encoding="utf-8")
        return 1, message


def _wait_with_cancel(
    process: subprocess.Popen,
    *,
    cancel_event: threading.Event | None,
    timeout_sec: int,
) -> int:
    """Wait for the process, terminating it on cancel or timeout.

    The process is spawned in its own session (start_new_session=True) so the
    whole pier / docker-compose tree can be killed as one group.
    """
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            return process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process_tree(process)
                process.wait()
                return process.returncode or 1
            if timeout_sec and time.monotonic() >= deadline:
                _terminate_process_tree(process)
                process.wait()
                raise subprocess.TimeoutExpired(
                    cmd=process.args,
                    timeout=timeout_sec,
                )


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Terminate the process group, escalating to SIGKILL after a grace period."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()


def _append_log(log_path: Path, message: str, error: subprocess.TimeoutExpired) -> None:
    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n{message}\n")
        if error.stdout:
            log_handle.write(str(error.stdout))
        if error.stderr:
            log_handle.write(str(error.stderr))
