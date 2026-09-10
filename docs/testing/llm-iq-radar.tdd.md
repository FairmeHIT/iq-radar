# LLM IQ Radar TDD Evidence

Source plan: `docs/superpowers/plans/2026-08-03-llm-iq-radar.md`

## Evidence

| Task | RED Evidence | GREEN Evidence |
|---|---|---|
| Config contracts | `ModuleNotFoundError: No module named 'iqradar'` | `uv run pytest tests/unit/test_config_loader.py -v` passed 6 tests |
| Run schema/redaction | `ModuleNotFoundError: No module named 'iqradar.schemas'` | Task tests passed 19 tests; final suite includes these tests |
| Metrics | `ModuleNotFoundError: No module named 'iqradar.metrics'` | `uv run pytest tests/unit/test_metrics.py -v` passed 10 tests |
| DeepSWE discovery/matrix | `ModuleNotFoundError: No module named 'iqradar.runner'` | Task tests passed 6 tests |
| Runner adapter | `ModuleNotFoundError: No module named 'iqradar.runner.dradar_adapter'` | `uv run pytest tests/unit/test_dradar_adapter.py tests/integration/test_mock_runner_ingest.py -v` passed 5 tests |
| CLI | `ModuleNotFoundError: No module named 'iqradar.cli'` | `uv run pytest tests/integration/test_cli.py -v` passed 3 tests |
| Flask API | `ModuleNotFoundError: No module named 'iqradar.api'` | `uv run pytest tests/integration/test_api.py -v` passed 4 tests |
| Vue dashboard | Vite failed to resolve `./App.vue` | `npm test`, `npm run build`, and `npm run test:e2e` passed |

## Final Commands

```bash
uv run pytest -v
cd web && npm test && npm run build && npm run test:e2e
```

## Known Gaps

- Real DRadar/Pier command wiring still requires cloning and inspecting upstream repositories locally.
- `--max-cost-usd` budget stop is specified but not fully enforced in the current CLI implementation.
- Full 113-task DeepSWE execution has not been run.
# Dashboard Workbench TDD Evidence

Source journeys were derived from the requested visual dataset and testing workflow.

| # | What is guaranteed | Test file or command | Test type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | JSON datasets can be imported, listed, and exported without exposing filesystem paths | `tests/integration/test_api.py` | integration | PASS | `.venv/bin/pytest tests/integration/test_api.py -q`: 7 passed |
| 2 | Invalid dataset names and invalid test limits are rejected | `tests/integration/test_api.py` | integration | PASS | `.venv/bin/pytest tests/integration/test_api.py -q`: 7 passed |
| 3 | The dashboard offers dataset import/export and test controls | `web/tests/e2e/dashboard.spec.ts` | E2E | PASS | `npm run test:e2e`: 1 passed |
| 4 | A real benchmark submission returns a pollable job resource and never blocks the request | `tests/integration/test_api.py::test_test_run_submission_returns_a_job_resource` | integration | PASS | `.venv/bin/python -m pytest tests/integration/test_api.py -q`: 8 passed |
| 5 | The short-lived model proxy rejects requests without its per-run token | `tests/unit/test_proxy.py` | unit | PASS | `.venv/bin/python -m pytest tests/integration/test_api.py tests/unit/test_proxy.py tests/integration/test_mock_runner_ingest.py -q`: 13 passed |
| 6 | Two consecutive task timeouts are retained with durations and trigger a stop condition | `tests/unit/test_jobs.py` | unit | PASS | `.venv/bin/python -m pytest tests/unit/test_jobs.py tests/integration/test_api.py -q`: 9 passed |
| 7 | Model choices are loaded from server-side YAML without exposing environment names | `tests/integration/test_api.py::test_models_endpoint_lists_safe_configured_model_choices` | integration | PASS | `.venv/bin/python -m pytest tests/integration/test_api.py tests/unit/test_jobs.py tests/unit/test_proxy.py -q`: 13 passed |

RED: `npm test -- App.test.ts` failed because `fetchDatasets` did not exist. The asynchronous-run RED case returned HTTP 405 before `/api/test-runs` was added. GREEN: API client, dashboard workbench, asynchronous test job manager, and component mock were added. Python integration coverage is scoped to the new API behavior; the project does not have a configured coverage command.

## Safe Test Events

Source journeys were derived from the request to make active benchmark execution visible in the dashboard.

| # | What is guaranteed | Test file or command | Test type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | A submitted test job exposes its initial structured progress event through the pollable job resource | `tests/integration/test_api.py::test_test_run_submission_returns_a_job_resource` | integration | PASS | `.venv/bin/python -m pytest -q`: 69 passed |
| 2 | The dashboard displays the structured events returned while a test is active | `web/src/App.test.ts` | unit | PASS | `npm test -- --run src/App.test.ts`: 2 passed |
| 3 | Starting a test displays the completed event in the browser flow | `web/tests/e2e/dashboard.spec.ts` | E2E | PASS | `npm run test:e2e`: 1 passed |

RED: `.venv/bin/python -m pytest tests/integration/test_api.py::test_test_run_submission_returns_a_job_resource -q` failed with `KeyError: 'events'` before the job resource exposed progress events. GREEN: the full Python suite passed with 69 tests; the Vue unit test, production build, and Playwright flow passed. Events are structured, status-derived messages only; child-process output and exception details remain server-side. No coverage command is configured for this project.
