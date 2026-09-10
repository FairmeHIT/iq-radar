import { expect, test } from '@playwright/test'

test('dashboard shell renders', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      json: {
        success: true,
        data: {
          snapshot_id: 'snapshot-a',
          summary: {
            schema_version: '1.0',
            generated_at: '2026-08-03T13:00:00Z',
            summaries: [{
              benchmark: { name: 'deep-swe', version: 'local' },
              model: 'model-a', effort: 'high', tasks_total: 2, tasks_scored: 2,
              infra_error_count: 0, tasks_passed: 1, tasks_failed: 1,
              pass_rate: 0.5, pass_rate_percent: 50, iq: 75, avg_cost_usd: 0.01,
              total_cost_usd: 0.02, avg_wall_time_sec: 60, tasks_per_hour: 60,
              avg_input_tokens: 1000, avg_output_tokens: 500, output_tokens_per_min: 500,
              avg_agent_steps: 3, agent_steps_per_hour: 180, cost_per_pass_usd: 0.02,
              cost_per_iq_point_usd: 0.00026, quota_percent_per_task: 0.05,
              estimated_tasks_per_week: 2000, estimated_passes_per_week: 1000,
              output_quota_percent_per_task: null,
              confidence: { method: 'wilson', level: 0.95, lower: 0.0945, upper: 0.9055 },
            }],
          },
          iq_radar: [{ model: 'model-a', effort: 'high', axes: { iq: 50, pass_rate: 50, stability: 18.9, speed: 60, cost_efficiency: 99 } }],
          quota_radar: [{ model: 'model-a', effort: 'high', axes: { quota_remaining_friendliness: 99.95, tasks_per_week: 100, passes_per_week: 100, cost_per_pass_efficiency: 98, token_efficiency: 66 } }],
          runs: [],
        },
        error: null,
      },
    })
  })
  await page.route('**/api/models', async (route) => {
    await route.fulfill({ json: { success: true, data: { source: 'gateway', error: null, models: [{ id: 'model-a', display_name: 'Model A', label: 'gateway / Model A', efforts: ['high'] }] }, error: null } })
  })
  await page.route('**/api/gateway-settings', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ json: { success: true, data: { models_base_url: '', models_api_key: '', inference_base_url: '', inference_api_key: '', updated_at: '2026-08-28T10:00:00+00:00' }, error: null } })
      return
    }
    await route.fulfill({ json: { success: true, data: { models_base_url: '', models_api_key: '', inference_base_url: '', inference_api_key: '', updated_at: '' }, error: null } })
  })
  await page.route('**/api/deepswe-runs/latest', async (route) => {
    await route.fulfill({ json: { success: true, data: null, error: null } })
  })
  await page.route('**/api/deepswe-runs', async (route) => {
    await route.fulfill({
      status: 202,
      json: {
        success: true,
        data: {
          run_id: 'run-1', status: 'queued', model_id: 'model-a', n_tasks: 3, sample_seed: 0,
          created_at: '2026-08-04T08:00:00Z', completed_at: null, error: null, jobs_dir: null,
        },
        error: null,
      },
    })
  })
  await page.route('**/api/deepswe-runs/run-1', async (route) => {
    await route.fulfill({
      json: {
        success: true,
        data: {
          run_id: 'run-1', status: 'completed', model_id: 'model-a', n_tasks: 3, sample_seed: 0,
          created_at: '2026-08-04T08:00:00Z', completed_at: '2026-08-04T08:03:00Z', error: null, jobs_dir: null,
        },
        error: null,
      },
    })
  })
  await page.route('**/api/deepswe-runs/run-1/publication', async (route) => {
    await route.fulfill({ json: { success: true, data: null, error: null } })
  })
  await page.route('**/api/deepswe-runs/run-1/logs*', async (route) => {
    const url = new URL(route.request().url())
    const source = url.searchParams.get('source')
    if (source === null) {
      await route.fulfill({
        json: {
          success: true,
          data: {
            sources: [
              { name: 'pier', path: '/logs/pier.log' },
              { name: 'trial', path: '/logs/trial.log' },
            ],
            selected: null,
            content: '',
          },
          error: null,
        },
      })
    } else {
      await route.fulfill({
        json: {
          success: true,
          data: {
            sources: [],
            selected: source,
            path: `/logs/${source}.log`,
            content: `[${source}] building image\n[${source}] running agent\n[${source}] verifier passed\n`,
          },
          error: null,
        },
      })
    }
  })
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'IQRadar' })).toBeVisible()
  await expect(page.locator('[data-testid="dashboard-page"]')).toBeVisible()
  await expect(page.locator('[data-testid="iq-radar"]')).toBeVisible()
  await expect(page.locator('[data-testid="quota-radar"]')).toBeVisible()
  await expect(page.locator('[data-testid="iq-radar"] .chart-canvas svg')).toBeVisible()
  await expect(page.locator('[data-testid="quota-radar"] .chart-canvas svg')).toBeVisible()
  await expect(page.locator('[data-testid="degradation-trend"] .chart-canvas svg')).toBeVisible()
  await expect(page.locator('.kpi')).toHaveCount(6)
  await expect(page.locator('[data-testid="test-page"]')).toHaveCount(0)
  await expect(page.locator('[data-testid="start-test"]')).toHaveCount(0)

  await page.locator('[data-testid="test-page-tab"]').click()

  await expect(page.locator('[data-testid="dashboard-page"]')).toHaveCount(0)
  await expect(page.locator('[data-testid="test-page"]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'DeepSWE 测试工作台' })).toBeVisible()
  await expect(page.locator('[data-testid="test-model"]')).toBeVisible()
  await expect(page.locator('[data-testid="start-test"]')).toBeVisible()
  await page.locator('[data-testid="start-test"]').click()
  await expect(page.locator('[data-testid="execution-result"]')).toContainText('评测完成')
  await expect(page.locator('[data-testid="run-logs"]')).toBeVisible()
  await expect(page.locator('[data-testid="log-content"]')).toContainText('building image')
  await page.locator('[data-testid="log-source"]').selectOption('trial')
  await expect(page.locator('[data-testid="log-content"]')).toContainText('[trial] building image')
})

test('missing initial snapshot is rendered as an explicit, chart-backed demo', async ({ page }) => {
  await page.route('**/api/dashboard', async (route) => {
    await route.fulfill({
      status: 404,
      json: {
        success: false,
        data: null,
        error: 'aggregate data not found',
      },
    })
  })

  await page.goto('/')

  await expect(page.locator('[data-testid="data-mode"]')).toContainText('模拟数据')
  await expect(page.locator('.demo-notice')).toContainText('固定模拟数据')
  await expect(page.locator('.kpi')).toHaveCount(6)
  await expect(page.locator('[data-testid="iq-radar"] .chart-canvas svg')).toBeVisible()
  await expect(page.locator('[data-testid="quota-radar"] .chart-canvas svg')).toBeVisible()
  await expect(page.locator('[data-testid="degradation-trend"] .chart-canvas svg')).toBeVisible()
  await expect(page.locator('[data-testid="run-table"] tbody tr')).toHaveCount(8)
})
