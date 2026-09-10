# Dashboard Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable local dataset management, model configuration, and visual test execution from the IQRadar dashboard.

**Architecture:** Add validated local API routes for datasets and run requests, retaining the project envelope format. The Vue dashboard consumes these routes through typed client functions and presents a compact workbench above the existing telemetry visualizations.

**Tech Stack:** Flask, Pydantic, Vue 3, TypeScript, Vitest, Playwright.

## Global Constraints

- Keep API responses in the existing `success` / `data` / `error` envelope.
- Do not accept or persist provider credentials through the browser.
- Validate dataset records and test request limits at the API boundary.
- Preserve the existing read-only dashboard filters and visualizations.

---

### Task 1: Dataset and test API

**Files:**
- Modify: `src/iqradar/api/app.py`
- Modify: `src/iqradar/api/routes.py`
- Test: `tests/integration/test_api.py`

**Interfaces:**
- Produces `GET/POST /api/datasets`, `GET /api/datasets/<name>/export`, and `POST /api/tests`.
- Each route returns the project API envelope and rejects malformed input with HTTP 400.

- [ ] **Step 1: Write failing integration cases** covering import/list/export and an accepted test request.
- [ ] **Step 2: Run** `pytest tests/integration/test_api.py -q` and confirm the routes are missing.
- [ ] **Step 3: Implement** data-path-backed dataset storage, request validation, and a deterministic local test result.
- [ ] **Step 4: Run** `pytest tests/integration/test_api.py -q` and confirm the cases pass.

### Task 2: Typed browser API and workbench UI

**Files:**
- Modify: `web/src/types/radar.ts`
- Modify: `web/src/api/client.ts`
- Modify: `web/src/App.vue`
- Modify: `web/src/style.css`
- Test: `web/src/App.test.ts`

**Interfaces:**
- Consumes `Dataset`, `TestRequest`, and `TestExecution` API contracts.
- Produces controls for dataset import/export, parameter selection, run execution, and accuracy/result display.

- [ ] **Step 1: Write failing component expectations** for the workbench controls and a displayed execution result.
- [ ] **Step 2: Run** `npm test -- App.test.ts` and confirm the UI expectation fails.
- [ ] **Step 3: Implement** typed client methods and a responsive workbench with loading/error states.
- [ ] **Step 4: Run** `npm test -- App.test.ts` and confirm it passes.

### Task 3: End-to-end and build verification

**Files:**
- Modify: `web/tests/e2e/dashboard.spec.ts`
- Modify: `docs/testing/llm-iq-radar.tdd.md`

- [ ] **Step 1: Add an E2E assertion** for the visible workbench and start-test control.
- [ ] **Step 2: Run** `npm run test:e2e` and confirm the new test initially fails if the UI is absent.
- [ ] **Step 3: Run** `npm test`, `npm run build`, and the Python integration suite with project dependencies installed.
- [ ] **Step 4: Record** RED/GREEN evidence and any environment limitation in `docs/testing/llm-iq-radar.tdd.md`.
