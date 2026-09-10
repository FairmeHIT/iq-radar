# IQRadar Data Schema

## DeepSWE Run State

Each run is stored at `data/deepswe/runs/<run_id>/state.json`:

```text
run_id
status          queued | running | completed | failed
model_id
n_tasks
sample_seed
base_url        deep-swe .env OPENAI_BASE_URL (for run identity)
created_at
completed_at
error
jobs_dir        Pier job output directory
```

## Evaluation Run JSONL

`data/deepswe/runs/<run_id>/records.jsonl` contains one `RunRecord` per completed
Pier trial, produced by `iqradar.deepswe.importer`. Required top-level fields:

```text
schema_version
run_id
benchmark
model
result
usage
cost
artifacts
created_at
```

Allowed statuses:

```text
passed
failed
timeout
runner_error
verifier_error
budget_stopped
skipped
```

## Question-Level Evaluation Report

Every run can be exported as a question-level report that records the
success/failure of *each requested question*, including questions that were
requested but never executed:

- API: `GET /api/deepswe-runs/<run_id>/evaluation-report`
- Persisted copy: `data/deepswe/runs/<run_id>/evaluation-report.json`
  (regenerated on every request; the retry UI refreshes it automatically)

Report structure (`report_version` 1.0):

```text
run                     run state snapshot (model, benchmark, effort, ...)
coverage
  requested_tasks       questions asked of the runner (catalog or n_tasks)
  recorded_tasks        questions with a recorded result
  missing_tasks         requested but never recorded
  coverage_rate         recorded / requested
  status_counts         per RunStatus counter (+ not_executed)
  success_count         passed
  model_failure_count   executed but the answer/verifier did not pass
  infrastructure_error_count  gateway/harness/judge/verifier failures
  not_executed_count    requested with no recorded result
  scored_count_current_metric   pass-rate denominator under the current IQ rule
  pass_rate_current_metric      passed / scored_count_current_metric
  failure_category_counts     stable failure categories (see below)
  error_type_counts           raw adapter error_type values (failures only)
questions[]             one row per requested question (index order)
taxonomy                description of each outcome and the scoring rule
```

Stable failure categories (`failure_category`) in addition to the raw
adapter-specific `error_type`:

```text
model_wrong_answer            failed + *_match / judge_incorrect / verifier_failed
model_empty_or_unparseable    answer could not be extracted (empty_response)
gateway_error                 runner_error (HTTP/transport failures)
judge_error                   LLM judge call failed (re-runnable)
judge_unclear                 judge verdict could not be parsed
harness_error                 harness/container exception (failed + exception type)
verifier_error                verifier_error (incl. empty model response)
timeout                       run-level deadline reached before the question ran
budget_stopped                budget exhausted (reserved; no adapter emits it)
skipped                       excluded from scoring (reserved)
not_executed                  requested but no recorded result
unknown_failure               failed with no recognisable reason
```

`outcome` groups every row into `success`, `model_failure`,
`infrastructure_error`, `not_executed` or `skipped`.

Scoring note: the current IQ metric excludes `runner_error`/`verifier_error`
from the pass-rate denominator, counts `timeout` as a scored failure, and
drops `skipped`. The report keeps this rule visible via
`scored_count_current_metric` / `pass_rate_current_metric` so evaluation
reports can quote it without changing dashboard numbers.

Allowed efforts:

```text
low
medium
high
max
```

## Published Snapshot

`data/reporting/current.json` identifies the active immutable snapshot. Its
`summary.json` contains:

```text
schema_version
generated_at
summaries[]
```

Each summary is grouped by benchmark, model, and effort. Skipped records are excluded from aggregate denominators.

The snapshot also contains `runs.jsonl`. Publication switches `current.json`
only after both files are complete.

## API Envelope

All Flask endpoints return:

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

Error responses set `success` to false and put a message in `error`.
