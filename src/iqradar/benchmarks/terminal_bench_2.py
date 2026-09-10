from __future__ import annotations

import json
import os
import random
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, Callable

from iqradar.benchmarks.base import BenchmarkBackend, pier_job_progress
from iqradar.benchmarks.mini_trajectory import (
    MINI_TRAJECTORY_FILENAME,
    read_mini_trajectory_usage,
)
from iqradar.config.schema import BenchmarkConfig
from iqradar.schemas.run_record import RunRecord
from iqradar.settings.inference import (
    MISSING_INFERENCE_ENDPOINT,
    resolve_inference_endpoint,
)
from iqradar.shared.mirror_proxy import (
    MIRROR_PROXY_PORT,
    mirror_proxy_alive,
    mirror_proxy_host,
)


# --- CN mirror overrides ------------------------------------------------------
#
# Harbor's agent setup runs `apt-get update && apt-get install curl
# build-essential git`, then `curl … astral.sh … | sh` (uv from GitHub) and
# `uv tool install mini-swe-agent` inside the task container. Upstream hosts
# (deb.debian.org / archive.ubuntu.com / pypi.org / github) are throttled to a
# crawl from this network: a 13 MB Debian index takes 30-40 s (vs 0.75 s from
# TUNA), which turned agent setup into a guaranteed 360 s
# AgentSetupTimeoutError. Harbor's setup commands cannot be modified, but
# ``--extra-docker-compose`` bind mounts can inject fast mirrors.

_CN_MIRROR = "mirrors.tuna.tsinghua.edu.cn"
_CN_PYPI = "https://pypi.tuna.tsinghua.edu.cn/simple"

_UBUNTU_SUITE_BY_VERSION = {
    "26.04": "resolute",
    "25.10": "questing",
    "25.04": "plucky",
    "24.04": "noble",
    "23.10": "mantic",
    "23.04": "lunar",
    "22.04": "jammy",
    "20.04": "focal",
}
_UBUNTU_DEB822_SUITES = {"noble", "mantic", "lunar", "plucky", "questing", "resolute"}


def _dockerfile_from_base(dockerfile: Path) -> str | None:
    """First FROM base image of a Dockerfile (``None`` when absent/unparseable)."""
    try:
        text = dockerfile.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FROM"):
            continue
        for token in stripped.split()[1:]:
            if token.startswith("--"):
                continue  # skip --platform=… style flags
            if token.upper() == "AS":
                break
            return token
    return None


def _classify_apt_family(base: str | None) -> tuple[str, str] | None:
    """Map a FROM base image to ``(family, suite)``; ``None`` when uncertain.

    Conservative on purpose: a wrong-suite mount would make apt pull packages
    from another release and silently dist-upgrade the task container.
    """
    if not base or "@" in base:  # digest-pinned: suite not knowable statically
        return None
    ref = base.lower()
    repo = ref.split(":", 1)[0]
    tag = ref.split(":", 1)[1] if ":" in ref else "latest"
    if "ubuntu" in repo:
        for version, suite in _UBUNTU_SUITE_BY_VERSION.items():
            if version in tag:
                return ("ubuntu", suite)
        return None
    if repo.startswith(("python", "debian")):
        for suite in ("bookworm", "trixie", "bullseye"):
            if suite in tag:
                return ("debian", suite)
        if repo.startswith("debian"):
            for version, suite in (("13", "trixie"), ("12", "bookworm"), ("11", "bullseye")):
                if tag.startswith(version):
                    return ("debian", suite)
            if tag.startswith(("latest", "stable")):
                return ("debian", "trixie")
        # Unpinned python images track the current Debian stable.
        return ("debian", "trixie")
    return None


def _tuna_apt_sources(family: str, suite: str) -> tuple[str, str]:
    """Return ``(file content, container mount point)`` for the CN mirror."""
    if family == "debian":
        content = (
            "Types: deb\n"
            f"URIs: http://{_CN_MIRROR}/debian\n"
            f"Suites: {suite} {suite}-updates\n"
            "Components: main\n"
            "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
            "\n"
            "Types: deb\n"
            f"URIs: http://{_CN_MIRROR}/debian-security\n"
            f"Suites: {suite}-security\n"
            "Components: main\n"
            "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
        )
        return content, "/etc/apt/sources.list.d/debian.sources"
    if suite in _UBUNTU_DEB822_SUITES:
        content = (
            "Types: deb\n"
            f"URIs: http://{_CN_MIRROR}/ubuntu\n"
            f"Suites: {suite} {suite}-updates {suite}-security\n"
            "Components: main\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        )
        return content, "/etc/apt/sources.list.d/ubuntu.sources"
    content = (
        f"deb http://{_CN_MIRROR}/ubuntu/ {suite} main\n"
        f"deb http://{_CN_MIRROR}/ubuntu/ {suite}-updates main\n"
        f"deb http://{_CN_MIRROR}/ubuntu/ {suite}-security main\n"
    )
    return content, "/etc/apt/sources.list"


class TerminalBench2Backend(BenchmarkBackend):
    """Terminal-Bench 2.0 via the Harbor CLI (``harbor`` / ``harbor run``).

    TB2 tasks live under ``tasks_path`` as Harbor-format directories
    (``task.toml`` + ``instruction.md`` + ``environment/`` + ``solution/`` +
    ``tests/``). Harbor builds each task's docker environment image (tagged
    ``<task>__<hash>__env-main`` and ``...__verifier__trial-main``) and runs
    the configured agent against it, writing per-trial ``result.json`` under
    ``--jobs-dir/<job>/<task>__<hash>/``.

    Every run rebuilds its environments: harbor tags env images
    ``<task>__<random-trial-hash>__env-main`` (per-job), so images cannot be
    shared across runs and ``--no-force-build --no-delete`` only avoids
    rebuilding within one resumed job. Interrupted runs are continued via
    ``run(resume_run_id=...)`` → ``harbor job resume``: finished trials are
    kept, in-flight ones restart, and the new run id symlinks to the old
    job dir so every read path sees one artifact tree.

    The ``tb`` 0.2.x harness (v1, ``original-tasks/``) is a separate backend
    (:class:`TerminalBenchBackend`); TB2 uses the newer ``task.toml`` format
    and the Harbor runner, not ``tb``.
    """

    benchmark_type = "terminal-bench-2"
    log_source_names = ("run", "agent", "results")

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
        self._run_log_path = run_log_path or (
            lambda run_id: self.jobs_root / run_id / "job.log"
        )
        self._tasks_path = config.tasks_path
        # Default agent: harbor's built-in mini-swe-agent (litellm-based,
        # reads OPENAI_BASE_URL / OPENAI_API_KEY). Any ``module:Class``
        # custom import path is supported the same way (harbor --agent
        # accepts a custom import path).
        self._agent = config.agent or "mini-swe-agent"

    def base_url(self) -> str:
        return resolve_inference_endpoint(self._gateway).for_container().base_url

    # --- dataset helpers ----------------------------------------------------

    def list_task_ids(self) -> list[str]:
        """Discover Harbor task directories (each has ``task.toml``).

        When ``test_tasks`` is set, only those task ids (that exist on disk)
        are exposed — sampling and task_count operate on the fixed subset.
        """
        if not self._tasks_path.is_dir():
            return []
        discovered = sorted(
            path.name
            for path in self._tasks_path.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and path.name != "README.md"
            and (path / "task.toml").is_file()
        )
        fixed = self._config.test_tasks
        if not fixed:
            return discovered
        fixed_set = set(fixed)
        return [task_id for task_id in discovered if task_id in fixed_set]

    def sample_task_ids(self, n_tasks: int, sample_seed: int) -> list[str]:
        """Deterministic sampling matching pier / v1 semantics."""
        all_ids = self.list_task_ids()
        random.Random(sample_seed).shuffle(all_ids)
        return all_ids[: max(0, n_tasks)]

    def task_count(self) -> int:
        return len(self.list_task_ids())

    def question_catalog(
        self, *, n_tasks: int, sample_seed: int
    ) -> list[dict[str, object]]:
        """Expose Harbor's deterministic sampled task IDs for reports."""
        return [
            {"task_id": task_id}
            for task_id in self.sample_task_ids(n_tasks, sample_seed)
        ]

    def _env_image_repo_names(self) -> set[str]:
        """All locally built TB2 env image repo basenames (e.g. the prefix of
        ``<task>__<rand7>__env-main``). Tag-agnostic: harbor 0.20 appends a
        random 7-char suffix per trial, so presence is matched on the task name
        + role rather than an exact tag."""
        if not _env_flag("IQRADAR_TB2_SKIP_ENV_SCAN"):
            try:
                out = subprocess.run(
                    ["docker", "images", "--format", "{{.Repository}}"],
                    capture_output=True, text=True, timeout=30,
                ).stdout or ""
            except (OSError, subprocess.TimeoutExpired):
                return set()
            return {
                line.split("__")[0]
                for line in out.splitlines()
                if "__env-" in line.strip()
            }
        return set()

    def _missing_env_images(self, task_ids: list[str]) -> list[str]:
        """Sampled tasks that have no prebuilt/restored env image locally yet."""
        present = self._env_image_repo_names()
        return [tid for tid in task_ids if tid not in present]

    def _gpu_task_names(self) -> list[str]:
        """Task ids whose environment requires >=1 GPU.

        On a GPU-less host (plain docker, no nvidia-docker) harbor aborts the
        whole job with ``RuntimeError: Task requires 1 GPU(s)`` the moment it
        reaches such a task, so both prewarm and eval must exclude them via
        ``--exclude-task-name`` or the entire run dies mid-batch.
        """
        gpu_tasks: list[str] = []
        if not self._tasks_path.is_dir():
            return gpu_tasks
        for path in sorted(self._tasks_path.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            toml_path = path / "task.toml"
            if not toml_path.is_file():
                continue
            try:
                text = toml_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if re.search(r"(?im)^\s*gpus\s*[:=]\s*[1-9]\d*", text):
                gpu_tasks.append(path.name)
        return gpu_tasks

    def progress(self, run_id: str) -> dict[str, int] | None:
        """题目进度：harbor 与 pier 同构，解析 job 级 result.json 统计。

        老版本 harbor 不写 stats 时，退化为统计已产出 trial 级
        result.json 的任务目录数（import_records 用同一标记判断完成）。
        """
        job_dir = self._job_dir(run_id)
        if job_dir is None:
            return None
        parsed = pier_job_progress(job_dir / "result.json")
        if parsed is not None:
            return parsed
        try:
            completed = len(self._trial_dirs(job_dir))
        except OSError:
            return None
        return {"completed": completed} if completed > 0 else None

    def preflight(self) -> str | None:
        """Runnable check for the service layer: harbor binary + task dataset."""
        if shutil.which("harbor") is None:
            return "harbor executable not found (install harbor)"
        if not self._tasks_path.is_dir():
            return f"tasks_path does not exist: {self._tasks_path}"
        if not self.list_task_ids():
            return f"no tasks found in {self._tasks_path}"
        return None

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
        # retry_gateway_failures / gateway_retry_rounds 仅 api-eval 实现：
        # 容器化基准的每题网关调用无法单独重跑，这里接受并忽略。
        if resume_run_id:
            # Continue the interrupted harbor job. Model, task set, gateway
            # env and retry policy all come from the old job's config.json
            # (harbor refuses a config mismatch), so sampling, the prewarm
            # gate and endpoint resolution are skipped entirely; the old
            # job's env images are reused via --no-delete semantics.
            command, resume_error = self._prepare_resume(
                run_id=run_id,
                model_name=model_name,
                resume_run_id=resume_run_id,
            )
            if resume_error is not None:
                return 1, resume_error
        else:
            if not self._tasks_path.is_dir():
                return 1, f"tasks_path does not exist: {self._tasks_path}"
            task_ids = self.sample_task_ids(n_tasks, sample_seed)
            if not task_ids:
                return 1, f"no tasks found in {self._tasks_path}"
            # Prewarm-readiness gate: harbor 0.20 gives every trial a fresh
            # random image tag, so an eval that runs while the 74-task image
            # set hasn't been prewarmed must live-build env images on this
            # throttled network and blows the build_timeout_sec (the exact
            # EnvironmentStartTimeoutError we saw in the monitor). Refuse to
            # start until each sampled task has a prebuilt/restored env
            # image, unless the operator explicitly overrides. GPU-only
            # tasks are excluded from the harbor run (--exclude-task-name),
            # so they must not trip the gate either.
            gpu_tasks = set(self._gpu_task_names())
            runnable_task_ids = [t for t in task_ids if t not in gpu_tasks]
            missing_env = self._missing_env_images(runnable_task_ids)
            if missing_env and not _env_flag("IQRADAR_SKIP_TB2_PREWARM_GATE"):
                missing_repr = ", ".join(sorted(missing_env)[:8])
                if len(missing_env) > 8:
                    missing_repr += ", ..."
                return 1, (
                    "TB2 prewarm incomplete: no env image for "
                    f"{len(missing_env)} sampled task(s) ({missing_repr}). "
                    "Run scripts/prewarm_environments.py --benchmark terminal-bench-2 "
                    "(or restore a tb2-env/tb2-base backup) first, otherwise every "
                    "trial live-builds its env on the throttled network and times out. "
                    "Set IQRADAR_SKIP_TB2_PREWARM_GATE=1 to force-run anyway."
                )
            endpoint = resolve_inference_endpoint(self._gateway).for_container()
            if not endpoint.base_url:
                return 1, MISSING_INFERENCE_ENDPOINT
            command = self._harbor_command(
                run_id=run_id,
                model_name=model_name,
                task_ids=task_ids,
                endpoint=endpoint,
                n_concurrent=n_concurrent,
            )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        # Apply the clean DOCKER_CONFIG (no credsStore:desktop.exe) so harbor's
        # public ghcr.io / docker.io base-image pulls don't fail on credential
        # lookup under the native WSL dockerd. Created by prewarm_environments.
        clean_cfg = _project_root() / "data" / "prewarm" / ".docker-config"
        if (clean_cfg / "config.json").is_file():
            env["DOCKER_CONFIG"] = str(clean_cfg)
        # Custom agents (``module:Class`` import paths) are imported inside
        # the harbor process; make iqradar importable there via PYTHONPATH.
        if ":" in self._agent:
            env["PYTHONPATH"] = os.pathsep.join(
                [_iqradar_src_dir(), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep)
        try:
            with log_path.open("w", encoding="utf-8") as log_handle:
                process = subprocess.Popen(
                    command,
                    cwd=str(_project_root()),
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                timed_out = False
                try:
                    returncode = _wait_with_cancel(
                        process,
                        cancel_event=cancel_event,
                        timeout_sec=self.default_timeout_sec,
                    )
                except subprocess.TimeoutExpired:
                    # Outer budget exhausted: _wait_with_cancel already killed
                    # the harbor process group. Import whatever trials managed
                    # to finish instead of crashing the run.
                    timed_out = True
                    returncode = 124
            if cancel_event is not None and cancel_event.is_set():
                return 1, "cancelled by user"
            if timed_out:
                return 124, (
                    f"harbor killed after outer timeout {self.default_timeout_sec}s; "
                    "importing finished trials only"
                )
            return (
                (0, "") if returncode == 0 else (returncode, "harbor exited non-zero")
            )
        except FileNotFoundError:
            return 1, "harbor executable not found (install harbor)"

    # --- resume ---------------------------------------------------------------

    def _prepare_resume(
        self, *, run_id: str, model_name: str, resume_run_id: str
    ) -> tuple[list[str] | None, str | None]:
        """Validate an interrupted job and stage the ``harbor job resume`` call.

        harbor re-reads everything (model, task set, gateway env, retry
        policy) from the old job's ``config.json`` and refuses a config
        mismatch, so resume only needs the job dir to exist and to carry the
        requested model. ``jobs_root/<run_id>`` becomes a symlink to the old
        job dir so every iq_radar read path (progress, logs, records, runner
        liveness) resolves against the resumed job without further mapping.
        Returns ``(command, None)`` or ``(None, error)``.
        """
        job_dir = self.jobs_root / resume_run_id
        config_path = job_dir / "config.json"
        if not config_path.is_file():
            return None, (
                f"cannot resume {resume_run_id}: no harbor job config at "
                f"{config_path} (was the run finished by this backend?)"
            )
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as caught:
            return None, (
                f"cannot resume {resume_run_id}: unreadable job config ({caught})"
            )
        wanted = _harbor_model_name(model_name)
        recorded = {
            str(agent.get("model_name"))
            for agent in config.get("agents") or []
            if isinstance(agent, dict)
        }
        if wanted not in recorded:
            return None, (
                f"cannot resume {resume_run_id}: that job ran "
                f"{sorted(recorded)} but this submission requests {wanted}; "
                "resubmit without resume to start a fresh job"
            )
        # Resolve symlink chains (resume-of-a-resume) so the new link always
        # points at the real job dir; the marker and the resume command then
        # agree on the same --job-path string.
        target = resume_run_id
        for _ in range(8):
            link = self.jobs_root / target
            if not link.is_symlink():
                break
            target = os.readlink(link)
        new_link = self.jobs_root / run_id
        if os.path.lexists(new_link) and not new_link.is_symlink():
            return None, f"cannot resume: {new_link} already exists and is not a symlink"
        if not new_link.is_symlink():
            new_link.symlink_to(target, target_is_directory=True)
        return self._harbor_resume_command(target), None

    def _harbor_resume_command(self, job_name: str) -> list[str]:
        """Command continuing ``jobs_root/<job_name>`` in place.

        ``--filter-error-type CancelledError`` is harbor's own resume default
        (spelled out so a harbor default change cannot silently alter
        semantics): trials interrupted mid-flight — no result.json after a
        hard kill, or CancelledError results after a graceful stop — are
        re-run, while genuinely errored trials keep their results so a
        resume never re-spends tokens on failures the first run already
        recorded.
        """
        return [
            "harbor",
            "job",
            "resume",
            "--job-path",
            str(self.jobs_root / job_name),
            "--filter-error-type",
            "CancelledError",
        ]

    def has_partial_results(self, run_id: str) -> bool:
        """True when the harbor job dir holds at least one finished trial.

        harbor writes per-trial ``result.json`` the moment a trial ends, so
        trials completed before an interruption survive a server restart
        and the run is resumable with ``harbor job resume``.
        """
        job_dir = self._job_dir(run_id)
        if job_dir is None:
            return False
        try:
            return bool(self._trial_dirs(job_dir))
        except OSError:
            return False

    def _cn_override_args(self, task_ids: list[str]) -> list[str]:
        """Harbor flags injecting CN mirrors for the in-container agent setup.

        Preferred path: when the host mirror proxy
        (:mod:`iqradar.shared.mirror_proxy`) is running, mount an apt.conf.d
        proxy drop-in. Proxies are suite-agnostic, so mixed-suite batches
        (the common full-eval case) accelerate too.

        Fallback (proxy down): bind-mount a TUNA apt sources file over the
        image's own sources — only safe when every sampled task shares one
        known apt family/suite (a wrong suite would dist-upgrade the
        container), so unknown/mixed bases stay on upstream mirrors.

        Both paths also mount the host's uv binary into
        ``/usr/local/bin/uv`` (setup then skips the astral.sh/GitHub
        download) and ``/etc/uv/uv.toml`` pointing uv at the TUNA PyPI
        mirror. Everything rides on ``--extra-docker-compose`` bind mounts
        because harbor's own setup commands cannot be modified.
        """
        args = ["--agent-setup-timeout-multiplier", "3.0"]
        overrides = self.jobs_root / ".overrides"
        if mirror_proxy_alive():
            overrides.mkdir(parents=True, exist_ok=True)
            apt_conf = overrides / "apt-tuna-proxy.conf"
            apt_conf.write_text(
                "Acquire::http::Proxy "
                f'"http://{mirror_proxy_host()}:{MIRROR_PROXY_PORT}/";\n',
                encoding="utf-8",
            )
            return args + self._emit_extra_compose(
                overrides,
                [(apt_conf, "/etc/apt/apt.conf.d/99iqradar-tuna-proxy")],
            )
        family_suite: tuple[str, str] | None = None
        for task_id in task_ids:
            base = _dockerfile_from_base(
                self._tasks_path / task_id / "environment" / "Dockerfile"
            )
            family = _classify_apt_family(base)
            if family is None:
                return args  # uncertain base: keep upstream mirrors
            if family_suite is None:
                family_suite = family
            elif family_suite != family:
                return args  # mixed suites: a single mount cannot fit all
        if family_suite is None:
            return args
        family, suite = family_suite
        overrides.mkdir(parents=True, exist_ok=True)
        content, mount_point = _tuna_apt_sources(family, suite)
        sources_file = overrides / f"{family}-{suite}.sources"
        sources_file.write_text(content, encoding="utf-8")
        return args + self._emit_extra_compose(
            overrides, [(sources_file, mount_point)]
        )

    def _emit_extra_compose(
        self, overrides: Path, mounts: list[tuple[Path, str]]
    ) -> list[str]:
        """Write the extra compose file for ``mounts`` + shared uv overrides."""
        uv_toml = overrides / "uv.toml"
        uv_toml.write_text(
            f'[[index]]\nurl = "{_CN_PYPI}"\ndefault = true\n', encoding="utf-8"
        )
        volumes = [f"      - {src.resolve()}:{dst}:ro" for src, dst in mounts]
        volumes.append(f"      - {uv_toml.resolve()}:/etc/uv/uv.toml:ro")
        uv_bin = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
        if Path(uv_bin).is_file():
            # glibc build: safe for the debian/ubuntu bases we accelerate.
            volumes.append(f"      - {uv_bin}:/usr/local/bin/uv:ro")
        compose = overrides / "extra-compose.yaml"
        compose.write_text(
            "services:\n  main:\n    volumes:\n" + "\n".join(volumes) + "\n",
            encoding="utf-8",
        )
        return ["--extra-docker-compose", str(compose)]

    def _harbor_command(
        self,
        *,
        run_id: str,
        model_name: str,
        task_ids: list[str],
        endpoint: Any,
        n_concurrent: int | None = None,
    ) -> list[str]:
        # Speed/latency knobs. Under this restricted network the on-the-fly env
        # builds that harbor must do for tasks whose image was not prewarmed
        # routinely blow the task-declared build_timeout_sec (600-1800s). Raise
        # the budget with a multiplier (kept modest: too high extends errored
        # hangs, too low re-FAILs; env vars allow override per deployment).
        env_build_mult = float(os.environ.get("IQRADAR_HARBOR_ENV_BUILD_TIMEOUT_MULT", "3.0"))
        timeout_mult = float(os.environ.get("IQRADAR_HARBOR_TIMEOUT_MULT", "1.0"))
        # Trial-level retry for transient infra failures (env build / network /
        # gateway blips) so a flaky build doesn't immediately mark the trial
        # errored. Retry the classic infra/timeout families, never the semantic
        # verifier failures.
        # NOTE: harbor --n-attempts is "independent runs PER TASK" (not retries);
        # keep it at 1 or every task runs N times and cost/time double. The retry
        # knob is --max-retries below.
        n_attempts = int(os.environ.get("IQRADAR_HARBOR_TRIAL_ATTEMPTS", "1"))
        max_retries = int(os.environ.get("IQRADAR_HARBOR_TRIAL_RETRIES", "1"))
        retry_include = (
            "EnvironmentStartTimeoutError",
            "AgentSetupTimeoutError",
            "NetworkConnectionError",
            "NonZeroAgentExitCodeError",
            "RuntimeError",
        )
        cmd = [
            "harbor",
            "run",
            "--path",
            str(self._tasks_path),
            "--agent",
            self._agent,
            "--model",
            _harbor_model_name(model_name),
            "--no-force-build",
            "--no-delete",
            "--n-concurrent",
            # Per-run override (the test page's concurrency field) wins over
            # the benchmark.yaml ``default_concurrency`` baked in at init.
            str(n_concurrent or self.n_concurrent),
            "--jobs-dir",
            str(self.jobs_root),
            "--job-name",
            run_id,
            "--yes",
            "--timeout-multiplier",
            str(timeout_mult),
            "--environment-build-timeout-multiplier",
            str(env_build_mult),
            "--n-attempts",
            str(n_attempts),
            "--max-retries",
            str(max_retries),
        ]
        for exc_name in retry_include:
            cmd += ["--retry-include", exc_name]
        # Select exactly the sampled task subset (harbor --include-task-name
        # accepts a glob; a bare task dir name matches the dataset task).
        for task_id in task_ids:
            cmd.extend(["--include-task-name", task_id])
        # GPU-only tasks abort the whole harbor job on this GPU-less host; drop
        # them so a sampled subset that happens to contain one doesn't nuke the
        # run. (Same exclusion the prewarm applies.)
        for gpu_task in self._gpu_task_names():
            cmd.extend(["--exclude-task-name", gpu_task])
        # Gateway env projected into the in-container agent (OPENAI_BASE_URL is
        # already container-reachable via for_container()).
        for key, value in endpoint.agent_env().items():
            if value:
                cmd.extend(["--ae", f"{key}={value}"])
        # The gateway is reached over a slow/remote link (often a public IP);
        # mini-swe-agent's per-call retry (MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT)
        # already softens 5xx/BadGateway, but only if we forward the knob.
        try:
            llm_retries = os.environ.get("IQRADAR_LLM_RETRY_ATTEMPTS", "10")
            cmd += ["--ae", f"MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT={int(llm_retries)}"]
        except ValueError:
            pass
        # CN mirrors + generous setup timeout for the agent setup phase.
        cmd.extend(self._cn_override_args(task_ids))
        return cmd

    def runner_marker(self, run_id: str) -> str:
        # A resumed run's harbor process is `harbor job resume --job-path
        # <old job dir>` — no --job-name on its command line. The symlink
        # (created by _prepare_resume) carries the mapping so liveness
        # checks keep working across server restarts.
        link = self.jobs_root / run_id
        if link.is_symlink():
            target = os.readlink(link)
            return f"--job-path {self.jobs_root / target}"
        return f"--job-name {run_id}"

    # --- import -------------------------------------------------------------

    def import_records(
        self,
        *,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str = "high",
        prices: object = None,
    ) -> list[RunRecord]:
        job_dir = self._job_dir(run_id)
        if job_dir is None or not job_dir.is_dir():
            return []
        records: list[RunRecord] = []
        for trial_dir in sorted(self._trial_dirs(job_dir)):
            result_path = trial_dir / "result.json"
            if not result_path.is_file():
                continue
            try:
                trial = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records.append(
                self._trial_record(
                    trial=trial,
                    trial_dir=trial_dir,
                    run_id=run_id,
                    model_id=model_id,
                    base_url_hash=base_url_hash,
                    effort=effort,
                )
            )
        return records

    def _job_dir(self, run_id: str) -> Path | None:
        """Locate the harbor job directory for ``run_id``.

        ``--job-name <run_id>`` usually makes harbor create
        ``<jobs_root>/<run_id>/``. If harbor appended a timestamp suffix
        instead, fall back to the newest ``<run_id>*`` directory.
        """
        direct = self.jobs_root / run_id
        if direct.is_dir():
            return direct
        candidates = sorted(
            (p for p in self.jobs_root.glob(f"{run_id}*") if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None

    def _trial_dirs(self, job_dir: Path) -> list[Path]:
        return [
            p
            for p in job_dir.iterdir()
            if p.is_dir() and "__" in p.name and (p / "result.json").is_file()
        ]

    def _trial_record(
        self,
        *,
        trial: dict[str, Any],
        trial_dir: Path,
        run_id: str,
        model_id: str,
        base_url_hash: str,
        effort: str,
    ) -> RunRecord:
        task_name = str(trial.get("task_name") or "")
        # task_name is namespaced ("terminal-bench/music-harmony"); the bare
        # dir name is the stable task id (matches list_task_ids / test_tasks).
        task_id = task_name.split("/")[-1] or trial_dir.name.split("__")[0]
        reward = self._reward(trial)
        is_resolved = reward >= 1.0
        exception = trial.get("exception_info")
        status, error_type, error_message = self._outcome(is_resolved, exception)
        wall_time_sec = self._trial_wall_time(trial)
        agent_result = trial.get("agent_result") or {}
        # harbor 的 agent_result（AgentContext）有 n_input/n_output/n_cache_tokens
        # 但没有步数；agent_steps 从 harbor 写入 trial agent/ 目录的 mini
        # trajectory 采集（info.model_stats.api_calls）。
        trajectory = read_mini_trajectory_usage(
            trial_dir / "agent" / MINI_TRAJECTORY_FILENAME
        )
        return RunRecord.model_validate(
            {
                "run_id": f"terminal-bench-2__{task_id}__{model_id}__{effort}",
                "benchmark": {
                    "name": "terminal-bench-2",
                    "version": "2.0",
                    "task_id": task_id,
                    "repo": "unknown",
                    "language": "unknown",
                    "task_path": str(trial_dir),
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
                    "verifier_passed": is_resolved,
                    "exit_code": 0,
                    "error_type": error_type,
                    "error_message_redacted": error_message,
                },
                "usage": {
                    "input_tokens": _int(agent_result.get("n_input_tokens")),
                    "output_tokens": _int(agent_result.get("n_output_tokens")),
                    "cached_input_tokens": _int(agent_result.get("n_cache_tokens")),
                    "agent_steps": (
                        trajectory["agent_steps"] if trajectory else 0
                    ),
                    "wall_time_sec": wall_time_sec,
                    "usage_estimated": False,
                },
                "cost": {
                    "currency": "USD",
                    "input_cost": 0.0,
                    "cached_input_cost": 0.0,
                    "output_cost": 0.0,
                    "total_cost": 0.0,
                },
                "artifacts": {
                    "patch_path": None,
                    "log_path": self._find_log(trial_dir, "agent"),
                    "verifier_path": self._find_log(trial_dir, "results"),
                },
                "created_at": datetime.now(UTC),
            }
        )

    @staticmethod
    def _reward(trial: dict[str, Any]) -> float:
        """Extract the scalar reward from ``verifier_result.rewards``.

        Harbor supports non-binary rewards; a task is "passed" only at the
        full reward value (1.0 for the standard test-exit-code tasks).
        """
        verifier = trial.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        if isinstance(rewards, dict):
            if "reward" in rewards:
                return _as_float(rewards["reward"])
            for value in rewards.values():
                f = _as_float(value)
                if f is not None:
                    return f
        return 0.0

    @staticmethod
    def _outcome(
        is_resolved: bool, exception: Any
    ) -> tuple[str, str | None, str | None]:
        if exception:
            if isinstance(exception, dict):
                et = str(
                    exception.get("type")
                    or exception.get("exception_type")
                    or "runner_error"
                )
                # Harbor writes the message under "exception_message" (see
                # harbor.models.trial.result.ExceptionInfo); "message" /
                # "exception" were legacy guesses that always yielded None.
                msg = (
                    exception.get("exception_message")
                    or exception.get("message")
                    or exception.get("exception")
                )
            else:
                et = "runner_error"
                msg = exception
            return "failed", et, _truncate(msg)
        if is_resolved:
            return "passed", None, None
        return "failed", "verifier_failed", None

    @staticmethod
    def _trial_wall_time(trial: dict[str, Any]) -> float:
        for start_key, end_key in [
            ("started_at", "finished_at"),
            (
                "agent_execution",
                "agent_execution",
            ),  # fallback handled below for nested phases
        ]:
            if start_key == "agent_execution":
                continue
            started = trial.get(start_key)
            ended = trial.get(end_key)
            secs = _delta_seconds(started, ended)
            if secs is not None:
                return secs
        # nested phase timestamps: agent_execution.started_at / finished_at
        agent_exec = trial.get("agent_execution") or {}
        secs = _delta_seconds(
            agent_exec.get("started_at"), agent_exec.get("finished_at")
        )
        return secs or 0.0

    @staticmethod
    def _find_log(trial_dir: Path, kind: str) -> str | None:
        if kind == "agent":
            agent_dir = trial_dir / "agent"
            if agent_dir.is_dir():
                for candidate in sorted(agent_dir.iterdir(), reverse=True):
                    if candidate.is_file():
                        return str(candidate)
            return None
        if kind == "results":
            path = trial_dir / "result.json"
            return str(path) if path.is_file() else None
        return None

    # --- logs ---------------------------------------------------------------

    def _log_path(self, run_id: str, source: str) -> Path | None:
        if source == "run":
            # Harbor writes its real progress to <job_dir>/job.log; its stdout
            # (captured into runs/<run_id>/pier.log) stays empty for the whole
            # run, so the "运行日志" panel must read job.log instead. Before
            # harbor creates the job dir (early image-build phase) there is no
            # log yet and the frontend shows its environment-build hint.
            job_dir = self._job_dir(run_id)
            if job_dir is None:
                return None
            path = job_dir / "job.log"
            return path if path.is_file() else None
        if source == "results":
            job_dir = self._job_dir(run_id)
            if job_dir is not None:
                path = job_dir / "result.json"
                return path if path.is_file() else None
            return None
        if source == "agent":
            path = self._latest_trial_file(run_id, "agent")
            return path if path is not None and path.is_file() else None
        return None

    def _latest_trial_file(self, run_id: str, subdir: str) -> Path | None:
        """Return the most recently modified file in any trial's ``subdir``."""
        job_dir = self._job_dir(run_id)
        if job_dir is None or not job_dir.is_dir():
            return None
        candidates: list[Path] = []
        for trial_dir in self._trial_dirs(job_dir):
            target = trial_dir / subdir
            if target.is_dir():
                candidates.extend(p for p in target.iterdir() if p.is_file())
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def job_finished(self, run_id: str) -> bool:
        """True when harbor's job-level result.json has been finalized.

        Harbor writes ``<jobs_root>/<run_id>/result.json`` at startup as a
        live progress file (``finished_at`` null, ``n_running_trials`` > 0);
        a plain file-existence check marks every run "completed" the instant
        harbor begins, so the batch worker polls a falsely-finished run,
        skips to the next model, and leaves harbor grinding in the background
        with an empty "运行日志". Finalized means ``finished_at`` is set,
        matching the deep-swe backend's guard against the same progress file.
        """
        job_dir = self._job_dir(run_id)
        if job_dir is None:
            return False
        job_result = job_dir / "result.json"
        if not job_result.is_file():
            return False
        try:
            data = json.loads(job_result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return bool(isinstance(data, dict) and data.get("finished_at"))


def _iqradar_src_dir() -> Path:
    """Absolute path of the iqradar ``src`` package directory (for PYTHONPATH
    when harbor imports a custom ``module:Class`` agent)."""
    return Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    """Project root (parent of ``src``)."""
    return Path(__file__).resolve().parents[3]


def _harbor_model_name(model_name: str) -> str:
    """harbor agents are litellm-based; prefix ``openai/`` so the gateway
    (OPENAI_BASE_URL) is used as the provider."""
    if model_name.startswith("openai/"):
        return model_name
    return f"openai/{model_name}"


def _int(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _env_flag(name: str) -> bool:
    """True when the named env var is set to a truthy 1/on/yes/true value."""
    raw = os.environ.get(name, "")
    return raw.strip().lower() in {"1", "on", "yes", "true"}


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truncate(value: object, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit] or None


def _delta_seconds(started: object, ended: object) -> float | None:
    if not started or not ended:
        return None
    try:
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return max(0.0, (end - start).total_seconds())


def _wait_with_cancel(
    process: subprocess.Popen,
    *,
    cancel_event: threading.Event | None,
    timeout_sec: int,
) -> int:
    """Wait for the harbor process, terminating it on cancel or timeout.

    The process is spawned in its own session (start_new_session=True) so the
    whole harbor / docker-build tree can be killed as one group.
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
