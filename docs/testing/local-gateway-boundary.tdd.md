# Local Gateway Boundary TDD Evidence

Source plan: responsibilities derived from the OpenAI-compatible local gateway split.

| # | Guarantee | Test target | Result |
|---|---|---|---|
| 1 | Worker passes the configured gateway URL and optional auth/routing settings directly to Pier without an IQ Radar credential proxy | `tests/unit/test_dradar_adapter.py` | PASS |
| 2 | IQ Radar API accepts datasets and model aliases, then creates a pollable gateway-backed benchmark job | `tests/integration/test_api.py` | PASS |
| 3 | The dashboard exposes dataset/model selection, test progress, and radar views | `web/src/App.test.ts`, `web/tests/e2e/dashboard.spec.ts` | PASS |

RED: API tests failed while `/api/models` and `/api/test-runs` were absent; the UI test failed while its model client was absent.

GREEN: `uv run python -m pytest -q` passed 67 tests; `npm test`, `npm run build`, and `npm run test:e2e` passed.
