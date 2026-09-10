import { describe, expect, it } from 'vitest'
import type { DashboardBundle, ModelEffortSummary, RadarSeries } from './types'
import { buildDashboardView, normalizeSelection, radarSeriesName, summaryRowName } from './viewModel'

const emptyBundle: DashboardBundle = {
  snapshot_id: 'empty',
  summary: { schema_version: '1.0', generated_at: '2026-08-05T00:00:00Z', summaries: [] },
  iq_radar: [],
  quota_radar: [],
  runs: [],
}

function summary(overrides: Partial<ModelEffortSummary>): ModelEffortSummary {
  return {
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
    ...overrides,
  }
}

describe('dashboard view model', () => {
  it('uses a deterministic, clearly identified demo bundle when the API has no values', () => {
    const first = buildDashboardView(emptyBundle)
    const second = buildDashboardView(emptyBundle)

    expect(first.isDemo).toBe(true)
    expect(first.bundle.snapshot_id).toBe('demo-dashboard-v1')
    expect(first.bundle.summary.summaries.length).toBeGreaterThanOrEqual(4)
    expect(first.bundle.iq_radar.length).toBeGreaterThanOrEqual(4)
    expect(first.bundle.runs.length).toBeGreaterThanOrEqual(6)
    expect(second.bundle).toEqual(first.bundle)
  })

  it('never fills partial real API responses with simulated values', () => {
    const realSummary = summary({ model: 'real-model' })
    const view = buildDashboardView({
      ...emptyBundle,
      snapshot_id: 'real-snapshot',
      summary: { ...emptyBundle.summary, summaries: [realSummary] },
    })

    expect(view.isDemo).toBe(false)
    expect(view.bundle.summary.summaries).toEqual([realSummary])
    expect(view.bundle.iq_radar).toEqual([])
    expect(view.bundle.runs).toEqual([])
  })

  it('aggregates headline metrics across visible models instead of selecting the first row', () => {
    const view = buildDashboardView({
      ...emptyBundle,
      summary: {
        ...emptyBundle.summary,
        summaries: [
          summary({ model: 'small', tasks_total: 2, tasks_scored: 2, tasks_passed: 1, tasks_failed: 1, iq: 75, total_cost_usd: 2, avg_wall_time_sec: 10 }),
          summary({ model: 'large', tasks_total: 8, tasks_scored: 8, tasks_passed: 7, tasks_failed: 1, iq: 100, total_cost_usd: 8, avg_wall_time_sec: 20 }),
        ],
      },
    })

    expect(view.metrics.tasksTotal).toBe(10)
    expect(view.metrics.tasksPassed).toBe(8)
    expect(view.metrics.passRatePercent).toBe(80)
    expect(view.metrics.iq).toBe(95)
    expect(view.metrics.totalCostUsd).toBe(10)
    expect(view.metrics.avgWallTimeSec).toBe(18)
  })

  it('computes pass rate and IQ on the scored denominator, excluding infra errors', () => {
    const view = buildDashboardView({
      ...emptyBundle,
      summary: {
        ...emptyBundle.summary,
        summaries: [
          // 4 个任务里 1 个 runner_error：通过率应为 1/3 而非 1/4。
          summary({ model: 'flaky', tasks_total: 4, tasks_scored: 3, infra_error_count: 1, tasks_passed: 1, tasks_failed: 3, iq: 50 }),
          summary({ model: 'solid', tasks_total: 1, tasks_scored: 1, infra_error_count: 0, tasks_passed: 1, tasks_failed: 0, iq: 150 }),
        ],
      },
    })

    expect(view.metrics.tasksTotal).toBe(5)
    expect(view.metrics.tasksScored).toBe(4)
    expect(view.metrics.infraErrorCount).toBe(1)
    expect(view.metrics.tasksPassed).toBe(2)
    expect(view.metrics.passRatePercent).toBeCloseTo(50, 10)
    // IQ 按 tasks_scored 加权：(50×3 + 150×1) / 4。
    expect(view.metrics.iq).toBeCloseTo(75, 10)
  })

  it('falls back to counting every task when an old snapshot omits the scored fields', () => {
    const legacy = summary({ tasks_scored: undefined as unknown as number, infra_error_count: undefined as unknown as number, tasks_total: 2, tasks_passed: 1 })
    const view = buildDashboardView({
      ...emptyBundle,
      summary: { ...emptyBundle.summary, summaries: [legacy] },
    })

    expect(view.metrics.tasksScored).toBe(2)
    expect(view.metrics.infraErrorCount).toBe(0)
    expect(view.metrics.passRatePercent).toBe(50)
  })

  it('resets a stale filter after refreshed options no longer contain it', () => {
    expect(normalizeSelection('model-a', ['model-b', 'model-c'])).toBe('all')
    expect(normalizeSelection('model-b', ['model-b', 'model-c'])).toBe('model-b')
    expect(normalizeSelection('all', ['model-b'])).toBe('all')
  })

  it('appends the benchmark to series names only when model/effort collide across benchmarks', () => {
    const series: RadarSeries[] = [
      { model: 'model-a', effort: 'high', benchmark: 'deep-swe', axes: {} },
      { model: 'model-a', effort: 'high', benchmark: 'api-eval-demo', axes: {} },
      { model: 'model-b', effort: 'high', benchmark: 'deep-swe', axes: {} },
    ]

    expect(radarSeriesName(series[0]!, series)).toBe('model-a / high · deep-swe')
    expect(radarSeriesName(series[1]!, series)).toBe('model-a / high · api-eval-demo')
    expect(radarSeriesName(series[2]!, series)).toBe('model-b / high')
  })

  it('keeps legacy series names when the backend omits the benchmark field', () => {
    const series: RadarSeries[] = [{ model: 'model-a', effort: 'high', axes: {} }]
    expect(radarSeriesName(series[0]!, series)).toBe('model-a / high')
  })

  it('disambiguates summary row labels for multi-benchmark snapshots', () => {
    const deepSwe = summary({ benchmark: { name: 'deep-swe', version: 'local' } })
    const apiEval = summary({ benchmark: { name: 'api-eval-demo', version: '1' } })
    const rows = [deepSwe, apiEval]

    expect(summaryRowName(deepSwe, rows)).toBe('model-a / high · deep-swe')
    expect(summaryRowName(apiEval, rows)).toBe('model-a / high · api-eval-demo')
    expect(summaryRowName(deepSwe, [deepSwe])).toBe('model-a / high')
  })
})
