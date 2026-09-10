# Benchmark Adapter Refactor TDD Evidence

## Source

User request: standardize and modularize the dashboard test feature so future benchmarks can be plugged in without coupling benchmark implementations together. Current active benchmark remains DeepSWE.

## User Journeys

- As an operator, I can run the current DeepSWE test flow through the same UI and CLI behavior as before.
- As a developer, I can add a new benchmark by implementing a benchmark adapter and registering it, without changing the UI job orchestration.
- As a developer, I can configure a benchmark `type` independently from the benchmark config key.

## RED/GREEN Evidence

| # | Guarantee | Test | Type | Evidence |
|---|---|---|---|---|
| 1 | Default benchmark registry exposes DeepSWE as the initial adapter. | `tests/unit/test_benchmark_adapters.py` | Unit | RED: missing `iqradar.benchmarks`; GREEN: included in full pytest pass. |
| 2 | UI job orchestration uses the selected adapter and does not call DeepSWE-specific discovery directly. | `tests/integration/test_api.py::test_ui_job_uses_pluggable_benchmark_adapter` | Integration | RED: missing adapter interface/job support; GREEN: targeted test passed. |
| 3 | Matrix items carry `benchmark_name` and generate benchmark-prefixed run IDs. | `tests/unit/test_run_matrix.py` | Unit | GREEN: matrix test expects `deep-swe__task-a__model-small__low`. |
| 4 | CLI run path uses the benchmark adapter registry. | `tests/integration/test_cli.py::test_run_command_discovers_task_and_writes_raw_record` | Integration | GREEN: fake adapter registry produces a standard raw record. |
| 5 | Config key remains the benchmark identity when `type` selects a different adapter. | `tests/integration/test_api.py::test_ui_job_uses_config_key_as_benchmark_identity_when_type_selects_adapter` | Integration | GREEN: raw record uses `toy-suite__task-a__model-a__high`. |
| 6 | CLI uses the adapter default command template when no override is supplied. | `tests/integration/test_cli.py::test_run_command_uses_adapter_default_command_template` | Integration | GREEN: fake adapter observed its own default command template. |
| 7 | Generic benchmark tasks do not require DeepSWE TOML/instruction/verifier paths. | `tests/unit/test_benchmark_adapters.py::test_benchmark_task_does_not_require_deepswe_file_layout` | Unit | GREEN: minimal `BenchmarkTask` can be constructed from id and path. |
| 8 | Adapter task exceptions become failed task records rather than failing the whole job. | `tests/integration/test_api.py::test_ui_job_records_adapter_exception_as_failed_task` | Integration | GREEN: job completes with one failed task and redacted `adapter_error`. |

## Validation

- `.venv/bin/python -m pytest -q` -> `78 passed`
- `npm test` -> `2 passed`, `5 tests`
- `npm run build` -> Vite production build succeeded
- `npm run test:e2e` -> `1 passed`

No git checkpoint was created because this workspace is not inside a Git repository.
