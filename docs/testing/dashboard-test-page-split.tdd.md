# Dashboard/Test Page Split TDD Evidence

## Source

User request: separate the UI test workbench and market dashboard into two pages, with the market dashboard shown by default.

## User Journeys

- As a dashboard user, I want `/` to open on the market dashboard so I can inspect IQ, quota, trend, and run telemetry without test controls mixed in.
- As an evaluator, I want a separate test page so I can import datasets, select a model, and run tests without leaving those controls on the dashboard.

## RED/GREEN Report

| # | What is guaranteed | Test file or command | Test type | Result | Evidence |
|---|--------------------|----------------------|-----------|--------|----------|
| 1 | The default view renders the dashboard page and hides dataset import/model/start-test controls | `web/src/App.test.ts` | Component | PASS | RED: `npm test -- App.test.ts` failed because `dashboard-page` and `test-page-tab` did not exist. GREEN: same command passed, 2 tests. |
| 2 | The test workbench renders only after selecting the test page and still shows safe job progress events | `web/src/App.test.ts` | Component | PASS | GREEN: `npm test -- App.test.ts` passed, 4 tests. |
| 3 | Browser flow starts on the dashboard, then switches to the test page and runs a mocked test job | `web/tests/e2e/dashboard.spec.ts` | E2E | PASS | `npm run test:e2e -- dashboard.spec.ts` passed, 1 test. |
| 4 | The frontend still typechecks and bundles through Vite | `npm run build` | Build | PASS | Vite build completed successfully. |
| 5 | Stale dashboard failures do not leak onto the test page after switching pages | `web/src/App.test.ts` | Component | PASS | RED: stale `dashboard unavailable` was visible on the test page. GREEN: page-scoped errors and request ids hide stale errors. |
| 6 | A dashboard refresh failure after a completed test run does not overwrite the test page with an unrelated error | `web/src/App.test.ts` | Component | PASS | GREEN: completed test events remain visible and `dashboard refresh failed` is not shown on the test page. |

## Validation Commands

```bash
npm test -- App.test.ts
npm run build
npm run test:e2e -- dashboard.spec.ts
npm test
```

## Coverage And Gaps

The project currently has no configured coverage script in `web/package.json`. The relevant component, build, and E2E checks passed.

`npx vue-tsc --noEmit` was also checked. It still fails on existing project configuration issues: missing Node type definitions for Vite/Rollup, ES lib support for `Symbol.asyncDispose`, and Vitest config typing in `vite.config.ts`. The test fixture type widening reported during review was fixed by annotating the summary fixture as `AggregateSummary`.
