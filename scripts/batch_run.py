#!/usr/bin/env python3
"""IQRadar batch runner: N models x 50 tasks (seed 0), then combined publish.

Detached execution:
    nohup .venv/bin/python scripts/batch_run.py >> data/batch_run.log 2>&1 &

Resumable: runs already completed for (model_id, n_tasks, sample_seed) are
detected from the run store and skipped; the combined snapshot is published
only after every model finishes.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE = "http://127.0.0.1:8081"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = PROJECT_ROOT / "data" / "deepswe" / "runs"

MODELS = [
    "gateway/deepseek-v4-flash",
    "gateway/glm-5.2",
    "gateway/example-35b",
    "gateway/example-236b",
]
N_TASKS = 10
SAMPLE_SEED = 0
POLL_SEC = 60
PROGRESS_LOG_SEC = 600


def log(message: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def api(path: str, method: str = "GET", body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except json.JSONDecodeError:
            return exc.code, {}
    except OSError:
        return 0, {}


def find_existing_run(model_id: str) -> dict | None:
    for state_path in RUNS_DIR.glob("*/state.json"):
        try:
            run = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            run.get("model_id") == model_id
            and run.get("n_tasks") == N_TASKS
            and run.get("sample_seed") == SAMPLE_SEED
        ):
            return run
    return None


def submit_with_retry(model_id: str) -> dict:
    for attempt in range(10000):
        status, payload = api(
            "/api/deepswe-runs",
            "POST",
            {"model_id": model_id, "n_tasks": N_TASKS, "sample_seed": SAMPLE_SEED},
        )
        if status == 202:
            return payload["data"]
        if status == 409:
            if attempt % 5 == 0:
                log(f"  active run present, waiting for a free slot (attempt {attempt})")
            time.sleep(POLL_SEC)
            continue
        log(f"  submit failed: {status} {payload.get('error')}; retrying in 60s")
        time.sleep(POLL_SEC)
    raise RuntimeError("give up waiting for the active run slot")


def run_model(model_id: str) -> dict | None:
    existing = find_existing_run(model_id)
    if existing is not None and existing.get("status") == "completed":
        log(f"[skip] {model_id} already completed as {existing['run_id']}")
        return existing
    if existing is not None and existing.get("status") == "running":
        log(f"[resume] {model_id} still running as {existing['run_id']}")
        run_id = existing["run_id"]
    else:
        run = submit_with_retry(model_id)
        run_id = run["run_id"]
        log(f"[submit] {model_id} -> {run_id} (50 tasks, seed 0)")

    started = time.monotonic()
    last_progress = 0.0
    while True:
        time.sleep(POLL_SEC)
        status, payload = api(f"/api/deepswe-runs/{run_id}")
        run = payload.get("data") if status == 200 else None
        if run is None:
            if int(time.monotonic() - last_progress) > PROGRESS_LOG_SEC:
                log(f"  {model_id} poll trouble (status={status}); retrying")
                last_progress = time.monotonic()
            continue
        if run["status"] in ("completed", "failed"):
            elapsed = (time.monotonic() - started) / 3600
            log(
                f"[done] {model_id} status={run['status']} "
                f"elapsed={elapsed:.1f}h error={run.get('error')}"
            )
            return run if run["status"] == "completed" else None
        if time.monotonic() - last_progress > PROGRESS_LOG_SEC:
            log(f"  {model_id} still {run['status']} ({run.get('n_tasks')} tasks)")
            last_progress = time.monotonic()


def publish_combined(runs: list[dict]) -> None:
    from iqradar.config.loader import parse_price_config
    from iqradar.publication.service import DashboardPublisher
    from iqradar.reporting.repository import FileDashboardRepository
    from iqradar.schemas.run_record import RunRecord

    prices_path = PROJECT_ROOT / "configs" / "prices.example.yaml"
    repo = FileDashboardRepository(
        PROJECT_ROOT / "data" / "reporting",
        legacy_summary_path=PROJECT_ROOT / "data" / "aggregate" / "radar.json",
        legacy_runs_path=PROJECT_ROOT / "data" / "raw" / "runs",
    )
    publisher = DashboardPublisher(repo, prices_path)

    records: list[RunRecord] = []
    for run in runs:
        run_id = run["run_id"]
        status, payload = api(f"/api/deepswe-runs/{run_id}/records")
        items = payload.get("data") or []
        parsed = [RunRecord.model_validate(item) for item in items]
        records.extend(parsed)
        log(f"  {run['model_id']}: {len(parsed)} records")
    if not records:
        log("no records at all; skipping publish")
        return
    batch_id = (
        f"batch-50x{len(runs)}-{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
    )
    # 累积发布（radar-v3 起默认）：新纪录与当前快照合并去重，跨批次累积。
    publication = publisher.publish_records(batch_id, records, merge_with_current=True)
    log(f"published snapshot {publication.snapshot_id} ({len(records)} records)")
    status, dash = api("/api/dashboard")
    data = dash.get("data") or {}
    models = sorted(
        {s["model"] for s in data.get("summary", {}).get("summaries", [])}
    )
    log(f"dashboard now shows: {models}")


def main() -> None:
    log(f"batch start: {len(MODELS)} models x {N_TASKS} tasks, seed {SAMPLE_SEED}")
    status, _ = api("/health/readiness")
    if status != 200:
        log(f"FATAL: IQRadar API unreachable (status={status})")
        return

    completed: list[dict] = []
    for model_id in MODELS:
        log(f"=== model: {model_id} ===")
        run = run_model(model_id)
        if run is not None:
            completed.append(run)
        else:
            log(f"WARN: {model_id} did not complete; continuing with the rest")

    if not completed:
        log("no completed runs; skipping publish")
        return
    log(f"all models finished; publishing combined snapshot from {len(completed)} runs")
    publish_combined(completed)
    log("batch finished")


if __name__ == "__main__":
    main()
