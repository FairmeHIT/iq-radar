# DeepSWE Pipeline Diagnostics TDD Evidence

## Root Cause

The two-task smoke dataset is valid. All 113 local DeepSWE tasks contain the
required task metadata, instruction, environment, and verifier files. The
failure came from the worker pipeline:

1. The UI worker overrode the benchmark's 7200-second timeout with 120 seconds.
2. Timeout stopped the direct Pier process before Docker cleanup completed,
   leaving a task container behind.
3. The long-running Flask process survived a WSL mount replacement with a
   detached working directory;
   resolving relative benchmark paths raised a filename-less
   `FileNotFoundError`.
4. The task container received a loopback gateway URL, which points to the
   container itself instead of the host local gateway.
5. Pier's real trial exception lived in nested `result.json` and was not shown
   by the UI.

## Regression Guarantees

| Guarantee | Test target |
|---|---|
| UI events expose expandable, redacted execution details and prompt previews | `tests/integration/test_api.py`, `web/src/App.test.ts`, `web/tests/e2e/dashboard.spec.ts` |
| UI and CLI anchor relative benchmark paths without process `cwd` resolution | `tests/integration/test_api.py`, `tests/integration/test_cli.py`, `tests/unit/test_dradar_adapter.py` |
| The benchmark timeout is honored and Pier receives a cleanup grace period | `tests/integration/test_api.py`, `tests/unit/test_dradar_adapter.py` |
| Docker agents map loopback gateway URLs to `host.docker.internal` | `tests/unit/test_dradar_adapter.py` |
| Reward zero, missing verifier, runner exit, and nested Pier errors remain distinct | `tests/unit/test_dradar_adapter.py` |
| Only fresh verifier and Pier result artifacts contribute status, usage, and cost | `tests/unit/test_dradar_adapter.py` |
| Gateway credentials never appear in command arguments, logs, or UI events | `tests/unit/test_dradar_adapter.py`, `tests/integration/test_api.py` |

## Verification

- Backend: 95 tests passed, 90.50% statement coverage.
- Frontend: 5 component tests passed; TypeScript check and production build passed.
- Browser: 1 Playwright end-to-end test passed, including expanded diagnostics.
- Security: production npm dependencies reported 0 vulnerabilities.
- Preflight: Pier 0.3.0 is available; the local gateway is reachable from both host and task container.
