# Local LLM IQ Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, self-hosted LLM IQ radar that runs DeepSWE tasks through open-source DRadar-compatible tooling with the user's own model URL and key, exports IQ/speed/cost/quota JSON, and visualizes "IQ degradation radar" plus "quota radar" charts.

**Architecture:** The project is a local-first benchmark pipeline: a CLI runner prepares a DeepSWE task matrix, invokes DRadar/Pier-compatible execution, ingests verifier artifacts into a stable JSON schema, aggregates metrics, and serves them through a read-only Flask API. A Vue 3 + ECharts dashboard reads the local API and renders radar, trend, and table views without storing API keys in frontend or output files.

**Tech Stack:** Python 3.11+, uv, Typer, Pydantic, Flask, pytest, Docker, Vue 3, Vite, TypeScript, ECharts, Playwright.

## Global Constraints

- Use the user's own OpenAI-compatible `base_url`, `api_key`, and `model` for model calls.
- API keys must be read only from environment variables or a local `.env` file that is never committed.
- Do not write API keys, bearer tokens, auth headers, or full `.env` contents to JSON exports, logs, patches, frontend bundles, or test fixtures.
- Use open-source `SecurityMind/dradar` locally as the benchmark execution baseline where practical.
- Use DeepSWE tasks from `datacurve-ai/deep-swe`, especially the 113 tasks under `tasks/`.
- Preserve raw run artifacts, verifier outputs, and normalized JSON records so results can be audited.
- IQ metric uses the public CodexRadar-style convention: `iq = pass_rate_percent * 1.5`, capped to `0..150`.
- Every aggregate IQ value must show sample size and Wilson confidence interval to avoid misleading small-sample conclusions.
- MVP supports subset runs through `--limit`, `--task-list`, `--language`, and `--sample-seed`; full 113-task runs are optional and budget-gated.
- Run orchestration must support timeout, retry, resume, concurrency limit, and `--max-cost-usd`.
- WebUI is read-only for MVP; running expensive benchmarks stays in CLI.
- External network actions are read/clone/install only unless explicitly approved by the user.

---

## Source Notes

- Reference sites: `https://deng.codexradar.com/` and `https://codexradar.com/`.
- DRadar repository: `https://github.com/SecurityMind/dradar`.
- DeepSWE repository: `https://github.com/datacurve-ai/deep-swe`.
- DeepSWE site: `https://deepswe.datacurve.ai/`.
- DeepSWE paper: `https://arxiv.org/abs/2607.07946`.
- The arXiv abstract states DeepSWE has 113 original long-horizon software engineering tasks across 91 active open-source repositories and five languages, with hand-written verifiers and released trajectories.
- Treat public reference sites as metric/UI inspiration only. Do not scrape or depend on private service endpoints for local scoring.

## Product Scope

### In Scope

- Local CLI for configuring providers, checking dependencies, running DeepSWE task subsets, aggregating outputs, and launching the dashboard.
- Provider adapter for OpenAI-compatible chat/completions APIs.
- Reasoning-effort matrix: `low`, `medium`, `high`, `max`.
- Cost model based on local price config per 1M input/output/cached tokens.
- JSONL raw run export and aggregate JSON export.
- Flask API that reads local aggregate files.
- Vue dashboard with:
  - model and effort filters,
  - IQ radar,
  - quota radar,
  - degradation/drift chart,
  - run table,
  - raw artifact links where safe.
- Tests for config validation, metrics, redaction, ingestion, API, and chart rendering.

### Out of Scope for MVP

- Hosted leaderboard service.
- Multi-user authentication.
- WebUI-triggered benchmark execution.
- Reimplementing CodexRadar private backend.
- Exact visual clone of proprietary assets or private API responses.
- Guaranteed support for non-OpenAI-compatible providers without an adapter.

## User Workflows

### Workflow 1: Configure a Model

1. User creates `.env`:

```bash
LLM_BASE_URL=https://example.com/v1
LLM_API_KEY=replace-with-local-secret
LLM_MODEL=my-reasoning-model
```

2. User creates or edits `configs/models.yaml`:

```yaml
models:
  - id: my-model
    display_name: My Reasoning Model
    provider: openai-compatible
    env:
      base_url: LLM_BASE_URL
      api_key: LLM_API_KEY
      model: LLM_MODEL
    effort:
      supported: true
      parameter_path: reasoning_effort
      values:
        low: low
        medium: medium
        high: high
        max: max
```

3. User validates setup:

```bash
iqradar doctor
```

Expected result:

```text
OK python>=3.11
OK docker
OK deep-swe tasks path
OK model config my-model
OK API connectivity
OK secret redaction rules
```

### Workflow 2: Run a Budgeted Sample

```bash
iqradar run \
  --benchmark deep-swe \
  --model my-model \
  --efforts low,high,max \
  --limit 5 \
  --sample-seed 42 \
  --concurrency 1 \
  --timeout-sec 7200 \
  --max-cost-usd 10
```

Expected outputs:

```text
data/raw/runs/2026-08-03T120000Z_deep-swe_my-model_low.jsonl
data/raw/runs/2026-08-03T120000Z_deep-swe_my-model_high.jsonl
data/raw/runs/2026-08-03T120000Z_deep-swe_my-model_max.jsonl
data/artifacts/2026-08-03T120000Z/
```

### Workflow 3: Aggregate and Visualize

```bash
iqradar aggregate data/raw/runs --out data/aggregate/radar.json
iqradar serve --host 127.0.0.1 --port 8080
```

Expected dashboard URL:

```text
http://127.0.0.1:8080
```

## Repository Layout

Create this layout in the implementation phase:

```text
configs/
  benchmark.yaml
  models.example.yaml
  prices.example.yaml
data/
  .gitkeep
  aggregate/.gitkeep
  raw/runs/.gitkeep
docs/
  superpowers/plans/2026-08-03-llm-iq-radar.md
src/
  iqradar/
    __init__.py
    cli.py
    api/
      __init__.py
      app.py
      routes.py
    config/
      __init__.py
      loader.py
      schema.py
    ingest/
      __init__.py
      artifacts.py
      normalize.py
      redact.py
    metrics/
      __init__.py
      aggregate.py
      confidence.py
      cost.py
      quota.py
    provider/
      __init__.py
      openai_compatible.py
    runner/
      __init__.py
      deepswe.py
      dradar_adapter.py
      matrix.py
      resume.py
    schemas/
      __init__.py
      run_record.py
      summary.py
tests/
  fixtures/
    artifacts/
    sample_runs.jsonl
  unit/
  integration/
web/
  package.json
  index.html
  src/
    App.vue
    api/client.ts
    charts/IqRadar.vue
    charts/QuotaRadar.vue
    charts/DegradationTrend.vue
    components/RunTable.vue
    types/radar.ts
```

## Data Contracts

### Raw Run Record

Each benchmark attempt writes one JSON object per line.

```json
{
  "schema_version": "1.0",
  "run_id": "2026-08-03T120000Z_deepswe_my-model_high_0001",
  "benchmark": {
    "name": "deep-swe",
    "version": "local-git-sha-or-release",
    "task_id": "happy-dom__abort-pending-body-reads",
    "repo": "capricorn86/happy-dom",
    "language": "typescript",
    "task_path": "vendor/deep-swe/tasks/happy-dom__abort-pending-body-reads"
  },
  "model": {
    "provider": "openai-compatible",
    "base_url_hash": "sha256:base-url-only-hash",
    "name": "my-model",
    "effort_requested": "high",
    "effort_effective": true
  },
  "result": {
    "status": "passed",
    "verifier_passed": true,
    "exit_code": 0,
    "error_type": null,
    "error_message_redacted": null
  },
  "usage": {
    "input_tokens": 120000,
    "output_tokens": 45000,
    "cached_input_tokens": 0,
    "agent_steps": 82,
    "wall_time_sec": 3660,
    "usage_estimated": false
  },
  "cost": {
    "currency": "USD",
    "input_cost": 0.24,
    "cached_input_cost": 0.0,
    "output_cost": 0.9,
    "total_cost": 1.14
  },
  "artifacts": {
    "patch_path": "data/artifacts/2026-08-03T120000Z/task/patch.diff",
    "log_path": "data/artifacts/2026-08-03T120000Z/task/run.log",
    "verifier_path": "data/artifacts/2026-08-03T120000Z/task/reward.json"
  },
  "created_at": "2026-08-03T12:00:00Z"
}
```

Allowed `result.status` values:

```text
passed
failed
timeout
runner_error
verifier_error
budget_stopped
skipped
```

Allowed `model.effort_requested` values:

```text
low
medium
high
max
```

### Aggregate Summary

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-08-03T13:00:00Z",
  "benchmark": {
    "name": "deep-swe",
    "version": "local-git-sha-or-release"
  },
  "summaries": [
    {
      "model": "my-model",
      "effort": "high",
      "tasks_total": 30,
      "tasks_passed": 18,
      "tasks_failed": 12,
      "pass_rate": 0.6,
      "pass_rate_percent": 60.0,
      "iq": 90.0,
      "avg_cost_usd": 1.14,
      "total_cost_usd": 34.2,
      "avg_wall_time_sec": 3660.0,
      "tasks_per_hour": 0.98,
      "avg_input_tokens": 120000.0,
      "avg_output_tokens": 45000.0,
      "output_tokens_per_min": 737.7,
      "avg_agent_steps": 82.0,
      "cost_per_pass_usd": 1.9,
      "cost_per_iq_point_usd": 0.38,
      "quota_percent_per_task": 5.7,
      "estimated_tasks_per_week": 17,
      "estimated_passes_per_week": 10,
      "confidence": {
        "method": "wilson",
        "level": 0.95,
        "lower": 0.42,
        "upper": 0.75
      }
    }
  ]
}
```

### Radar API Response

```json
{
  "iq_radar": [
    {
      "model": "my-model",
      "effort": "high",
      "axes": {
        "iq": 90.0,
        "pass_rate": 60.0,
        "stability": 83.0,
        "speed": 41.0,
        "cost_efficiency": 72.0
      }
    }
  ],
  "quota_radar": [
    {
      "model": "my-model",
      "effort": "high",
      "axes": {
        "quota_remaining_friendliness": 94.3,
        "tasks_per_week": 68.0,
        "passes_per_week": 60.0,
        "cost_per_pass_efficiency": 74.0,
        "token_efficiency": 62.0
      }
    }
  ]
}
```

Normalize radar axes to `0..100` for chart readability. Keep original metrics in the summary table.

## Metric Definitions

### Pass Rate

```text
pass_rate = tasks_passed / tasks_total
pass_rate_percent = pass_rate * 100
```

Only `result.status = passed` counts as passed. `timeout`, `runner_error`, and `verifier_error` count as not passed unless the run is explicitly marked `skipped`.

### IQ

```text
iq = min(150, max(0, pass_rate_percent * 1.5))
```

Examples:

```text
pass_rate = 0.40 -> IQ = 60
pass_rate = 0.60 -> IQ = 90
pass_rate = 1.00 -> IQ = 150
```

### Wilson Confidence Interval

Use a 95% Wilson interval for pass rate:

```text
z = 1.96
center = (p + z^2 / (2n)) / (1 + z^2 / n)
margin = z * sqrt((p(1-p) + z^2 / (4n)) / n) / (1 + z^2 / n)
lower = max(0, center - margin)
upper = min(1, center + margin)
```

### Cost

```text
input_cost = input_tokens / 1_000_000 * input_usd_per_1m
cached_input_cost = cached_input_tokens / 1_000_000 * cached_input_usd_per_1m
output_cost = output_tokens / 1_000_000 * output_usd_per_1m
total_cost = input_cost + cached_input_cost + output_cost
cost_per_pass = total_cost / tasks_passed
cost_per_iq_point = total_cost / iq
```

When `tasks_passed = 0`, set `cost_per_pass_usd = null`. When `iq = 0`, set `cost_per_iq_point_usd = null`.

### Speed

```text
avg_wall_time_sec = sum(wall_time_sec) / tasks_total
tasks_per_hour = tasks_total / (sum(wall_time_sec) / 3600)
output_tokens_per_min = sum(output_tokens) / (sum(wall_time_sec) / 60)
agent_steps_per_hour = sum(agent_steps) / (sum(wall_time_sec) / 3600)
```

### Quota

Initial quota model uses a user-defined weekly budget:

```yaml
quota:
  weekly_budget_usd: 20.0
  weekly_output_token_budget: null
```

Cost-based quota:

```text
quota_percent_per_task = avg_cost_usd / weekly_budget_usd * 100
estimated_tasks_per_week = floor(weekly_budget_usd / avg_cost_usd)
estimated_passes_per_week = floor(estimated_tasks_per_week * pass_rate)
```

Token-based quota is optional:

```text
output_quota_percent_per_task = avg_output_tokens / weekly_output_token_budget * 100
```

## Configuration Files

### `configs/benchmark.yaml`

```yaml
benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: vendor/deep-swe
    tasks_path: vendor/deep-swe/tasks
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: data/artifacts
```

### `configs/prices.example.yaml`

```yaml
prices:
  my-model:
    currency: USD
    input_usd_per_1m: 2.0
    cached_input_usd_per_1m: 0.0
    output_usd_per_1m: 20.0
quota:
  weekly_budget_usd: 20.0
  weekly_output_token_budget: null
```

### `.env.example`

```bash
LLM_BASE_URL=https://example.com/v1
LLM_API_KEY=replace-me
LLM_MODEL=my-reasoning-model
```

## CLI Specification

### `iqradar doctor`

Responsibilities:

- Verify Python version.
- Verify Docker availability.
- Verify configured DeepSWE local path or report clone command.
- Verify model config exists.
- Verify required environment variables exist.
- Make a minimal model API call when `--skip-api-check` is not set.
- Confirm secret redaction scanner catches a synthetic token.

### `iqradar run`

```bash
iqradar run \
  --benchmark deep-swe \
  --model my-model \
  --efforts low,medium,high,max \
  --limit 5 \
  --sample-seed 42 \
  --concurrency 1 \
  --timeout-sec 7200 \
  --max-cost-usd 10 \
  --resume
```

Responsibilities:

- Build a task matrix from DeepSWE tasks.
- Expand model and effort combinations.
- Skip completed records when `--resume` is set.
- Stop before launching the next task when estimated cost exceeds `--max-cost-usd`.
- Store every attempt as a raw JSONL record.
- Store patch/log/verifier artifacts under `data/artifacts/<run-group>/<task-id>/`.

### `iqradar aggregate`

```bash
iqradar aggregate data/raw/runs --out data/aggregate/radar.json
```

Responsibilities:

- Load JSONL files.
- Validate records against Pydantic schema.
- Group by benchmark, model, and effort.
- Compute pass rate, IQ, speed, cost, quota, and confidence metrics.
- Emit aggregate JSON for Flask and WebUI.

### `iqradar serve`

```bash
iqradar serve --host 127.0.0.1 --port 8080
```

Responsibilities:

- Serve Flask API.
- Serve built Vue static assets when present.
- Return clear error JSON when `data/aggregate/radar.json` is missing.

## Flask API Specification

### `GET /api/health`

```json
{
  "ok": true,
  "data_path": "data/aggregate/radar.json"
}
```

### `GET /api/summary`

Returns the aggregate summary JSON.

### `GET /api/radar/iq`

Returns normalized IQ radar series.

### `GET /api/radar/quota`

Returns normalized quota radar series.

### `GET /api/runs`

Query parameters:

```text
model=my-model
effort=high
status=passed
limit=100
```

Returns redacted raw run records.

## WebUI Specification

### Layout

- Top toolbar: benchmark selector, model selector, effort selector, refresh button.
- KPI strip: IQ, pass rate, sample size, total cost, average task time.
- Left chart: "降智雷达" comparing efforts for the selected model.
- Right chart: "额度雷达" comparing quota friendliness and cost efficiency.
- Bottom left: trend line for IQ, pass rate, and cost over run groups.
- Bottom right: task result table with status, cost, time, tokens, artifact links.

### Chart Semantics

IQ radar axes:

```text
IQ
Pass Rate
Stability
Speed
Cost Efficiency
```

Quota radar axes:

```text
Quota Friendliness
Tasks Per Week
Passes Per Week
Cost Per Pass Efficiency
Token Efficiency
```

Degradation trend:

```text
x-axis: run_group_created_at
y-axis: IQ / pass_rate_percent / avg_cost_usd
series: model + effort
```

### Visual Direction

- Use a dense operational dashboard, not a landing page.
- Use a dark neutral background with restrained accent colors for status and effort differences.
- Keep chart panels readable on 1366x768 desktop and 390x844 mobile.
- Use ECharts built-in radar and line charts.
- Never render secrets, full prompts, or full logs in the browser.

## Implementation Tasks

### Task 1: Project Scaffold and Config Contracts

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `configs/benchmark.yaml`
- Create: `configs/models.example.yaml`
- Create: `configs/prices.example.yaml`
- Create: `src/iqradar/__init__.py`
- Create: `src/iqradar/config/schema.py`
- Create: `src/iqradar/config/loader.py`
- Test: `tests/unit/test_config_loader.py`

**Interfaces:**
- Produces: `load_model_config(path: Path) -> ModelConfigSet`
- Produces: `load_price_config(path: Path) -> PriceConfig`
- Produces: `load_benchmark_config(path: Path) -> BenchmarkConfigSet`

- [ ] **Step 1: Write failing config loader tests**

Create tests that load YAML fixtures, validate required fields, and assert missing env variable names are reported without exposing values.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/unit/test_config_loader.py -v
```

Expected: fails because config package is not implemented.

- [ ] **Step 3: Implement Pydantic config schemas and YAML loader**

Implement strict models for benchmarks, models, effort mapping, prices, and quota. Reject unknown effort values outside `low`, `medium`, `high`, `max`.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/unit/test_config_loader.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore .env.example configs src/iqradar/config tests/unit/test_config_loader.py
git commit -m "feat: add iqradar config contracts"
```

### Task 2: Run Record Schema and Secret Redaction

**Files:**
- Create: `src/iqradar/schemas/run_record.py`
- Create: `src/iqradar/ingest/redact.py`
- Test: `tests/unit/test_run_record_schema.py`
- Test: `tests/unit/test_redaction.py`

**Interfaces:**
- Produces: `RunRecord`
- Produces: `redact_secret_text(text: str, known_secret_values: Sequence[str] = ()) -> str`
- Produces: `assert_no_secrets(text: str, known_secret_values: Sequence[str] = ()) -> None`

- [ ] **Step 1: Write failing schema and redaction tests**

Cover valid records, invalid statuses, invalid efforts, `sk-` token redaction, `Bearer` token redaction, and known secret value redaction.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/unit/test_run_record_schema.py tests/unit/test_redaction.py -v
```

Expected: fails because schema and redaction functions are not implemented.

- [ ] **Step 3: Implement schema and redaction**

Use Pydantic for record validation. Use conservative regexes for common token forms and exact replacement for known secret values.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/unit/test_run_record_schema.py tests/unit/test_redaction.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/iqradar/schemas src/iqradar/ingest/redact.py tests/unit/test_run_record_schema.py tests/unit/test_redaction.py
git commit -m "feat: add run schema and secret redaction"
```

### Task 3: Metrics Engine

**Files:**
- Create: `src/iqradar/metrics/confidence.py`
- Create: `src/iqradar/metrics/cost.py`
- Create: `src/iqradar/metrics/quota.py`
- Create: `src/iqradar/metrics/aggregate.py`
- Create: `src/iqradar/schemas/summary.py`
- Test: `tests/unit/test_metrics.py`

**Interfaces:**
- Produces: `wilson_interval(passed: int, total: int, z: float = 1.96) -> ConfidenceInterval`
- Produces: `calculate_cost(input_tokens: int, output_tokens: int, cached_input_tokens: int, price: ModelPrice) -> CostBreakdown`
- Produces: `aggregate_runs(records: Sequence[RunRecord], prices: PriceConfig) -> AggregateSummary`

- [ ] **Step 1: Write failing metric tests**

Test IQ examples `40% -> 60`, `60% -> 90`, `100% -> 150`, zero-pass cost handling, Wilson interval bounds, and quota calculations.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/unit/test_metrics.py -v
```

Expected: fails because metrics modules are not implemented.

- [ ] **Step 3: Implement metric modules**

Implement pure functions with immutable return values. Do not mutate input records.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/unit/test_metrics.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/iqradar/metrics src/iqradar/schemas/summary.py tests/unit/test_metrics.py
git commit -m "feat: add iq radar metrics"
```

### Task 4: DeepSWE Task Discovery and Run Matrix

**Files:**
- Create: `src/iqradar/runner/deepswe.py`
- Create: `src/iqradar/runner/matrix.py`
- Test: `tests/unit/test_deepswe_discovery.py`
- Test: `tests/unit/test_run_matrix.py`

**Interfaces:**
- Produces: `discover_deepswe_tasks(tasks_path: Path) -> list[DeepSweTask]`
- Produces: `build_run_matrix(tasks: Sequence[DeepSweTask], model_ids: Sequence[str], efforts: Sequence[Effort], limit: int | None, sample_seed: int | None) -> list[RunMatrixItem]`

- [ ] **Step 1: Write failing discovery and matrix tests**

Use fixture task directories containing `task.toml`, `instruction.md`, and verifier files. Verify deterministic sampling with `sample_seed = 42`.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/unit/test_deepswe_discovery.py tests/unit/test_run_matrix.py -v
```

Expected: fails because discovery and matrix modules are not implemented.

- [ ] **Step 3: Implement task discovery and run matrix**

Parse `task.toml` with Python TOML support. Infer task id from directory name and preserve repo/language metadata when available.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/unit/test_deepswe_discovery.py tests/unit/test_run_matrix.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/iqradar/runner/deepswe.py src/iqradar/runner/matrix.py tests/unit/test_deepswe_discovery.py tests/unit/test_run_matrix.py
git commit -m "feat: discover deepswe tasks"
```

### Task 5: DRadar/Pier Runner Adapter

**Files:**
- Create: `src/iqradar/runner/dradar_adapter.py`
- Create: `src/iqradar/runner/resume.py`
- Test: `tests/unit/test_dradar_adapter.py`
- Test: `tests/integration/test_mock_runner_ingest.py`

**Interfaces:**
- Produces: `run_matrix_item(item: RunMatrixItem, model: ModelConfig, benchmark: BenchmarkConfig, limits: RunLimits) -> RunRecord`
- Produces: `completed_run_keys(raw_run_paths: Sequence[Path]) -> set[str]`

- [ ] **Step 1: Inspect DRadar and DeepSWE local CLI commands**

```bash
git clone https://github.com/SecurityMind/dradar.git vendor/dradar
git clone https://github.com/datacurve-ai/deep-swe.git vendor/deep-swe
```

Then inspect their READMEs and CLI help locally before wiring commands:

```bash
find vendor/dradar -maxdepth 3 -type f | sort | sed -n '1,120p'
find vendor/deep-swe -maxdepth 3 -type f | sort | sed -n '1,120p'
```

- [ ] **Step 2: Write failing adapter tests with a fake subprocess**

Mock subprocess execution and assert timeout, exit code, artifact paths, usage parsing, and secret redaction.

- [ ] **Step 3: Run tests to verify failure**

```bash
uv run pytest tests/unit/test_dradar_adapter.py tests/integration/test_mock_runner_ingest.py -v
```

Expected: fails because adapter is not implemented.

- [ ] **Step 4: Implement adapter**

Wrap the actual DRadar/Pier command discovered in Step 1. Pass model credentials through environment variables only. Capture stdout/stderr into redacted logs. Convert verifier output to `RunRecord`.

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/unit/test_dradar_adapter.py tests/integration/test_mock_runner_ingest.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add vendor/.gitignore src/iqradar/runner/dradar_adapter.py src/iqradar/runner/resume.py tests/unit/test_dradar_adapter.py tests/integration/test_mock_runner_ingest.py
git commit -m "feat: add local benchmark runner adapter"
```

### Task 6: CLI Commands

**Files:**
- Create: `src/iqradar/cli.py`
- Test: `tests/integration/test_cli.py`

**Interfaces:**
- Produces executable commands: `iqradar doctor`, `iqradar run`, `iqradar aggregate`, `iqradar serve`.

- [ ] **Step 1: Write failing CLI tests**

Use Typer's test runner. Verify exit codes, missing config messages, aggregation output, and no secret leakage in command output.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/integration/test_cli.py -v
```

Expected: fails because CLI is not implemented.

- [ ] **Step 3: Implement CLI**

Wire config loading, task discovery, run matrix, adapter execution, aggregation, and Flask startup.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/integration/test_cli.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/iqradar/cli.py tests/integration/test_cli.py
git commit -m "feat: add iqradar cli"
```

### Task 7: Flask API

**Files:**
- Create: `src/iqradar/api/app.py`
- Create: `src/iqradar/api/routes.py`
- Test: `tests/integration/test_api.py`

**Interfaces:**
- Produces: `create_app(data_path: Path = Path("data/aggregate/radar.json")) -> Flask`
- Produces endpoints: `/api/health`, `/api/summary`, `/api/radar/iq`, `/api/radar/quota`, `/api/runs`.

- [ ] **Step 1: Write failing API tests**

Use Flask test client with fixture aggregate JSON and raw JSONL records.

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/integration/test_api.py -v
```

Expected: fails because API is not implemented.

- [ ] **Step 3: Implement Flask app and routes**

Return consistent response envelopes:

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/integration/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/iqradar/api tests/integration/test_api.py
git commit -m "feat: add local radar api"
```

### Task 8: Vue Dashboard

**Files:**
- Create: `web/package.json`
- Create: `web/index.html`
- Create: `web/src/App.vue`
- Create: `web/src/api/client.ts`
- Create: `web/src/types/radar.ts`
- Create: `web/src/charts/IqRadar.vue`
- Create: `web/src/charts/QuotaRadar.vue`
- Create: `web/src/charts/DegradationTrend.vue`
- Create: `web/src/components/RunTable.vue`
- Test: `web/src/App.test.ts`
- Test: `tests/e2e/dashboard.spec.ts`

**Interfaces:**
- Consumes: Flask endpoints from Task 7.
- Produces: responsive dashboard at `/`.

- [ ] **Step 1: Write failing component and E2E tests**

Assert the dashboard renders KPI values, chart containers, model filters, effort filters, and result table from fixture API responses.

- [ ] **Step 2: Run tests to verify failure**

```bash
cd web
npm test
npm run test:e2e
```

Expected: fails because WebUI is not implemented.

- [ ] **Step 3: Implement Vue dashboard**

Use ECharts radar and line charts. Keep dimensions stable with CSS grid and explicit min heights. Do not display prompts, raw logs, or secrets.

- [ ] **Step 4: Run tests to verify pass**

```bash
cd web
npm test
npm run test:e2e
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add web tests/e2e/dashboard.spec.ts
git commit -m "feat: add iq radar dashboard"
```

### Task 9: Verification, Docs, and Release Checklist

**Files:**
- Create: `README.md`
- Create: `docs/runbook.md`
- Create: `docs/data-schema.md`
- Modify: `docs/superpowers/plans/2026-08-03-llm-iq-radar.md`

**Interfaces:**
- Produces user docs for setup, running 5/30/113 tasks, interpreting IQ, and troubleshooting.

- [ ] **Step 1: Run the full local verification suite**

```bash
uv run pytest -v
cd web && npm test && npm run test:e2e
```

Expected: all tests pass.

- [ ] **Step 2: Run secret scan against outputs**

```bash
rg -n "sk-|Bearer |LLM_API_KEY|api_key|replace-with-local-secret" data src web tests README.md docs || true
```

Expected: only `.env.example`, docs examples, and test redaction fixtures contain synthetic placeholder strings.

- [ ] **Step 3: Run a mock benchmark smoke test**

```bash
iqradar run --benchmark deep-swe --model fixture-model --efforts low --limit 1 --max-cost-usd 0.01
iqradar aggregate data/raw/runs --out data/aggregate/radar.json
iqradar serve --host 127.0.0.1 --port 8080
```

Expected: the dashboard loads, API health is OK, and sample metrics match fixture expectations.

- [ ] **Step 4: Document operational workflow**

Write setup instructions, cost warnings, DeepSWE clone instructions, model config examples, and interpretation notes.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/runbook.md docs/data-schema.md docs/superpowers/plans/2026-08-03-llm-iq-radar.md
git commit -m "docs: add iqradar runbook"
```

## Acceptance Criteria

- `iqradar doctor` reports dependency, config, API connectivity, and redaction status.
- `iqradar run --limit 1` can produce one valid raw JSONL record using a mock or real configured provider.
- `iqradar aggregate` produces `data/aggregate/radar.json` matching the aggregate schema.
- IQ uses `pass_rate_percent * 1.5` and includes `tasks_total`, `tasks_passed`, and Wilson confidence bounds.
- Cost metrics are calculated from local `configs/prices.yaml`.
- Budget guard stops launching new tasks before exceeding `--max-cost-usd`.
- JSON/log outputs contain no real API keys or bearer tokens.
- Flask API returns consistent `success/data/error` envelopes.
- Vue dashboard renders IQ radar, quota radar, degradation trend, and run table from local JSON.
- Unit, integration, and E2E tests pass.
- Documentation explains how to configure a private model, run 5/30/113 task suites, interpret IQ, and handle cost limits.

## Open Engineering Decisions

- Exact DRadar/Pier command wiring must be confirmed after cloning `SecurityMind/dradar` locally because the public reference service and local repository may expose different entry points.
- If a provider does not return token usage, use a tokenizer estimate and set `usage_estimated = true`.
- EvalScope WebUI integration is a second-phase exporter unless the user prefers it over the Vue + Flask panel before implementation starts.
- SQLite indexing is optional and should be added only if JSONL scan latency becomes noticeable.

## Recommended First Build Order

1. Implement Tasks 1-3 first to lock data contracts and metrics.
2. Implement Task 7 with fixture data before expensive benchmark execution.
3. Implement Task 8 against fixture API responses so chart UX is testable early.
4. Implement Tasks 4-6 to connect real DeepSWE/DRadar execution.
5. Finish Task 9 hardening before any full 113-task run.
