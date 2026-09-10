# IQRadar Runbook

## Configure DeepSWE

1. Ensure the DeepSWE checkout exists at the path in `configs/benchmark.yaml`
   (default: `checkouts/deep-swe`) and has a populated `.env`. The agent runs
   in a no-network container, so use the Docker bridge gateway address (not
   `localhost`, which only works for host-side calls):
   ```bash
   OPENAI_BASE_URL=http://172.17.0.1:8080/v1
   OPENAI_API_KEY=sk-your-gateway-api-key
   ```
2. Copy `.env.example` to `.env` and set `GATEWAY_BASE_URL` (same gateway) and
   `GATEWAY_MODEL_NAME` (`gateway/deepseek-v4-flash`). The model gateway is
   the local Bifrost instance; IQRadar's own web port must not collide with it
   (use `IQ_RADAR_PORT=8081` when the gateway already occupies 8080).
3. Copy `configs/prices.example.yaml` to `configs/prices.yaml` and set
   token prices and the weekly quota budget.
4. (可选) 设置 `IQRADAR_MODEL_NAMES` in `.env` to a comma-separated list of
   gateway model names for the test page. When unset, the test page
   auto-discovers models from `{GATEWAY_BASE_URL}/models`; if the gateway is
   unreachable it falls back to `configs/models.yaml`.

## Run A Batch From The Test Page

1. Open `http://127.0.0.1:8080` and switch to the 测试 tab.
2. Pick a model, a sample count, and a sample seed, then start the run.
3. The backend spawns the installed `pier` binary against the configured tasks
   with `--agent-import-path cn_agent:CnMiniSweAgent`, `--env-file <deep-swe>/.env`,
   and `PYTHONPATH` set to the deep-swe checkout, writing job output under
   `data/deepswe/jobs/<run_id>/`.
4. Progress is read from Pier's job directory. When the run finishes, imported
   records land in `data/deepswe/runs/<run_id>/records.jsonl`.
5. Publish a specific result to the dashboard from the **评测记录** table's
   per-row `发布` button (`POST /api/deepswe-runs/<run_id>/publish`). The
   snapshot accumulates with the current dashboard (radar-v3 merge), so
   cross-benchmark models coexist. `回撤` removes that run's snapshot
   (`DELETE /api/deepswe-runs/<run_id>/publish`) and repoints `current.json`.

### Gateway-failure retest

For `api-eval` runs, upstream gateway timeouts or transient HTTP errors
(408/425/429/5xx, transport errors, empty responses, judge-side errors) can
fail individual questions without the model being wrong. The **评测记录** table
exposes a per-row `重测` button for finished `api-eval` runs that re-runs only
those gateway-failed questions (`POST /api/deepswe-runs/<run_id>/retry-gateway-failures`).
Model-wrong answers (exact/normalized match failures, judge `incorrect`/`unclear`)
are **not** re-run. If the run was already published, the dashboard snapshot is
refreshed automatically after the retry so the corrected results show up.

### Real-time logs

The test page shows a live log panel once a run exists. Pick a source:

- `模型响应 (LLM)` — the agent's latest model responses (reasoning + text),
  extracted live from the raw trajectory (`agent/mini-swe-agent.trajectory.json`).
  **Selected by default**, so a long trial never looks dead.
- `pier` — `data/deepswe/runs/<run_id>/pier.log` (Pier's own stdout, streamed live)
- `job.log` — `data/deepswe/jobs/<run_id>/job.log`
- `trial.log` — the latest trial's `trial.log` (agent run output, live)
- `agent` — the latest trial's `agent/mini-swe-agent.txt` (LLM/tool trace)
- `verifier` — the latest trial's `verifier/run.log` (grading)

The panel refreshes every 3 seconds and can auto-scroll. Tailing the same files
manually is equivalent to the deep-swe `USAGE.md` commands, e.g.
`tail -f jobs/*/*/trial.log`.

The dashboard uses each benchmark's `default_timeout_sec` (7200 seconds for the
bundled DeepSWE config) as the cap for the whole Pier run.

## Run Sizes

Each run covers `--n-tasks` sampled tasks (up to the configured total). Use the
test page's sample count and seed to control subset size and reproducibility.

## Test Diagnostics

Failure types distinguish the stage that failed:

- `pier_trial_error`: Pier completed, but a nested trial reported an agent or environment exception.
- `verifier_failed`: DeepSWE ran its verifier and returned a failing reward.
- `timeout`: the configured run timeout elapsed.

Inspect the live log panel on the test page (or
`data/deepswe/runs/<run_id>/pier.log` directly) for the full Pier output. For
agent install/runtime issues, open the trial's `agent/mini-swe-agent.txt`; for
grading issues, open `verifier/run.log`.

## Serve

```bash
uv run python -m iqradar.cli serve --host 127.0.0.1 --port 8080
```

Use `./start.sh` to build the frontend and serve. Run failures change only the
run status; the dashboard continues serving the last complete published
snapshot.

If the IQRadar server restarts mid-run, the Pier subprocess keeps running and
the run is reconciled automatically on the next status read: once Pier writes a
finalized job `result.json` (`finished_at` set) the run is marked completed,
and if the Pier process is gone without a finished result the run is marked
failed (`interrupted: server restarted mid-run`) so the single-active-run slot
is freed.

For frontend development:

```bash
cd web
npm run dev
```

## Interpreting IQ

- `pass_rate_percent = passed / total * 100`.
- `IQ = pass_rate_percent * 1.5`, capped at 150.
- Always inspect `tasks_total` and Wilson confidence bounds. Small samples are noisy.
- `cost_per_pass_usd` is null when there are zero passes.
- `usage_estimated = true` means token usage came from fallback or timeout data.
