import { createDemoDashboard } from './demoData'
import type { DashboardBundle, ModelEffortSummary, RadarSeries } from './types'

export interface DashboardMetrics {
  iq: number
  passRatePercent: number
  tasksTotal: number
  tasksScored: number
  tasksPassed: number
  infraErrorCount: number
  totalCostUsd: number
  avgWallTimeSec: number
  estimatedTasksPerWeek: number
}

export interface DashboardView {
  bundle: DashboardBundle
  isDemo: boolean
  metrics: DashboardMetrics
}

export function aggregateMetrics(summaries: ModelEffortSummary[]): DashboardMetrics {
  const tasksTotal = summaries.reduce((total, item) => total + item.tasks_total, 0)
  // 与后端 metrics/aggregate.py 口径一致：runner_error/verifier_error 属于
  // 基础设施故障，不进入通过率与 IQ 的分母（tasks_scored）。旧快照缺这些
  // 字段时退化为旧口径（全部任务计分、infra 计 0）。
  const scoredOf = (item: ModelEffortSummary) => item.tasks_scored ?? item.tasks_total
  const infraOf = (item: ModelEffortSummary) => item.infra_error_count ?? 0
  const tasksScored = summaries.reduce((total, item) => total + scoredOf(item), 0)
  const infraErrorCount = summaries.reduce((total, item) => total + infraOf(item), 0)
  const tasksPassed = summaries.reduce((total, item) => total + item.tasks_passed, 0)
  const weighted = (key: 'iq' | 'avg_wall_time_sec', weightOf: (item: ModelEffortSummary) => number) => {
    const weightTotal = summaries.reduce((total, item) => total + weightOf(item), 0)
    if (weightTotal === 0) return 0
    return summaries.reduce((total, item) => total + item[key] * weightOf(item), 0) / weightTotal
  }

  return {
    // IQ 定义在评分口径上（pass_rate×1.5），按 tasks_scored 加权。
    iq: weighted('iq', scoredOf),
    passRatePercent: tasksScored === 0 ? 0 : (tasksPassed / tasksScored) * 100,
    tasksTotal,
    tasksScored,
    tasksPassed,
    infraErrorCount,
    totalCostUsd: summaries.reduce((total, item) => total + item.total_cost_usd, 0),
    avgWallTimeSec: weighted('avg_wall_time_sec', (item) => item.tasks_total),
    estimatedTasksPerWeek: summaries.reduce((total, item) => total + item.estimated_tasks_per_week, 0),
  }
}

export function normalizeSelection(selection: string, available: string[]): string {
  return selection === 'all' || available.includes(selection) ? selection : 'all'
}

/**
 * 累积快照可含多个基准：同一 (model, effort) 会出现多行 series。仅当发生
 * 重名时追加基准名区分，保持单基准快照的图表文案不变。
 */
function disambiguatedLabel(
  base: string,
  benchmark: string | undefined,
  clash: boolean,
): string {
  return clash && benchmark ? `${base} · ${benchmark}` : base
}

export function radarSeriesName(item: RadarSeries, series: RadarSeries[]): string {
  const clash = series.filter(
    (other) => other.model === item.model && other.effort === item.effort,
  ).length > 1
  return disambiguatedLabel(`${item.model} / ${item.effort}`, item.benchmark, clash)
}

export function summaryRowName(item: ModelEffortSummary, summaries: ModelEffortSummary[]): string {
  const clash = summaries.filter(
    (other) => other.model === item.model && other.effort === item.effort,
  ).length > 1
  return disambiguatedLabel(`${item.model} / ${item.effort}`, item.benchmark?.name, clash)
}

function hasValues(bundle: DashboardBundle): boolean {
  return bundle.summary.summaries.length > 0 || bundle.iq_radar.length > 0 || bundle.quota_radar.length > 0 || bundle.runs.length > 0
}

export function buildDashboardView(bundle: DashboardBundle): DashboardView {
  const isDemo = !hasValues(bundle)
  const visibleBundle = isDemo ? createDemoDashboard() : bundle
  return {
    bundle: visibleBundle,
    isDemo,
    metrics: aggregateMetrics(visibleBundle.summary.summaries),
  }
}
