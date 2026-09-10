# Start Script TDD Evidence

## User journey

As a local IQ Radar user, I can run `./start.sh` from any directory and open
the dashboard without manually starting the Python server or building the web
application.

## RED

`uv run pytest -q tests/integration/test_start_script.py` failed because
`start.sh` did not exist.

## GREEN

`tests/integration/test_start_script.py` verifies that the executable script
has valid Bash syntax, resolves its own project directory, honors
`IQ_RADAR_PORT`, serves `/health/readiness`, and serves the dashboard root.

## Verification

- `uv run python -m pytest -q` - 69 passed
- `cd web && npm test` - 2 passed
- `cd web && npm run build` - passed
- `bash -n start.sh` - passed

The repository has no Bash coverage runner. The shell script is covered by its
black-box integration test rather than a line-coverage metric.
