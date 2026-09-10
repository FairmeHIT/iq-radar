#!/usr/bin/env python3
"""预热固定评测任务的 Docker 镜像（配合 ``benchmarks.test_tasks``）。

目标：把"构建/拉取镜像"从测试时挪到测试前，评测运行期间零网络依赖、
不受网络波动影响（配合 runner 的 ``--no-delete`` 保留镜像）。

用法::

    uv run python scripts/prewarm_environments.py \
        [--benchmark deep-swe|terminal-bench] \
        [--dry-run] [--jobs N] [--all] [--config configs/benchmark.yaml]

各基准行为:

deep-swe
    对 ``test_tasks`` 里每个任务生成与 pier 完全一致的 agent Dockerfile
    （复用 pier 的 ``write_agent_dockerfile`` + deep-swe 的 ``CnMiniSweAgent``，
    安装指纹与 pier 运行时一致），然后 ``docker buildx build --load`` 构建到
    本地。测试时 pier 的 ``docker compose build`` 直接命中 buildkit 缓存
    （秒级 CACHED），不访问任何 registry。

terminal-bench
    对 ``test_tasks`` 里每个任务 ``docker compose build``，打成 tb 运行时
    使用的 ``tb__<task>__client`` 标签。评测侧用 ``--no-rebuild --no-cleanup``，
    运行中不重建、结束后不删镜像。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIER_SITE_PACKAGES = (
    Path.home()
    / ".local"
    / "share"
    / "uv"
    / "tools"
    / "datacurve-pier"
    / "lib"
    / "python3.13"
    / "site-packages"
)


def _pier_site_packages() -> Path | None:
    return PIER_SITE_PACKAGES if PIER_SITE_PACKAGES.is_dir() else None


def _load_benchmark_config(config_path: Path):
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from iqradar.config.loader import load_benchmark_config

    return load_benchmark_config(config_path)


def _deep_swe_prewarm(backend, *, dry_run: bool, jobs: int) -> None:
    """Build the agent image for each fixed deep-swe task."""
    pier_dir = _pier_site_packages()
    deep_swe_dir = backend._config.local_path
    if pier_dir is None or not deep_swe_dir.is_dir():
        print(
            "deep-swe 预热需要 pier 工具与 deep-swe 检出（pier_dir / local_path "
            "未找到），跳过。",
            file=sys.stderr,
        )
        return
    sys.path.insert(0, str(pier_dir))
    sys.path.insert(0, str(deep_swe_dir))

    from pier.environments.agent_setup import write_agent_dockerfile
    from cn_agent import CnMiniSweAgent

    config = backend._config  # DeepSweConfig
    tasks_path = config.tasks_path
    test_tasks = list(config.test_tasks)
    if not test_tasks:
        print("deep-swe 未配置 test_tasks，跳过。", file=sys.stderr)
        return
    print(f"deep-swe 预热 {len(test_tasks)} 个任务：{', '.join(test_tasks)}")

    def build_one(task_name: str) -> tuple[str, bool]:
        task_toml = tasks_path / task_name / "task.toml"
        if not task_toml.is_file():
            return task_name, False
        with task_toml.open("rb") as handle:
            task_data = tomllib.load(handle)
        env_cfg = task_data.get("environment") or {}
        docker_image = env_cfg.get("docker_image")
        if not docker_image:
            return task_name, False
        # 与 pier 一致：agent 用户取 task.toml [agent].user（缺省 None → root），
        # 保证生成的 Dockerfile 与 pier 构建时逐字节一致，buildkit 才能命中缓存。
        agent_cfg = task_data.get("agent") or {}
        agent_user = agent_cfg.get("user")
        agent = CnMiniSweAgent(
            model_name="openai/gateway/deepseek-v4-flash",
            logs_dir=Path(tempfile.gettempdir()) / "prewarm-logs",
        )
        install = agent.install_spec()
        tag = f"iqradar-prewarm/{task_name}:{install.fingerprint()}"
        if dry_run:
            print(f"  [dry-run] 将构建 {tag} <- FROM {docker_image}")
            return task_name, True
        inspect = subprocess.run(
            ["docker", "image", "inspect", tag], capture_output=True, text=True
        )
        if inspect.returncode == 0:
            print(f"  skip {task_name} -> {tag}")
            return task_name, True
        with tempfile.TemporaryDirectory(prefix=f"prewarm-{task_name}-") as td:
            build_dir = Path(td)
            write_agent_dockerfile(
                build_dir=build_dir,
                source_environment_dir=build_dir,
                prebuilt_image_name=str(docker_image),
                install=install,
                user=agent_user,
            )
            # buildx 活动记录默认写在 ~/.docker（某些受限环境下只读）；指向
            # 项目内可写目录（与其他基准后端的做法一致）。
            buildx_config = PROJECT_ROOT / "data" / "prewarm" / ".buildx"
            buildx_config.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ)
            env["BUILDX_CONFIG"] = str(buildx_config)
            result = subprocess.run(
                [
                    "docker", "buildx", "build", "--load",
                    "-t", tag,
                    str(build_dir),
                ],
                capture_output=True,
                text=True,
                env=env,
            )
            if result.returncode != 0:
                print(
                    f"  构建失败 {task_name}: {result.stderr[-800:]}",
                    file=sys.stderr,
                )
                return task_name, False
            print(f"  ok {task_name} -> {tag}")
            return task_name, True

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(build_one, test_tasks))
    failed = [name for name, ok in results if not ok]
    if failed:
        raise SystemExit(f"预热失败的任务：{', '.join(failed)}")
    print("deep-swe 预热完成。")


def _terminal_bench_prewarm(
    backend, *, dry_run: bool, jobs: int, all_tasks: bool = False
) -> None:
    """Build ``tb__<task>__client`` images for the terminal-bench subset.

    ``all_tasks=True`` (``--all``) ignores the configured ``test_tasks`` and
    builds every task directory discovered under ``tasks_path`` — the full
    dataset (241 tasks for original-tasks), not just the fixed eval subset.
    """
    tasks_path: Path = backend._tasks_path
    if all_tasks:
        # Enumerate the raw dataset dir the same way list_task_ids() discovers
        # tasks, but WITHOUT the test_tasks filter (which would shrink it back
        # to the fixed eval subset).
        test_tasks = sorted(
            path.name
            for path in tasks_path.iterdir()
            if path.is_dir()
            and not path.name.startswith(".")
            and path.name != "README.md"
        )
        if not test_tasks:
            print("terminal-bench 无可用任务，跳过。", file=sys.stderr)
            return
        preview = ", ".join(test_tasks[:5])
        print(
            f"terminal-bench --all:预热全部 {len(test_tasks)} 个任务"
            f"（{preview}… 共 {len(test_tasks)}）"
        )
    else:
        test_tasks = list(backend._config.test_tasks or ())
        if not test_tasks:
            test_tasks = backend.list_task_ids()[:10]
            print(
                f"terminal-bench 未配置 test_tasks，预热发现的前 {len(test_tasks)} 个任务。",
                file=sys.stderr,
            )
        if not test_tasks:
            print("terminal-bench 无可用任务，跳过。", file=sys.stderr)
            return
        print(f"terminal-bench 预热 {len(test_tasks)} 个任务：{', '.join(test_tasks)}")
    buildx_config = PROJECT_ROOT / "data" / "prewarm" / ".buildx"
    buildx_config.mkdir(parents=True, exist_ok=True)
    dummy_logs = PROJECT_ROOT / "data" / "prewarm" / "tb-logs"
    dummy_logs.mkdir(parents=True, exist_ok=True)
    # ~/.docker/config.json 通常带 "credsStore": "desktop.exe"（Docker Desktop
    # 的凭据助手）。原生 dockerd 下 docker-credential-desktop.exe 不在 PATH，
    # 任何 FROM 公共 ghcr.io / docker.io 镜像的 compose build 都会因凭据
    # 查找失败而 deterministic 失败。用一份空配置覆盖 DOCKER_CONFIG：公共
    # 镜像匿名拉取即可，无需凭据助手。不改动用户的 ~/.docker/config.json。
    clean_docker_cfg = PROJECT_ROOT / "data" / "prewarm" / ".docker-config"
    clean_docker_cfg.mkdir(parents=True, exist_ok=True)
    (clean_docker_cfg / "config.json").write_text("{}\n", encoding="utf-8")

    def build_one(task_name: str) -> tuple[str, bool]:
        compose = tasks_path / task_name / "docker-compose.yaml"
        if not compose.is_file():
            print(f"  缺少 {compose}", file=sys.stderr)
            return task_name, False
        image = f"tb__{task_name.replace('.', '-')}__client"
        if dry_run:
            print(f"  [dry-run] 将构建 {image}")
            return task_name, True
        inspect = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True
        )
        if inspect.returncode == 0:
            print(f"  skip {task_name} -> {image}")
            return task_name, True
        env = dict(os.environ)
        env["BUILDX_CONFIG"] = str(buildx_config)
        env["DOCKER_CONFIG"] = str(clean_docker_cfg)
        env["T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME"] = image
        env["T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME"] = f"prewarm-{task_name}"
        env["T_BENCH_TEST_DIR"] = "/tests"
        env["T_BENCH_TASK_LOGS_PATH"] = str(dummy_logs)
        env["T_BENCH_CONTAINER_LOGS_PATH"] = "/logs"
        env["T_BENCH_TASK_AGENT_LOGS_PATH"] = str(dummy_logs)
        env["T_BENCH_CONTAINER_AGENT_LOGS_PATH"] = "/agent-logs"
        result = subprocess.run(
            [
                "docker", "compose",
                "-f", str(compose),
                "build",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            print(
                f"  构建失败 {task_name}: {(result.stderr or result.stdout)[-800:]}",
                file=sys.stderr,
            )
            return task_name, False
        print(f"  ok {task_name} -> {image}")
        return task_name, True

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        results = list(pool.map(build_one, test_tasks))
    failed = [name for name, ok in results if not ok]
    if failed:
        raise SystemExit(f"预热失败的任务：{', '.join(failed)}")
    print("terminal-bench 预热完成。")


def _tb2_prewarm(backend, *, dry_run: bool, jobs: int) -> None:
    """Build+keep every Terminal-Bench 2.0 task environment image.

    Unlike v1's per-task ``docker compose build``, TB2 prewarm is a single
    ``harbor run`` over the whole dataset: harbor builds each task's
    ``<task>__<hash>__env-main`` image and leaves it (``--no-delete``); eval
    runs reuse it via ``--no-force-build --no-delete``. ``--install-only``
    keeps ``_prepare()`` (environment image build + agent setup) but skips the
    agent run and verification, so prewarm is a fast env-image-only build — the
    exact thing eval's ``EnvironmentStartTimeoutError`` gate needs. The
    verifier image is deliberately deferred to eval-time verification (its base
    is already cached from the env build), so it doesn't re-trigger the slow
    prewarm.

    A clean ``DOCKER_CONFIG`` (no ``credsStore: desktop.exe``) is applied so
    harbor's public ghcr.io / docker.io base-image pulls don't fail on
    credential lookup under the native WSL dockerd.
    """
    tasks_path: Path = backend._tasks_path
    if not tasks_path.is_dir():
        print("terminal-bench-2 tasks_path 不存在，跳过。", file=sys.stderr)
        return
    test_tasks = list(backend._config.test_tasks or ())
    include_args: list[str] = []
    if test_tasks:
        for task_name in test_tasks:
            include_args += ["--include-task-name", task_name]
        scope = f"{len(test_tasks)} 个固定子集"
    else:
        scope = f"全部 {backend.task_count()} 个任务"
    # GPU-only tasks can't build their env on this GPU-less host (plain docker,
    # no nvidia-docker) and harbor aborts the whole job when it reaches one —
    # exclude them so prewarm covers every runnable task and completes.
    gpu_tasks = backend._gpu_task_names()
    if gpu_tasks:
        scope += f"（排除 {len(gpu_tasks)} 个 GPU 任务）"
    print(f"terminal-bench-2 预热 {scope}")
    buildx_config = PROJECT_ROOT / "data" / "prewarm" / ".buildx"
    buildx_config.mkdir(parents=True, exist_ok=True)
    clean_docker_cfg = PROJECT_ROOT / "data" / "prewarm" / ".docker-config"
    clean_docker_cfg.mkdir(parents=True, exist_ok=True)
    (clean_docker_cfg / "config.json").write_text("{}\n", encoding="utf-8")
    jobs_dir = PROJECT_ROOT / "data" / "tb2" / "prewarm"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    # Generous build windows: prewarm must build every task env on this slow
    # network; the task-declared build_timeout_sec (600-1800s) is not enough for
    # the full first-time install (github/conda throttled), which is precisely
    # why eval-time EnvironmentStartTimeoutError happens when prewarm is skipped.
    env_build_mult = os.environ.get("IQRADAR_HARBOR_ENV_BUILD_TIMEOUT_MULT", "3.0")
    setup_mult = os.environ.get("IQRADAR_HARBOR_SETUP_TIMEOUT_MULT", "3.0")
    cmd = [
        "harbor", "run",
        "--path", str(tasks_path),
        "--agent", "oracle",
        "--install-only",
        "--no-force-build",
        "--no-delete",
        "--n-concurrent", str(max(1, jobs)),
        "--jobs-dir", str(jobs_dir),
        "--yes",
        "--timeout-multiplier", "1.0",
        "--environment-build-timeout-multiplier", env_build_mult,
        "--agent-setup-timeout-multiplier", setup_mult,
        *include_args,
    ]
    for gpu_task in gpu_tasks:
        cmd += ["--exclude-task-name", gpu_task]
    if dry_run:
        print("  [dry-run] " + " ".join(cmd))
        return
    env = dict(os.environ)
    env["BUILDX_CONFIG"] = str(buildx_config)
    env["DOCKER_CONFIG"] = str(clean_docker_cfg)
    # Stream harbor's progress to the caller's stdout (tee'd to the log by the
    # background job); do not capture. harbor --no-delete keeps every built
    # image even when some trials error, so a non-zero exit is not fatal —
    # re-running skips already-built images and only fills the gaps.
    result = subprocess.run(cmd, env=env)
    count = subprocess.run(
        ["bash", "-c", "docker images --format '{{.Repository}}' "
         "| grep -cE '__env-[a-zA-Z0-9._-]+$|__verifier__trial-main$'"],
        capture_output=True, text=True,
    ).stdout.strip() or "?"
    if result.returncode != 0:
        print(
            f"  harbor 退出码 {result.returncode}；TB2 镜像数 {count}（部分任务失败，"
            "重跑会跳过已建镜像只补缺）",
            file=sys.stderr,
        )
    else:
        print(f"  ok harbor 完成；TB2 镜像数 {count}")
    # Durable readiness marker: which task ids now have a built/restored env
    # image. Written on every exit (even partial), so the eval prewarm gate and
    # operators share one machine-readable source of truth instead of guessing.
    try:
        built = subprocess.run(
            ["bash", "-c", "docker images --format '{{.Repository}}' "
             "| grep -E '__env-[a-zA-Z0-9._-]+$' "
             "| sed -E 's/__[a-zA-Z0-9]+__env-[a-zA-Z0-9._-]+$//' | sort -u"],
            capture_output=True, text=True,
        ).stdout.split()
        # Total RUNNABLE task set to judge "complete" against: the fixed
        # test_tasks subset when configured, otherwise every on-disk task
        # minus the GPU-only tasks that this host can never build/eval.
        if test_tasks:
            total_tasks = len([t for t in test_tasks if t not in gpu_tasks])
        else:
            total_tasks = backend.task_count() - len(gpu_tasks)
        ready = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "task_count": len(built),
            "task_ids": sorted(built),
            "total_tasks": total_tasks,
            "gpu_excluded": sorted(gpu_tasks),
            "complete": len(built) >= total_tasks,
        }
        (jobs_dir / "ready.json").write_text(
            json.dumps(ready, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    print("terminal-bench-2 预热完成。")


PREWARMERS = {
    "deep-swe": _deep_swe_prewarm,
    "terminal-bench": _terminal_bench_prewarm,
    "terminal-bench-2": _tb2_prewarm,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        default="all",
        choices=[*sorted(PREWARMERS), "all"],
        help="要预热的基准（默认 all）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的操作")
    parser.add_argument("--jobs", type=int, default=1, help="并行构建数（deep-swe / terminal-bench）")
    parser.add_argument(
        "--all",
        action="store_true",
        help="terminal-bench: 忽略 test_tasks，预热 tasks_path 下全部任务（完整 241 题）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "benchmark.yaml",
        help="benchmark.yaml 路径",
    )
    args = parser.parse_args()

    config_set = _load_benchmark_config(args.config)
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from iqradar.benchmarks.registry import build_backends  # noqa: PLC0415

    backends = build_backends(config_set, PROJECT_ROOT)
    names = sorted(PREWARMERS) if args.benchmark == "all" else [args.benchmark]
    for name in names:
        if name not in backends:
            raise SystemExit(f"configs/benchmark.yaml 里没有基准 {name}")
        kwargs: dict = {"dry_run": args.dry_run, "jobs": args.jobs}
        if name == "terminal-bench":
            kwargs["all_tasks"] = args.all
        PREWARMERS[name](backends[name], **kwargs)


if __name__ == "__main__":
    main()
