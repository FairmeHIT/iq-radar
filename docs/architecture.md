# IQRadar Architecture

IQRadar separates evaluation from reporting at the process, module, and data boundaries.

```text
Test page API -> Benchmark run -> Job directory -> private records
                                                      |
                                            explicit publication
                                                      |
Dashboard bundle API <- current pointer <- immutable reporting snapshot
```

## Multi-Benchmark Design

IQRadar supports multiple benchmark backends through a common interface:

```text
                      +-- DeepSweBackend (pier subprocess)
                      |
BenchmarkBackend ----+-- TerminalBenchBackend (tb CLI process)
                      |
                      +-- TerminalBench2Backend (harbor)
                      |
                      +-- ApiEvalBackend (direct gateway HTTP)
```

Eval-time model calls (`POST /v1/chat/completions`) have a single source: the
test-page **模型调用** pair, resolved by `iqradar.settings.inference.InferenceEndpoint`.
In-process backends call it directly; Docker/pier agents inherit the same pair
as `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `MSWEA_API_KEY`. The **模型列表获取**
pair (`GET /v1/models`) stays separate.

Each backend implements `BenchmarkBackend` (defined in `src/iqradar/benchmarks/base.py`):

- `run()` — execute the benchmark with the given model, number of tasks, and seed
- `import_records()` — convert the benchmark's output into a list of `RunRecord` objects
- `job_finished()` — check if the benchmark's job-level result file is finalized
- `runner_alive()` / `runner_marker()` — detect whether the benchmark runner process is still running
- `log_sources()` / `log_content()` — expose live log files for the web UI
- `progress()` — question-level progress (`{"total": N, "completed": M}` plus
  optional `running`/`pending`) for the test page's 题目进度 display.
  Pier/harbor backends parse the job-level `result.json` stats; api-eval keeps
  an in-memory counter during execution (falling back to `results.jsonl` line
  counts after a restart). `DeepSweService.progress()` merges backend counts
  with the run's `n_tasks` (active runs without artifacts fall back to
  `0/n_tasks`).

Benchmarks are configured in `configs/benchmark.yaml` and registered via `build_backends()` in the registry. The `DeepSweService` dispatches `submit()`, `records()`, `log_sources()`, and reconciliation to the appropriate backend based on the `benchmark` field stored in each run's state.

## Benchmark Backend Details

### DeepSWE (pier)

- **Type**: `deep-swe`
- **Runner**: `pier` subprocess (Harbor-compatible runner binary)
- **Agent**: `cn_agent:CnMiniSweAgent` (Chinese-mirror mini-swe-agent)
- **Data**: `data/datasets/deep-swe` (symlink to `checkouts/deep-swe/tasks/`)
- **Artifacts**: `data/deepswe/jobs/<run_id>/` (per-trial directories)
- **Logs**: pier, job, trial, agent, verifier, llm (model response trajectory)

### Terminal-Bench (v1, disabled)

- **Type**: `terminal-bench`
- **Runner**: `tb` CLI (PyPI `terminal-bench==0.2.18`)
- **Agent**: `iqradar.benchmarks.tb_agents:MiniSweAgentCompat` (mini-swe-agent
  drop-in that tolerates nested-slash model names, uses `MSWEA_API_KEY`)
- **Status**: the v1 config block, dataset checkout
  (`checkouts/terminal-bench-1`), and job artifacts (`data/terminal-bench`,
  legacy `data/tbench`) were removed; the backend code
  (`terminal_bench.py`, `tb_agents.py`) is retained so v1 can be re-enabled by
  restoring a `terminal-bench:` block in `benchmark.yaml` and re-cloning the
  checkout. Active runs use Terminal-Bench 2.0 (see `data/tb2/jobs/`).
- **Logs**: run, results

## Dependency Rules

- `iqradar.benchmarks` — benchmark adapters (backends). Must not import `iqradar.reporting` or `iqradar.publication`.
- `iqradar.deepswe` — run submission, execution, and private result records. Must not import `iqradar.reporting` or `iqradar.publication`.
- `iqradar.reporting` — dashboard queries and versioned snapshots. Must not import `iqradar.deepswe` or `iqradar.benchmarks`.
- `iqradar.publication` — the only module allowed to bridge deep-swe records and reporting snapshots.
- `iqradar.api.app` — the composition root. Wires feature blueprints, builds backends, and injects dependencies without implementing business logic.
- `web/src/features/dashboard` and `web/src/features/deepswe` — separate state, API clients, and types. `App.vue` is only the navigation shell.

The boundary tests are in `tests/architecture/test_module_boundaries.py` and `web/src/architecture.test.ts`.

## Runtime Boundaries

- Flask only validates requests, submits runs, and persists run state. The runner subprocess (pier, tb) or Docker containers run detached; run progress is derived from the benchmark's job directory on each status read.
- Each run has an isolated directory under `data/deepswe/runs/<run_id>/` with its own state file, records, and a `benchmark` field indicating which backend produced it.
- The test page restores the latest persisted run after a reload, polls the benchmark's run endpoint, and exposes the appropriate log sources for the run's benchmark type.
- Run completion never calls a dashboard API. Publishing is a separate user command and API resource.

## Storage

```text
data/datasets/deep-swe                     symlink → checkouts/deep-swe/tasks
data/datasets/terminal-bench-2              symlink → checkouts/terminal-bench/tasks
data/datasets/api-eval/*.jsonl             prepared api-eval question sets
data/deepswe/runs/<run_id>/state.json      run lifecycle + request + benchmark
data/deepswe/runs/<run_id>/pier.log        Pier stdout/stderr (deep-swe only)
data/deepswe/runs/<run_id>/records.jsonl   imported RunRecords
data/deepswe/jobs/<run_id>/                Pier job output (deep-swe only)
data/tb2/jobs/<run_id>/                    Terminal-Bench 2.0 job output
data/reporting/snapshots/<snapshot_id>/summary.json
data/reporting/snapshots/<snapshot_id>/runs.jsonl
data/reporting/snapshots/<snapshot_id>/manifest.json
data/reporting/publications/<run_id>.json
data/reporting/current.json
```

Run completion — single, batch, or multi-bench — does not update the dashboard: batches no longer auto-publish on completion. Publication is triggered explicitly from the test page's 评测记录 list (单条发布 or 发布选中). The batch endpoint `POST /api/deepswe-runs/publish-batch` takes several completed run ids and publishes them as ONE accumulated snapshot, writing a per-run publication marker for each so the records list shows every selected run as published and a per-run 回撤 resolves to the shared snapshot. Publication reads each records/config input once, merges the new records with the current versioned snapshot's records (deduplicated by the deterministic record `run_id`, newest `created_at` wins — projection `radar-v3`), writes a complete, create-once snapshot directory, then atomically replaces `current.json`. Its identity includes those exact merged input bytes and the projection version, so re-publishing a run never resurfaces stale results, and `?merge=false` still opts into a replacement snapshot built only from the given runs. The manifest records every carried-forward source in `source_job_ids`, and a snapshot counts as "source deleted" only once any of those sources is gone from the test page. `delete_snapshot` scans every `publications/*.json` marker and drops any still pointing at the removed snapshot (including per-run markers from batch publishes) — a re-published source keeps its newer mapping. The per-run publication marker is written only after activation, so it cannot claim that an inactive snapshot was published. Dashboard requests pin `current.json` once and return the summary, radar projections, and run rows in one response, so a page cannot mix two snapshot versions.

`data/aggregate/radar.json` and `data/raw/runs` remain read-only legacy fallbacks until the first versioned snapshot is published. New run results never use those paths.

## Extension Points

- Add benchmark backends by implementing `BenchmarkBackend` in `src/iqradar/benchmarks/` and registering it in `registry.py`.
- Add reporting projections inside `iqradar.publication`; do not invoke projections from the run executor.
- Add dashboard views inside the dashboard frontend feature; do not import deepswe state.
- Bump `PROJECTION_VERSION` whenever publication semantics change so the snapshot identity remains reproducible.

## Local Security Boundary

IQRadar is a local operational tool. The server binds only to loopback, API requests from non-loopback clients are rejected, and cross-origin writes are denied. Private run directories, SQLite files, and reporting snapshots are created with owner-only permissions. Add authentication and request rate limiting before changing this deployment boundary.