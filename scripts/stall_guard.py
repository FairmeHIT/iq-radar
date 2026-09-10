#!/usr/bin/env python3
"""IQRadar batch stall guard.

Detached helper that watches the active DeepSWE trial of the running batch.
If the agent's trajectory file stops being written for STALL_MINUTES, it kills
the non-essential processes inside the trial container (long-running test
daemons that hold the tool's stdout pipe open, which hangs mini-swe-agent).

Safe processes (kept): the container keepalive wrappers, the mini-swe-agent
process, its bash wrapper and tee.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

# 项目根目录：默认取本脚本所在仓库根，可用 IQ_RADAR_PROJECT_ROOT 环境变量覆盖。
PROJECT_ROOT = Path(
    os.environ.get("IQ_RADAR_PROJECT_ROOT", Path(__file__).resolve().parents[1])
)
JOBS_DIR = PROJECT_ROOT / "data" / "deepswe" / "jobs"
RUNS_DIR = PROJECT_ROOT / "data" / "deepswe" / "runs"
STALL_MINUTES = 25
CHECK_INTERVAL_SEC = 300
LOG_PATH = PROJECT_ROOT / "data" / "stall_guard.log"

SAFE_CMD_MARKERS = (
    "sleep infinity",
    "sleep 3600",
    "mini-swe-agent",
    "tee /logs/agent/mini-swe-agent.txt",
    "sh -c",
    "bash -c",
)


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def active_run() -> str | None:
    for state_path in RUNS_DIR.glob("*/state.json"):
        try:
            run = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if run.get("status") in ("queued", "running"):
            return run["run_id"]
    return None


def latest_trial(run_id: str) -> Path | None:
    job_dir = JOBS_DIR / run_id
    if not job_dir.is_dir():
        return None
    trials = [
        path
        for path in job_dir.iterdir()
        if path.is_dir() and (path / "agent" / "mini-swe-agent.trajectory.json").is_file()
    ]
    if not trials:
        return None
    return max(trials, key=lambda path: path.stat().st_mtime)


def container_name(trial_dir: Path) -> str | None:
    trial_name = trial_dir.name
    try:
        output = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for name in output.splitlines():
        if name.startswith(trial_name.lower()):
            return name
    return None


def kill_non_essential(container: str) -> int:
    """Kill non-essential processes inside the container; returns count."""
    try:
        output = subprocess.run(
            ["docker", "top", container],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    killed = 0
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 8:
            continue
        host_pid, cmd = parts[1], " ".join(parts[7:])
        if any(marker in cmd for marker in SAFE_CMD_MARKERS):
            continue
        # Resolve the container PID via NSpid and kill as root inside the ctr.
        try:
            status = Path(f"/proc/{host_pid}/status").read_text()
            nspids = [
                tok
                for tok in status.splitlines()
                if tok.startswith("NSpid:")
            ]
            container_pid = nspids[0].split()[-1] if nspids else host_pid
        except OSError:
            continue
        result = subprocess.run(
            ["docker", "exec", "-u", "root", container, "sh", "-c",
             f"kill -9 {container_pid} 2>/dev/null; echo rc=$?"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if "rc=0" in result.stdout:
            killed += 1
            log(f"  killed pid {host_pid} (ctr {container_pid}): {cmd[:80]}")
    return killed


def main() -> None:
    log("stall guard started")
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        run_id = active_run()
        if run_id is None:
            continue
        trial = latest_trial(run_id)
        if trial is None:
            continue
        traj = trial / "agent" / "mini-swe-agent.trajectory.json"
        try:
            age_min = (time.time() - traj.stat().st_mtime) / 60
        except OSError:
            continue
        if age_min < STALL_MINUTES:
            continue
        container = container_name(trial)
        if container is None:
            log(f"STALL: {trial.name} trajectory {age_min:.0f}m old, no container found")
            continue
        log(f"STALL: {trial.name} trajectory {age_min:.0f}m old; cleaning container {container}")
        count = kill_non_essential(container)
        if count == 0:
            log("  no non-essential processes; will re-check next cycle")


if __name__ == "__main__":
    main()
