export interface ConfidenceInterval {
  method: 'wilson'
  level: number
  lower: number
  upper: number
}

export interface ModelEffortSummary {
  benchmark: { name: string; version: string }
  model: string
  effort: 'low' | 'medium' | 'high' | 'max'
  tasks_total: number
  /** 达到评分裁决的任务数（剔除 runner_error/verifier_error），pass_rate 与 IQ 的分母 */
  tasks_scored: number
  /** 基础设施错误数（网关不可达/agent 崩溃/校验器崩溃），不计入通过率与 IQ 分母 */
  infra_error_count: number
  tasks_passed: number
  tasks_failed: number
  pass_rate: number
  pass_rate_percent: number
  iq: number
  avg_cost_usd: number
  total_cost_usd: number
  avg_wall_time_sec: number
  tasks_per_hour: number
  avg_input_tokens: number
  avg_output_tokens: number
  output_tokens_per_min: number
  avg_agent_steps: number
  agent_steps_per_hour: number
  cost_per_pass_usd: number | null
  cost_per_iq_point_usd: number | null
  quota_percent_per_task: number
  estimated_tasks_per_week: number
  estimated_passes_per_week: number
  output_quota_percent_per_task: number | null
  confidence: ConfidenceInterval
}

export interface AggregateSummary {
  schema_version: '1.0'
  generated_at: string
  summaries: ModelEffortSummary[]
}

export interface RadarSeries {
  model: string
  effort: string
  /** 累积快照可含多个基准：同一模型/强度会有多行 series，用于区分重名（旧后端缺省）。 */
  benchmark?: string
  axes: Record<string, number>
}

export interface RunRecord {
  run_id: string
  benchmark: {
    name: string
    version: string
    task_id: string
    repo: string
    language: string
    task_path: string
  }
  model: {
    provider: string
    base_url_hash: string
    name: string
    effort_requested: string
    effort_effective: boolean
  }
  result: {
    status: string
    verifier_passed: boolean
    exit_code: number | null
    error_type: string | null
    error_message_redacted: string | null
  }
  usage: {
    input_tokens: number
    output_tokens: number
    cached_input_tokens: number
    total_tokens?: number | null
    agent_steps: number
    wall_time_sec: number
    first_token_sec?: number | null
    first_content_sec?: number | null
    generation_time_sec?: number | null
    output_tokens_per_sec?: number | null
    usage_estimated: boolean
  }
  cost: {
    currency: string
    input_cost: number
    cached_input_cost: number
    output_cost: number
    total_cost: number
  }
  artifacts: {
    patch_path: string | null
    log_path: string | null
    verifier_path: string | null
  }
  created_at: string
}

export interface DashboardBundle {
  snapshot_id: string
  summary: AggregateSummary
  iq_radar: RadarSeries[]
  quota_radar: RadarSeries[]
  runs: RunRecord[]
}

export interface SnapshotInfo {
  snapshot_id: string
  published_at: string
  source_job_id: string
  /** 累积（radar-v3）快照的全部来源；单源旧快照退化为 [source_job_id]。 */
  source_job_ids?: string[]
  models: string[]
  tasks_total: number
  is_current: boolean
  /** 源 run/批次是否仍存在于测试页「评测记录」；缺省视为存在（旧后端兼容） */
  source_exists?: boolean
}

export interface SnapshotDeleteResult {
  snapshot_id: string
  source_job_id: string
  current_snapshot_id: string | null
}
