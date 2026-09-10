import type { DashboardBundle, ModelEffortSummary, RunRecord } from './types'

const GENERATED_AT = '2026-08-05T09:30:00Z'

interface DemoSummaryInput {
  model: string
  effort: ModelEffortSummary['effort']
  total: number
  passed: number
  /** runner_error/verifier_error 等基础设施错误数，不计入评分分母 */
  infra?: number
  avgCost: number
  avgTime: number
  weeklyTasks: number
}

function createSummary(input: DemoSummaryInput): ModelEffortSummary {
  const tasksScored = input.total - (input.infra ?? 0)
  const passRate = tasksScored ? input.passed / tasksScored : 0
  const iq = passRate * 150
  return {
    benchmark: { name: 'deep-swe-demo', version: '2026.08' },
    model: input.model,
    effort: input.effort,
    tasks_total: input.total,
    tasks_scored: tasksScored,
    infra_error_count: input.infra ?? 0,
    tasks_passed: input.passed,
    tasks_failed: input.total - input.passed,
    pass_rate: passRate,
    pass_rate_percent: passRate * 100,
    iq,
    avg_cost_usd: input.avgCost,
    total_cost_usd: input.avgCost * input.total,
    avg_wall_time_sec: input.avgTime,
    tasks_per_hour: 3600 / input.avgTime,
    avg_input_tokens: 7200 + input.total * 31,
    avg_output_tokens: 2100 + input.passed * 47,
    output_tokens_per_min: (2100 + input.passed * 47) / (input.avgTime / 60),
    avg_agent_steps: 8.4,
    agent_steps_per_hour: (8.4 * 3600) / input.avgTime,
    cost_per_pass_usd: (input.avgCost * input.total) / input.passed,
    cost_per_iq_point_usd: input.avgCost / iq,
    quota_percent_per_task: 100 / input.weeklyTasks,
    estimated_tasks_per_week: input.weeklyTasks,
    estimated_passes_per_week: Math.round(input.weeklyTasks * passRate),
    output_quota_percent_per_task: 72 / input.weeklyTasks,
    confidence: { method: 'wilson', level: 0.95, lower: Math.max(0, passRate - 0.16), upper: Math.min(1, passRate + 0.12) },
  }
}

const summaries = [
  createSummary({ model: 'example-35b', effort: 'high', total: 24, passed: 20, avgCost: 0.42, avgTime: 612, weeklyTasks: 238 }),
  createSummary({ model: 'deepseek-v4-flash', effort: 'max', total: 20, passed: 15, avgCost: 0.31, avgTime: 488, weeklyTasks: 322 }),
  createSummary({ model: 'glm-5.2', effort: 'high', total: 22, passed: 15, avgCost: 0.27, avgTime: 536, weeklyTasks: 286 }),
  // 带 2 个基础设施错误：演示「剔除 infra 后的评分口径」UI 提示。
  createSummary({ model: 'qwen3-coder-next', effort: 'medium', total: 18, passed: 11, infra: 2, avgCost: 0.18, avgTime: 421, weeklyTasks: 418 }),
]

const iqRadar = [
  { model: 'example-35b', effort: 'high', axes: { iq: 83.3, pass_rate: 83.3, stability: 86, speed: 72, cost_efficiency: 79 } },
  { model: 'deepseek-v4-flash', effort: 'max', axes: { iq: 75, pass_rate: 75, stability: 78, speed: 84, cost_efficiency: 86 } },
  { model: 'glm-5.2', effort: 'high', axes: { iq: 68.2, pass_rate: 68.2, stability: 74, speed: 78, cost_efficiency: 90 } },
  { model: 'qwen3-coder-next', effort: 'medium', axes: { iq: 61.1, pass_rate: 61.1, stability: 70, speed: 91, cost_efficiency: 96 } },
]

const quotaRadar = [
  { model: 'example-35b', effort: 'high', axes: { quota_remaining_friendliness: 82, tasks_per_week: 72, passes_per_week: 76, cost_per_pass_efficiency: 78, token_efficiency: 73 } },
  { model: 'deepseek-v4-flash', effort: 'max', axes: { quota_remaining_friendliness: 86, tasks_per_week: 79, passes_per_week: 82, cost_per_pass_efficiency: 87, token_efficiency: 84 } },
  { model: 'glm-5.2', effort: 'high', axes: { quota_remaining_friendliness: 90, tasks_per_week: 75, passes_per_week: 74, cost_per_pass_efficiency: 91, token_efficiency: 88 } },
  { model: 'qwen3-coder-next', effort: 'medium', axes: { quota_remaining_friendliness: 94, tasks_per_week: 92, passes_per_week: 86, cost_per_pass_efficiency: 97, token_efficiency: 93 } },
]

const runInputs = [
  ['django-query-regression', 'example-35b', 'high', 'passed', 558, 4210, 0.39, 'python'],
  ['react-stream-cleanup', 'deepseek-v4-flash', 'max', 'passed', 431, 3184, 0.28, 'typescript'],
  ['rust-index-boundary', 'glm-5.2', 'high', 'failed', 614, 2750, 0.31, 'rust'],
  ['go-context-cancel', 'qwen3-coder-next', 'medium', 'passed', 389, 2416, 0.16, 'go'],
  ['fastapi-schema-union', 'example-35b', 'high', 'passed', 603, 3992, 0.44, 'python'],
  ['vite-worker-resolution', 'deepseek-v4-flash', 'max', 'failed', 502, 2921, 0.33, 'typescript'],
  ['sqlalchemy-session-scope', 'glm-5.2', 'high', 'passed', 497, 3017, 0.25, 'python'],
  ['node-esm-loader', 'qwen3-coder-next', 'medium', 'passed', 416, 2268, 0.17, 'javascript'],
] as const

function createRun(input: (typeof runInputs)[number], index: number): RunRecord {
  const [taskId, model, effort, status, wallTime, outputTokens, cost, language] = input
  return {
    run_id: `demo-${index + 1}-${taskId}`,
    benchmark: { name: 'deep-swe-demo', version: '2026.08', task_id: taskId, repo: `iqradar/${taskId}`, language, task_path: `tasks/${taskId}` },
    model: { provider: 'demo-provider', base_url_hash: 'sha256:demo', name: model, effort_requested: effort, effort_effective: true },
    result: { status, verifier_passed: status === 'passed', exit_code: status === 'passed' ? 0 : 1, error_type: status === 'passed' ? null : 'verifier_failed', error_message_redacted: null },
    usage: { input_tokens: outputTokens * 3, output_tokens: outputTokens, cached_input_tokens: Math.round(outputTokens * 0.8), agent_steps: 8 + (index % 4), wall_time_sec: wallTime, usage_estimated: false },
    cost: { currency: 'USD', input_cost: cost * 0.46, cached_input_cost: cost * 0.08, output_cost: cost * 0.46, total_cost: cost },
    artifacts: { patch_path: null, log_path: `data/artifacts/demo-${index + 1}/run.log`, verifier_path: null },
    created_at: new Date(Date.parse(GENERATED_AT) - index * 37 * 60 * 1000).toISOString(),
  }
}

export function createDemoDashboard(): DashboardBundle {
  return {
    snapshot_id: 'demo-dashboard-v1',
    summary: { schema_version: '1.0', generated_at: GENERATED_AT, summaries: structuredClone(summaries) },
    iq_radar: structuredClone(iqRadar),
    quota_radar: structuredClone(quotaRadar),
    runs: runInputs.map(createRun),
  }
}
