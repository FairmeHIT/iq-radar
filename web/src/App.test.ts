import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import * as dashboardClient from './features/dashboard/api'
import * as testClient from './features/test/api'
import * as leaderboardClient from './features/leaderboard/api'
import type { AggregateSummary, DashboardBundle, RadarSeries, RunRecord } from './features/dashboard/types'
import type { DeepSweBatch, DeepSweRun, MultiBenchState } from './features/test/types'

const summary: AggregateSummary = {
  schema_version: '1.0',
  generated_at: '2026-08-03T13:00:00Z',
  summaries: [
    {
      benchmark: { name: 'deep-swe', version: 'local' },
      model: 'model-a',
      effort: 'high',
      tasks_total: 2,
      tasks_scored: 2,
      infra_error_count: 0,
      tasks_passed: 1,
      tasks_failed: 1,
      pass_rate: 0.5,
      pass_rate_percent: 50,
      iq: 75,
      avg_cost_usd: 0.01,
      total_cost_usd: 0.02,
      avg_wall_time_sec: 60,
      tasks_per_hour: 60,
      avg_input_tokens: 1000,
      avg_output_tokens: 500,
      output_tokens_per_min: 500,
      avg_agent_steps: 3,
      agent_steps_per_hour: 180,
      cost_per_pass_usd: 0.02,
      cost_per_iq_point_usd: 0.00026,
      quota_percent_per_task: 0.05,
      estimated_tasks_per_week: 2000,
      estimated_passes_per_week: 1000,
      output_quota_percent_per_task: null,
      confidence: { method: 'wilson', level: 0.95, lower: 0.0945, upper: 0.9055 },
    },
  ],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
}

function mockDashboardData(options: {
  iqRadar?: RadarSeries[]
  quotaRadar?: RadarSeries[]
  runs?: RunRecord[]
} = {}) {
  vi.spyOn(dashboardClient, 'fetchDashboard').mockResolvedValue({
    snapshot_id: 'snapshot-a',
    summary,
    iq_radar: options.iqRadar ?? [],
    quota_radar: options.quotaRadar ?? [],
    runs: options.runs ?? [],
  })
  vi.spyOn(dashboardClient, 'fetchSnapshots').mockResolvedValue([
    { snapshot_id: 'snapshot-a', published_at: '2026-08-03T13:00:00Z', source_job_id: 'run-1', models: ['model-a'], tasks_total: 2, is_current: true },
  ])
}

function mockTestOptions() {
  vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
    source: 'gateway',
    error: null,
    models: [
      { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
    ],
  })
  vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
    { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE' },
  ])
  vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(null)
  vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
  vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
  vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([])
  vi.spyOn(testClient, 'fetchGatewaySettings').mockResolvedValue({
    models_base_url: '',
    models_api_key: '',
    inference_base_url: '',
    inference_api_key: '',
    updated_at: '',
  })
  vi.spyOn(leaderboardClient, 'fetchScenarios').mockResolvedValue([
    { id: 'backend', label: '后端开发', description: '代码生成', icon: 'Code' },
  ])
  vi.spyOn(leaderboardClient, 'fetchScenarioView').mockResolvedValue({
    scenario_id: 'backend',
    scenario_label: '后端开发',
    scenario_description: '代码生成',
    rankings: [],
    posters: [],
  })
}

function completedRun(overrides: Partial<DeepSweRun> = {}): DeepSweRun {
  return {
    run_id: 'run-1',
    status: 'completed',
    model_id: 'model-a',
    n_tasks: 3,
    sample_seed: 0,
    created_at: '2026-08-04T08:00:00Z',
    completed_at: '2026-08-04T08:03:00Z',
    error: null,
    jobs_dir: null,
    benchmark: 'deep-swe',
    effort: 'high',
    ...overrides,
  }
}

function runsList(): DeepSweRun[] {
  return [
    completedRun({ run_id: 'run-1', model_id: 'model-a', status: 'completed' }),
    completedRun({
      run_id: 'run-2',
      model_id: 'model-b',
      status: 'failed',
      error: 'interrupted: timeout',
      completed_at: '2026-08-04T08:05:00Z',
    }),
  ]
}

async function mountTestPageWithRuns(runs: DeepSweRun[]) {
  mockDashboardData()
  mockTestOptions()
  vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue(runs)
  const wrapper = mount(App)
  await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
  await new Promise((resolve) => setTimeout(resolve, 0))
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('App', () => {
  afterEach(() => {
    window.localStorage.clear()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders the market dashboard by default without test controls', async () => {
    mockDashboardData({
      iqRadar: [{ model: 'model-a', effort: 'high', axes: { iq: 50, pass_rate: 50, stability: 18.9, speed: 60, cost_efficiency: 99 } }],
      quotaRadar: [{ model: 'model-a', effort: 'high', axes: { quota_remaining_friendliness: 99.95, tasks_per_week: 100, passes_per_week: 100, cost_per_pass_efficiency: 98, token_efficiency: 66 } }],
      runs: [{
        run_id: 'task-a__model-a__high',
        benchmark: { name: 'deep-swe', version: 'local', task_id: 'task-a', repo: 'owner/repo', language: 'python', task_path: 'tasks/task-a' },
        model: { provider: 'openai-compatible', base_url_hash: 'sha256:test', name: 'model-a', effort_requested: 'high', effort_effective: true },
        result: { status: 'passed', verifier_passed: true, exit_code: 0, error_type: null, error_message_redacted: null },
        usage: { input_tokens: 1000, output_tokens: 500, cached_input_tokens: 0, agent_steps: 3, wall_time_sec: 60, usage_estimated: false },
        cost: { currency: 'USD', input_cost: 0, cached_input_cost: 0, output_cost: 0, total_cost: 0 },
        artifacts: { patch_path: null, log_path: 'data/artifacts/run.log', verifier_path: null },
        created_at: '2026-08-03T13:00:00Z',
      }],
    })
    const wrapper = mount(App)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('IQRadar')
    expect(wrapper.text()).toContain('75.0')
    expect(wrapper.text()).toContain('50.0%')
    expect(wrapper.find('[data-testid="model-filter"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="iq-radar"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="quota-radar"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="degradation-trend"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('task-a')
    expect(wrapper.find('[data-testid="dashboard-page"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="test-page"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="model-picker"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="start-test"]').exists()).toBe(false)
  })

  it('switches between published snapshots via the batch selector', async () => {
    vi.spyOn(dashboardClient, 'fetchSnapshots').mockResolvedValue([
      { snapshot_id: 'snap-new', published_at: '2026-09-02T08:58:00Z', source_job_id: 'run-2', models: ['model-b'], tasks_total: 11, is_current: true },
      { snapshot_id: 'snap-old', published_at: '2026-08-30T20:31:00Z', source_job_id: 'batch-1', models: ['model-a', 'model-b'], tasks_total: 40, is_current: false },
    ])
    const fetchDashboardSpy = vi.spyOn(dashboardClient, 'fetchDashboard')
    fetchDashboardSpy.mockResolvedValueOnce({
      snapshot_id: 'snap-new',
      summary,
      iq_radar: [],
      quota_radar: [],
      runs: [],
    })
    const oldSummary: AggregateSummary = {
      schema_version: '1.0',
      generated_at: '2026-08-30T20:31:00Z',
      summaries: [{
        ...summary.summaries[0],
        model: 'model-b',
        tasks_total: 40,
        tasks_passed: 20,
        pass_rate_percent: 50,
      }],
    }
    fetchDashboardSpy.mockResolvedValueOnce({
      snapshot_id: 'snap-old',
      summary: oldSummary,
      iq_radar: [],
      quota_radar: [],
      runs: [],
    })

    const wrapper = mount(App)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="snapshot-filter"]').exists()).toBe(true)
    expect(fetchDashboardSpy).toHaveBeenCalledWith('snap-new')

    const select = wrapper.find('[data-testid="snapshot-filter"]')
    await select.setValue('snap-old')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(fetchDashboardSpy).toHaveBeenCalledWith('snap-old')
    expect(fetchDashboardSpy).toHaveBeenCalledTimes(2)
  })

  it('renders test controls only on the test page and starts a DeepSWE run', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.spyOn(testClient, 'submitDeepSweRun').mockResolvedValue(completedRun({ status: 'queued' }))
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)

    const wrapper = mount(App)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(wrapper.find('[data-testid="test-page"]').exists()).toBe(false)

    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="dashboard-page"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="test-page"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="model-picker"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="model-picker"]').text()).toContain('gateway / Model A')
    expect(wrapper.find('[data-testid="start-test"]').exists()).toBe(true)

    // 勾选模型后再点击开始
    await wrapper.find('[data-testid="model-picker"] input[type="checkbox"]').setValue(true)
    await wrapper.vm.$nextTick()
    await wrapper.find('[data-testid="start-test"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.submitDeepSweRun).toHaveBeenCalledWith({ model_id: 'gateway/model-a', benchmark: 'deep-swe', effort: 'high' })
    expect(wrapper.find('[data-testid="execution-result"]').text()).toContain('评测完成')
  })

  it('filters the model picker by provider (first slash segment)', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'provider-a/model-a', display_name: 'Model A', provider: 'provider-a', label: 'provider-a / Model A', efforts: ['high'] },
        { id: 'provider-a/minimax/minimax-m2.7', display_name: 'MiniMax M2.7', provider: 'provider-a', label: 'provider-a/minimax/minimax-m2.7', efforts: ['high'] },
        { id: 'provider-b/glm-5.2', display_name: 'GLM 5.2', provider: 'provider-b', label: 'provider-b/glm-5.2', efforts: ['high'] },
        { id: 'provider-b/deepseek-v4-flash', display_name: 'DeepSeek V4 Flash', provider: 'provider-b', label: 'provider-b/deepseek-v4-flash', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE' },
    ])
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 供应商筛选条可见：全部 + 去重后的供应商（provider-a / provider-b）
    const filter = wrapper.find('[data-testid="provider-filter"]')
    expect(filter.exists()).toBe(true)
    expect(filter.text()).toContain('全部')
    expect(filter.text()).toContain('provider-a')
    expect(filter.text()).toContain('provider-b')
    expect(wrapper.find('[data-testid="model-picker"]').text()).toContain('provider-a / Model A')
    expect(wrapper.find('[data-testid="model-picker"]').text()).toContain('provider-b/glm-5.2')

    // 切到 provider-b：列表只显示该供应商的模型
    const chips = wrapper.findAll('[data-testid="provider-filter"] button')
    const providerBChip = chips.find((chip) => chip.text() === 'provider-b')
    expect(providerBChip).toBeTruthy()
    await providerBChip!.trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="model-picker"]').text()).not.toContain('provider-a / Model A')
    expect(wrapper.find('[data-testid="model-picker"]').text()).toContain('provider-b/glm-5.2')
    expect(wrapper.find('[data-testid="model-picker"]').text()).toContain('provider-b/deepseek-v4-flash')

    // 筛选态下“全选”只勾选当前供应商的模型
    const allButton = wrapper.findAll('button.picker-link').find((button) => button.text() === '全选')
    expect(allButton).toBeTruthy()
    await allButton!.trigger('click')
    await wrapper.vm.$nextTick()

    const checked = wrapper
      .findAll('[data-testid="model-picker"] input[type="checkbox"]')
      .filter((input) => (input.element as HTMLInputElement).checked)
      .map((input) => (input.element as HTMLInputElement).value)
    expect(checked.sort()).toEqual(['provider-b/deepseek-v4-flash', 'provider-b/glm-5.2'])
    expect(wrapper.find('[data-testid="model-picker-count"]').text()).toContain('已选 2/4')

    // 切回“全部”恢复完整列表
    const allChip = wrapper.findAll('[data-testid="provider-filter"] button').find((chip) => chip.text() === '全部')
    expect(allChip).toBeTruthy()
    await allChip!.trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="model-picker"]').text()).toContain('provider-a / Model A')
  })

  it('publishes a completed DeepSWE run to the dashboard', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([completedRun()])
    const publicationSpy = vi.spyOn(testClient, 'fetchDeepSweRunPublication')
    publicationSpy.mockResolvedValue(null)
    vi.spyOn(testClient, 'publishDeepSweRun').mockImplementation(async () => {
      publicationSpy.mockResolvedValue({ snapshot_id: 'snapshot-x', source_job_id: 'run-1' })
      return { snapshot_id: 'snapshot-x', source_job_id: 'run-1' }
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="publish-run-run-1"]').exists()).toBe(true)
    await wrapper.find('[data-testid="publish-run-run-1"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.publishDeepSweRun).toHaveBeenCalledWith('run-1')
    expect(wrapper.find('[data-testid="execution-result"]').text()).toContain('snapshot-x')
  })

  it('unpublishes a published run from the records table', async () => {
    mockDashboardData()
    mockTestOptions()
    const publishedRun = completedRun({ snapshot_id: 'snapshot-x' })
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(publishedRun)
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([publishedRun])
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue({
      snapshot_id: 'snapshot-x',
      source_job_id: 'run-1',
    })
    const unpublishSpy = vi
      .spyOn(testClient, 'unpublishDeepSweRun')
      .mockResolvedValue({ snapshot_id: 'snapshot-x', current_snapshot_id: null })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="publish-run-run-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="unpublish-run-run-1"]').exists()).toBe(true)
    await wrapper.find('[data-testid="unpublish-run-run-1"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(unpublishSpy).toHaveBeenCalledWith('run-1')
  })

  it('publishes selected records to the dashboard as one snapshot', async () => {
    mockDashboardData()
    mockTestOptions()
    const runA = completedRun({ run_id: 'run-a', model_id: 'model-a', snapshot_id: null })
    const runB = completedRun({ run_id: 'run-b', model_id: 'model-b', snapshot_id: null })
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(runA)
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([runA, runB])
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    const publishSpy = vi
      .spyOn(testClient, 'publishDeepSweRuns')
      .mockResolvedValue({
        snapshot_id: 'snapshot-multi',
        source_job_id: 'run-a',
        published_run_ids: ['run-a', 'run-b'],
      })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 勾选两条完成的记录，点「发布选中」：批量发布端点收到两条 run id。
    await wrapper.find('[data-testid="run-select-run-a"]').setValue(true)
    await wrapper.find('[data-testid="run-select-run-b"]').setValue(true)
    await wrapper.find('[data-testid="publish-selected-runs"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(publishSpy).toHaveBeenCalledWith(['run-a', 'run-b'])
    expect(wrapper.find('[data-testid="runs-notice"]').text()).toContain('已发布 2 条记录到大盘')
  })

  it('retests single questions or all questions from the records table', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE', category: 'docker' },
      { id: 'gpqa-diamond', type: 'api-eval', label: 'GPQA Diamond', category: 'api-eval' },
    ])
    const apiRun = completedRun({
      run_id: 'run-gpqa',
      benchmark: 'gpqa-diamond',
      snapshot_id: null,
      retryable_infrastructure_failure_count: 1,
    })
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(apiRun)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchGatewaySettings').mockResolvedValue({
      models_base_url: '',
      models_api_key: '',
      inference_base_url: '',
      inference_api_key: '',
      updated_at: '',
    })
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([apiRun])
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    const questions = [
      { index: 0, task_id: 'q-1', status: 'passed', outcome: 'success', failure_category: null, error_type: null, error_message: null },
      { index: 1, task_id: 'q-2', status: 'runner_error', outcome: 'infrastructure_error', failure_category: 'gateway_error', error_type: 'TimeoutError', error_message: 'gateway timeout' },
      { index: 2, task_id: 'q-3', status: 'failed', outcome: 'model_failure', failure_category: 'model_wrong_answer', error_type: 'exact_match', error_message: 'wrong answer' },
    ]
    vi.spyOn(testClient, 'fetchRunQuestions').mockResolvedValue(questions)
    const retrySpy = vi
      .spyOn(testClient, 'retryRunQuestions')
      .mockResolvedValue({ ...apiRun, status: 'running' })
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue({ ...apiRun, status: 'completed' })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="retry-run-run-gpqa"]').exists()).toBe(true)
    await wrapper.find('[data-testid="retry-run-run-gpqa"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 弹窗展示逐题结果，每题可单独重测
    expect(wrapper.find('[data-testid="questions-modal"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="questions-modal"]').text()).toContain('q-1')
    expect(wrapper.find('[data-testid="questions-modal"]').text()).toContain('q-2')
    expect(wrapper.find('[data-testid="questions-modal"]').text()).toContain('q-3')

    await wrapper.find('[data-testid="retry-question-q-2"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    expect(retrySpy).toHaveBeenCalledWith('run-gpqa', ['q-2'])

    // 重测成功后弹窗关闭；等待记录轮询恢复终态，再执行「全部重测」
    await vi.waitFor(() => {
      expect(wrapper.find('[data-testid="retry-run-run-gpqa"]').exists()).toBe(true)
    })
    await wrapper.find('[data-testid="retry-run-run-gpqa"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    await wrapper.find('[data-testid="retry-all-questions"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    expect(retrySpy).toHaveBeenCalledWith('run-gpqa', ['q-2'])
  })

  it('disables records-table retest when an api-eval run has no infrastructure failures', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'gpqa-diamond', type: 'api-eval', label: 'GPQA Diamond', category: 'api-eval' },
    ])
    const apiRun = completedRun({
      run_id: 'run-gpqa-ok',
      benchmark: 'gpqa-diamond',
      snapshot_id: null,
      retryable_infrastructure_failure_count: 0,
    })
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(apiRun)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchGatewaySettings').mockResolvedValue({
      models_base_url: '',
      models_api_key: '',
      inference_base_url: '',
      inference_api_key: '',
      updated_at: '',
    })
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([apiRun])
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    const fetchQuestionsSpy = vi.spyOn(testClient, 'fetchRunQuestions').mockResolvedValue([])

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    const retryButton = wrapper.find('[data-testid="retry-run-run-gpqa-ok"]')
    expect(retryButton.exists()).toBe(true)
    expect(retryButton.attributes('disabled')).toBeDefined()
    expect(retryButton.attributes('title')).toContain('没有基础设施/调用链失败题可重测')
    await retryButton.trigger('click')
    await wrapper.vm.$nextTick()
    expect(fetchQuestionsSpy).not.toHaveBeenCalled()
  })

  it('resumes an interrupted api-eval run from the records table', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'gpqa-diamond', type: 'api-eval', label: 'GPQA Diamond', category: 'api-eval' },
    ])
    const interruptedRun = completedRun({
      run_id: 'run-gpqa-interrupted',
      status: 'failed',
      benchmark: 'gpqa-diamond',
      error: 'interrupted: server restarted mid-run',
      resumable_partial_results: true,
      retryable_infrastructure_failure_count: 0,
    })
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(interruptedRun)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchGatewaySettings').mockResolvedValue({
      models_base_url: '',
      models_api_key: '',
      inference_base_url: '',
      inference_api_key: '',
      updated_at: '',
    })
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([interruptedRun])
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    const submitSpy = vi.spyOn(testClient, 'submitDeepSweRun').mockResolvedValue({
      ...interruptedRun,
      run_id: 'run-gpqa-resumed',
      status: 'running',
      completed_at: null,
      error: null,
    })
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue({
      ...interruptedRun,
      run_id: 'run-gpqa-resumed',
      status: 'completed',
      completed_at: '2026-08-04T08:08:00Z',
      error: null,
      resumable_partial_results: false,
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    const resumeButton = wrapper.find('[data-testid="resume-run-run-gpqa-interrupted"]')
    expect(resumeButton.exists()).toBe(true)
    expect(resumeButton.attributes('disabled')).toBeUndefined()
    await resumeButton.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(submitSpy).toHaveBeenCalledWith({
      model_id: 'model-a',
      n_tasks: 3,
      sample_seed: 0,
      benchmark: 'gpqa-diamond',
      effort: 'high',
      resume_run_id: 'run-gpqa-interrupted',
    })
  })

  it('refreshes the dashboard with published results after navigating back from the test page', async () => {
    const emptyBundle: DashboardBundle = {
      snapshot_id: 'empty',
      summary: { schema_version: '1.0', generated_at: '', summaries: [] },
      iq_radar: [],
      quota_radar: [],
      runs: [],
    }
    const publishedBundle: DashboardBundle = {
      snapshot_id: 'snapshot-x',
      summary,
      iq_radar: [{ model: 'model-a', effort: 'high', axes: { iq: 50, pass_rate: 50, stability: 18.9, speed: 60, cost_efficiency: 99 } }],
      quota_radar: [{ model: 'model-a', effort: 'high', axes: { quota_remaining_friendliness: 99.95, tasks_per_week: 100, passes_per_week: 100, cost_per_pass_efficiency: 98, token_efficiency: 66 } }],
      runs: [],
    }
    const fetchDashboardSpy = vi.spyOn(dashboardClient, 'fetchDashboard')
    fetchDashboardSpy.mockResolvedValueOnce(emptyBundle)
    mockTestOptions()
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([completedRun()])
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    vi.spyOn(testClient, 'publishDeepSweRun').mockResolvedValue({ snapshot_id: 'snapshot-x', source_job_id: 'run-1' })

    const wrapper = mount(App)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    expect(fetchDashboardSpy).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="data-mode"]').text()).toContain('模拟数据')

    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    await wrapper.find('[data-testid="publish-run-run-1"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    fetchDashboardSpy.mockResolvedValueOnce(publishedBundle)
    await wrapper.find('[data-testid="dashboard-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(fetchDashboardSpy).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="data-mode"]').text()).toContain('实时数据')
    expect(wrapper.text()).not.toContain('DEMO')
  })

  it('does not show a stale dashboard load error after switching to the test page', async () => {
    const dashboardLoad = deferred<DashboardBundle>()
    vi.spyOn(dashboardClient, 'fetchDashboard').mockReturnValue(dashboardLoad.promise)
    vi.spyOn(dashboardClient, 'fetchSnapshots').mockResolvedValue([])
    mockTestOptions()

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))

    dashboardLoad.reject(new Error('dashboard unavailable'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="test-page"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('dashboard unavailable')
  })

  it('keeps polling a recovered DeepSWE run after a transient API failure', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.mocked(testClient.fetchLatestDeepSweRun).mockResolvedValue(completedRun({ status: 'running' }))
    vi.spyOn(testClient, 'fetchDeepSweRun')
      .mockRejectedValueOnce(new Error('temporary polling failure'))
      .mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(wrapper.text()).toContain('temporary polling failure')

    await new Promise((resolve) => setTimeout(resolve, 1100))
    await wrapper.vm.$nextTick()

    expect(testClient.fetchDeepSweRun).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="execution-result"]').text()).toContain('评测完成')
    expect(wrapper.text()).not.toContain('temporary polling failure')
  })

  it('shows an environment-build hint while a run is running with no trial logs yet', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.mocked(testClient.fetchLatestDeepSweRun).mockResolvedValue(
      completedRun({ status: 'running' }),
    )
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue(
      completedRun({ status: 'running' }),
    )
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchRunLogSources').mockResolvedValue([
      { name: 'pier', path: '/tmp/run-1/pier.log' },
      { name: 'job', path: '/tmp/run-1/job.log' },
    ])
    vi.spyOn(testClient, 'fetchRunLog').mockResolvedValue({
      sources: [
        { name: 'pier', path: '/tmp/run-1/pier.log' },
        { name: 'job', path: '/tmp/run-1/job.log' },
      ],
      selected: 'pier',
      path: '/tmp/run-1/pier.log',
      content: '',
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="build-hint"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('正在构建评测环境')
    expect(wrapper.find('[data-testid="log-content"] pre').exists()).toBe(false)
  })

  it('shows question progress (completed/total) for a running run', async () => {
    mockDashboardData()
    mockTestOptions()
    const progress = { total: 10, completed: 3, running: 1, pending: 6 }
    vi.mocked(testClient.fetchLatestDeepSweRun).mockResolvedValue(
      completedRun({ status: 'running', progress }),
    )
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue(
      completedRun({ status: 'running', progress }),
    )
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchRunLogSources').mockResolvedValue([
      { name: 'pier', path: '/tmp/run-1/pier.log' },
    ])
    vi.spyOn(testClient, 'fetchRunLog').mockResolvedValue({
      sources: [{ name: 'pier', path: '/tmp/run-1/pier.log' }],
      selected: 'pier',
      path: '/tmp/run-1/pier.log',
      content: 'pier output',
      progress,
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 评测进程卡：进度条 + 题目进度 3/10（含作答中/待处理细分）
    const taskProgress = wrapper.find('[data-testid="task-progress"]')
    expect(taskProgress.exists()).toBe(true)
    expect(taskProgress.text()).toContain('题目进度 3/10')
    expect(taskProgress.text()).toContain('作答中 1')
    expect(taskProgress.text()).toContain('待处理 6')
    expect(wrapper.find('.task-progress-fill').attributes('style')).toContain('width: 30%')
    // 日志面板标题：同样的进度徽标
    expect(wrapper.find('[data-testid="log-progress"]').text()).toContain('题目进度 3/10')
  })

  it('falls back to the plain task count when progress is unknown', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.mocked(testClient.fetchLatestDeepSweRun).mockResolvedValue(
      completedRun({ status: 'running', progress: null }),
    )
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue(
      completedRun({ status: 'running', progress: null }),
    )
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchRunLogSources').mockResolvedValue([
      { name: 'pier', path: '/tmp/run-1/pier.log' },
    ])
    vi.spyOn(testClient, 'fetchRunLog').mockResolvedValue({
      sources: [{ name: 'pier', path: '/tmp/run-1/pier.log' }],
      selected: 'pier',
      path: '/tmp/run-1/pier.log',
      content: 'pier output',
      progress: null,
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="task-progress"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="log-progress"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="execution-result"]').text()).toContain('任务 3 个')
  })

  it('falls back to terminal-bench run logs instead of requesting the stale llm source', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE' },
      { id: 'terminal-bench', type: 'terminal-bench', label: 'Terminal-Bench' },
    ])
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'submitDeepSweRun').mockResolvedValue(completedRun({
      run_id: 'tb-run-1',
      status: 'queued',
      benchmark: 'terminal-bench',
    }))
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue(completedRun({
      run_id: 'tb-run-1',
      benchmark: 'terminal-bench',
    }))
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchRunLogSources').mockResolvedValue([
      { name: 'run', path: '/tmp/tb-run-1/run.log' },
    ])
    vi.spyOn(testClient, 'fetchRunLog').mockResolvedValue({
      sources: [{ name: 'run', path: '/tmp/tb-run-1/run.log' }],
      selected: 'run',
      path: '/tmp/tb-run-1/run.log',
      content: 'terminal-bench output',
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    await wrapper.find('input[value="terminal-bench"]').setValue(true)
    await wrapper.find('[data-testid="model-picker"] input[type="checkbox"]').setValue(true)
    await wrapper.find('[data-testid="start-test"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.submitDeepSweRun).toHaveBeenCalledWith({ model_id: 'gateway/model-a', benchmark: 'terminal-bench', effort: 'high' })
    expect(testClient.fetchRunLog).toHaveBeenCalledWith('tb-run-1', 'run', undefined)
    expect(testClient.fetchRunLog).not.toHaveBeenCalledWith('tb-run-1', 'llm', 6)
    expect(wrapper.find('[data-testid="log-content"]').text()).toContain('terminal-bench output')
    expect(wrapper.text()).not.toContain('invalid log source')
  })

  function failedBatch(overrides: Partial<DeepSweBatch> = {}): DeepSweBatch {
    return {
      batch_id: 'batch-1',
      status: 'cancelled',
      n_tasks: 3,
      sample_seed: 0,
      created_at: '2026-08-04T08:00:00Z',
      completed_at: '2026-08-04T08:00:05Z',
      current_model: 'model-a',
      error: 'interrupted: server restarted mid-batch',
      snapshot_id: null,
      benchmark: 'deep-swe',
      models: [
        { model_id: 'model-a', model_name: 'openai/model-a', status: 'completed', run_id: 'run-1' },
        { model_id: 'model-b', model_name: 'openai/model-b', status: 'pending', run_id: null },
      ],
      ...overrides,
    }
  }

  it('rescues an interrupted batch via the 接续 button', async () => {
    mockDashboardData()
    mockTestOptions()
    // 恢复被中断的批次：模型A已完成，模型B pending，批次被误标为失败
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(failedBatch())
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 批次卡片显示已取消/失败状态（cancelled 批次显示"已取消"，failed 显示错误详情）
    expect(wrapper.find('[data-testid="batch-progress"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('已取消')

    // 接续按钮可用（有未完成的模型）
    const resumeButton = wrapper.find('[data-testid="resume-test"]')
    expect(resumeButton.exists()).toBe(true)
    expect(resumeButton.attributes('disabled')).toBeUndefined()

    // 点击接续：调用 API，进入轮询
    vi.spyOn(testClient, 'resumeBatch').mockResolvedValue({
      ...failedBatch(),
      status: 'running',
      completed_at: null,
      error: null,
      current_model: 'model-b',
    })
    vi.spyOn(testClient, 'fetchBatch').mockResolvedValue({
      ...failedBatch(),
      status: 'running',
      error: null,
    })
    await resumeButton.trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.resumeBatch).toHaveBeenCalledWith('batch-1')
    // 批次状态已更新为 running
    expect(wrapper.find('[data-testid="run-phase"]').text()).toContain('批量评测进行中')
  })

  it('submits a multi-model batch with default model-level parallelism', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'jt/dsv4', display_name: 'jt dsv4', provider: 'jt', label: 'jt / dsv4', efforts: ['high'] },
        { id: 'jz/dsv4', display_name: 'jz dsv4', provider: 'jz', label: 'jz / dsv4', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'terminal-bench-2', type: 'terminal-bench-2', label: 'Terminal-Bench 2.0', category: 'docker' },
    ])

    const batch: DeepSweBatch = {
      batch_id: 'batch-parallel',
      status: 'running',
      n_tasks: 10,
      sample_seed: 0,
      created_at: '2026-08-04T08:00:00Z',
      completed_at: null,
      current_model: 'jt/dsv4',
      error: null,
      snapshot_id: null,
      benchmark: 'terminal-bench-2',
      max_concurrent: 2,
      models: [
        { model_id: 'jt/dsv4', model_name: 'openai/jt/dsv4', status: 'running', run_id: 'run-1' },
        { model_id: 'jz/dsv4', model_name: 'openai/jz/dsv4', status: 'running', run_id: 'run-2' },
      ],
    }
    const submitSpy = vi.spyOn(testClient, 'submitBatch').mockResolvedValue(batch)
    vi.spyOn(testClient, 'fetchBatch').mockResolvedValue({ ...batch, status: 'completed', snapshot_id: null })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    const checkboxes = wrapper.findAll('[data-testid="model-picker"] input[type="checkbox"]')
    expect(checkboxes.length).toBe(2)
    await checkboxes[0].setValue(true)
    await checkboxes[1].setValue(true)

    expect(wrapper.find('[data-testid="parallel-models"]').exists()).toBe(false)

    await wrapper.find('[data-testid="start-test"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(submitSpy).toHaveBeenCalledWith(expect.objectContaining({
      model_ids: ['jt/dsv4', 'jz/dsv4'],
      benchmark: 'terminal-bench-2',
      max_concurrent: 2,
    }))
  })

  it('submits runs without retry flags; retesting happens from the records table', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE', category: 'docker' },
      { id: 'gpqa-diamond', type: 'api-eval', label: 'GPQA Diamond', category: 'api-eval', task_count: 198 },
    ])
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'submitDeepSweRun').mockResolvedValue(completedRun({ status: 'queued' }))
    vi.spyOn(testClient, 'fetchDeepSweRun').mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 评测参数卡已移除：重测配置不再出现在提交表单里
    expect(wrapper.find('[data-testid="gateway-retry-toggle"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="effort-select"]').exists()).toBe(true)

    await wrapper.find('input[value="deep-swe"]').setValue(false)
    await wrapper.find('input[value="gpqa-diamond"]').setValue(true)
    const questionCount = wrapper.find('input[aria-label="GPQA Diamond本次评测题数"]')
    expect(questionCount.exists()).toBe(true)
    expect((questionCount.element as HTMLInputElement).value).toBe('198')
    await questionCount.setValue('100')
    await wrapper.find('[data-testid="model-picker"] input[type="checkbox"]').setValue(true)
    await wrapper.find('[data-testid="start-test"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.submitDeepSweRun).toHaveBeenCalledWith({
      model_id: 'gateway/model-a',
      benchmark: 'gpqa-diamond',
      effort: 'high',
      n_tasks: 100,
    })
  })

  it('persists API benchmark question counts across page remounts', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE', category: 'docker' },
      { id: 'gpqa-diamond', type: 'api-eval', label: 'GPQA Diamond', category: 'api-eval', task_count: 198 },
    ])
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchDeepSweRuns').mockResolvedValue([])
    vi.spyOn(testClient, 'fetchGatewaySettings').mockResolvedValue({
      models_base_url: '',
      models_api_key: '',
      inference_base_url: '',
      inference_api_key: '',
      updated_at: '',
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    const questionCount = wrapper.find('input[aria-label="GPQA Diamond本次评测题数"]')
    await questionCount.setValue('100')
    expect(window.localStorage.getItem('iqradar:test:benchmark-task-counts:v1')).toBe(
      JSON.stringify({ 'gpqa-diamond': 100 }),
    )
    wrapper.unmount()

    const remounted = mount(App)
    await remounted.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await remounted.vm.$nextTick()

    const restored = remounted.find('input[aria-label="GPQA Diamond本次评测题数"]')
    expect((restored.element as HTMLInputElement).value).toBe('100')
  })

  it('submits multi-bench runs with default parallelism and no retry flags', async () => {
    mockDashboardData()
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'gateway',
      error: null,
      models: [
        { id: 'gateway/model-a', display_name: 'Model A', provider: 'gateway', label: 'gateway / Model A', efforts: ['high'] },
      ],
    })
    vi.spyOn(testClient, 'fetchBenchmarks').mockResolvedValue([
      { id: 'deep-swe', type: 'deep-swe', label: 'DeepSWE', category: 'docker' },
      { id: 'gpqa-diamond', type: 'api-eval', label: 'GPQA Diamond', category: 'api-eval' },
      { id: 'aime-2024', type: 'api-eval', label: 'AIME 2024', category: 'api-eval' },
    ])
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(null)
    vi.spyOn(testClient, 'fetchLatestMultiBatch').mockResolvedValue(null)

    const multiBatch: MultiBenchState = {
      batch_id: 'multi-retry-1',
      kind: 'multi-bench',
      status: 'running',
      max_concurrent: 2,
      n_tasks: 10,
      sample_seed: 0,
      effort: 'high',
      n_concurrent: 1,
      created_at: '2026-08-04T08:00:00Z',
      completed_at: null,
      snapshot_id: null,
      error: null,
      items: [
        { benchmark: 'gpqa-diamond', model_id: 'gateway/model-a', model_name: 'openai/model-a', status: 'running', run_id: 'run-1' },
        { benchmark: 'aime-2024', model_id: 'gateway/model-a', model_name: 'openai/model-a', status: 'pending', run_id: null },
      ],
    }
    const submitSpy = vi.spyOn(testClient, 'submitMultiBench').mockResolvedValue(multiBatch)
    vi.spyOn(testClient, 'fetchMultiBatch').mockResolvedValue({
      ...multiBatch,
      status: 'completed',
      completed_at: '2026-08-04T08:05:00Z',
      snapshot_id: 'snap-multi-1',
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    // 选中多个 API 基准即自动组成多基准矩阵。
    await wrapper.find('input[value="deep-swe"]').setValue(false)
    await wrapper.find('input[value="gpqa-diamond"]').setValue(true)
    await wrapper.find('input[value="aime-2024"]').setValue(true)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="multi-bench-toggle"]').exists()).toBe(false)

    await wrapper.find('[data-testid="model-picker"] input[type="checkbox"]').setValue(true)
    await wrapper.find('[data-testid="start-test"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(submitSpy).toHaveBeenCalledWith(expect.objectContaining({
      max_concurrent: 2,
      effort: 'high',
    }))
    const payload = vi.mocked(submitSpy).mock.calls[0][0]
    expect(payload).not.toHaveProperty('retry_gateway_failures')
    expect(payload).not.toHaveProperty('gateway_retry_rounds')
  })

  it('saves gateway settings from the test page and refreshes the model list', async () => {
    mockDashboardData()
    mockTestOptions()
    const saveSpy = vi.spyOn(testClient, 'saveGatewaySettings').mockResolvedValue({
      models_base_url: 'http://new.example/v1',
      models_api_key: 'sk-new',
      inference_base_url: 'http://new.example/v1',
      inference_api_key: 'sk-new',
      updated_at: '2026-08-28T10:00:00+00:00',
    })
    vi.spyOn(testClient, 'testGatewayModels').mockResolvedValue({
      ok: true,
      base_url: 'http://new.example/v1',
      model_count: 2,
      model_ids: ['a/one', 'b/two'],
      latency_ms: 12,
      error: null,
    })

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="gateway-settings"]').exists()).toBe(true)
    const fetchModelsSpy = vi.spyOn(testClient, 'fetchModels')
    fetchModelsSpy.mockClear()

    await wrapper.find('[data-testid="gw-models-base-url"]').setValue('http://new.example/v1')
    await wrapper.find('[data-testid="gw-models-key"]').setValue('sk-new')
    await wrapper.find('[data-testid="gw-test-models"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    expect(testClient.testGatewayModels).toHaveBeenCalledWith({
      base_url: 'http://new.example/v1',
      api_key: 'sk-new',
    })
    // 测试成功 → 自动保存当前表单并按新网关刷新待测模型，立即可选
    expect(saveSpy).toHaveBeenCalledTimes(1)
    expect(saveSpy).toHaveBeenCalledWith({
      models_base_url: 'http://new.example/v1',
      models_api_key: 'sk-new',
      inference_base_url: '',
      inference_api_key: '',
      inference_effort: 'high',
    })
    expect(testClient.fetchModels).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="gateway-settings"]').text()).toContain('待测模型已更新')
    expect(wrapper.find('[data-testid="gateway-settings"]').text()).toContain('连接成功')

    fetchModelsSpy.mockClear()
    saveSpy.mockClear()
    await wrapper.find('[data-testid="gw-save"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(saveSpy).toHaveBeenCalledWith({
      models_base_url: 'http://new.example/v1',
      models_api_key: 'sk-new',
      inference_base_url: '',
      inference_api_key: '',
      inference_effort: 'high',
    })
    // 保存后按新网关重新拉取待测模型列表
    expect(testClient.fetchModels).toHaveBeenCalledTimes(1)
  })

  it('warns when the gateway falls back to the local directory', async () => {
    mockDashboardData()
    mockTestOptions()
    // 正常路径：无回退警告
    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="model-source-warning"]').exists()).toBe(false)
    wrapper.unmount()

    // 网关不可达：明示失败原因
    vi.spyOn(testClient, 'fetchModels').mockResolvedValue({
      source: 'yaml',
      error: 'URLError: connection refused',
      models: [
        { id: 'deepseek-v4-flash', display_name: 'DeepSeek V4 Flash', provider: 'gateway', efforts: ['high'] },
      ],
    })
    const fallbackWrapper = mount(App)
    await fallbackWrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await fallbackWrapper.vm.$nextTick()
    expect(fallbackWrapper.find('[data-testid="model-source-warning"]').text()).toContain('connection refused')
  })

  it('hides the 接续 button when the batch has no incomplete models', async () => {
    mockDashboardData()
    mockTestOptions()
    vi.spyOn(testClient, 'fetchLatestDeepSweRun').mockResolvedValue(completedRun())
    vi.spyOn(testClient, 'fetchLatestBatch').mockResolvedValue(failedBatch({
      models: [
        { model_id: 'model-a', model_name: 'openai/model-a', status: 'completed', run_id: 'run-1' },
        { model_id: 'model-b', model_name: 'openai/model-b', status: 'completed', run_id: 'run-2' },
      ],
    }))
    vi.spyOn(testClient, 'fetchDeepSweRunPublication').mockResolvedValue(null)

    const wrapper = mount(App)
    await wrapper.find('[data-testid="test-page-tab"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    const resumeButton = wrapper.find('[data-testid="resume-test"]')
    expect(resumeButton.exists()).toBe(true)
    expect(resumeButton.attributes('disabled')).toBeDefined()
  })

  it('lists filtered 评测记录 and deletes selected runs in batch', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountTestPageWithRuns(runsList())

    const panel = wrapper.find('[data-testid="runs-panel"]')
    expect(panel.exists()).toBe(true)
    expect(panel.text()).toContain('评测记录')
    // 两条记录都渲染了
    expect(wrapper.find('[data-testid="delete-run-run-2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="runs-selected-count"]').text()).toContain('已选 0 / 2')

    // 筛选「仅失败」后只剩失败的 run-2
    await wrapper.find('[data-testid="runs-status-filter"]').setValue('failed')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="delete-run-run-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="delete-run-run-2"]').exists()).toBe(true)

    // 勾选 run-2 后批量删除
    await wrapper.find('[data-testid="run-select-run-2"]').setValue(true)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="runs-selected-count"]').text()).toContain('已选 1 / 1')
    expect(wrapper.find('[data-testid="delete-selected-runs"]').attributes('disabled')).toBeUndefined()

    vi.spyOn(testClient, 'deleteDeepSweRuns').mockResolvedValue({
      deleted: ['run-2'],
      skipped: [],
    })
    await wrapper.find('[data-testid="delete-selected-runs"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.deleteDeepSweRuns).toHaveBeenCalledWith(['run-2'])
    expect(wrapper.find('[data-testid="runs-notice"]').text()).toContain('已删除 1 条')
    confirmSpy.mockRestore()
  })

  it('deletes a single run from its row button', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountTestPageWithRuns(runsList())

    vi.spyOn(testClient, 'deleteDeepSweRun').mockResolvedValue({ run_id: 'run-2', deleted: true })
    await wrapper.find('[data-testid="delete-run-run-2"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(testClient.deleteDeepSweRun).toHaveBeenCalledWith('run-2')
    expect(wrapper.find('[data-testid="runs-notice"]').text()).toContain('已删除 1 条')
    confirmSpy.mockRestore()
  })

  it('reports skipped active runs after a batch delete', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = await mountTestPageWithRuns(runsList())

    await wrapper.find('[data-testid="run-select-run-1"]').setValue(true)
    await wrapper.find('[data-testid="run-select-run-2"]').setValue(true)
    await wrapper.vm.$nextTick()

    vi.spyOn(testClient, 'deleteDeepSweRuns').mockResolvedValue({
      deleted: ['run-2'],
      skipped: [{ run_id: 'run-1', reason: 'active run cannot be deleted; cancel it first' }],
    })
    await wrapper.find('[data-testid="delete-selected-runs"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-testid="runs-notice"]').text()).toContain('跳过 1 条运行中')
    confirmSpy.mockRestore()
  })
})
